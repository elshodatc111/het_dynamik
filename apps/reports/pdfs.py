"""Dinamika solishtiruvini PDF ga yozish.

Har bir bo‘lim — alohida bet (Letter, albom), foydalanuvchi namunasidagi dizaynda: sarlavha, «D I N A M I K A»,
«Hudud nomi» qutisi va rangli jadval (shablon `excel_styles.json` va parser/layouts.py dan). Shablonsiz bo‘lim bo‘lsa —
oddiy (A | B | Farq) jadvalga qaytadi."""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas

from apps.core import pdf
from apps.core.templatetags.ui import fmt_delta, fmt_metric

from .excel import GREEN_FIX, PCT_BODY, _styles
from .parser.layouts import HETK_SPECS

PAGE_W, PAGE_H = landscape(letter)
F, FB = pdf.F, pdf.FB
FIX = {"00FF00": "00B050", "0000FF": "0000FF"}


def _col(hex6):
    return HexColor("#" + GREEN_FIX.get(hex6, hex6 or "000000"))


def _spaced(n: int) -> str:
    """12345 -> «12 345»."""
    return f"{n:,}".replace(",", " ")


def _runs_width(runs, k, scale=1.0):
    return sum(stringWidth(r["t"], FB if r.get("b", True) else F, r["sz"] / k * scale) for r in runs)


def _draw_runs(c, runs, x, base, k, scale=1.0):
    for r in runs:
        size = r["sz"] / k * scale
        font = FB if r.get("b", True) else F
        c.setFont(font, size)
        c.setFillColor(_col(r["color"]))
        c.drawString(x, base, r["t"])
        x += stringWidth(r["t"], font, size)


def _fit_scale(runs, k, max_w):
    w = _runs_width(runs, k)
    return min(1.0, max_w / w) if w else 1.0


def _wrap_header(runs, k, width, height, scale=1.0):
    """Sarlavha katagi matnini so‘zlarga bo‘lib, qatorlarga joylaydi. Sig‘masa, shriftni kichraytiradi."""
    while True:
        tokens = []
        for r in runs:
            for i, w in enumerate(r["t"].split(" ")):
                if w:
                    tokens.append((w, r))
        lines, cur, cw = [], [], 0.0
        for w, r in tokens:
            font, size = (FB if r.get("b", True) else F), r["sz"] / k * scale
            ww = stringWidth(w, font, size)
            sp = stringWidth(" ", font, size)
            if cur and cw + sp + ww > width:
                lines.append(cur)
                cur, cw = [], 0.0
            cur.append((w, r))
            cw += (sp if len(cur) > 1 else 0) + ww
        if cur:
            lines.append(cur)
        lh = [max(r["sz"] / k * scale for _, r in ln) * 1.18 for ln in lines]
        too_wide = any(stringWidth(w, FB, r["sz"] / k * scale) > width for ln in lines for w, r in ln)
        if (sum(lh) <= height and not too_wide) or scale < 0.45:
            return lines, lh, scale
        scale *= 0.92


def _draw_header_cell(c, x, y_top, w, h, cell, k):
    c.setFillColor(HexColor("#" + cell["fill"]))
    c.setStrokeColor(black)
    c.setLineWidth(0.5)
    c.rect(x, PAGE_H - y_top - h, w, h, fill=1, stroke=1)
    runs = cell["runs"]
    if not runs:
        return
    c.saveState()
    if cell["rot"]:
        c.translate(x + w / 2, PAGE_H - y_top - h / 2)
        c.rotate(90)
        bw, bh = h - 4, w - 2
        origin = (-bw / 2, -bh / 2)
    else:
        bw, bh = w - 3, h - 2
        c.translate(x + 1.5, PAGE_H - y_top - h + 1)
        origin = (0, 0)
    lines, lh, sc = _wrap_header(runs, k, bw, bh)
    y = origin[1] + (bh + sum(lh)) / 2
    for ln, hgt in zip(lines, lh):
        y -= hgt
        lw = sum(stringWidth(wd, FB if r.get("b", True) else F, r["sz"] / k * sc) for wd, r in ln)
        lw += sum(stringWidth(" ", F, ln[0][1]["sz"] / k * sc) for _ in ln[1:])
        x0 = origin[0] + (bw - lw) / 2
        for i, (wd, r) in enumerate(ln):
            font, size = (FB if r.get("b", True) else F), r["sz"] / k * sc
            c.setFont(font, size)
            c.setFillColor(_col(r["color"]))
            c.drawString(x0, y + hgt * 0.22, wd)
            x0 += stringWidth(wd + (" " if i < len(ln) - 1 else ""), font, size)
    c.restoreState()


def _fmt_count(v, jami):
    if v is None:
        return ""
    n = int(v)
    if n == 0:
        return "-" if jami else ""
    return _spaced(n) if n > 0 else "-" + _spaced(-n)


def _fmt_pct(v, jami, key):
    if v is None:
        return ""
    f = float(v)
    if jami or key not in PCT_BODY:
        return f"{f:.1f}".replace(".", ",") + "%"
    return f"{round(f):d}%"


def _draw_cell_text(c, text, x, y_top, w, h, size, bold, color, align):
    if not text:
        return
    font = FB if bold else F
    size = min(size, (w - 4) / max(stringWidth(text, font, size), 1) * size)
    c.setFont(font, size)
    c.setFillColor(_col(color))
    base = PAGE_H - y_top - h / 2 - size * 0.35
    tw = stringWidth(text, font, size)
    tx = x + 2 if align == "left" else (x + (w - tw) / 2 if align == "center" else x + w - 2 - tw)
    c.drawString(tx, base, text)


def _x0(st):
    x = st["table_x0"]
    return x if x > 0 else st["title"][2]["items"][0]["x0"]


def _section_page(c, sec, st, a, b):
    xs_all = [_x0(st), st["table_x1"]] + [it[e] for ln in st["title"] for it in ln["items"] for e in ("x0", "x1")]
    lo, hi = min(xs_all), max(xs_all)
    avail = PAGE_W - 36
    s = min(1.0, avail / (hi - lo)) if hi - lo else 1.0
    c.saveState()
    c.translate(18 - lo * s if lo * s < 18 or s < 1 else 0, PAGE_H * (1 - s))
    c.scale(s, s)
    _section_body(c, sec, st, a, b, s)
    c.restoreState()


def _section_body(c, sec, st, a, b, s):
    key = sec["section"].key
    spec = HETK_SPECS[key]
    k = st["k"]
    ttl = st["title"]
    x0, x1 = _x0(st), st["table_x1"]
    # sarlavha qatorlari
    for line in ttl[:2]:
        runs = line["items"][0]["runs"]
        sc = _fit_scale(runs, k, (x1 - x0) * 1.05)
        w = _runs_width(runs, k, sc)
        size = max(r["sz"] for r in runs) / k * sc
        _draw_runs(c, runs, (x0 + x1) / 2 - w / 2, PAGE_H - line["top"] - size * 0.82, k, sc)
    meta = ttl[2]
    top = meta["top"]
    items = meta["items"]
    it_r = items[-1]
    runs0 = items[0]["runs"]
    size_l = runs0[0]["sz"] / k
    base = PAGE_H - top - size_l * 1.0
    c.setFont(F, size_l)
    c.setFillColor(black)
    lx = items[0]["x0"]
    c.drawString(lx, base, "Hudud nomi:")
    bsz = (items[1]["runs"][0] if len(items) == 3 else runs0[-1])["sz"] / k
    if len(items) == 3:
        bx0, bx1 = items[1]["x0"], items[1]["x1"]
    else:
        bx0 = lx + stringWidth("Hudud nomi: ", F, size_l) + 4
        bx1 = bx0 + stringWidth("Qashqadaryo HETK", FB, bsz)
    c.setStrokeColor(_col("0066FF"))
    c.setLineWidth(0.8)
    c.rect(bx0 - 6, base - 4, max(bx1 - bx0, stringWidth('Qashqadaryo HETK', FB, bsz)) + 12, bsz + 8, fill=0, stroke=1)
    c.setFont(FB, bsz)
    c.setFillColor(_col("0066FF"))
    c.drawString(bx0, base, "Qashqadaryo HETK")
    rsz = it_r["runs"][0]["sz"] / k
    c.setFont(FB, rsz)
    c.setFillColor(_col(it_r["runs"][0]["color"]))
    c.drawRightString(it_r["x1"], base, f"Dinamika: {a:%d.%m.%Y} - {b:%d.%m.%Y}")

    # jadval geometriyasi
    n_rows = len(sec["rows"])
    hh = [h / k for h in st["header_h"]]
    row_h, jami_h = st["row_h"] / k, st["jami_h"] / k
    table_top = top + size_l + 16
    need = sum(hh) + sum(jami_h if r["is_total"] else row_h for r in sec["rows"])
    room = PAGE_H - 24 / s - table_top
    f = min(1.0, room / need)
    hh = [h * f for h in hh]
    row_h, jami_h = row_h * f, jami_h * f
    widths = st["col_w"]
    sw = sum(widths)
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w / sw * (x1 - x0))

    ys = [table_top]
    for h in hh:
        ys.append(ys[-1] + h)
    for cell in st["header"]:
        _draw_header_cell(c, xs[cell["c0"]], ys[cell["r0"]], xs[cell["c1"] + 1] - xs[cell["c0"]],
                          ys[cell["r1"] + 1] - ys[cell["r0"]], cell, k)

    y = ys[-1]
    for row in sec["rows"]:
        jami = row["is_total"]
        h = jami_h if jami else row_h
        data = {m.key: cd for m, cd in zip(sec["metrics"], row["cells"])}
        last_metric = None
        ci = 0
        while ci < len(spec):
            item = spec[ci]
            cs = st["cols"][ci]["jami" if jami else "body"]
            body = st["cols"][ci]["body"]
            span_to = ci
            if item not in (None, "district", "D") and ci + 1 < len(spec) and spec[ci + 1] is None:
                span_to = ci + 1
            w = xs[span_to + 1] - xs[ci]
            fill = cs["fill"] if jami else body["fill"]
            if jami and fill is None and ci:
                fill = st["cols"][ci - 1]["jami"]["fill"]
            c.setStrokeColor(black)
            c.setLineWidth(0.4)
            if fill:
                c.setFillColor(HexColor("#" + fill))
            c.rect(xs[ci], PAGE_H - y - h, w, h, fill=1 if fill else 0, stroke=1)
            size = cs["sz"] / k
            align = body["align"] or "right"
            align = "right" if align == "center" and item != "district" else align
            if item == "district":
                _draw_cell_text(c, row["district"].name, xs[ci], y, w, h, (cs["sz"] if jami else 10) / k, jami, "000000",
                                "center" if jami else "left")
            elif item == "D":
                src = data.get(last_metric)
                d = src["diff"] if src else None
                if d is not None and float(d) != 0:
                    up, dn = (body["up"] or "FF0000"), (body["dn"] or "0000FF")
                    n = int(d)
                    _draw_cell_text(c, ("↑ " if n > 0 else "↓ ") + _spaced(abs(n)), xs[ci], y, w, h,
                                    (cs["sz"] if not jami else body["sz"]) / k, True, up if n > 0 else dn, "right")
            elif item is not None:
                pct = item.startswith("%")
                name = item[1:] if pct else item
                src = data.get(name)
                v = src["b"] if src else None
                text = _fmt_pct(v, jami, key) if pct else _fmt_count(v, jami)
                _draw_cell_text(c, text, xs[ci], y, w, h, size, cs["b"], cs["color"], align)
                if not pct:
                    last_metric = name
            ci = span_to + 1
        y += h


def compare_pdf(comp: dict, user=None) -> bytes:
    styles = _styles()
    a, b = comp["a"], comp["b"]
    if not all(s["section"].key in styles and s["section"].key in HETK_SPECS for s in comp["sections"]):
        return _generic_pdf(comp, user)
    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    pdf._setup()
    c.setTitle(f"Dinamika {a:%d.%m.%Y} - {b:%d.%m.%Y}")
    for sec in comp["sections"]:
        _section_page(c, sec, styles[sec["section"].key], a, b)
        c.showPage()
    c.save()
    return buf.getvalue()


def _generic_pdf(comp: dict, user=None) -> bytes:
    a, b = comp["a"], comp["b"]
    da, db = f"{a:%d.%m}", f"{b:%d.%m}"
    blocks = []
    for sec in comp["sections"]:
        metrics = sec["metrics"]
        h1 = [("Hudud", 1, 3)] + [(g["name"] or "—", 3 * len(g["metrics"]), 1) for g in sec["groups"]]
        h2 = [(m.label, 3, 1) for m in metrics]
        h3 = [x for _ in metrics for x in ((da, 1, 1), (db, 1, 1), ("Farq", 1, 1))]
        rows, total_n = [], 0
        for row in sec["rows"]:
            line = [row["district"].name]
            for cd in row["cells"]:
                line += [fmt_metric(cd["a"], cd["kind"]), fmt_metric(cd["b"], cd["kind"]), fmt_delta(cd["diff"], cd["kind"])]
            if row["is_total"]:
                rows.insert(total_n, line)
                total_n += 1
            else:
                rows.append(line)
        blocks.append({"heading": sec["section"].title, "head": [h1, h2, h3], "rows": rows, "total_rows": total_n,
                       "sep_cols": [1 + 3 * i for i in range(len(metrics))], "first_w": 92})
    return pdf.make_pdf("Qashqadaryo HETK — kunlik dinamika", blocks, big=True,
                        meta_left=f"Solishtirish: {a:%d.%m.%Y} → {b:%d.%m.%Y}", footer="Qashqadaryo HETK · dinamika")
