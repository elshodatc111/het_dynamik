"""PDF hisobotni o‘qish: jadvallar -> tuzilgan natija."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pdfplumber

from .catalog import METRIC_BY_KEY, SECTION_BY_KEY
from .districts import DISTRICT_KEYS, TOTAL_KEY, district_key
from .layouts import find_layout

DATE_RANGE = re.compile(r"Динамика:\s*(\d{2}\.\d{2}\.\d{4})\s*[-–]\s*(\d{2}\.\d{2}\.\d{4})")
TOTAL_MERGED = re.compile(r"^(Жами|Jami)\s+([\d\s]+)$", re.I)
ARROWS = "↑↓"

# Sahifa sarlavhasi -> bo‘lim (tartib muhim: aniqroq qoidalar oldin)
SECTION_RULES = (
    ("tafovut_balans", ("баланс ва истеъмолчилар",)),
    ("balans_yuklama", ("баланс ҳисоблагичлар ва трансформаторлар",)),
    ("billing_tafovut", ("биллинг",)),
    ("smartapp", ("smartapp",)),
    ("obxod48", ("48 соат",)),
    ("aloqasiz", ("алоқага чиқмаган",)),
    ("uzilgan", ("тармоқдан узилган", "ўчирилган ҳолда")),
    ("konsentratorlar", ("концентратор",)),
)


@dataclass
class SectionData:
    key: str
    variant: str
    page: int
    # {district_key: {metric: Decimal}} — joriy sana (B)
    cur: dict = field(default_factory=dict)
    # {district_key: {metric: Decimal}} — oldingi sana (A) holati
    prev: dict = field(default_factory=dict)
    # {district_key: {metric: int}} — PDF dagi ↑↓ o‘zgarishlar (faqat tekshiruv uchun)
    pdf_delta: dict = field(default_factory=dict)


@dataclass
class ParsedReport:
    prev_date: date | None = None
    report_date: date | None = None
    pages: int = 0
    sections: dict = field(default_factory=dict)   # key -> SectionData
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    skipped_pages: list = field(default_factory=list)

    @property
    def variant(self) -> str:
        vs = sorted({s.variant for s in self.sections.values()})
        return "+".join(vs) if vs else "?"

    @property
    def ok(self) -> bool:
        return not self.errors


# ------------------------------------------------------------------ yordamchilar
def _clean(cell) -> str:
    return (cell or "").replace("\n", " ").replace("\xa0", " ").strip()


def parse_number(text: str):
    """'10 633' -> 10633 ; '3,2%' -> 3.2 ; '-' / '' -> 0. Yaroqsiz bo‘lsa None."""
    t = _clean(text).replace("%", "")
    t = re.sub(r"\s+", "", t).replace(",", ".")
    if t in ("", "-", "–", "—"):
        return Decimal(0)
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def parse_delta(text: str):
    t = _clean(text)
    if not t:
        return 0
    neg = "↓" in t or "-" in t.replace("–", "-")
    digits = re.sub(r"[^\d,\.]", "", t).replace(",", ".")
    if not digits:
        return 0
    try:
        v = int(round(float(digits)))
    except ValueError:
        return None
    return -v if neg else v


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%d.%m.%Y").date()


def _detect_section(page_text: str):
    head = "\n".join((page_text or "").split("\n")[:3]).lower()
    for key, needles in SECTION_RULES:
        if any(n in head for n in needles):
            return key
    return None


def _first_filled(row):
    for i, c in enumerate(row):
        if _clean(c):
            return i
    return None


def _data_rows(table):
    """Jadvaldagi hudud/Jami qatorlarini (offset, katak ro‘yxati) ko‘rinishida qaytaradi."""
    rows = []
    for raw in table:
        off = _first_filled(raw)
        if off is None:
            continue
        name = _clean(raw[off])
        cells = [_clean(c) for c in raw[off:]]
        m = TOTAL_MERGED.match(name)
        if m:  # "Жами 17 498" — nom va birinchi qiymat bitta katakka qo‘shilib qolgan
            cells[0] = "Жами"
            if len(cells) > 1 and not cells[1]:
                cells[1] = m.group(2).strip()
            name = "Жами"
        key = district_key(name)
        if key:
            rows.append((key, cells))
    return rows


# ------------------------------------------------------------------ asosiy funksiya
def parse_pdf(path) -> ParsedReport:
    rep = ParsedReport()
    try:
        pdf = pdfplumber.open(path)
    except Exception as exc:  # noqa: BLE001
        rep.errors.append(f"PDF faylni ochib bo‘lmadi: {exc}")
        return rep

    with pdf:
        rep.pages = len(pdf.pages)
        ranges = set()
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            m = DATE_RANGE.search(text)
            if m:
                ranges.add((m.group(1), m.group(2)))

            section = _detect_section(text)
            tables = page.extract_tables()
            candidate = None
            for t in tables:
                rows = _data_rows(t)
                if len(rows) >= 10:
                    candidate = (t, rows)
                    break
            if candidate is None:
                rep.skipped_pages.append(pno)  # diagramma/dashboard sahifasi
                continue
            if section is None:
                rep.errors.append(f"{pno}-sahifa: bo‘lim sarlavhasi taniilmadi.")
                continue
            if section in rep.sections:
                rep.errors.append(f"{pno}-sahifa: «{section}» bo‘limi PDF da ikki marta uchradi.")
                continue
            _parse_section(rep, section, pno, *candidate)

        if not ranges:
            rep.errors.append("PDF da «Динамика: dd.mm.yyyy - dd.mm.yyyy» sanalari topilmadi.")
        elif len(ranges) > 1:
            rep.errors.append(f"PDF sahifalarida har xil sanalar bor: {sorted(ranges)}")
        else:
            a, b = next(iter(ranges))
            rep.prev_date, rep.report_date = _parse_date(a), _parse_date(b)
            if rep.prev_date >= rep.report_date:
                rep.errors.append(f"Sanalar tartibi noto‘g‘ri: {a} – {b}")

    if not rep.sections and not rep.errors:
        rep.errors.append("PDF da hech qanday jadval topilmadi.")
    return rep


def _parse_section(rep: ParsedReport, section: str, pno: int, table, rows):
    ncols = len(rows[0][1])
    total_cells = next((c for k, c in rows if k == TOTAL_KEY), rows[0][1])
    layout = find_layout(section, ncols, total_cells)
    if layout is None:
        rep.errors.append(
            f"{pno}-sahifa «{SECTION_BY_KEY[section].short}»: noma’lum jadval tuzilmasi "
            f"({ncols} ta ustun). Yangi shablon qo‘shish kerak (parser/layouts.py)."
        )
        return
    variant, spec, opts = layout
    coalesce = opts.get("coalesce", {})
    sd = SectionData(key=section, variant=variant, page=pno)

    seen = set()
    for dkey, cells in rows:
        if dkey in seen:
            rep.warnings.append(f"{pno}-sahifa: «{dkey}» qatori takrorlandi, birinchisi olindi.")
            continue
        seen.add(dkey)
        if len(cells) != len(spec):
            rep.errors.append(
                f"{pno}-sahifa, «{dkey}» qatori: {len(cells)} ta katak, kutilgani {len(spec)}."
            )
            continue
        cur, prev, delta = {}, {}, {}
        last_metric = None
        for idx, item in enumerate(spec):
            cell = cells[idx]
            if item in (None, "district"):
                continue
            if item == "D":
                if last_metric is not None:
                    dv = parse_delta(cell)
                    if dv is not None:
                        delta[last_metric] = dv
                continue
            name, target = item, cur
            if name.startswith("%"):
                name = name[1:]
            elif name.endswith("@prev"):
                name, target = name[:-5], prev
            if name in coalesce:  # birlashtirilgan katak: oraliqdagi barcha bo‘laklarni yig‘amiz
                cell = " ".join(cells[i] for i in coalesce[name] if i < len(cells) and cells[i])
            val = parse_number(cell)
            if val is None:
                rep.errors.append(f"{pno}-sahifa, «{dkey}», {idx}-ustun: raqam emas: «{cell}»")
                continue
            target[name] = val
            if not item.startswith("%"):   # foiz ustunidan keyingi ↑↓ — oldingi SON ustuniga tegishli
                last_metric = name
        for name in list(cur) + list(prev):
            if (section, name) not in METRIC_BY_KEY:
                rep.errors.append(f"Katalogda yo‘q ko‘rsatkich: {section}.{name}")
        sd.cur[dkey], sd.prev[dkey], sd.pdf_delta[dkey] = cur, prev, delta

    rep.sections[section] = sd
