"""O‘qilgan natijani tekshirish. Xatolar saqlashga to‘sqinlik qiladi, ogohlantirishlar — yo‘q."""
from decimal import Decimal

from .catalog import METRIC_BY_KEY, SECTION_BY_KEY
from .districts import DISTRICT_KEYS, TOTAL_KEY
from .extract import ParsedReport


def validate(rep: ParsedReport) -> ParsedReport:
    """`rep.errors` va `rep.warnings` ga tekshiruv natijalarini qo‘shadi."""
    for skey, sd in rep.sections.items():
        title = SECTION_BY_KEY[skey].short

        missing = [d for d in DISTRICT_KEYS + (TOTAL_KEY,) if d not in sd.cur]
        if missing:
            rep.errors.append(f"«{title}»: {len(missing)} ta qator topilmadi ({', '.join(missing[:4])}…).")
            continue

        # 1) hududlar yig‘indisi == "Jami"
        for (s, mkey), mdef in METRIC_BY_KEY.items():
            if s != skey or not mdef.additive or mkey not in sd.cur[TOTAL_KEY]:
                continue
            total = sd.cur[TOTAL_KEY][mkey]
            summed = sum((sd.cur[d].get(mkey, Decimal(0)) for d in DISTRICT_KEYS), Decimal(0))
            if summed != total:
                rep.warnings.append(
                    f"«{title}» / {mdef.label}: hududlar yig‘indisi {summed:,.0f}, PDF dagi «Jami» {total:,.0f}."
                )

        # 2) foizlar 0..100 oralig‘ida
        for d, vals in sd.cur.items():
            for mkey, v in vals.items():
                mdef = METRIC_BY_KEY[(skey, mkey)]
                if mdef.kind == "pct" and not (Decimal(0) <= v <= Decimal(100)):
                    rep.warnings.append(f"«{title}» / {d} / {mdef.label}: foiz oraliqdan tashqarida ({v}).")

        # 3) fayl ichidagi o‘zgarish: joriy - oldingi == PDF dagi ↑↓ (oldingi holat ustuni bor bo‘lsa)
        for d in sd.cur:
            if d == TOTAL_KEY:
                continue  # PDF dagi «Jami» qatori strelkasi ba’zan yangilanmay qoladi; hududlar bo‘yicha tekshiramiz
            for mkey, pv in sd.prev.get(d, {}).items():
                cv = sd.cur[d].get(mkey)
                pdf_delta = sd.pdf_delta.get(d, {}).get(mkey)
                if cv is not None and pdf_delta is not None and (cv - pv) != pdf_delta:
                    rep.warnings.append(
                        f"«{title}» / {d} / {METRIC_BY_KEY[(skey, mkey)].label}: "
                        f"hisoblangan farq {cv - pv:+,.0f}, PDF dagi strelka {pdf_delta:+,}."
                    )
    return rep


def check_against_db(rep: ParsedReport, lookup) -> list:
    """Bazadagi qiymatlar bilan solishtirish.

    `lookup(section, district, metric, date) -> Decimal | None`.
    Oldingi sana (A) holati bazada bo‘lsa, PDF dagi A qiymatiga mos kelishi kerak.
    Qaytaradi: ogohlantirishlar ro‘yxati.
    """
    out = []
    for skey, sd in rep.sections.items():
        title = SECTION_BY_KEY[skey].short
        for d, prev_vals in sd.prev.items():
            for mkey, pv in prev_vals.items():
                db_val = lookup(skey, d, mkey, rep.prev_date)
                if db_val is not None and db_val != pv:
                    out.append(
                        f"«{title}» / {d} / {METRIC_BY_KEY[(skey, mkey)].label}: "
                        f"{rep.prev_date:%d.%m.%Y} uchun bazada {db_val:,.0f}, bu PDF da {pv:,.0f}."
                    )
        # PDF dagi ↑↓ ni bazadagi oldingi kun bilan solishtirish
        for d, deltas in sd.pdf_delta.items():
            if d == TOTAL_KEY:
                continue
            for mkey, dv in deltas.items():
                if mkey in sd.prev.get(d, {}):
                    continue  # ichki tekshiruvda ko‘rilgan
                cv = sd.cur[d].get(mkey)
                av = lookup(skey, d, mkey, rep.prev_date)
                if cv is not None and av is not None and (cv - av) != dv:
                    out.append(
                        f"«{title}» / {d} / {METRIC_BY_KEY[(skey, mkey)].label}: "
                        f"bazadagi {rep.prev_date:%d.%m.%Y} bilan farq {cv - av:+,.0f}, PDF strelkasi {dv:+,}."
                    )
    return out
