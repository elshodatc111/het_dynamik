"""Monitoring hisobotlarini PDF ga yozish."""
from __future__ import annotations

from io import BytesIO

from reportlab.lib.colors import HexColor, black
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from apps.core import pdf
from apps.reports import pdfs as pdfs_r

from . import services
from .models import Snapshot

PAGE_W, PAGE_H = pdfs_r.PAGE_W, pdfs_r.PAGE_H
F, FB = pdf.F, pdf.FB
TITLE = ("Qashqadaryo HETK hududida CAS dasturida offline konsentratorlar va aloqaga chiqmay qolgan "
         "hisoblagichlar holati bo‘yicha")


def _n(v):
    return "—" if v is None else f"{int(v):,}".replace(",", " ")


def _d(v):
    if v is None:
        return "—"
    v = int(v)
    return "–" if v == 0 else (f"↑ {v:,}".replace(",", " ") if v > 0 else f"↓ {-v:,}".replace(",", " "))


TITLE_LINES = ("Qashqadaryo HETK hududida CAS dasturida offline konsentratorlar va",
               "aloqaga chiqmay qolgan hisoblagichlar holati bo‘yicha")
GROUP_COLORS = {"Aholi": "548235", "Axoli": "548235", "Yuridik": "2E75B6", "Balans": "C55A11"}


def _cell(text, fill="DDEBF7", color="000000", sz=8.5, runs=None):
    return {"fill": fill, "rot": False, "runs": runs or [{"t": text, "color": color, "b": True, "sz": sz}]}


def _head_cells(head, d_cols, delta_cols=()):
    """[(matn, colspan, rowspan)] qatorlari -> sarlavha katakchalari (r0,c0,r1,c1,...)."""
    cells, occupied = [], set()
    for r, line in enumerate(head):
        c = 0
        for text, cs, rs in line:
            while (r, c) in occupied:
                c += 1
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    occupied.add((rr, cc))
            if r == 0 and c == 0:
                cell = _cell(text, sz=11)
            elif r == 0:
                cell = _cell(text, color="C00000", sz=9.5)
            elif text in ("Farq",):
                cell = _cell("", runs=[{"t": "↑ ", "color": "FF0000", "b": True, "sz": 8}, {"t": "↓", "color": "0000FF", "b": True, "sz": 8}])
            elif text in GROUP_COLORS:
                cell = _cell(text, color=GROUP_COLORS[text], sz=8)
            elif r == len(head) - 1 and len(text) == 5 and text[2] == ".":   # «07.11»
                cell = _cell(text, color="1F3864", sz=8)
            else:
                cell = _cell(text, color="262626", sz=8.5)
            cell.update(r0=r, c0=c, r1=r + rs - 1, c1=c + cs - 1)
            cells.append(cell)
            c += cs
    return cells


def _fit(text, font, size, width):
    w = stringWidth(text, font, size)
    return size if w <= width else max(size * width / w, 4.5)


def _designed_pdf(head, total, rows, sep_cols, date_text, note="", first_w=96):
    """Dinamika namunasidagi dizayn: sarlavha, «D I N A M I K A», Hudud nomi qutisi, ko‘k-och sarlavha, sariq Jami.
    Jadval bitta varoqda (Letter, albom). `total`/`rows`: [(esp, [(matn, rang|None)...])]."""
    ncols = sum(cs for _t, cs, _r in head[0])
    left, right = 28, PAGE_W - 28
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    pdf._setup()
    c.setTitle("Offline monitoring")
    cx = PAGE_W / 2
    y = 30
    for line in TITLE_LINES:
        c.setFont(FB, 13.5)
        c.setFillColor(black)
        c.drawCentredString(cx, PAGE_H - y - 11, line)
        y += 17
    y += 3
    c.setFont(FB, 14)
    c.setFillColor(HexColor("#FF0000"))
    c.drawCentredString(cx, PAGE_H - y - 12, "D I N A M I K A" if "→" in date_text or "-" in date_text else "M A ’ L U M O T")
    y += 30
    base = PAGE_H - y - 11
    c.setFont(F, 11)
    c.setFillColor(black)
    c.drawString(left, base, "Hudud nomi:")
    bx = left + stringWidth("Hudud nomi:", F, 11) + 12
    bw = stringWidth(services.REGION_NAME, FB, 11) + 14
    c.setStrokeColor(HexColor("#0066FF"))
    c.setLineWidth(0.8)
    c.rect(bx - 6, base - 4, bw, 17, fill=0, stroke=1)
    c.setFont(FB, 11)
    c.setFillColor(HexColor("#0066FF"))
    c.drawString(bx, base, services.REGION_NAME)
    c.setFont(FB, 11)
    c.setFillColor(HexColor("#0000FF"))
    c.drawRightString(right, base, date_text)
    y += 28

    widths = [first_w] + [(right - left - first_w) / (ncols - 1)] * (ncols - 1) if ncols > 1 else [right - left]
    xs = [left]
    for w in widths:
        xs.append(xs[-1] + w)
    n_head = len(head)
    body = [("T", total)] + [("R", r) for r in rows]
    avail = PAGE_H - 26 - y
    hh = [15, 21, 15][:n_head] + [15] * max(0, n_head - 3)
    row_h = max(min((avail - sum(hh) - (6 if note else 0)) / len(body), 19), 9)
    scale = min(1.0, (avail - (8 if note else 0)) / (sum(hh) + row_h * len(body)))
    ys = [y]
    for h in hh:
        ys.append(ys[-1] + h * scale)
    for cell in _head_cells(head, None):
        pdfs_r._draw_header_cell(c, xs[cell["c0"]], ys[cell["r0"]], xs[cell["c1"] + 1] - xs[cell["c0"]],
                                 ys[cell["r1"] + 1] - ys[cell["r0"]], cell, 1.0)
    yy = ys[-1]
    for kind, (esp, vals) in body:
        h = row_h * scale
        jami = kind == "T"
        c.setLineWidth(0.4)
        c.setStrokeColor(black)
        for i in range(ncols):
            fill = "FFE699" if jami else None
            c.setFillColor(HexColor("#" + fill) if fill else black)
            c.rect(xs[i], PAGE_H - yy - h, xs[i + 1] - xs[i], h, fill=1 if fill else 0, stroke=1)
        size = min(8.5, h * 0.62)
        c.setFont(FB if jami else F, size)
        c.setFillColor(black)
        c.drawString(xs[0] + 3, PAGE_H - yy - h / 2 - size * 0.35, esp)
        for i, (text, color) in enumerate(vals, 1):
            if not text:
                continue
            font = FB if (jami or color or i in sep_cols) else F
            sz = _fit(text, font, size, xs[i + 1] - xs[i] - 3)
            c.setFont(font, sz)
            c.setFillColor(HexColor("#" + color) if color else black)
            c.drawRightString(xs[i + 1] - 2, PAGE_H - yy - h / 2 - sz * 0.35, text)
        yy += h
    if note:
        c.setFont(F, 7.5)
        c.setFillColor(HexColor("#595959"))
        c.drawString(left, PAGE_H - yy - 11, note)
    c.showPage()
    c.save()
    return buf.getvalue()


def _cnt(v, jami=False):
    if v is None:
        return ""
    v = int(v)
    return ("-" if jami else "") if v == 0 else f"{v:,}".replace(",", " ")


def _delta(v):
    if v is None or int(v) == 0:
        return ("", None)
    v = int(v)
    return (f"↑ {v:,}".replace(",", " "), "FF0000") if v > 0 else (f"↓ {-v:,}".replace(",", " "), "0000FF")


def svod_pdf(snap: Snapshot) -> bytes:
    g = services.grid_for(snap)
    nd, ng = len(g["d_labels"]), len(g["gkeys"])
    h1, h2, h3 = [("ESP", 1, 3)], [], []
    if g["has_dcu"]:
        h1.append(("Offline konsentratorlar", nd + 1, 1))
        h2 += [("JAMI", 1, 2)] + [(lb, 1, 2) for lb in g["d_labels"]]
    if g["has_meters"]:
        h1.append(("Aloqadan chiqib ketgan hisoblagichlar", 1 + len(g["m_labels"]) * (1 + ng), 1))
        h2.append(("JAMI", 1, 2))
        h2 += [(lb, 1 + ng, 1) for lb in g["m_labels"]]
        h3 += [x for _ in g["m_labels"] for x in [("JAMI", 1, 1)] + [(n, 1, 1) for n in g["gnames_list"]]]
    line = lambda r, j: (r["esp"], [(_cnt(v, j), None) for v in r["vals"]])  # noqa: E731
    sep = [i + 1 for i, c in enumerate(g["cols"]) if c["sep"]]
    note = (f"Konsentratorlar: {g['d_min']} kundan kam offline hisobga olinmagan · "
            f"hisoblagichlar: {g['m_min']} kundan kam aloqasizlar hisobga olinmagan.")
    return _designed_pdf([h1, h2, h3], line(g["total"], True), [line(r, False) for r in g["rows"]], sep,
                         f"Ma’lumot: {snap.report_date:%d.%m.%Y}", note, first_w=100)


def compare_pdf(a: Snapshot, b: Snapshot, tbl: dict) -> bytes:
    def line(r, jami):
        out = []
        for c in r["cells"]:
            out += [(_cnt(c["a"], jami), None), (_cnt(c["b"], jami), None), _delta(c["d"])]
        return (r["esp"], out)

    return _designed_pdf(tbl["head"], line(tbl["total"], True), [line(r, False) for r in tbl["rows"]], tbl["sep_cols"],
                         f"Dinamika: {a.report_date:%d.%m.%Y} - {b.report_date:%d.%m.%Y}",
                         "↑ — aloqasizlar ko‘paygan (yomonlashgan), ↓ — kamaygan (yaxshilangan).", first_w=96)


LIST_LIMIT = 4000


def dcu_list_pdf(qs, snap: Snapshot, desc: str) -> tuple[bytes, bool]:
    total = qs.count()
    head = [[("№", 1, 1), ("ESP", 1, 1), ("Podstansiya", 1, 1), ("Fider", 1, 1), ("Konsentrator", 1, 1), ("Kun", 1, 1), ("Mas’ul", 1, 1)]]
    rows = [[str(i), r.esp, r.substation, r.feeder, r.device_no, str(r.days), r.manager]
            for i, r in enumerate(qs[:LIST_LIMIT], 1)]
    return _list(f"Offline konsentratorlar · {desc}", snap, head, rows, [28, 90, 150, 120, 80, 32, 220], total)


def meter_list_pdf(qs, snap: Snapshot, desc: str) -> tuple[bytes, bool]:
    total = qs.count()
    head = [[("№", 1, 1), ("ESP", 1, 1), ("Podstansiya", 1, 1), ("Fider", 1, 1), ("Iste’molchi", 1, 1), ("Hisoblagich", 1, 1),
             ("Kun", 1, 1), ("Guruh", 1, 1)]]
    rows = [[str(i), r.esp, r.substation, r.feeder, r.consumer_name, r.meter_no, str(r.days), r.get_group_display()]
            for i, r in enumerate(qs[:LIST_LIMIT], 1)]
    return _list(f"Aloqaga chiqmay qolgan hisoblagichlar · {desc}", snap, head, rows, [28, 80, 120, 100, 170, 80, 30, 60], total)


def _list(title, snap, head, rows, widths, total):
    scale = 780 / sum(widths)
    blocks = [{"head": head, "rows": rows, "col_widths": [w * scale for w in widths], "font": 6.6, "align": "LEFT",
               "note": (f"Jami {total:,} ta; PDF da dastlabki {LIST_LIMIT:,} tasi ko‘rsatildi — to‘liq ro‘yxat uchun Excelni yuklang." if total > LIST_LIMIT else "")
               .replace(",", " ")}]
    return pdf.make_pdf(title, blocks, meta_left=f"Hudud nomi: {services.REGION_NAME}",
                        meta_right=f"Ma’lumot: {snap.report_date:%d.%m.%Y} · jami {total:,} ta".replace(",", " ")), total > LIST_LIMIT
