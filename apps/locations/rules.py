"""Location shabloni: ustunlar, tekshiruv qoidalari, shablon / xatolar / eksport fayllari."""
from __future__ import annotations

import re
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

# (kalit, sarlavha, kenglik)
COLUMNS = [
    ("kind", "Type", 11), ("number", "Nomer", 18), ("lat", "Latitude", 15), ("lon", "Longitude", 15),
    ("district", "Tuman", 20), ("substation", "Potstansiya", 28), ("feeder", "Fider", 22), ("tp", "TP", 12),
    ("account", "Hisob raqam", 16), ("fio", "FIO", 34), ("address", "Addres", 44), ("consumer_type", "Istemolchi turi", 18),
]
KEYS = [c[0] for c in COLUMNS]
NCOLS = len(COLUMNS)
# Eski fayllardagi sarlavhalar: «location» — aslida kenglik, «Latude» — aslida uzunlik
HEADER_ALIASES = {
    "type": "kind", "nomer": "number", "latitude": "lat", "location": "lat", "longitude": "lon", "latude": "lon",
    "tuman": "district", "potstansiya": "substation", "fider": "feeder", "tp": "tp", "hisobraqam": "account",
    "fio": "fio", "addres": "address", "adres": "address", "address": "address", "istemolchituri": "consumer_type",
}

KINDS = {"meter": "Meter", "dcu": "DCU", "balans": "Balans"}
DISTRICTS = [
    "Qarshi ShETK", "Yakkabog‘ TETK", "Qarshi TETK", "G‘uzor TETK", "Chiroqchi TETK", "Ko‘kdala TETK", "Koson TETK",
    "Qamashi TETK", "Nishon TETK", "Kitob TETK", "Kasbi TETK", "Shahrisabz ShETK", "Shahrisabz TETK", "Muborak TETK",
    "Dehqonobod TETK", "Mirishkor TETK", "Baxoriston ETK",
]
CONSUMER_TYPES = {"axoli": "Axoli", "aholi": "Axoli", "yuridik": "Yuridik", "konsentrator": "Konsentrator", "balans": "Balans"}
ALLOWED_TYPES = {"Meter": ("Axoli", "Yuridik"), "DCU": ("Konsentrator",), "Balans": ("Balans",)}
# Qashqadaryo atrofi (shundan tashqaridagi koordinata xato hisoblanadi)
LAT_MIN, LAT_MAX = 37.0, 39.6
LON_MIN, LON_MAX = 64.5, 68.2
PLACEHOLDERS = {"", "-", "—", "–", "--"}
MAX_ROWS = 200_000
MAX_BYTES = 60 * 1024 * 1024


def _k(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower().replace("‘", "").replace("’", "").replace("ʻ", "").replace("'", "").replace("`", ""))


DISTRICT_MAP = {_k(d): d for d in DISTRICTS}


def cell_text(v) -> str:
    """Katak qiymati matn sifatida (Excel raqamlari 12345.0 emas, 12345)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return re.sub(r"\s+", " ", str(v)).strip()


def map_headers(row) -> tuple[dict[int, str], str]:
    """1-qator sarlavhalaridan {ustun_indeksi: kalit}. Tartib muhim emas. Qaytaradi: (xarita, xato_matni)."""
    found: dict[int, str] = {}
    for i, v in enumerate(row):
        key = HEADER_ALIASES.get(_k(cell_text(v)))
        if key and key not in found.values():
            found[i] = key
    missing = [c[1] for c in COLUMNS if c[0] not in found.values()]
    if missing:
        return {}, "Sarlavhada ustun topilmadi: " + ", ".join(missing) + ". Shablonni qaytadan yuklab oling."
    return found, ""


def _coord(text: str):
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def validate_row(raw: dict) -> tuple[dict, list[str]]:
    """raw — {kalit: katak qiymati}. Qaytaradi: (tozalangan qiymatlar, xatolar)."""
    errs: list[str] = []
    t = {k: cell_text(raw.get(k)) for k in KEYS}
    for k in KEYS:
        if t[k] in PLACEHOLDERS:
            t[k] = ""
    kind = KINDS.get(t["kind"].lower())
    if not kind:
        errs.append("Type: Meter, DCU yoki Balans bo‘lishi kerak")
    # Nomer
    num = t["number"].upper().replace(" ", "")
    if not num:
        errs.append("Nomer kiritilmagan")
    elif kind in ("DCU", "Balans"):
        if not re.fullmatch(r"\d{12}", num):
            errs.append("Nomer: 12 xonali raqam bo‘lishi kerak")
    elif kind == "Meter":
        m = re.fullmatch(r"EX-?(\d{6,7})", num)
        if m:
            num = f"EX-{m.group(1)}"
        elif not re.fullmatch(r"\d{12}", num):
            errs.append("Nomer: 12 xonali raqam yoki EX-123456 ko‘rinishida bo‘lishi kerak")
    elif not re.fullmatch(r"\d{12}|EX-?\d{6,7}", num):
        errs.append("Nomer noto‘g‘ri")
    # koordinata
    lat, lon = _coord(t["lat"]), _coord(t["lon"])
    if not t["lat"] or lat is None:
        errs.append("Latitude (kenglik) kiritilmagan yoki raqam emas")
    if not t["lon"] or lon is None:
        errs.append("Longitude (uzunlik) kiritilmagan yoki raqam emas")
    if lat is not None and lon is not None:
        if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
            swapped = LAT_MIN <= lon <= LAT_MAX and LON_MIN <= lat <= LON_MAX
            errs.append("Koordinata Qashqadaryo hududidan tashqarida" + (" (kenglik va uzunlik almashib qolgan bo‘lishi mumkin)" if swapped else ""))
    # tuman va boshqalar
    district = DISTRICT_MAP.get(_k(t["district"]))
    if not t["district"]:
        errs.append("Tuman kiritilmagan")
    elif not district:
        errs.append("Tuman ro‘yxatda yo‘q: " + t["district"][:40])
    for k, label in (("substation", "Potstansiya"), ("feeder", "Fider"), ("tp", "TP")):
        if not t[k]:
            errs.append(f"{label} kiritilmagan")
    # iste'molchi turi
    ctype = CONSUMER_TYPES.get(_k(t["consumer_type"]), "")
    if kind:
        if not ctype and kind in ("DCU", "Balans"):
            ctype = ALLOWED_TYPES[kind][0]
        if not t["consumer_type"] and kind == "Meter":
            errs.append("Istemolchi turi kiritilmagan (Axoli yoki Yuridik)")
        elif ctype not in ALLOWED_TYPES[kind]:
            errs.append(f"Istemolchi turi {kind} uchun {' yoki '.join(ALLOWED_TYPES[kind])} bo‘lishi kerak")
    # hisoblagich uchun majburiy
    if kind == "Meter":
        for k, label in (("account", "Hisob raqam"), ("fio", "FIO"), ("address", "Addres")):
            if not t[k]:
                errs.append(f"{label} kiritilmagan")
    clean = {"kind": kind or "", "number": num, "lat": lat, "lon": lon, "district": district or "", "substation": t["substation"][:150],
             "feeder": t["feeder"][:150], "tp": t["tp"][:60], "account": t["account"][:60], "fio": t["fio"][:250],
             "address": t["address"][:500], "consumer_type": ctype}
    return clean, errs


def normalize_query(text: str) -> str:
    """Bot orqali yozilgan raqamni qidiruv shakliga keltiradi (bo‘sh joy, kichik harf, EX belgisi)."""
    q = re.sub(r"[\s​]+", "", (text or "")).upper()
    m = re.fullmatch(r"EX-?(\d{4,8})", q)
    return f"EX-{m.group(1)}" if m else q


# ------------------------------------------------------------------ fayllar
HEAD_FILL = PatternFill("solid", fgColor="DDE8F6")
THIN = Side(style="thin", color="B7C2D3")
TEXT_COLS = {"number", "account", "tp"}


def _style_sheet(ws, ncols, extra_cols=None):
    for i, (_k, head, w) in enumerate(COLUMNS, 1):
        c = ws.cell(row=1, column=i, value=head)
        c.font, c.fill = Font(bold=True, name="Calibri"), HEAD_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
        ws.column_dimensions[get_column_letter(i)].width = w
    for i, key in enumerate(KEYS, 1):
        if key in TEXT_COLS:
            ws.column_dimensions[get_column_letter(i)].number_format = "@"
    ws.row_dimensions[1].height = 24
    ws.freeze_panes = "A2"


def build_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Лист1"
    _style_sheet(ws, NCOLS)
    lists = wb.create_sheet("Ro‘yxatlar")
    for i, d in enumerate(DISTRICTS, 1):
        lists.cell(row=i, column=1, value=d)
    lists.sheet_state = "hidden"
    last = 5000
    dv_kind = DataValidation(type="list", formula1='"Meter,DCU,Balans"', allow_blank=False, showErrorMessage=True,
                             errorTitle="Type", error="Meter, DCU yoki Balans tanlang")
    dv_dist = DataValidation(type="list", formula1=f"='Ro‘yxatlar'!$A$1:$A${len(DISTRICTS)}", allow_blank=False, showErrorMessage=True,
                             errorTitle="Tuman", error="Ro‘yxatdan tanlang")
    dv_type = DataValidation(type="list", formula1='"Axoli,Yuridik,Konsentrator,Balans"', allow_blank=True, showErrorMessage=True)
    dv_lat = DataValidation(type="decimal", operator="between", formula1=str(LAT_MIN), formula2=str(LAT_MAX), allow_blank=False, showErrorMessage=True,
                            errorTitle="Latitude", error=f"{LAT_MIN} – {LAT_MAX} oralig‘ida bo‘lishi kerak")
    dv_lon = DataValidation(type="decimal", operator="between", formula1=str(LON_MIN), formula2=str(LON_MAX), allow_blank=False, showErrorMessage=True,
                            errorTitle="Longitude", error=f"{LON_MIN} – {LON_MAX} oralig‘ida bo‘lishi kerak")
    for dv, col in ((dv_kind, "A"), (dv_lat, "C"), (dv_lon, "D"), (dv_dist, "E"), (dv_type, "L")):
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{last}")
    guide = wb.create_sheet("Qo‘llanma")
    rows = [
        ("Location shabloni — to‘ldirish qoidalari",),
        ("",),
        ("Type", "Meter (aholi/yuridik hisoblagich), DCU (konsentrator) yoki Balans (balans hisoblagich)"),
        ("Nomer", "Noyob. DCU va Balans — 12 xonali raqam. Meter — 12 xonali raqam yoki EX-1234567. Bazada bor bo‘lsa yangilanadi, yo‘q bo‘lsa qo‘shiladi"),
        ("Latitude / Longitude", f"Kenglik {LAT_MIN}–{LAT_MAX}, uzunlik {LON_MIN}–{LON_MAX}. O‘nli kasr nuqta bilan (38.8610)"),
        ("Tuman", "Ro‘yxatdan tanlang (17 ta)"),
        ("Potstansiya, Fider, TP", "Hamma qatorda majburiy"),
        ("Hisob raqam, FIO, Addres", "Meter uchun majburiy. DCU va Balans uchun «-» yoki bo‘sh qoldirish mumkin"),
        ("Istemolchi turi", "Meter: Axoli yoki Yuridik; DCU: Konsentrator; Balans: Balans"),
        ("",),
        ("Namuna", "Meter | 121206035571 | 38.8610 | 65.7890 | Qarshi TETK | NS Qarshi 35/10 kV | Fider 10 kV | 293 | 350007 | Aliyev Vali | Qarshi sh. Mustaqillik 1 | Axoli"),
    ]
    for r in rows:
        guide.append(list(r))
    guide["A1"].font = Font(bold=True, size=13)
    guide.column_dimensions["A"].width = 26
    guide.column_dimensions["B"].width = 120
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def build_error_file(rows: list[tuple[list, str]]) -> bytes:
    """rows: [(asl_qator_qiymatlari[12], xato_matni)]. Oxirgi ustun — «Xatolar»."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Лист1"
    _style_sheet(ws, NCOLS)
    c = ws.cell(row=1, column=NCOLS + 1, value="Xatolar")
    c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="D1384F")
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.column_dimensions[get_column_letter(NCOLS + 1)].width = 70
    for vals, msg in rows:
        ws.append([cell_text(v) if i not in (2, 3) else v for i, v in enumerate(vals)] + [msg])
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def build_export(queryset) -> bytes:
    from openpyxl import Workbook as WB
    wb = WB(write_only=True)
    ws = wb.create_sheet("Лист1")
    for i, (_k, _h, w) in enumerate(COLUMNS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.append([c[1] for c in COLUMNS])
    for p in queryset.iterator(chunk_size=2000):
        ws.append([p.kind, p.number, p.lat, p.lon, p.district, p.substation, p.feeder, p.tp, p.account or "-", p.fio or "-", p.address or "-", p.consumer_type])
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
