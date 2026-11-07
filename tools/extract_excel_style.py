"""Namunaviy HETK PDF dan Excel uchun dizayn shablonini (ranglar, shriftlar, birlashgan kataklar, ustun
kengliklari) o‘qib `apps/reports/excel_styles.json` ga yozadi.

Ishlatish:  python tools/extract_excel_style.py tests/fixtures/hisobot_2026-11-01_06_hetk.pdf
Excel yaratishda PDF o‘qilmaydi — faqat shu JSON ishlatiladi.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apps.reports.parser import layouts  # noqa: E402
from apps.reports.parser.translit import to_latin  # noqa: E402

PAGES = {  # bo‘lim -> sahifa raqami (HETK 9 betli hisobotida)
    "konsentratorlar": 1, "aloqasiz": 2, "uzilgan": 3, "balans_yuklama": 4,
    "obxod48": 6, "tafovut_balans": 7, "billing_tafovut": 8,
}
BODY_PT = 10.0


def hexcol(c):
    if c is None:
        return "000000"
    if isinstance(c, (int, float)):
        c = (c,)
    if not all(isinstance(x, (int, float)) for x in c):   # naqsh/gradient (masalan, ustunli diagramma)
        return None
    if len(c) == 1:
        c = (c[0],) * 3
    if len(c) == 4:  # CMYK
        k = c[3]
        c = tuple((1 - x) * (1 - k) for x in c[:3])
    return "".join(f"{round(max(0, min(1, x)) * 255):02X}" for x in c[:3])


def center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def inside(pt, b, pad=0.0):
    return b[0] - pad <= pt[0] <= b[2] + pad and b[1] - pad <= pt[1] <= b[3] + pad


class Page:
    def __init__(self, pg):
        self.pg = pg
        self.fills = []
        for r in pg.rects:
            if r.get("fill") and r["non_stroking_color"] is not None:
                col = hexcol(r["non_stroking_color"])
                if col is None or col == "FFFFFF":
                    continue
                w, h = r["x1"] - r["x0"], r["bottom"] - r["top"]
                if w < 3 or h < 3:       # chiziq (hoshiya) — to‘ldirish emas
                    continue
                self.fills.append(((r["x0"], r["top"], r["x1"], r["bottom"]), col, w * h))

    def fill_at(self, b):
        c = center(b)
        best = None
        for bb, col, area in self.fills:
            if inside(c, bb) and (best is None or area < best[1]):
                best = (col, area)
        return best[0] if best else None

    def chars_in(self, b, pad=0.6):
        out = []
        for ch in self.pg.chars:
            c = ((ch["x0"] + ch["x1"]) / 2, (ch["top"] + ch["bottom"]) / 2)
            if inside(c, b, pad):
                out.append(ch)
        return out


def is_rot(ch):
    return abs(ch["matrix"][1]) > 0.5      # pdfplumber «upright» belgisi ishonchsiz — matritsaga qaraymiz


def real_size(ch):
    return (ch["x1"] - ch["x0"]) if is_rot(ch) else ch["size"]


def style_of(ch):
    name = ch["fontname"]
    return (hexcol(ch["non_stroking_color"]), "Bold" in name, "Italic" in name, round(real_size(ch), 1))


def runs_from(chars, k):
    """Belgilarni o‘qish tartibida (satrlar bo‘yicha) bir xil uslubli bo‘laklarga ajratadi."""
    if not chars:
        return [], False
    if not any(c["text"].strip() for c in chars):
        return [], False
    rotated = any(is_rot(c) for c in chars)
    if rotated:
        seq = [c for c in chars if is_rot(c)]   # PDF oqimi tartibi = o‘qish tartibi
    else:
        lines = []
        for ch in sorted(chars, key=lambda c: (round(c["top"]), c["x0"])):
            if lines and abs(lines[-1][0] - ch["top"]) < max(2.5, ch["size"] * 0.5):
                lines[-1][1].append(ch)
            else:
                lines.append([ch["top"], [ch]])
        seq = []
        for i, (_, ln) in enumerate(lines):
            ln.sort(key=lambda c: c["x0"])
            while ln and ln[0]["text"].isspace():
                ln.pop(0)
            if i:
                seq.append(None)         # satr almashishi -> bo‘sh joy
            seq.extend(ln)
    runs, prev = [], None
    for ch in seq:
        if ch is None:
            if runs:
                runs[-1]["t"] += " "
            prev = None
            continue
        st = style_of(ch)
        txt = ch["text"]
        if rotated and prev is not None and abs(ch["x0"] - prev["x0"]) > 2.0 and runs \
                and not runs[-1]["t"].endswith(" "):
            runs[-1]["t"] += " "            # vertikal matnda yangi satr
        if txt.isspace():
            if runs and not runs[-1]["t"].endswith(" "):
                runs[-1]["t"] += " "
            prev = ch
            continue
        if prev is not None and not rotated and not prev["text"].isspace() \
                and ch["x0"] - prev["x1"] > ch["size"] * 0.22:
            if runs and not runs[-1]["t"].endswith(" "):
                runs[-1]["t"] += " "
        if runs and runs[-1]["_st"] == st:
            runs[-1]["t"] += txt
        else:
            runs.append({"t": txt, "_st": st})
        prev = ch
    out = []
    for r in runs:
        color, b, i, sz = r["_st"]
        out.append({"t": _fix(to_latin(r["t"])), "color": color, "b": b, "i": i, "sz": round(sz * k, 1)})
    return out, rotated


def _fix(t):
    t = re.sub(r"(?<=[A-Za-z])- (?=[A-Za-z])", "-", t)
    return t.replace("ontsentrator", "onsentrator").replace("Kontsentrator", "Konsentrator").replace("  ", " ")


def mode(vals, default=None):
    vals = [v for v in vals if v is not None]
    return Counter(vals).most_common(1)[0][0] if vals else default


def align_of(chars, b):
    if not chars:
        return None
    chars = [c for c in chars if c["text"].strip()]
    if not chars:
        return None
    x0, x1 = min(c["x0"] for c in chars), max(c["x1"] for c in chars)
    left, right = x0 - b[0], b[2] - x1
    if abs(left - right) < max(3.0, (b[2] - b[0]) * 0.12):
        return "center"
    return "left" if left < right else "right"


def snap(vals, tol=1.0):
    vals = sorted(vals)
    out = []
    for v in vals:
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def idx(edges, v, tol=1.5):
    return min(range(len(edges)), key=lambda i: abs(edges[i] - v))


def split_by_gap(chars, c0, c1, xs0, xs1):
    """Bitta katakda ikki alohida matn (katta oraliq bilan) bo‘lsa — ikki katakka ajratadi."""
    vis = sorted([c for c in chars if c["text"].strip()], key=lambda c: c["x0"])
    if c1 == c0 or len(vis) < 2 or any(is_rot(c) for c in vis):
        return [(c0, c1, chars)]
    size = mode([round(c["size"], 1) for c in vis], 9.0)
    cut = None
    for a, b in zip(vis, vis[1:]):
        if b["x0"] - a["x1"] > size * 2.5:
            cut = (a["x1"] + b["x0"]) / 2
            break
    if cut is None:
        return [(c0, c1, chars)]
    m = min(range(c0, c1), key=lambda i: abs(xs1[i] - cut))
    left = [c for c in chars if (c["x0"] + c["x1"]) / 2 < cut]
    right = [c for c in chars if (c["x0"] + c["x1"]) / 2 >= cut]
    return [(c0, m, left), (m + 1, c1, right)]


def merge_stacks(header):
    """Bir xil ustun oralig‘idagi ketma-ket (tepa-past) kataklar PDF da bitta birlashgan katak bo‘lgan."""
    header = sorted(header, key=lambda h: (h["c0"], h["c1"], h["r0"]))
    out = []
    for h in header:
        prev = out[-1] if out else None
        low = "".join(r["t"] for r in h["runs"]).strip()
        if prev and (prev["c0"], prev["c1"]) == (h["c0"], h["c1"]) and prev["r1"] + 1 == h["r0"] \
                and low not in ("soni", "%", "↑ ↓"):      # pastki «soni / % / ↑↓» yorliqlari alohida katak
            runs = list(prev["runs"])
            if runs and h["runs"] and not runs[-1]["t"].endswith(" "):
                runs[-1] = dict(runs[-1], t=runs[-1]["t"] + " ")
            prev["runs"] = runs + list(h["runs"])
            prev["r1"] = h["r1"]
            prev["fill"] = prev["fill"] or h["fill"]
            prev["rot"] = prev["rot"] or h["rot"]
        else:
            out.append(dict(h))
    return out


def extract_page(pg, spec):
    P = Page(pg)
    # katak chegaralari faqat qora chiziqlardan olinadi (rang to‘ldirish chekkalari birlashgan kataklarni bo‘lib yuboradi)
    only_borders = pg.filter(lambda o: o["object_type"] != "rect" or hexcol(o.get("non_stroking_color")) == "000000")
    tb = max(only_borders.find_tables(), key=lambda t: len(t.cells))     # sarlavha birlashmalari uchun
    t = max(pg.find_tables(), key=lambda t: len(t.cells))                 # ustun/qator to‘ri uchun
    total_cols = len(t.rows[0].cells)
    off = total_cols - len(spec)           # ba’zi sahifalarda chapda bo‘sh qo‘shimcha ustun bor
    assert off >= 0, (total_cols, len(spec))
    ncols = len(spec)
    rows = t.rows
    # «Жами» qatori
    jr = next(i for i, r in enumerate(rows)
              if r.cells[off] and "Жами" in (pg.crop(r.cells[off]).extract_text() or ""))
    body_rows = [r for r in rows[jr + 1:] if r.cells[off]]
    full = next((r.cells for r in body_rows if all(r.cells[off:])), None)
    if full is None:                         # birlashgan kataklar: ustun chegaralarini barcha qatorlardan yig‘amiz
        e0 = snap([c[0] for r in rows[jr:] for c in r.cells[off:] if c])
        e1 = snap([c[2] for r in rows[jr:] for c in r.cells[off:] if c])
        assert len(e0) == ncols and len(e1) == ncols, (len(e0), len(e1), ncols)
        xs0, xs1 = e0, e1
    else:
        xs0 = [c[0] for c in full[off:]]
        xs1 = [c[2] for c in full[off:]]
    jtop = rows[jr].cells[off][1]

    # shkala: tana qatorining balandligi -> 15 pt
    row_h_pdf = mode([round(r.cells[off][3] - r.cells[off][1], 1) for r in body_rows])
    # tana shrifti
    sizes = []
    for r in body_rows:
        for ch in P.chars_in(r.cells[off + 1] if r.cells[off + 1] else r.cells[off]):
            sizes.append(round(ch["size"], 1))
    k = BODY_PT / mode(sizes, 8.0)

    def pt(v):
        return round(v * k, 1)

    col_w = [round(((x1 - x0) * k * 96 / 72 - 5) / 7, 2) for x0, x1 in zip(xs0, xs1)]

    # ---------------- sarlavha kataklari
    hcells = [c for c in tb.cells if c[3] <= jtop + 0.6 and c[2] > xs0[0] + 1]
    ys = snap([c[1] for c in hcells] + [c[3] for c in hcells])
    header = []
    for c in hcells:
        c0, c1 = idx(xs0, c[0]), idx(xs1, c[2])
        r0, r1 = idx(ys, c[1]), idx(ys, c[3]) - 1
        chars = P.chars_in(c)
        fill = P.fill_at(c)
        c1 = max(c0, c1)
        parts = split_by_gap(chars, c0, c1, xs0, xs1)
        for (pc0, pc1, pchars) in parts:
            runs, rot = runs_from(pchars, k)
            header.append({"r0": r0, "c0": pc0, "r1": max(r0, r1), "c1": pc1, "fill": fill, "runs": runs, "rot": rot})
    header = merge_stacks(header)
    header_h = [pt(ys[i + 1] - ys[i]) for i in range(len(ys) - 1)]

    # ---------------- ustun uslublari (tana va «Jami»)
    def col_style(cell_for, spec_item):
        fills, colors, bolds, sizes_, aligns, ups, dns = [], [], [], [], [], [], []
        for r in cell_for:
            cb = r.cells[ci + off] if r.cells[ci + off] else None
            if cb is None:
                continue
            fills.append(P.fill_at(cb))
            chars = P.chars_in(cb)
            if not chars:
                continue
            aligns.append(align_of(chars, cb))
            for ch in chars:
                if not ch["text"].strip():
                    continue
                if ch["text"] == "↑":
                    ups.append(hexcol(ch["non_stroking_color"]))
                elif ch["text"] == "↓":
                    dns.append(hexcol(ch["non_stroking_color"]))
                elif ch["text"].isdigit() or ch["text"] in "-%,":
                    colors.append(hexcol(ch["non_stroking_color"]))
                    bolds.append("Bold" in ch["fontname"])
                    sizes_.append(round(ch["size"] * k, 1))
        return {
            "fill": mode(fills), "color": mode(colors, "000000"), "b": mode(bolds, False),
            "sz": mode(sizes_, BODY_PT), "align": mode(aligns, "center"),
            "up": mode(ups), "dn": mode(dns),
        }

    cols = []
    for ci in range(ncols):
        body = col_style(body_rows, spec[ci])
        jami = col_style([rows[jr]], spec[ci])
        up = body["up"] or jami["up"]
        dn = body["dn"] or jami["dn"]
        pair = {"FF0000": "0000FF", "0000FF": "FF0000", "00FF00": "FF0000", "00B050": "FF0000"}
        if spec[ci] == "D":
            if up is None and dn is not None:
                up = "0000FF" if dn == "FF0000" else "FF0000"
            if dn is None and up is not None:
                dn = pair.get(up, "0000FF")
            up, dn = up or "FF0000", dn or "0000FF"
        body["up"] = jami["up"] = up
        body["dn"] = jami["dn"] = dn
        cols.append({"body": body, "jami": jami})

    # ---------------- sahifa sarlavhasi (jadvaldan yuqori)
    top_chars = [c for c in pg.chars if c["bottom"] < t.bbox[1] - 1 and c["text"].strip()]
    lines = []
    for ch in sorted(top_chars, key=lambda c: (round(c["top"]), c["x0"])):
        if lines and abs(lines[-1][0] - ch["top"]) < 3:
            lines[-1][1].append(ch)
        else:
            lines.append([ch["top"], [ch]])
    title = []
    for _, ln in lines:
        ln.sort(key=lambda c: c["x0"])
        # bir satrdagi bo‘laklarni katta bo‘shliq bo‘yicha ajratamiz (chap yorliq / quti / o‘ng sana)
        groups, cur = [], [ln[0]]
        for a, b in zip(ln, ln[1:]):
            if b["x0"] - a["x1"] > 25:
                groups.append(cur); cur = [b]
            else:
                cur.append(b)
        groups.append(cur)
        items = []
        for g in groups:
            runs, _ = runs_from(g, k)
            runs = [r for r in runs if r["color"] != "FFFFFF"]
            if not runs:
                continue
            items.append({"x0": g[0]["x0"], "x1": g[-1]["x1"], "runs": runs})
        title.append({"top": ln[0]["top"], "items": items})
    return {
        "ncols": ncols, "col_w": col_w, "header_h": header_h,
        "jami_h": pt(rows[jr].cells[off][3] - rows[jr].cells[off][1]),
        "row_h": pt(row_h_pdf), "header": header, "cols": cols,
        "title": title, "table_x0": t.bbox[0], "table_x1": t.bbox[2], "k": k,
    }


def main(pdf_path):
    out = {}
    with pdfplumber.open(pdf_path) as pdf:
        for key, pno in PAGES.items():
            out[key] = extract_page(pdf.pages[pno - 1], layouts.HETK_SPECS[key])
            print(key, "ok:", out[key]["ncols"], "ustun,", len(out[key]["header"]), "sarlavha katagi")
    dst = ROOT / "apps" / "reports" / "excel_styles.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("yozildi:", dst)


if __name__ == "__main__":
    main(sys.argv[1])
