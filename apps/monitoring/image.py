"""Umumiy svodni rasm (PNG) ko‘rinishida chizish — Telegramga yuborish uchun.

Jadval Excel shabloniga mos: ESP × (offline konsentratorlar, aloqasiz hisoblagichlar davrlar va guruhlar bo‘yicha).
Pillow bilan chiziladi (brauzer yoki tashqi dastur kerak emas). Shrift: Inter (apps/monitoring/fonts)."""
from __future__ import annotations

import io
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import services
from .models import Snapshot

FONT_DIR = Path(__file__).parent / "fonts"
S = 2  # sifat uchun masshtab

WHITE, INK, MUTED, FAINT = (255, 255, 255), (20, 28, 44), (96, 108, 128), (176, 184, 198)
HEAD, HEAD_DARK, TOTAL = (220, 230, 241), (196, 211, 232), (255, 230, 153)
LINE, LINE_STRONG, ZEBRA = (190, 199, 213), (110, 124, 148), (247, 249, 252)
RED, BLUE = (225, 29, 46), (27, 44, 199)
BUCKET_TINT = [(240, 246, 255), (255, 250, 238), (255, 241, 240), (248, 238, 244)]


@lru_cache(maxsize=32)
def _font(weight: str, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / f"Inter-{weight}.otf"), round(size * S))


class _Canvas:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.im = Image.new("RGB", (w * S, h * S), WHITE)
        self.d = ImageDraw.Draw(self.im)

    def rect(self, x, y, w, h, fill=None, outline=None, width=1):
        self.d.rectangle([x * S, y * S, (x + w) * S - 1, (y + h) * S - 1], fill=fill, outline=outline, width=width * S if outline else 0)

    def line(self, x1, y1, x2, y2, fill=LINE, width=1):
        self.d.line([x1 * S, y1 * S, x2 * S, y2 * S], fill=fill, width=max(1, round(width * S)))

    def text(self, x, y, s, weight="Regular", size=13, fill=INK, anchor="l"):
        self.d.text((x * S, y * S), str(s), font=_font(weight, size), fill=fill, anchor={"l": "lm", "m": "mm", "r": "rm"}[anchor])

    def png(self) -> bytes:
        buf = io.BytesIO()
        self.im.save(buf, "PNG", optimize=True)
        return buf.getvalue()


def svod_png(snap: Snapshot) -> bytes:
    """Diskdagi kesh orqali: ma’lumot o‘zgarmasa rasm qayta chizilmaydi (serverga kam yuk)."""
    import hashlib
    import json

    from django.conf import settings

    g = services.grid_for(snap)
    key = hashlib.sha1(json.dumps([g["cols"], g["rows"], g["total"], services.REGION_NAME, str(snap.report_date)],
                                 sort_keys=True, default=str).encode()).hexdigest()[:16]
    d = Path(settings.MEDIA_ROOT) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"svod_{snap.pk}_{key}.png"
    if f.exists():
        return f.read_bytes()
    data = _draw_svod(snap, g)
    for old in d.glob(f"svod_{snap.pk}_*.png"):
        old.unlink(missing_ok=True)
    f.write_bytes(data)
    return data


def _draw_svod(snap: Snapshot, g: dict) -> bytes:
    f = services.fmt_int
    cols, rows, total = g["cols"], g["rows"], g["total"]
    ng, nd, nm = len(g["gkeys"]), len(g["d_labels"]), len(g["m_labels"])

    esp_w, cw, margin = 214, 64, 28
    W = margin * 2 + esp_w + cw * len(cols)
    head_h = (38, 30, 28)
    row_h = 30
    top = 150
    H = top + sum(head_h) + row_h * (len(rows) + 1) + 70
    c = _Canvas(W, H)

    # ---- sarlavha
    c.text(W / 2, 40, "Qashqadaryo HETK hududida CAS dasturida offline konsentratorlar va aloqaga chiqmay qolgan hisoblagichlar "
                      "holati bo‘yicha", "Bold", 20, INK, "m")
    c.text(W / 2, 78, "MA’LUMOT", "Bold", 22, RED, "m")
    c.text(margin, 118, "Hudud nomi:", "Regular", 14, INK)
    c.rect(margin + 96, 104, 170, 28, outline=(60, 100, 220))
    c.text(margin + 181, 118, services.REGION_NAME, "SemiBold", 14, (35, 85, 215), "m")
    c.text(W - margin, 118, f"Ma’lumot: {snap.report_date:%d.%m.%Y}", "Bold", 15, BLUE, "r")

    # ---- ustun koordinatalari
    x0 = margin
    xs = [x0 + esp_w + i * cw for i in range(len(cols) + 1)]
    ys = [top, top + head_h[0], top + head_h[0] + head_h[1], top + sum(head_h)]
    y_end = ys[3] + row_h * (len(rows) + 1)

    def block(xa, xb, ya, yb, label, fill=HEAD, weight="Bold", size=13, color=INK):
        c.rect(xa, ya, xb - xa, yb - ya, fill=fill)
        c.text((xa + xb) / 2, (ya + yb) / 2, label, weight, size, color, "m")

    # ---- sarlavha bloklari
    block(x0, x0 + esp_w, ys[0], ys[3], "ESP", size=15)
    idx = 0
    if g["has_dcu"]:
        block(xs[idx], xs[idx + 1 + nd], ys[0], ys[1], "Offline konsentratorlar", size=14)
        for k, lb in enumerate(["JAMI"] + g["d_labels"]):
            block(xs[idx + k], xs[idx + k + 1], ys[1], ys[3], lb, size=12.5)
        idx += 1 + nd
    if g["has_meters"]:
        span = 1 + nm * (1 + ng)
        block(xs[idx], xs[idx + span], ys[0], ys[1], "Aloqadan chiqib ketgan hisoblagichlar", size=14)
        block(xs[idx], xs[idx + 1], ys[1], ys[3], "JAMI", size=12.5)
        k = idx + 1
        for bi, lb in enumerate(g["m_labels"]):
            tint = BUCKET_TINT[min(bi, 3)]
            block(xs[k], xs[k + 1 + ng], ys[1], ys[2], lb, size=13)
            block(xs[k], xs[k + 1], ys[2], ys[3], "JAMI", size=11.5, color=MUTED, weight="SemiBold")
            for gi, name in enumerate(g["gnames_list"]):
                block(xs[k + 1 + gi], xs[k + 2 + gi], ys[2], ys[3], name, size=11.5, color=MUTED, weight="SemiBold")
            k += 1 + ng

    # ---- qatorlar
    def draw_row(y, label, vals, kind):
        is_total = kind == "total"
        if is_total:
            c.rect(x0, y, xs[-1] - x0, row_h, fill=TOTAL)
        elif kind == "zebra":
            c.rect(x0, y, xs[-1] - x0, row_h, fill=ZEBRA)
        c.text(x0 + (esp_w / 2 if is_total else 12), y + row_h / 2, label, "Bold" if is_total else "SemiBold", 14, INK, "m" if is_total else "l")
        for i, v in enumerate(vals):
            xr = xs[i + 1] - 9
            if v is None:
                c.text(xr, y + row_h / 2, "—", "Regular", 13.5, FAINT, "r")
            elif v == 0 and not is_total:
                c.text(xr, y + row_h / 2, "0", "Regular", 13.5, FAINT, "r")
            else:
                bold = is_total or cols[i]["g"] is None and cols[i]["sec"] == "m" and cols[i]["b"] is None
                c.text(xr, y + row_h / 2, f(v), "Bold" if is_total else ("SemiBold" if bold else "Regular"), 14, INK, "r")

    y = ys[3]
    draw_row(y, "JAMI", total["vals"], "total")
    for n, r in enumerate(rows):
        y += row_h
        draw_row(y, r["esp"], r["vals"], "zebra" if n % 2 else "plain")

    # ---- to‘r chiziqlari
    n = len(cols)
    c.line(x0, ys[0], x0, y_end, LINE_STRONG, 1.4)
    c.line(x0 + esp_w, ys[0], x0 + esp_w, y_end, LINE_STRONG, 1.4)
    for j in range(1, n + 1):
        x = xs[j]
        if j == n or cols[j]["sec"] != cols[j - 1]["sec"]:
            y_start, strong = ys[0], True          # bo‘limlar chegarasi
        elif cols[j]["sec"] == "dcu":
            y_start, strong = ys[1], False         # konsentrator davrlari
        elif cols[j]["sep"]:
            y_start, strong = ys[1], True          # hisoblagich davrlari chegarasi
        else:
            y_start, strong = ys[2], False         # guruhlar
        c.line(x, y_start, x, y_end, LINE_STRONG if strong else LINE, 1.4 if strong else 0.7)
    for yy in (ys[0], ys[3], y_end):
        c.line(x0, yy, xs[-1], yy, LINE_STRONG, 1.4)
    c.line(x0 + esp_w, ys[1], xs[-1], ys[1], LINE_STRONG, 0.9)
    first_bucket = next((k for k, col in enumerate(cols) if col["sec"] == "m" and col["b"] is not None), None)
    if first_bucket is not None:
        c.line(xs[first_bucket], ys[2], xs[-1], ys[2], LINE_STRONG, 0.9)
    for r_i in range(len(rows)):
        yy = ys[3] + row_h * (r_i + 2)
        c.line(x0, yy - row_h + row_h, xs[-1], yy - row_h + row_h, LINE, 0.7)
    c.line(x0, ys[3] + row_h, xs[-1], ys[3] + row_h, LINE_STRONG, 1.0)

    # ---- pastki yozuv
    fy = y_end + 28
    notes = []
    if g["has_dcu"]:
        notes.append(f"Konsentratorlar: {g['d_min']} kundan kam offline hisobga olinmagan")
    if g["has_meters"]:
        notes.append(f"hisoblagichlar: {g['m_min']} kundan kam aloqasizlar hisobga olinmagan")
    c.text(margin, fy, " · ".join(notes), "Regular", 12, MUTED)
    c.text(W - margin, fy, f"Yaratildi: {datetime.now():%d.%m.%Y %H:%M}", "Regular", 12, MUTED, "r")
    return c.png()
