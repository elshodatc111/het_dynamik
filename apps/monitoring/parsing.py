"""Excel hisobotlarini o‘qish: offline konsentratorlar (DCU) va aloqaga chiqmagan hisoblagichlar.

Ustun nomlari rus, ingliz va lotin variantlarida kelishi mumkin. Yangi nom chiqsa, pastdagi
``DCU_COLUMNS`` / ``METER_COLUMNS`` ro‘yxatiga qo‘shing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

DCU_COLUMNS = {
    "esp": ["ЭСП", "ESP", "ETK"],
    "substation": ["Подстанция", "Substation"],
    "feeder": ["Фидер", "Feeder"],
    "tp": ["ТП", "TP", "Трансформатор", "Transformer", "Номер трансформатора", "Transformer number"],
    "device_no": ["№ Устройства", "Номер устройства", "Device No", "Device number", "Device ID", "Device serial"],
    "status": ["Статус", "Status"],
    "status_at": ["Время онлайна/офлайна", "Время офлайна", "Online/Offline time", "Offline time", "Online time"],
    "operator": ["Оператор связи", "Оператор", "Carrier", "Operator", "Network operator"],
    "sim": ["Номер сим карты", "Номер SIM", "SIM number", "SIM card number", "SIM"],
    "ip": ["IP-адрес устройства", "IP адрес", "Device IP", "IP address", "IP"],
    "lat": ["Широта", "Latitude"],
    "lon": ["Долгота", "Longitude"],
    "manager": ["Имя менеджера", "Manager name", "Manager"],
    "manager_phone": ["Телефон менеджера", "Manager phone"],
    "phone": ["Телефон", "Phone"],
}
DCU_REQUIRED = ("device_no", "status")

METER_COLUMNS = {
    "esp": ["ЭСП", "ESP", "ETK"],
    "substation": ["Подстанция", "Substation"],
    "feeder": ["Фидер", "Feeder"],
    "transformer": ["Номер трансформатора", "Трансформатор", "ТП", "Transformer number", "Transformer", "TP"],
    "account": ["Лицевой счет потребителя", "Лицевой счет", "Consumer account", "Account number", "Account"],
    "consumer_type": ["Тип потребителя", "Consumer type", "Customer type"],
    "consumer_name": ["Имя потребителя", "Consumer name", "Customer name"],
    "meter_no": ["Номер счетчика", "Meter number", "Meter No", "Meter ID"],
    "comm_type": ["Тип связи", "Communication type", "Connection type"],
    "manager": ["Имя менеджера", "Manager name", "Manager"],
    "manager_phone": ["Телефон менеджера", "Manager phone"],
    "days": ["Несобранный период (день)", "Несобранный период, дни", "Uncollected period (day)",
             "Uncollected period (days)", "Not collected (days)"],
    "gap_time": ["Несобранный период (время)", "Uncollected period (time)"],
    "gps": ["GPS(X,Y)", "GPS", "Coordinates"],
    "address": ["Адрес установки", "Installation address", "Address"],
}
METER_REQUIRED = ("meter_no", "days")

MAX_FILE_SIZE = 80 * 1024 * 1024
SCAN_HEADER_ROWS = 25

_DATETIME_FORMATS = ("%d/%m/%Y %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M",
                     "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d")


def hkey(text) -> str:
    """Sarlavhani solishtirish uchun kalit: kichik harf, belgilarsiz."""
    return re.sub(r"[\W_]+", "", str(text or "").lower().replace("ё", "е"))


def _alias_map(columns: dict) -> dict:
    out = {}
    for field_name, aliases in columns.items():
        for a in aliases:
            out.setdefault(hkey(a), field_name)
    return out


_DCU_MAP = _alias_map(DCU_COLUMNS)
_METER_MAP = _alias_map(METER_COLUMNS)


def clean(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y %H:%M:%S")
    s = ILLEGAL_CHARACTERS_RE.sub("", str(v))
    return re.sub(r"\s+", " ", s).strip()


def parse_datetime(v) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = clean(v)
    if not s:
        return None
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def date_hint_from_name(name: str) -> tuple[date | None, datetime | None]:
    """Fayl nomidan sana: ``08112026_154737`` (ddmmyyyy_HHMMSS), ``2026-11-08`` yoki ``08.11.2026``."""
    m = re.search(r"(?<!\d)(\d{2})(\d{2})(\d{4})[_\- ]?(\d{2})(\d{2})(\d{2})(?!\d)", name)
    if m:
        d, mo, y, h, mi, s = (int(x) for x in m.groups())
        try:
            dt = datetime(y, mo, d, h, mi, s)
            return dt.date(), dt
        except ValueError:
            pass
    m = re.search(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", name)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), None
        except ValueError:
            pass
    m = re.search(r"(?<!\d)(\d{2})[.\-](\d{2})[.\-](\d{4})(?!\d)", name)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))), None
        except ValueError:
            pass
    return None, None


@dataclass
class Parsed:
    kind: str = ""  # "dcu" | "meters"
    name: str = ""
    rows: list = field(default_factory=list)
    total_rows: int = 0  # fayldagi ma’lumot qatorlari
    skipped: int = 0
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    date_hint: date | None = None
    time_hint: datetime | None = None
    header_labels: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.kind)


def _find_header(rows, alias_map, required):
    best = None
    for idx, row in enumerate(rows[:SCAN_HEADER_ROWS]):
        mapping = {}
        for ci, cell in enumerate(row):
            f = alias_map.get(hkey(cell))
            if f and f not in mapping:
                mapping[f] = ci
        if all(r in mapping for r in required) and (best is None or len(mapping) > len(best[1])):
            best = (idx, mapping)
    return best


def _count_rows(path: Path) -> int:
    """Birinchi varaqdagi qatorlar sonini (taxminan) tez sanaydi — progress foizi uchun."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            names = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
            if not names:
                return 0
            n, tail = 0, b""
            with z.open(names[0]) as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    data = tail + chunk
                    n += data.count(b"<row ")
                    tail = data[-4:]
            return n
    except Exception:  # noqa: BLE001
        return 0


def _read_rows(path: Path, progress=None) -> list:
    """progress(frac | None, qatorlar_soni) — har ~1500 qatorda chaqiriladi."""
    total = _count_rows(path) if progress else 0
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            # Ba’zi tizimlar fayl o‘lchamini noto‘g‘ri (A1:A1) yozadi — shunda faqat bitta qator o‘qilib qolardi
            ws.reset_dimensions()
            rows = []
            for r in ws.iter_rows(values_only=True):
                rows.append(tuple(r))
                if progress and len(rows) % 1500 == 0:
                    progress(min(len(rows) / total, 0.99) if total else None, len(rows))
            if rows:
                return rows
        return []
    finally:
        wb.close()


def parse_file(path: Path, original_name: str, offline_keywords: list[str], progress=None) -> Parsed:
    """Faylni ochib turini (DCU yoki hisoblagichlar) aniqlaydi va qatorlarni o‘qiydi.

    progress(bosqich, frac, soni): bosqich «read» (qatorlar o‘qilmoqda) yoki «analyse» (tahlil)."""
    res = Parsed(name=original_name)
    res.date_hint, res.time_hint = date_hint_from_name(original_name)
    if not original_name.lower().endswith((".xlsx", ".xlsm")):
        res.errors.append("Faqat .xlsx fayl qabul qilinadi (eski .xls ni Excel da «.xlsx» qilib saqlang).")
        return res
    if path.stat().st_size > MAX_FILE_SIZE:
        res.errors.append("Fayl juda katta.")
        return res
    with open(path, "rb") as fh:
        if fh.read(2) != b"PK":
            res.errors.append("Bu haqiqiy .xlsx fayl emas.")
            return res
    try:
        rows = _read_rows(path, (lambda f, n: progress("read", f, n)) if progress else None)
    except Exception as exc:  # noqa: BLE001 — buzuq fayl har xil xato berishi mumkin
        res.errors.append(f"Faylni o‘qib bo‘lmadi: {type(exc).__name__}")
        return res
    if not rows:
        res.errors.append("Fayl bo‘sh.")
        return res

    if progress:
        progress("read", 1.0, len(rows))
        progress("analyse", 0.0, 0)
    dcu_h = _find_header(rows, _DCU_MAP, DCU_REQUIRED)
    mtr_h = _find_header(rows, _METER_MAP, METER_REQUIRED)
    if dcu_h and (not mtr_h or len(dcu_h[1]) >= len(mtr_h[1])):
        _parse_dcu(res, rows, dcu_h, offline_keywords)
    elif mtr_h:
        _parse_meters(res, rows, mtr_h)
    else:
        res.errors.append(
            "Fayl turi aniqlanmadi. Konsentratorlar faylida «№ Устройства» va «Статус», hisoblagichlar faylida "
            "«Номер счетчика» va «Несобранный период (день)» ustunlari bo‘lishi kerak."
        )
    return res


def _get(row, mapping, name):
    i = mapping.get(name)
    return row[i] if i is not None and i < len(row) else None


def _parse_dcu(res: Parsed, rows, header, offline_keywords):
    res.kind = "dcu"
    idx, mp = header
    res.header_labels = {k: clean(rows[idx][v]) for k, v in mp.items()}
    for opt in ("esp", "substation", "feeder", "status_at"):
        if opt not in mp:
            res.warnings.append(f"«{opt}» ustuni topilmadi.")
    seen, no_id, dup, online = set(), 0, 0, 0
    for row in rows[idx + 1:]:
        if not any(c not in (None, "") for c in row):
            continue
        res.total_rows += 1
        dev = clean(_get(row, mp, "device_no"))
        if not dev:
            no_id += 1
            continue
        status = clean(_get(row, mp, "status")).lower()
        if not any(k in status for k in offline_keywords):
            online += 1
            continue
        if dev in seen:
            dup += 1
            continue
        seen.add(dev)
        res.rows.append({
            "device_no": dev[:40],
            "esp": clean(_get(row, mp, "esp"))[:80],
            "substation": clean(_get(row, mp, "substation"))[:120],
            "feeder": clean(_get(row, mp, "feeder"))[:120],
            "tp": clean(_get(row, mp, "tp"))[:60],
            "operator": clean(_get(row, mp, "operator"))[:60],
            "sim": clean(_get(row, mp, "sim"))[:40],
            "ip": clean(_get(row, mp, "ip"))[:45],
            "status_at": parse_datetime(_get(row, mp, "status_at")),
            "lat": clean(_get(row, mp, "lat"))[:30],
            "lon": clean(_get(row, mp, "lon"))[:30],
            "manager": clean(_get(row, mp, "manager")),
            "manager_phone": clean(_get(row, mp, "manager_phone")),
            "phone": clean(_get(row, mp, "phone"))[:120],
        })
    res.skipped = no_id + dup + online
    if no_id:
        res.warnings.append(f"{no_id} ta qatorda qurilma raqami yo‘q — o‘tkazib yuborildi.")
    if dup:
        res.warnings.append(f"{dup} ta takroriy qurilma raqami — birinchisi qoldirildi.")
    if online:
        res.warnings.append(f"{online} ta qator offline emas (holati boshqa) — hisobga olinmadi.")
    if not res.rows:
        res.errors.append("Faylda offline konsentrator topilmadi (holat so‘zlarini sozlamalardan tekshiring).")


def _parse_meters(res: Parsed, rows, header):
    res.kind = "meters"
    idx, mp = header
    res.header_labels = {k: clean(rows[idx][v]) for k, v in mp.items()}
    for opt in ("esp", "consumer_type", "account", "consumer_name"):
        if opt not in mp:
            res.warnings.append(f"«{opt}» ustuni topilmadi.")
    bad_days = 0
    for row in rows[idx + 1:]:
        if not any(c not in (None, "") for c in row):
            continue
        res.total_rows += 1
        raw_days = _get(row, mp, "days")
        try:
            days = int(float(str(raw_days).replace(",", ".").strip()))
            if days < 0:
                raise ValueError
        except (TypeError, ValueError):
            bad_days += 1
            continue
        res.rows.append({
            "esp": clean(_get(row, mp, "esp"))[:80],
            "substation": clean(_get(row, mp, "substation"))[:120],
            "feeder": clean(_get(row, mp, "feeder"))[:120],
            "transformer": clean(_get(row, mp, "transformer"))[:60],
            "account": clean(_get(row, mp, "account"))[:40],
            "consumer_type": clean(_get(row, mp, "consumer_type"))[:80],
            "consumer_name": clean(_get(row, mp, "consumer_name"))[:255],
            "meter_no": clean(_get(row, mp, "meter_no"))[:60],
            "comm_type": clean(_get(row, mp, "comm_type"))[:30],
            "manager": clean(_get(row, mp, "manager")),
            "manager_phone": clean(_get(row, mp, "manager_phone")),
            "days": days,
            "gap_time": clean(_get(row, mp, "gap_time"))[:20],
            "gps": clean(_get(row, mp, "gps"))[:60],
            "address": clean(_get(row, mp, "address"))[:500],
        })
    res.skipped = bad_days
    if bad_days:
        res.warnings.append(f"{bad_days} ta qatorda kun soni noto‘g‘ri — o‘tkazib yuborildi.")
    if not res.rows:
        res.errors.append("Faylda hisoblagich qatorlari topilmadi.")
