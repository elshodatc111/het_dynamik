"""Solishtirma hisobotni Excel (.xlsx) ga yozish. Ranglar neytral: yaxshi/yomon bo‘yicha bo‘yalmaydi."""
from __future__ import annotations

import re
from io import BytesIO

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

NAVY = "1F2A44"
SOFT = "E8ECF4"
TOTAL_FILL = "F2F4F7"
LINE = "D0D5DD"
GRAY = "667085"

FMT_COUNT = "#,##0"
FMT_PCT = '0.0"%"'
FMT_DIFF = '"↑ "#,##0;"↓ "-#,##0;"–"'
FMT_DIFF_PCT = '"↑ "0.0;"↓ "-0.0;"–"'

thin = Side(style="thin", color=LINE)
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
HEAD_FONT = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
HEAD_FILL = PatternFill("solid", fgColor=NAVY)
SOFT_FILL = PatternFill("solid", fgColor=SOFT)
TOTAL_PF = PatternFill("solid", fgColor=TOTAL_FILL)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)


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
    ws["A1"].font = Font(bold=True, size=16, color=NAVY)
    ws["A2"] = f"Solishtirish: {da} → {db}"
    ws["A2"].font = Font(size=11, color=GRAY)
    prepared = timezone.localtime().strftime("%d.%m.%Y %H:%M")
    who = (user.get_full_name() or user.username) if user and getattr(user, "is_authenticated", False) else ""
    ws["A3"] = f"Tayyorlandi: {prepared}" + (f" · {who}" if who else "")
    ws["A3"].font = Font(size=10, color=GRAY)

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
                cell.alignment = LEFT if c <= 2 else Alignment(horizontal="right", vertical="center")
                if c in (3, 4):
                    cell.number_format = FMT_PCT if m.kind == "pct" else FMT_COUNT
                if c == 5:
                    cell.number_format = FMT_DIFF_PCT if m.kind == "pct" else FMT_DIFF
            r += 1
    for col, w in zip("ABCDE", (26, 58, 16, 16, 14)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A6"

    # -------- Har bir bo‘lim uchun varaq
    for sec in comp["sections"]:
        ws = wb.create_sheet(_sheet_name(sec["section"].short, used))
        ws.sheet_view.showGridLines = False
        ncols = 1 + 3 * len(sec["metrics"])
        ws.cell(row=1, column=1, value=sec["section"].title).font = Font(bold=True, size=13, color=NAVY)
        ws.cell(row=2, column=1, value=f"{da} → {db}").font = Font(size=10, color=GRAY)

        top, mid, sub = 4, 5, 6
        ws.cell(row=top, column=1, value="Hudud")
        ws.merge_cells(start_row=top, start_column=1, end_row=sub, end_column=1)

        col = 2
        for g in sec["groups"]:
            span = 3 * len(g["metrics"])
            ws.cell(row=top, column=col, value=g["name"])
            if span > 1:
                ws.merge_cells(start_row=top, start_column=col, end_row=top, end_column=col + span - 1)
            for m in g["metrics"]:
                ws.cell(row=mid, column=col, value=m.label)
                ws.merge_cells(start_row=mid, start_column=col, end_row=mid, end_column=col + 2)
                for k, lab in enumerate((da, db, "Farq")):
                    ws.cell(row=sub, column=col + k, value=lab)
                col += 3
        for rr in (top, mid, sub):
            for cc in range(1, ncols + 1):
                cell = ws.cell(row=rr, column=cc)
                cell.alignment, cell.border = CENTER, BORDER
                cell.font = HEAD_FONT if rr != sub else Font(bold=True, size=9, color=NAVY)
                cell.fill = HEAD_FILL if rr != sub else SOFT_FILL
        ws.row_dimensions[mid].height = 42
        ws.row_dimensions[top].height = 24

        row_i = 7
        for row in sec["rows"]:
            cell = ws.cell(row=row_i, column=1, value=row["district"].name)
            cell.alignment, cell.border = LEFT, BORDER
            cell.font = Font(bold=row["is_total"], size=10)
            if row["is_total"]:
                cell.fill = TOTAL_PF
            col = 2
            for m, cd in zip(sec["metrics"], row["cells"]):
                for k, key in enumerate(("a", "b", "diff")):
                    v = _num(cd[key])
                    cell = ws.cell(row=row_i, column=col + k, value=("—" if v is None else v))
                    cell.border = BORDER
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    cell.font = Font(bold=row["is_total"], size=10, color=(GRAY if k == 2 else "000000"))
                    if row["is_total"]:
                        cell.fill = TOTAL_PF
                    if v is not None:
                        if k < 2:
                            cell.number_format = FMT_PCT if m.kind == "pct" else FMT_COUNT
                        else:
                            cell.number_format = FMT_DIFF_PCT if m.kind == "pct" else FMT_DIFF
                col += 3
            row_i += 1

        ws.column_dimensions["A"].width = 18
        for c in range(2, ncols + 1):
            ws.column_dimensions[get_column_letter(c)].width = 11 if (c - 2) % 3 != 2 else 9
        ws.freeze_panes = ws.cell(row=7, column=2)
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
