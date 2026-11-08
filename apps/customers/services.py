"""Fayl tekshiruvi va saqlash."""
from __future__ import annotations

from io import BytesIO

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from openpyxl import load_workbook

from . import rules
from .models import Customer, EtkStat, Submission


class FileRejected(Exception):
    """Butun fayl qabul qilinmaydi (format, hajm, qatorlar soni)."""


def read_rows(data: bytes) -> list[tuple[int, list]]:
    """Lист1 dagi bo‘sh bo‘lmagan qatorlar: [(Excel qator raqami, [A..N qiymatlari])]. Boshqa varaqlar o‘qilmaydi."""
    if len(data) > rules.MAX_BYTES:
        raise FileRejected("Fayl hajmi 3 MB dan oshmasligi kerak.")
    try:
        wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 — buzuq yoki Excel bo‘lmagan fayl
        raise FileRejected("Fayl o‘qilmadi. Faqat Excel (.xlsx) fayl yuboring.") from None
    try:
        ws = next((w for w in wb.worksheets if rules._norm(w.title) in rules.SHEET_NAMES), None)
        if ws is None:
            raise FileRejected("«Лист1» varag‘i topilmadi. Faqat shablondagi birinchi varaq o‘qiladi.")
        rows, scanned = [], 0
        head = None
        for i, row in enumerate(ws.iter_rows(min_row=1, max_col=rules.NCOLS, values_only=True), 1):
            row = list(row) + [None] * (rules.NCOLS - len(row))
            if i == 2:
                head = row
                msg = rules.check_headers(row)
                if msg:
                    raise FileRejected(msg)
                continue
            if i < 3:
                continue
            scanned += 1
            if scanned > 5000:
                raise FileRejected(f"Faylda juda ko‘p qator bor. Bir faylda {rules.MAX_ROWS} tadan ko‘p qator bo‘lmasin.")
            if all(rules.cell_text(v) == "" for v in row[1:]):
                continue
            rows.append((i, row))
            if len(rows) > rules.MAX_ROWS:
                raise FileRejected(f"Faylda {rules.MAX_ROWS} tadan ko‘p qator bor. Bir faylda {rules.MAX_ROWS} tadan oshmasin, bir necha faylga bo‘lib yuboring.")
        if head is None:
            raise FileRejected("Fayl bo‘sh yoki shablon sarlavhasi topilmadi.")
        if not rows:
            raise FileRejected("Faylda ma’lumot qatori topilmadi (3-qatordan boshlab to‘ldiring).")
        return rows
    finally:
        wb.close()


def process(user, filename: str, data: bytes, on_step=None) -> dict:
    """Faylni tekshiradi, to‘g‘ri qatorlarni saqlaydi. Qaytaradi: {submission, total, ok, bad, error_file(bytes|None), samples[]}.
    FileRejected — fayl butunlay rad etilganda (jurnalga ham yoziladi)."""
    try:
        rows = read_rows(data)
    except FileRejected as exc:
        Submission.objects.create(user=user, filename=filename[:200], rejected=str(exc)[:300])
        raise
    clean: list[tuple[int, list, dict]] = []
    bad: list[tuple[int, list, str]] = []
    samples: list[str] = []
    for xl_row, raw_list in rows:
        raw = dict(zip(rules.KEYS, raw_list))
        values, errs = rules.validate_row(raw)
        if errs:
            bad.append((xl_row, raw_list, "; ".join(errs)))
            if len(samples) < 5:
                samples.append(f"{xl_row}-qator: {errs[0]}")
        else:
            clean.append((xl_row, raw_list, values))
    # H takrorlari: avval bazada bo‘lsa — rad; fayl ichida birinchisi qabul, keyingilari rad
    existing = set(Customer.objects.filter(meter_no__in=[v["meter_no"] for _r, _l, v in clean]).values_list("meter_no", flat=True))
    seen: dict[str, int] = {}
    final: list[tuple[int, dict]] = []
    for xl_row, raw_list, v in clean:
        no = v["meter_no"]
        if no in existing:
            msg = "Hisoblagich raqami takrorlangan: bu raqam avval yuklangan"
        elif no in seen:
            msg = f"Hisoblagich raqami takrorlangan: faylda {seen[no]}-qatorda ham bor"
        else:
            seen[no] = xl_row
            final.append((xl_row, v))
            continue
        bad.append((xl_row, raw_list, msg))
        if len(samples) < 5:
            samples.append(f"{xl_row}-qator: {msg}")
    if on_step:
        on_step("💾 Tekshiruvdan o‘tganlar saqlanmoqda…")
    sub = Submission.objects.create(user=user, filename=filename[:200], total=len(rows))
    saved = 0
    saved_rows: set[int] = set()
    try:
        with transaction.atomic():
            Customer.objects.bulk_create([Customer(submission=sub, user=user, **v) for _r, v in final], batch_size=200)
        saved = len(final)
        saved_rows = {r for r, _v in final}
    except IntegrityError:   # poyga: shu orada boshqasi shu raqamni saqlab qo‘ydi — qatorma-qator
        for xl_row, v in final:
            try:
                with transaction.atomic():
                    Customer.objects.create(submission=sub, user=user, **v)
                saved += 1
                saved_rows.add(xl_row)
            except IntegrityError:
                raw_list = next(l for r, l, _v in clean if r == xl_row)
                bad.append((xl_row, raw_list, "Hisoblagich raqami takrorlangan: bu raqam avval yuklangan"))
    sub.ok, sub.bad = saved, len(rows) - saved
    sub.save(update_fields=["ok", "bad"])
    record_stats(rows, saved_rows)
    samples = samples  # birinchi 5 ta xato (qator tartibida emas bo‘lishi mumkin)
    return {"submission": sub, "total": sub.total, "ok": sub.ok, "bad": sub.bad, "samples": samples,
            "error_file": rules.build_error_file([(l, m) for _r, l, m in sorted(bad, key=lambda t: t[0])]) if bad else None}


def record_stats(rows, saved_rows: set[int], day=None):
    """ETK kodi bo‘yicha kunlik sanoq: yuborilgan, to‘g‘ri (saqlangan) va xato qatorlar."""
    day = day or timezone.localdate()
    agg: dict[str, list[int]] = {}
    for xl_row, raw in rows:
        etk = rules.cell_text(raw[1])
        etk = etk if etk in rules.ETK_CODES else ""      # noto‘g‘ri/bo‘sh kod — «noma’lum» guruhi
        a = agg.setdefault(etk, [0, 0, 0])
        a[0] += 1
        a[1 if xl_row in saved_rows else 2] += 1
    for etk, (sent, ok, bad) in agg.items():
        stat, created = EtkStat.objects.get_or_create(date=day, etk_code=etk, defaults={"sent": sent, "ok": ok, "bad": bad})
        if not created:
            EtkStat.objects.filter(pk=stat.pk).update(sent=F("sent") + sent, ok=F("ok") + ok, bad=F("bad") + bad)
