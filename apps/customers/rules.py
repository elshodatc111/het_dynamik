"""CUSTOMER_SHABLON.xlsx: ustunlar, tekshiruv qoidalari va shablon fayli."""
from __future__ import annotations

import re
from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

ETK_CODES = ("10207", "10212", "10220", "10224", "10229", "10231", "10232", "10233", "10234", "10235", "10237",
             "10240", "10242", "10245", "10246", "10250", "10401")
METER_TYPES = ("EX18", "EX18-1", "EX518 100A", "EX518 5A", "EX518 100V", "TE")
COMM_TYPES = ("GPRS", "PLC", "RS485")
PHASES = ("1", "3", "4")
CONSUMER_TYPES = ("1", "2")
MAX_ROWS = 500
MAX_BYTES = 3 * 1024 * 1024
SHEET = "Лист1"
SHEET_NAMES = {"лист1", "list1", "sheet1"}

# (kalit, Excel sarlavhasi — shablondagidek, xato matnlarida ko‘rinadigan nom)
COLUMNS = [
    ("n", "№", "№"),
    ("etk_code", "ЭТК КОДИ (киритилиши шарт)", "ETK kodi"),
    ("trans_code", "ТРАНСФОРМАТОР КОДИ (киритилиши шарт)", "Transformator kodi"),
    ("trans_name", "ТРАНСФОРМАТОР НОМИ (киритилиши шарт)", "Transformator nomi"),
    ("consumer_name", "ИСТЕЪМОЛЧИ НОМИ(киритилиши шарт)", "Iste’molchi nomi"),
    ("consumer_code", "ИСТЕЪМОЛЧИ КОДИ /ШАРТНОМА РАҚАМИ (киритилиши шарт)", "Iste’molchi kodi / shartnoma raqami"),
    ("meter_type", "АМАЛДАГИ ҲИСОБЛАГИЧ ТУРИ( Улгуржи истеъмолчилар учун киртилиши шарт EX18, EX18-1, EX518 100A, EX518 5A, EX518 100V, TE)", "Hisoblagich turi"),
    ("meter_no", "АМАЛДАГИ ҲИСОБЛАГИЧ РАҚАМИ( Улгуржи истеъмолчилар учун киритилиши шарт)", "Hisoblagich raqami"),
    ("address", "ИСТЕЪМОЛЧИ МАНЗИЛИ(киритилиши шарт)", "Iste’molchi manzili"),
    ("phase", "1-Ф,3-Ф, 3-Ф 100В (киритилиши шарт; бир фазалик истеъмолчи учун 1 рақами, уч фазалик истеъмолчи учун 3 рақами, уч фазалик 100В истеъмиолчи учун 4 қўйилади)", "Faza (1, 3, 4)"),
    ("ct_ratio", "ИСТЕЪМОЛЧИ ТОК ТРАНСФОРМАТОРИ КОЭФФИЦИЕНТИ(ток трансформатори бўлмаган истеъмолчиларга 1/1 рақами қўйилсин)", "Tok transformatori koeffitsienti"),
    ("consumer_type", "ИСТЕЪМОЛЧИ ТУРИ (киритилиши шарт. Маиший истеъмолчи учун 1 рақами қўйилсин, улгуржи истеъмолчи учун 2 рақами қўйилсин)\n", "Iste’molchi turi (1, 2)"),
    ("comm_type", "Ҳисоблагич алоқа типи (киритилиши шарт-GPRS,PLC,RS485)  \n", "Aloqa turi"),
    ("vt_ratio", "ИСТЕЪМОЛЧИ КУЧЛАНИШ ТРАНСФОРМАТОРИ КОЭФФИЦИЕНТИ(агар қиймат киритилмаса, коэффициент 1/1 деб қабул қилинади)", "Kuchlanish transformatori koeffitsienti"),
]
KEYS = [c[0] for c in COLUMNS]
LABEL = {c[0]: c[2] for c in COLUMNS}
NCOLS = len(COLUMNS)
ERR_HEADER = "Xatolar"


def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def check_headers(row) -> str:
    """2-qator sarlavhalari shablonga mosligi. Mos bo‘lmasa — xabar, aks holda ''."""
    for i, (_k, head, label) in enumerate(COLUMNS):
        got = _norm(row[i] if i < len(row) else "")
        want = _norm(head)[:18]
        if not got.startswith(want):
            return f"Fayl shablonga mos emas: {get_column_letter(i + 1)} ustun sarlavhasi «{label}» bo‘lishi kerak. Yangi shablonni «Customer shablon» tugmasi orqali oling."
    return ""


# ------------------------------------------------------------------ qiymatlar
def cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    if isinstance(v, (datetime, date)):
        return v.strftime("%d.%m.%Y")
    return str(v).strip()


RATIO_RE = re.compile(r"^(\d{1,6})\s*/\s*(\d{1,6})$")


def validate_row(raw: dict) -> tuple[dict, list[str]]:
    """raw: kalit -> asl katak qiymati. Qaytaradi: (tozalangan qiymatlar, xatolar ro‘yxati)."""
    out, errs = {}, []
    text = {k: cell_text(raw.get(k)) for k in KEYS if k != "n"}

    def need(k):
        if not text[k]:
            errs.append(f"{LABEL[k]} kiritilmagan")
            return False
        return True

    # ETK kodi
    if need("etk_code"):
        if text["etk_code"] not in ETK_CODES:
            errs.append(f"ETK kodi noto‘g‘ri («{text['etk_code'][:12]}»): ruxsat etilgan 17 ta koddan biri bo‘lishi kerak")
        else:
            out["etk_code"] = text["etk_code"]
    # Transformator kodi: aniq 5 xonali, boshidagi nol saqlangan (matn)
    if need("trans_code"):
        v = text["trans_code"]
        if not re.fullmatch(r"\d{5}", v):
            hint = ""
            if isinstance(raw.get("trans_code"), (int, float)) and len(v) < 5:
                hint = " (katak matn formatida emas, boshidagi nol yo‘qolgan — katakni «Matn» formatiga o‘tkazib, qayta yozing)"
            errs.append(f"Transformator kodi 5 xonali raqam bo‘lishi kerak, masalan 01328{hint}")
        else:
            out["trans_code"] = v
    for k in ("trans_name", "consumer_name", "consumer_code", "address"):
        if need(k):
            out[k] = text[k]
    # Hisoblagich turi
    if need("meter_type"):
        canon = {re.sub(r"\s+", " ", t).lower(): t for t in METER_TYPES}
        t = canon.get(re.sub(r"\s+", " ", text["meter_type"]).lower())
        if t is None:
            errs.append("Hisoblagich turi faqat EX18, EX18-1, EX518 100A, EX518 5A, EX518 100V, TE bo‘lishi mumkin")
        else:
            out["meter_type"] = t
    # Hisoblagich raqami: 12 xonali
    if need("meter_no"):
        if not re.fullmatch(r"\d{12}", text["meter_no"]):
            errs.append("Hisoblagich raqami 12 xonali raqam bo‘lishi kerak")
        else:
            out["meter_no"] = text["meter_no"]
    # Faza, istemolchi turi, aloqa turi
    if need("phase"):
        if text["phase"] not in PHASES:
            errs.append("Faza faqat 1, 3 yoki 4 bo‘lishi mumkin")
        else:
            out["phase"] = text["phase"]
    if need("consumer_type"):
        if text["consumer_type"] not in CONSUMER_TYPES:
            errs.append("Iste’molchi turi faqat 1 (maishiy) yoki 2 (ulgurji) bo‘lishi mumkin")
        else:
            out["consumer_type"] = text["consumer_type"]
    if need("comm_type"):
        up = text["comm_type"].upper().replace(" ", "")
        if up not in COMM_TYPES:
            errs.append("Aloqa turi faqat GPRS, PLC yoki RS485 bo‘lishi mumkin")
        else:
            out["comm_type"] = up
    # Transformator koeffitsientlari: a/b
    for k in ("ct_ratio", "vt_ratio"):
        if not need(k):
            continue
        raw_v = raw.get(k)
        if isinstance(raw_v, (datetime, date)) or not RATIO_RE.match(text[k]):
            errs.append(f"{LABEL[k]} «son/son» ko‘rinishida bo‘lishi kerak, masalan 200/5 yoki 1/1 (katakni «Matn» formatida kiriting)")
            continue
        a, b = map(int, RATIO_RE.match(text[k]).groups())
        if a == 0 or b == 0:
            errs.append(f"{LABEL[k]}da nol bo‘lishi mumkin emas")
        else:
            out[k] = f"{a}/{b}"
    return out, errs


# ------------------------------------------------------------------ Excel fayl (shablon / xatolar / yuklab olish)
# Dizayn CUSTOMER_SHABLON.xlsx dan olingan: oq fon, hamma kataklarda ingichka chegara, markazlashgan sarlavha
# (ustunlarga qarab 8–11 pt, matn o‘ralgan), 2-qator balandligi 78,75, ustun kengliklari.
WIDTHS = [4.14, 13.29, 19.29, 19.71, 40.14, 21.86, 24.71, 16.43, 42.71, 26.14, 27.43, 20.43, 17.86, 21.57]
HEAD_PT = [11, 11, 11, 11, 11, 10, 9, 9, 11, 8, 8, 8, 8, 8]
HEAD_H = 78.75
TNR_COLS = {3, 4, 5, 7, 8}        # 0 dan: D, E, F, H, I — Times New Roman, matn o‘raladi
LEFT_COLS = {2, 4}                # C va E — chapga tekislangan
TEXT_FMT_COLS = {1, 2, 3, 5, 7, 9, 10, 11, 12, 13}   # B, D, F, H, J..N — «Matn» formati (boshidagi nol saqlanadi)
_THIN = Side(style="thin")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
WHITE = PatternFill("solid", fgColor="FFFFFF")


def body_style(i: int) -> dict:
    """Ma’lumot katagi uslubi (i — 0 dan ustun raqami)."""
    tnr = i in TNR_COLS
    return {
        "font": Font(name="Times New Roman" if tnr else "Calibri", size=12, color="FF0000" if i == 5 else None),
        "alignment": Alignment(horizontal="left" if i in LEFT_COLS else "center", vertical=None if tnr or i in LEFT_COLS else "center",
                               wrap_text=True if (tnr or i in (5, 6)) else None),
        "border": BORDER, "number_format": "@" if i in TEXT_FMT_COLS else "General",
    }


def head_style(i: int, row: int) -> dict:
    return {"font": Font(name="Calibri", size=12 if row == 1 else HEAD_PT[i]), "fill": WHITE, "border": BORDER,
            "alignment": Alignment(horizontal="center", vertical="center", wrap_text=True if row == 2 and i else None),
            "number_format": "@" if i in TEXT_FMT_COLS and row == 2 else "General"}


def _apply(cell, st: dict):
    for k, v in st.items():
        setattr(cell, k, v)


def _header(ws, extra_header: bool = False):
    for i in range(NCOLS):
        c = ws.cell(row=1, column=i + 1, value=i)
        _apply(c, head_style(i, 1))
        c = ws.cell(row=2, column=i + 1, value=COLUMNS[i][1] if i else "№")
        _apply(c, head_style(i, 2))
    if extra_header:
        c = ws.cell(row=2, column=NCOLS + 1, value=ERR_HEADER)
        _apply(c, {"font": Font(name="Calibri", size=11, bold=True, color="C00000"), "fill": PatternFill("solid", fgColor="FCE4D6"),
                   "border": BORDER, "alignment": Alignment(horizontal="center", vertical="center")})
        ws.column_dimensions[get_column_letter(NCOLS + 1)].width = 60
    ws.row_dimensions[2].height = HEAD_H
    for i, w in enumerate(WIDTHS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _add_validations(ws, last_row: int):
    def dv(col, formula, msg, kind="list", operator=None):
        d = DataValidation(type=kind, formula1=formula, operator=operator, allow_blank=True, showErrorMessage=True,
                           errorStyle="stop", errorTitle="Noto‘g‘ri qiymat", error=msg)
        d.add(f"{col}3:{col}{last_row}")
        ws.add_data_validation(d)

    dv("B", '"' + ",".join(ETK_CODES) + '"', "Faqat ro‘yxatdagi ETK kodlaridan birini tanlang.")
    dv("G", '"' + ",".join(METER_TYPES) + '"', "Faqat EX18, EX18-1, EX518 100A, EX518 5A, EX518 100V, TE.")
    dv("J", '"1,3,4"', "Faqat 1, 3 yoki 4.")
    dv("L", '"1,2"', "Faqat 1 (maishiy) yoki 2 (ulgurji).")
    dv("M", '"GPRS,PLC,RS485"', "Faqat GPRS, PLC yoki RS485.")
    dv("C", "5", "Transformator kodi aniq 5 xonali bo‘lsin (masalan 01328).", kind="textLength", operator="equal")
    dv("H", "12", "Hisoblagich raqami aniq 12 xonali bo‘lsin.", kind="textLength", operator="equal")


def _body_rows(ws, rows: list[list], first_row: int = 3, fill_to: int | None = None):
    for r, vals in enumerate(rows, first_row):
        for i in range(NCOLS):
            c = ws.cell(row=r, column=i + 1, value=vals[i] if i < len(vals) else None)
            _apply(c, body_style(i))
    for r in range(first_row + len(rows), (fill_to or 0) + 1):   # bo‘sh qatorlar ham shablondagidek formatlangan
        for i in range(NCOLS):
            _apply(ws.cell(row=r, column=i + 1), body_style(i))


def build_template() -> bytes:
    """Bo‘sh shablon: shablon dizayni, B–N «Matn» formatida (boshidagi nol saqlanadi), ro‘yxatli tekshiruvlar bilan."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    _header(ws)
    _body_rows(ws, [], fill_to=MAX_ROWS + 2)
    _add_validations(ws, MAX_ROWS + 2)
    wb.create_sheet("Лист2")
    wb.create_sheet("Лист3")
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_error_file(rows: list[tuple[list, str]]) -> bytes:
    """Faqat xatoli qatorlar: asl qiymatlar (matn sifatida) + oxirida «Xatolar» ustuni."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    _header(ws, extra_header=True)
    _body_rows(ws, [[i] + [cell_text(v) for v in vals[1:NCOLS]] for i, (vals, _e) in enumerate(rows, 1)], fill_to=MAX_ROWS + 2)
    for r, (_vals, err) in enumerate(rows, 3):
        e = ws.cell(row=r, column=NCOLS + 1, value=err)
        _apply(e, {"font": Font(name="Calibri", size=11, color="C00000"), "border": BORDER,
                   "alignment": Alignment(wrap_text=True, vertical="center")})
    _add_validations(ws, MAX_ROWS + 2)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_export(queryset) -> bytes:
    """CAS ga yuklash uchun: shablon bilan bir xil sarlavha dizayni va A–N ustunlar (write-only — xotirani tejaydi)."""
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(SHEET)
    for i, w in enumerate(WIDTHS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[2].height = HEAD_H

    def cell(value, st):
        c = WriteOnlyCell(ws, value=value)
        _apply(c, st)
        return c

    ws.append([cell(i, head_style(i, 1)) for i in range(NCOLS)])
    ws.append([cell(COLUMNS[i][1] if i else "№", head_style(i, 2)) for i in range(NCOLS)])
    styles = [body_style(i) for i in range(NCOLS)]
    for n, c in enumerate(queryset.iterator(chunk_size=500), 1):
        vals = [n, c.etk_code, c.trans_code, c.trans_name, c.consumer_name, c.consumer_code, c.meter_type, c.meter_no,
                c.address, c.phase, c.ct_ratio, c.consumer_type, c.comm_type, c.vt_ratio]
        ws.append([cell(v, styles[i]) for i, v in enumerate(vals)])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
