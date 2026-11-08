"""PDF yaratish (reportlab): jadval hisobotlari uchun umumiy yordamchi. Shrift: DejaVu Sans Condensed (o‘zbek harflari va ↑↓ belgilari bor)."""
from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT_DIR = Path(__file__).parent / "fonts"
F, FB = "DejaVuC", "DejaVuC-Bold"
_ready = False

HEAD_BG = colors.HexColor("#DCE6F1")
TOTAL_BG = colors.HexColor("#FFE699")
GRID = colors.HexColor("#8A97AD")
UP, DOWN, MUTED = colors.HexColor("#C0233F"), colors.HexColor("#0B8A5E"), colors.HexColor("#8A94A6")


def _setup():
    global _ready
    if not _ready:
        pdfmetrics.registerFont(TTFont(F, str(FONT_DIR / "DejaVuSansCondensed.ttf")))
        pdfmetrics.registerFont(TTFont(FB, str(FONT_DIR / "DejaVuSansCondensed-Bold.ttf")))
        pdfmetrics.registerFontFamily(F, normal=F, bold=FB)
        _ready = True


def clean(text) -> str:
    """Ingichka/ajralmas bo‘sh joylarni oddiy bo‘sh joyga almashtiradi."""
    return re.sub(r"[   ]", " ", "" if text is None else str(text))


def _expand(head):
    """[[(matn, colspan, rowspan)...]...] -> (matritsa, SPAN buyruqlari)."""
    nrows = len(head)
    ncols = sum(c for _, c, _ in head[0]) if head else 0
    grid = [["" for _ in range(ncols)] for _ in range(nrows)]
    used = [[False] * ncols for _ in range(nrows)]
    spans = []
    for r, row in enumerate(head):
        c = 0
        for text, cs, rs in row:
            while c < ncols and used[r][c]:
                c += 1
            grid[r][c] = clean(text)
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    used[rr][cc] = True
            if cs > 1 or rs > 1:
                spans.append(("SPAN", (c, r), (c + cs - 1, r + rs - 1)))
            c += cs
    return grid, spans


def _block_table(block, avail_w):
    head, spans = _expand(block["head"])
    nh = len(head)
    body = [[clean(x) for x in row] for row in block["rows"]]
    ncols = len(head[0])
    first_w = block.get("first_w", 96)
    maxlen = max([len(x) for r in body for x in r[1:]] + [len(clean(t)) / max(cs, 1) for row in block["head"][1:] for t, cs, _ in row] + [3])
    other = (avail_w - first_w) / max(ncols - 1, 1)
    fs = max(4.6, min(8.5, (other - 5) / (0.5 * maxlen)))
    if block.get("col_widths"):
        widths = block["col_widths"]
        fs = block.get("font", 7)
    else:
        widths = [first_w] + [other] * (ncols - 1)
    data = head + body
    st = [
        ("FONT", (0, 0), (-1, -1), F, fs), ("FONT", (0, 0), (-1, nh - 1), FB, fs),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("BACKGROUND", (0, 0), (-1, nh - 1), HEAD_BG),
        ("ALIGN", (0, 0), (-1, nh - 1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, nh), (-1, -1), block.get("align", "RIGHT")), ("ALIGN", (0, nh), (0, -1), "LEFT"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ] + spans
    nt = block.get("total_rows", 0)
    if nt:
        st += [("BACKGROUND", (0, nh), (-1, nh + nt - 1), block.get("total_bg", TOTAL_BG)), ("FONT", (0, nh), (-1, nh + nt - 1), FB, fs)]
    for r, row in enumerate(body, nh):
        for c, v in enumerate(row):
            if v.startswith("↑"):
                st.append(("TEXTCOLOR", (c, r), (c, r), UP))
            elif v.startswith("↓"):
                st.append(("TEXTCOLOR", (c, r), (c, r), DOWN))
            elif v in ("0", "–", "—") and c > 0:
                st.append(("TEXTCOLOR", (c, r), (c, r), MUTED))
    for c in block.get("sep_cols", ()):
        st.append(("LINEBEFORE", (c, 0), (c, -1), 1.0, GRID))
    t = Table(data, colWidths=widths, repeatRows=nh)
    t.setStyle(TableStyle(st))
    return t


def make_pdf(title: str, blocks: list[dict], *, subtitle: str = "", meta_left: str = "", meta_right: str = "",
             big: bool = False, footer: str = "Qashqadaryo HETK") -> bytes:
    """blocks: [{heading, head, rows, total_rows, first_w, sep_cols, note}]. big=True — A3 albom."""
    _setup()
    page = landscape(A3 if big else A4)
    m = 24
    buf = BytesIO()
    prepared = timezone.localtime().strftime("%d.%m.%Y %H:%M")

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFont(F, 7)
        canvas.setFillColor(colors.HexColor("#6B7488"))
        canvas.drawString(m, 12, f"{footer} · tayyorlandi {prepared}")
        canvas.drawRightString(page[0] - m, 12, f"{doc.page}-bet")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=m, rightMargin=m, topMargin=m, bottomMargin=m + 6, title=clean(title))
    h1 = ParagraphStyle("h1", fontName=FB, fontSize=13 if not big else 16, leading=17, alignment=1)
    sub = ParagraphStyle("sub", fontName=FB, fontSize=11, leading=14, alignment=1, textColor=colors.HexColor("#D00000"))
    meta = ParagraphStyle("meta", fontName=F, fontSize=8.5, leading=11)
    metar = ParagraphStyle("metar", parent=meta, alignment=2, fontName=FB, textColor=colors.HexColor("#1F3FBF"))
    h2 = ParagraphStyle("h2", fontName=FB, fontSize=10, leading=13, spaceBefore=4, spaceAfter=3)
    note = ParagraphStyle("note", fontName=F, fontSize=7, leading=9, textColor=colors.HexColor("#6B7488"))
    story = [Paragraph(clean(title), h1)]
    if subtitle:
        story.append(Paragraph(clean(subtitle), sub))
    if meta_left or meta_right:
        t = Table([[Paragraph(clean(meta_left), meta), Paragraph(clean(meta_right), metar)]], colWidths=[(page[0] - 2 * m) / 2] * 2)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        story += [Spacer(1, 4), t]
    story.append(Spacer(1, 6))
    for i, b in enumerate(blocks):
        if i:
            story.append(PageBreak())
        if b.get("heading"):
            story.append(Paragraph(clean(b["heading"]), h2))
        story.append(_block_table(b, page[0] - 2 * m))
        if b.get("note"):
            story += [Spacer(1, 4), Paragraph(clean(b["note"]), note)]
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buf.getvalue()
