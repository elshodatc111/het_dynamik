"""Solishtirma hisobotni Excel (.xlsx) ga yozish. Ranglar neytral: yaxshi/yomon bo‘yicha bo‘yalmaydi."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.formatting.rule import CellIsRule, DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.pagebreak import Break

from .parser.layouts import HETK_SPECS

# Ranglar veb jadval (static/css/app.css, yorug‘ mavzu) bilan bir xil.
TEXT = "0F172A"
GRAY = "64748B"      # --muted
FAINT = "94A3B8"     # --faint
HEAD_BG = "DDEBF7"   # PDF dagi sarlavha rangi
TOTAL_BG = "EEF1FF"  # --primary-soft («Jami» qatori)
LINE = "E4E7EE"      # --border
TITLE = "2B43C9"     # --primary-text

FMT_COUNT = "#,##0"
FMT_PCT = '0.0"%"'
FMT_DIFF = '"↑ "#,##0;"↓ "-#,##0;"–"'
FMT_DIFF_PCT = '"↑ "0.0;"↓ "-0.0;"–"'

FONT = "Calibri"
thin = Side(style="thin", color=LINE)
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
HEAD_FONT = Font(bold=True, color=TEXT, name=FONT, size=10)
SUB_FONT = Font(bold=False, color=GRAY, name=FONT, size=9)
HEAD_FILL = PatternFill("solid", fgColor=HEAD_BG)
TOTAL_PF = PatternFill("solid", fgColor=TOTAL_BG)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center")


def _num(v):
    if v is None:
        return None
    f = float(v)
    return int(f) if f.is_integer() else f


def _sheet_name(text, used):
    name = re.sub(r"[\\/*?:\[\]]", "-", text)[:28]
    base, i = name, 2
    while name in used:
        name = f"{base[:25]}-{i}"
        i += 1
    used.add(name)
    return name


def _print_setup(ws, title_rows, title_cols=None, fit_height=0, footer_left=""):
    """Printerga tayyor: A4 albom, eni bo‘yicha 1 betga sig‘adi, sarlavha har betda takrorlanadi."""
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = fit_height
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = title_rows
    if title_cols:
        ws.print_title_cols = title_cols
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = 0.6
    ws.page_margins.bottom = 0.6
    ws.page_margins.header = 0.25
    ws.page_margins.footer = 0.25
    ws.oddFooter.left.text = footer_left or "Qashqadaryo HETK"
    ws.oddFooter.left.size = 8
    ws.oddFooter.center.text = "&D &T"
    ws.oddFooter.center.size = 8
    ws.oddFooter.right.text = "&P / &N"
    ws.oddFooter.right.size = 8


MAX_METRICS_PER_PAGE = 6          # bir betga ko‘pi bilan shuncha ko‘rsatkich (har biri 3 ustun)
_PX_A, _PX_VAL, _PX_DIFF = 145, 65, 61  # ustun kengliklari (piksel), masshtabni hisoblash uchun


def _paginate_columns(ws, groups):
    """Keng jadvalni guruh chegarasida betlarga bo‘ladi; «Hudud» ustuni va sarlavha har betda takrorlanadi.

    «Eniga sig‘dirish» o‘rniga aniq masshtab ishlatiladi — shunda harflar o‘qiladigan o‘lchamda qoladi.
    """
    pages, cur = [], 0                       # har bir bet: ko‘rsatkichlar soni
    for g in groups:
        n = len(g["metrics"])
        while n > MAX_METRICS_PER_PAGE:      # juda katta guruh — o‘zi bir necha betga bo‘linadi
            if cur:
                pages.append(cur); cur = 0
            pages.append(MAX_METRICS_PER_PAGE); n -= MAX_METRICS_PER_PAGE
        if cur and cur + n > MAX_METRICS_PER_PAGE:
            pages.append(cur); cur = 0
        cur += n
    if cur:
        pages.append(cur)

    col = 1                                  # 1 — «Hudud»
    for k in pages[:-1]:
        col += 3 * k
        ws.col_breaks.append(Break(id=col))  # shu ustundan keyin yangi bet
    widest = max(pages)
    page_px = _PX_A + widest * (2 * _PX_VAL + _PX_DIFF)
    printable_px = 10.85 * 96                # A4 albom, 0.4" chetlar
    ws.sheet_properties.pageSetUpPr.fitToPage = False
    ws.page_setup.fitToWidth = None
    ws.page_setup.fitToHeight = None
    ws.page_setup.scale = max(40, min(100, int(printable_px / page_px * 100)))



# ============================================================ PDF dagi dinamika jadvali ko‘rinishi
STYLE_FILE = Path(__file__).with_name("excel_styles.json")
BLACK = Side(style="thin", color="000000")
BOX = Border(left=BLACK, right=BLACK, top=BLACK, bottom=BLACK)
GREEN_FIX = {"00FF00": "00B050"}      # toza yashil o‘qilmaydi — PDF dagi yashilga yaqin rang
PCT_BODY = {"aloqasiz": '0"%"', "uzilgan": '0"%"'}   # PDF da tana qatorlarida butun foiz, «Jami» da kasrli
FMT_BODY_COUNT = "#,##0;-#,##0;"                      # nol — bo‘sh (PDF dagidek)
FMT_JAMI_COUNT = '#,##0;-#,##0;"-"'
FMT_ARROW = '"↑ "#,##0;"↓ "-#,##0;'


@lru_cache(maxsize=1)
def _styles():
    try:
        return json.loads(STYLE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _argb(hex6):
    return "FF" + hex6


def _rich(runs, default_bold=True):
    """[{t,color,b,i,sz}] -> openpyxl boyoqli matni (bitta bo‘lak bo‘lsa — oddiy matn + shrift)."""
    blocks = []
    for r in runs:
        f = InlineFont(rFont="Calibri", sz=r["sz"], b=r.get("b", default_bold), i=r.get("i", False),
                       color=_argb(r["color"]))
        blocks.append(TextBlock(f, r["t"]))
    return CellRichText(*blocks)


def _font_of(run):
    return Font(name="Calibri", size=run["sz"], bold=run.get("b", True), italic=run.get("i", False),
                color=run["color"])


def _put_runs(cell, runs):
    if not runs:
        return
    if len(runs) == 1:
        cell.value = runs[0]["t"]
        cell.font = _font_of(runs[0])
    else:
        cell.value = _rich(runs)
        cell.font = _font_of(runs[0])


def _box(ws, r0, c0, r1, c1, fill=None):
    """Birlashtirilgan oraliq: hamma kataklarga chegara va to‘ldirish."""
    for rr in range(r0, r1 + 1):
        for cc in range(c0, c1 + 1):
            cell = ws.cell(row=rr, column=cc)
            cell.border = BOX
            if fill:
                cell.fill = PatternFill("solid", fgColor=fill)


def _merge(ws, r0, c0, r1, c1):
    if (r0, c0) != (r1, c1):
        ws.merge_cells(start_row=r0, start_column=c0, end_row=r1, end_column=c1)


def _span_cols(widths, start, need, direction=1):
    """`start` ustundan boshlab kengligi `need` ga yetguncha ustunlar oralig‘i (0-asosli, kiritilgan)."""
    n, tot = len(widths), 0.0
    i = start
    while 0 <= i < n and tot < need:
        tot += widths[i]
        i += direction
    i -= direction
    return (start, i) if direction == 1 else (i, start)


def _pdf_sheet(wb, used, sec, st, a, b):
    key = sec["section"].key
    spec = HETK_SPECS[key]
    ncols = st["ncols"]
    ws = wb.create_sheet(_sheet_name(sec["section"].short, used))
    ws.sheet_view.showGridLines = False
    widths = st["col_w"]
    for i, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(i + 1)].width = w
    da, db = f"{a:%d.%m.%Y}", f"{b:%d.%m.%Y}"

    # ---------------- sahifa sarlavhasi
    title, dyn, meta = st["title"][0], st["title"][1], st["title"][2]
    ws.row_dimensions[1].height = 14
    for r_i, line, h in ((2, title, 27), (4, dyn, 27)):
        runs = line["items"][0]["runs"]
        ws.row_dimensions[r_i].height = h
        _merge(ws, r_i, 1, r_i, ncols)
        c = ws.cell(row=r_i, column=1)
        _put_runs(c, runs)
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[3].height = 6
    ws.row_dimensions[5].height = 6
    ws.row_dimensions[6].height = 24
    ws.row_dimensions[7].height = 6

    label_run = next((r for it in meta["items"] for r in it["runs"] if r["color"] == "000000"), None)
    box_run = next((r for it in meta["items"] for r in it["runs"] if r["color"] == "0066FF"), None)
    date_run = meta["items"][-1]["runs"][0]
    c = ws.cell(row=6, column=1, value="Hudud nomi:")
    c.font = _font_of(dict(label_run or box_run, b=False, color="000000"))
    c.alignment = Alignment(horizontal="left", vertical="center")
    b0, b1 = _span_cols(widths, 1, 26)
    _merge(ws, 6, b0 + 1, 6, b1 + 1)
    c = ws.cell(row=6, column=b0 + 1, value="Qashqadaryo HETK")
    c.font = _font_of(box_run)
    c.alignment = Alignment(horizontal="left", vertical="center")
    blue = Side(style="thin", color="0066FF")
    for cc in range(b0 + 1, b1 + 2):
        ws.cell(row=6, column=cc).border = Border(top=blue, bottom=blue,
                                                  left=blue if cc == b0 + 1 else None,
                                                  right=blue if cc == b1 + 1 else None)
    d0, d1 = _span_cols(widths, ncols - 1, 38, direction=-1)
    d0 = max(d0, b1 + 1)
    _merge(ws, 6, d0 + 1, 6, d1 + 1)
    c = ws.cell(row=6, column=d0 + 1, value=f"Dinamika: {da} - {db}")
    c.font = _font_of(date_run)
    c.alignment = Alignment(horizontal="right", vertical="center")

    # ---------------- jadval sarlavhasi
    H0 = 8
    for i, h in enumerate(st["header_h"]):
        ws.row_dimensions[H0 + i].height = h
    for h in st["header"]:
        r0, r1 = H0 + h["r0"], H0 + h["r1"]
        c0, c1 = h["c0"] + 1, h["c1"] + 1
        _merge(ws, r0, c0, r1, c1)
        _box(ws, r0, c0, r1, c1, h["fill"])
        cell = ws.cell(row=r0, column=c0)
        _put_runs(cell, h["runs"])
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True,
                                   text_rotation=90 if h["rot"] else 0)
    first = H0 + len(st["header_h"])

    # ---------------- ma’lumot qatorlari
    pct_dec_body = PCT_BODY.get(key, '0.0"%"')
    pct_dec_jami = '0.0"%"'
    kinds = {m.key: m.kind for m in sec["metrics"]}
    n_rows = len(sec["rows"])
    arrow_cols = {}

    for ri, row in enumerate(sec["rows"]):
        r = first + ri
        jami = row["is_total"]
        ws.row_dimensions[r].height = st["jami_h"] if jami else st["row_h"]
        data = {m.key: cd for m, cd in zip(sec["metrics"], row["cells"])}
        last_metric = None
        merged_prev = None
        for ci, item in enumerate(spec):
            cs = st["cols"][ci]["jami" if jami else "body"]
            body_cs = st["cols"][ci]["body"]
            cell = ws.cell(row=r, column=ci + 1)
            cell.border = BOX
            fill = cs["fill"] if jami else body_cs["fill"]
            if jami and fill is None:
                fill = st["cols"][ci - 1]["jami"]["fill"] if ci else None
            if fill:
                cell.fill = PatternFill("solid", fgColor=fill)
            color = GREEN_FIX.get(cs["color"], cs["color"])
            cell.font = Font(name="Calibri", size=cs["sz"], bold=cs["b"], color=color)
            align = body_cs["align"] or "right"
            if item == "district":
                cell.value = row["district"].name
                cell.alignment = Alignment(horizontal="left" if not jami else "center", vertical="center")
                cell.font = Font(name="Calibri", size=(cs["sz"] if jami else 10), bold=jami, color="000000")
                continue
            cell.alignment = Alignment(horizontal=("right" if align == "center" else align), vertical="center")
            if item is None:
                if ci > 0 and spec[ci - 1] not in (None, "district", "D"):
                    ws.merge_cells(start_row=r, start_column=ci, end_row=r, end_column=ci + 1)
                continue
            if item == "D":
                src = data.get(last_metric)
                v = _num(src["diff"]) if src else None
                cell.value = v
                cell.number_format = FMT_ARROW
                cell.font = Font(name="Calibri", size=cs["sz"] if not jami else body_cs["sz"], bold=True,
                                 color=body_cs["up"] or "FF0000")
                arrow_cols[ci] = (body_cs["up"], body_cs["dn"])
                continue
            pct = item.startswith("%")
            name = item[1:] if pct else item
            src = data.get(name)
            v = _num(src["b"]) if src else None
            cell.value = v
            if pct:
                cell.number_format = pct_dec_jami if jami else pct_dec_body
            else:
                cell.number_format = FMT_JAMI_COUNT if jami else FMT_BODY_COUNT
                last_metric = name

    last = first + n_rows - 1
    for ci, (up, dn) in arrow_cols.items():
        col = get_column_letter(ci + 1)
        rng = f"{col}{first}:{col}{last}"
        up, dn = GREEN_FIX.get(up, up) or "FF0000", GREEN_FIX.get(dn, dn) or "0000FF"
        ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThan", formula=["0"], font=Font(color=up)))
        ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["0"], font=Font(color=dn)))

    if key == "billing_tafovut":           # PDF dagi qizil ustunli diagramma
        col = get_column_letter(2)
        ws.conditional_formatting.add(
            f"{col}{first + 1}:{col}{last}",
            DataBarRule(start_type="num", start_value=0, end_type="max", color="FF6B6B", showValue=True),
        )

    ws.freeze_panes = ws.cell(row=first, column=2)
    # ---------------- chop etish: A4 albom, bitta betga (PDF dagidek)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_area = f"A1:{get_column_letter(ncols)}{last + 1}"
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = ws.page_margins.bottom = 0.5


def _generic_sheet(wb, used, sec, a, b, da, db):
    """Shablonsiz bo‘lim uchun oddiy (A | B | Farq) ko‘rinish."""
    ws = wb.create_sheet(_sheet_name(sec["section"].short, used))
    ws.sheet_view.showGridLines = False
    ncols = 1 + 3 * len(sec["metrics"])
    ws.cell(row=1, column=1, value=sec["section"].title).font = Font(bold=True, size=14, color=TEXT, name=FONT)
    ws.cell(row=2, column=1, value=f"{da} → {db}  ·  Qashqadaryo HETK").font = Font(size=10, color=GRAY, name=FONT)

    top, mid, sub = 4, 5, 6
    ws.cell(row=top, column=1, value="Hudud")
    ws.merge_cells(start_row=top, start_column=1, end_row=sub, end_column=1)

    col = 2
    for g in sec["groups"]:
        span = 3 * len(g["metrics"])
        ws.cell(row=top, column=col, value=g["name"] or "—")
        if span > 1:
            ws.merge_cells(start_row=top, start_column=col, end_row=top, end_column=col + span - 1)
        for m in g["metrics"]:
            ws.cell(row=mid, column=col, value=m.label)
            ws.merge_cells(start_row=mid, start_column=col, end_row=mid, end_column=col + 2)
            for k, lab in enumerate((f"{a:%d.%m}", f"{b:%d.%m}", "Farq")):
                ws.cell(row=sub, column=col + k, value=lab)
            col += 3
    for rr in (top, mid, sub):
        for cc in range(1, ncols + 1):
            cell = ws.cell(row=rr, column=cc)
            cell.alignment, cell.border = CENTER, BORDER
            cell.font = HEAD_FONT if rr != sub else SUB_FONT
            cell.fill = HEAD_FILL
    ws.row_dimensions[top].height = 26
    ws.row_dimensions[mid].height = 44
    ws.row_dimensions[sub].height = 22

    row_i = 7
    for row in sec["rows"]:
        cell = ws.cell(row=row_i, column=1, value=row["district"].name)
        cell.alignment, cell.border = LEFT, BORDER
        cell.font = Font(bold=True, size=10, name=FONT, color=TEXT)
        if row["is_total"]:
            cell.fill = TOTAL_PF
        col = 2
        for m, cd in zip(sec["metrics"], row["cells"]):
            for k, key in enumerate(("a", "b", "diff")):
                v = _num(cd[key])
                cell = ws.cell(row=row_i, column=col + k, value=("—" if v is None else v))
                cell.border = BORDER
                cell.alignment = RIGHT
                cell.font = Font(bold=(row["is_total"] or k < 2), size=10, name=FONT,
                                 color=(GRAY if k == 2 else TEXT))
                if row["is_total"]:
                    cell.fill = TOTAL_PF
                if v is not None:
                    if k < 2:
                        cell.number_format = FMT_PCT if m.kind == "pct" else FMT_COUNT
                    else:
                        cell.number_format = FMT_DIFF_PCT if m.kind == "pct" else FMT_DIFF
            col += 3
        row_i += 1

    ws.column_dimensions["A"].width = 20
    for c in range(2, ncols + 1):
        ws.column_dimensions[get_column_letter(c)].width = 8.5 if (c - 2) % 3 != 2 else 8
    ws.freeze_panes = ws.cell(row=7, column=2)
    _print_setup(ws, title_rows="4:6", title_cols="A:A", fit_height=0,
                 footer_left=f"{sec['section'].short}: {da} → {db}")
    _paginate_columns(ws, sec["groups"])


def build_workbook(comp: dict, user=None) -> bytes:
    a, b = comp["a"], comp["b"]
    da, db = f"{a:%d.%m.%Y}", f"{b:%d.%m.%Y}"
    wb = Workbook()
    used = set()

    # -------- Umumiy varaq
    ws = wb.active
    ws.title = _sheet_name("Umumiy", used)
    ws.sheet_view.showGridLines = False
    ws["A1"] = "Qashqadaryo HETK — kunlik dinamika"
    ws["A1"].font = Font(bold=True, size=16, color=TEXT, name=FONT)
    ws["A2"] = f"Solishtirish: {da} → {db}"
    ws["A2"].font = Font(size=11, color=GRAY, name=FONT)
    prepared = timezone.localtime().strftime("%d.%m.%Y %H:%M")
    who = (user.get_full_name() or user.username) if user and getattr(user, "is_authenticated", False) else ""
    ws["A3"] = f"Tayyorlandi: {prepared}" + (f" · {who}" if who else "")
    ws["A3"].font = Font(size=10, color=GRAY, name=FONT)

    head = ["Bo‘lim", "Ko‘rsatkich", da, db, "Farq"]
    r = 5
    for c, h in enumerate(head, start=1):
        cell = ws.cell(row=r, column=c, value=h)
        cell.font, cell.fill, cell.alignment, cell.border = HEAD_FONT, HEAD_FILL, CENTER, BORDER
    r += 1
    for sec in comp["sections"]:
        total_row = next(x for x in sec["rows"] if x["is_total"])
        for m, cell_d in zip(sec["metrics"], total_row["cells"]):
            vals = [sec["section"].short, f"{m.group}: {m.label}" if m.group else m.label,
                    _num(cell_d["a"]), _num(cell_d["b"]), _num(cell_d["diff"])]
            for c, v in enumerate(vals, start=1):
                cell = ws.cell(row=r, column=c, value=("—" if v is None else v))
                cell.border = BORDER
                cell.font = Font(size=10, name=FONT, bold=(c == 2 and False), color=(GRAY if c == 5 else TEXT))
                cell.alignment = LEFT if c <= 2 else RIGHT
                if c in (3, 4):
                    cell.number_format = FMT_PCT if m.kind == "pct" else FMT_COUNT
                if c == 5:
                    cell.number_format = FMT_DIFF_PCT if m.kind == "pct" else FMT_DIFF
            r += 1
    for col, w in zip("ABCDE", (26, 58, 16, 16, 14)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A6"
    _print_setup(ws, title_rows="5:5", fit_height=0)

    # -------- Har bir bo‘lim uchun varaq (PDF dagi dinamika jadvali ko‘rinishida)
    styles = _styles()
    for sec in comp["sections"]:
        key = sec["section"].key
        if key in styles and key in HETK_SPECS:
            _pdf_sheet(wb, used, sec, styles[key], a, b)
        else:
            _generic_sheet(wb, used, sec, a, b, da, db)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
