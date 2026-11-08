"""Excel hisobotlar: umumiy svod, nomma-nom ro‘yxatlar. Hammasi bayt (bytes) sifatida qaytadi."""
from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import services
from .models import GROUP_LABEL, GROUPS, DcuRecord, MeterRecord, Snapshot

HEAD_FILL = PatternFill("solid", fgColor="2F49D8")
SUB_FILL = PatternFill("solid", fgColor="EEF1FF")
TOTAL_FILL = PatternFill("solid", fgColor="E4E7EE")
THIN = Side(style="thin", color="D0D5E0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WHITE_BOLD = Font(bold=True, color="FFFFFF")

KINDS = {
    "svod": "Umumiy svod",
    "dcu": "Offline konsentratorlar (nomma-nom)",
}


def safe(v):
    """Excel formulasi sifatida o‘qilmasligi va XML uchun yaroqsiz belgilar bo‘lmasligi uchun."""
    if isinstance(v, str):
        v = ILLEGAL_CHARACTERS_RE.sub("", v)
        if v[:1] in ("=", "+", "@") or (v[:1] == "-" and len(v) > 1 and not v[1:2].isdigit()):
            return "⁠" + v
    return v


def _to_bytes(wb: Workbook) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _header(ws, row: int, headers, widths=None):
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=c, value=h)
        cell.font, cell.fill, cell.border = WHITE_BOLD, HEAD_FILL, BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 32
    if widths:
        for c, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(c)].width = w


def _fname(snap: Snapshot, prefix: str) -> str:
    return f"{prefix}_{snap.report_date:%Y-%m-%d}.xlsx"


# ------------------------------------------------------------------ nomma-nom ro‘yxatlar
DCU_HEAD = ["№", "ESP", "Podstansiya", "Fider", "TP", "Konsentrator raqami", "Offline (kun)", "Offline boshlangan sana",
            "Oxirgi holat vaqti", "Operator", "SIM karta", "IP manzil", "Mas’ul xodimlar", "Xodim telefonlari", "Telefon", "Koordinata"]
DCU_W = [6, 18, 26, 20, 8, 18, 12, 16, 19, 12, 22, 16, 40, 28, 14, 22]


def _dcu_row(i, r: DcuRecord):
    at = r.status_at
    if at is not None:
        from django.utils import timezone
        at = timezone.localtime(at).strftime("%d.%m.%Y %H:%M:%S") if timezone.is_aware(at) else at.strftime("%d.%m.%Y %H:%M:%S")
    coord = f"{r.lat}, {r.lon}" if r.lat and r.lon else ""
    return [i, r.esp, r.substation, r.feeder, r.tp, r.device_no, r.days, r.offline_since.strftime("%d.%m.%Y"), at or "",
            r.operator, r.sim, r.ip, r.manager, r.manager_phone, r.phone, coord]


def _fill_dcu_sheet(ws, qs):
    _header(ws, 1, DCU_HEAD, DCU_W)
    n = 0
    for n, r in enumerate(qs.order_by("-days", "esp", "substation", "feeder", "device_no").iterator(chunk_size=2000), 1):
        ws.append([safe(v) for v in _dcu_row(n, r)])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(DCU_HEAD))}{max(n, 1) + 1}"
    return n


def dcu_list_xlsx(snap: Snapshot) -> bytes:
    """Bitta fayl: «Hammasi» varag‘i va har bir davr uchun alohida varaq."""
    edges = services.dcu_edges()
    labels = services.bucket_labels(edges)
    base = DcuRecord.objects.filter(snapshot=snap, days__gte=edges[0])
    wb = Workbook()
    ws = wb.active
    ws.title = f"Hammasi ({edges[0]}+ kun)"[:31]
    _fill_dcu_sheet(ws, base)
    for i, label in enumerate(labels):
        _fill_dcu_sheet(wb.create_sheet(label[:31]), base.filter(services.bucket_q(edges, i)))
    return _to_bytes(wb)


METER_HEAD = ["№", "ESP", "Podstansiya", "Fider", "Transformator", "Lisevoy hisob", "Iste’molchi turi", "Guruh", "Iste’molchi",
              "Hisoblagich raqami", "Aloqa turi", "Aloqasiz (kun)", "Aloqasiz (vaqt)", "Mas’ul xodimlar", "Xodim telefonlari",
              "GPS", "O‘rnatilgan manzil"]
METER_W = [6, 18, 26, 20, 12, 14, 18, 10, 34, 18, 10, 12, 12, 40, 28, 24, 44]


def _meter_row(i, r: MeterRecord):
    return [i, r.esp, r.substation, r.feeder, r.transformer, r.account, r.consumer_type, GROUP_LABEL.get(r.group, r.group),
            r.consumer_name, r.meter_no, r.comm_type, r.days, r.gap_time, r.manager, r.manager_phone,
            "" if r.gps.strip() in (",", "") else r.gps, r.address]


def _fill_meter_sheet(ws, qs):
    _header(ws, 1, METER_HEAD, METER_W)
    n = 0
    for n, r in enumerate(qs.order_by("-days", "esp", "substation", "feeder", "transformer", "account").iterator(chunk_size=2000), 1):
        ws.append([safe(v) for v in _meter_row(n, r)])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(METER_HEAD))}{max(n, 1) + 1}"
    return n


def meters_bucket_xlsx(snap: Snapshot, bucket: int) -> tuple[bytes, str]:
    """Bitta davr uchun fayl: yuridik / aholi / balans (va kerak bo‘lsa boshqa) varaqlari."""
    edges = services.meter_edges()
    label = services.bucket_labels(edges)[bucket]
    base = MeterRecord.objects.filter(snapshot=snap).filter(services.bucket_q(edges, bucket))
    wb = Workbook()
    ws = wb.active
    ws.title = f"Hammasi ({base.count()})"[:31]
    _fill_meter_sheet(ws, base)
    first = False
    for key, title in GROUPS:
        qs = base.filter(group=key)
        if key == "boshqa" and not qs.exists():
            continue
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = f"{title} ({qs.count()})"[:31]
        _fill_meter_sheet(ws, qs)
    slug = label.replace("–", "-").replace("+ kun", "plus").replace(" kun", "").replace("+", "plus")
    return _to_bytes(wb), _fname(snap, f"aloqaga_chiqmay_qolgan_hisoblagichlar_{slug}_kun")


# ------------------------------------------------------------------ svod
def _title(ws, text, row, span):
    ws.cell(row=row, column=1, value=text).font = Font(bold=True, size=12)
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)


def _cell(ws, r, c, v, bold=False, fill=None, num=False, align=None):
    cell = ws.cell(row=r, column=c, value=v)
    cell.border = BORDER
    if bold:
        cell.font = Font(bold=True)
    if fill:
        cell.fill = fill
    if num:
        cell.number_format = "#,##0"
        cell.alignment = Alignment(horizontal="right")
    if align:
        cell.alignment = Alignment(horizontal=align)
    return cell


GRID_HEAD_FILL = PatternFill("solid", fgColor="DCE6F1")
GRID_TOTAL_FILL = PatternFill("solid", fgColor="FFE699")
GRID_LINE = Side(style="thin", color="7F8DA3")
GRID_BORDER = Border(left=GRID_LINE, right=GRID_LINE, top=GRID_LINE, bottom=GRID_LINE)


def _gcell(ws, r, c, v, *, bold=False, fill=None, num=False, align="center", color=None, size=11):
    cell = ws.cell(row=r, column=c, value=v)
    cell.border = GRID_BORDER
    cell.font = Font(bold=bold, color=color, size=size)
    if fill:
        cell.fill = fill
    cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=False)
    if num:
        cell.number_format = "#,##0;-#,##0;0"
    return cell


def _merge(ws, r1, c1, r2, c2, v, **kw):
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            _gcell(ws, r, c, v if (r, c) == (r1, c1) else None, **kw)
    if (r1, c1) != (r2, c2):
        ws.merge_cells(start_row=r1, start_column=c1, end_row=r2, end_column=c2)


def write_grid(ws, g: dict, r0: int, rows, total, *, signed=False) -> int:
    """Svod matritsasini (3 qatorli sarlavha + JAMI + ESP qatorlari) yozadi. Qaytaradi: keyingi bo‘sh qator."""
    d_labels, m_labels, gkeys, gnames = g["d_labels"], g["m_labels"], g["gkeys"], g["gnames"]
    nd, ng = len(d_labels), len(gkeys)
    simple = bool(g.get("simple"))
    hr = 2 if simple else 3  # sarlavha qatorlari soni
    hb = dict(bold=True, fill=GRID_HEAD_FILL)
    _merge(ws, r0, 1, r0 + hr - 1, 1, "ESP", size=12, **hb)
    c = 2
    if g["has_dcu"]:
        _merge(ws, r0, c, r0, c + nd, "Offline konsentratorlar", size=12, **hb)
        for k, lb in enumerate(["JAMI"] + d_labels):
            _merge(ws, r0 + 1, c + k, r0 + hr - 1, c + k, lb, **hb)
        c += nd + 1
    if g["has_meters"]:
        span = 1 + len(m_labels) * (1 + ng)
        _merge(ws, r0, c, r0, c + span - 1, "Aloqadan chiqib ketgan hisoblagichlar", size=12, **hb)
        _merge(ws, r0 + 1, c, r0 + hr - 1, c, "JAMI", **hb)
        c += 1
        for lb in m_labels:
            if simple:
                _merge(ws, r0 + 1, c, r0 + 1, c, lb, **hb)
            else:
                _merge(ws, r0 + 1, c, r0 + 1, c + ng, lb, **hb)
                for k, name in enumerate(["JAMI"] + [gnames[x] for x in gkeys]):
                    _gcell(ws, r0 + 2, c + k, name, **hb)
            c += 1 + ng
    ncols = c - 1
    r = r0 + hr
    for k, row in enumerate([total] + list(rows)):
        is_total = k == 0
        _gcell(ws, r, 1, row["esp"], bold=is_total, fill=GRID_TOTAL_FILL if is_total else None, align="left" if not is_total else "center")
        for j, v in enumerate(row["vals"], 2):
            cell = _gcell(ws, r, j, v if v is not None else "—", bold=is_total, fill=GRID_TOTAL_FILL if is_total else None, num=True,
                          align="right")
            if signed and isinstance(v, int):
                cell.number_format = "+#,##0;-#,##0;0"
        r += 1
    ws.column_dimensions["A"].width = 24
    for j in range(2, ncols + 1):
        ws.column_dimensions[get_column_letter(j)].width = 10.5
    ws.freeze_panes = ws.cell(row=r0 + hr, column=2)
    return r


def _grid_title(ws, g, ncols: int, date_text: str, subtitle: str | None = None):
    ws.cell(row=1, column=1, value="Qashqadaryo HETK hududida CAS dasturida offline konsentratorlar va aloqaga chiqmay qolgan "
                                   "hisoblagichlar holati bo‘yicha")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(ncols, 8))
    ws["A1"].font = Font(bold=True, size=14)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30
    ws.cell(row=2, column=1, value=subtitle or "MA’LUMOT")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max(ncols, 8))
    ws["A2"].font = Font(bold=True, size=14, color="FF0000")
    ws["A2"].alignment = Alignment(horizontal="center")
    ws.cell(row=3, column=1, value="Hudud nomi:").font = Font(size=11)
    ws.merge_cells(start_row=3, start_column=2, end_row=3, end_column=4)
    ws.cell(row=3, column=2, value=services.REGION_NAME).font = Font(bold=True, color="1F4FD8")
    ws.cell(row=3, column=max(ncols, 8), value=date_text).font = Font(bold=True, color="0000CC")
    ws.cell(row=3, column=max(ncols, 8)).alignment = Alignment(horizontal="right")


def svod_xlsx(snap: Snapshot) -> bytes:
    """Umumiy svod — foydalanuvchi shabloniga mos: ESP × (konsentratorlar, davrlar × guruhlar)."""
    g = services.grid_for(snap)
    wb = Workbook()
    ws = wb.active
    ws.title = "Svod"
    ncols = 1 + len(g["cols"])
    _grid_title(ws, g, ncols, f"Ma’lumot: {snap.report_date:%d.%m.%Y}")
    end = write_grid(ws, g, 5, g["rows"], g["total"])
    ws.cell(row=end + 1, column=1, value=f"Konsentratorlar: {g['d_min']} kundan kam offline hisobga olinmagan · "
                                          f"hisoblagichlar: {g['m_min']} kundan kam aloqasizlar hisobga olinmagan.").font = Font(italic=True, size=9, color="64748B")
    ws.sheet_view.showGridLines = False
    return _to_bytes(wb)


def compare_xlsx(snap_a: Snapshot, snap_b: Snapshot, tbl: dict) -> bytes:
    """Ikki sana solishtiruvi: har bir ko‘rsatkich uchun «A sana | B sana | Farq» ustunlari."""
    wb = Workbook()
    ws = wb.active
    ws.title = f"{snap_a.report_date:%d.%m} → {snap_b.report_date:%d.%m}"
    ncols = 1 + 3 * tbl["ncols"]
    _grid_title(ws, None, ncols, f"{snap_a.report_date:%d.%m.%Y} → {snap_b.report_date:%d.%m.%Y}", "SOLISHTIRISH")
    hb = dict(bold=True, fill=GRID_HEAD_FILL)
    r0 = 5
    occupied = set()
    for ri, row in enumerate(tbl["head"]):
        c = 1
        for text, cs, rs in row:
            while (ri, c) in occupied:
                c += 1
            _merge(ws, r0 + ri, c, r0 + ri + rs - 1, c + cs - 1, text, size=11 if ri == 0 else 10, **hb)
            for rr in range(ri, ri + rs):
                for cc in range(c, c + cs):
                    occupied.add((rr, cc))
            c += cs
    r = r0 + len(tbl["head"])
    for k, row in enumerate([tbl["total"]] + list(tbl["rows"])):
        is_total = k == 0
        fill = GRID_TOTAL_FILL if is_total else None
        _gcell(ws, r, 1, row["esp"], bold=is_total, fill=fill, align="left" if not is_total else "center")
        col = 2
        for cell in row["cells"]:
            _gcell(ws, r, col, cell["a"] if cell["a"] is not None else "—", bold=is_total, fill=fill, num=True, align="right")
            _gcell(ws, r, col + 1, cell["b"], bold=is_total, fill=fill, num=True, align="right")
            d = _gcell(ws, r, col + 2, cell["d"] if cell["d"] is not None else "—", bold=True, fill=fill, num=True, align="right",
                       color=("C0233F" if (cell["d"] or 0) > 0 else "0B8A5E" if (cell["d"] or 0) < 0 else "94A3B8"))
            d.number_format = '"↑ "#,##0;"↓ "-#,##0;"–"'
            col += 3
        r += 1
    ws.column_dimensions["A"].width = 24
    for j in range(2, ncols + 1):
        ws.column_dimensions[get_column_letter(j)].width = 9.5
    ws.freeze_panes = ws.cell(row=r0 + len(tbl["head"]), column=2)
    ws.sheet_view.showGridLines = False
    ws.cell(row=r + 1, column=1, value="↑ — aloqasizlar ko‘paygan (yomonlashgan), ↓ — kamaygan (yaxshilangan).").font = Font(italic=True, size=9, color="64748B")
    return _to_bytes(wb)


# ------------------------------------------------------------------ svod yacheykasi ro‘yxati (filtrlangan)
def filtered_dcu_xlsx(qs) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Konsentratorlar"
    _fill_dcu_sheet(ws, qs)
    return _to_bytes(wb)


def filtered_meters_xlsx(qs) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Hisoblagichlar"
    _fill_meter_sheet(ws, qs)
    return _to_bytes(wb)


def list_filename(prefix: str, snap: Snapshot, *parts: str) -> str:
    import re
    import unicodedata
    bits = []
    for p in parts:
        if not p:
            continue
        t = unicodedata.normalize("NFKD", str(p).replace("–", "-").replace("+", "plus")).encode("ascii", "ignore").decode()
        t = re.sub(r"[^A-Za-z0-9\-]+", "-", t).strip("-")
        if t:
            bits.append(t)
    return "_".join([prefix, *bits, f"{snap.report_date:%Y-%m-%d}"]) + ".xlsx"


# ------------------------------------------------------------------ yig‘ma
def available_files(snap: Snapshot) -> list[dict]:
    """Yuborish/yuklash mumkin bo‘lgan fayllar: [{kind, label}]."""
    items = []
    s = snap.summary or {}
    if snap.has_dcu:
        items.append({"kind": "dcu", "label": "Offline konsentratorlar — nomma-nom"})
    if snap.has_meters and not snap.meter_detail_purged:
        labels = services.bucket_labels(s.get("meter_edges") or services.meter_edges())
        for i, label in enumerate(labels):
            n = (s.get("meters") or {}).get("bucket_totals", [0] * len(labels))[i]
            items.append({"kind": f"m{i}", "label": f"Aloqaga chiqmay qolgan hisoblagichlar {label} — nomma-nom ({services.fmt_int(n)} ta)"})
    return items


def build_file(snap: Snapshot, kind: str) -> tuple[bytes, str]:
    if kind == "svod":
        return svod_xlsx(snap), _fname(snap, "svod")
    if kind == "dcu":
        return dcu_list_xlsx(snap), _fname(snap, "konsentratorlar")
    if kind.startswith("m") and kind[1:].isdigit():
        i = int(kind[1:])
        if 0 <= i < len(services.meter_edges()):
            return meters_bucket_xlsx(snap, i)
    raise KeyError(kind)
