from decimal import Decimal

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


def _fmt(v, decimals=0):
    if v is None:
        return "—"
    v = Decimal(v)
    s = f"{v:,.{decimals}f}".replace(",", " ")
    return s


@register.filter
def num(value, decimals=0):
    """12345 -> '12 345' (ingichka bo‘sh joy bilan)."""
    return _fmt(value, int(decimals))


@register.filter
def signed(value):
    """+12 / −5 / 0 (farq jadvallari uchun)."""
    if value is None:
        return "—"
    v = int(value)
    if v == 0:
        return "0"
    return ("+" if v > 0 else "−") + _fmt(abs(v), 0)


@register.filter
def darrow(value):
    """Farq: ↑ 12 / ↓ 5 (minus belgisiz)."""
    if value is None:
        return "—"
    v = int(value)
    return "–" if v == 0 else (f"↑ {_fmt(v, 0)}" if v > 0 else f"↓ {_fmt(-v, 0)}")


@register.filter
def pct(value):
    return "—" if value is None else _fmt(value, 1) + "%"


@register.filter
def delta(value, decimals=0):
    """O‘zgarish: ↑ 12 / ↓ -5 / – (rangsiz, faqat belgi)."""
    if value is None:
        return "—"
    v = Decimal(value)
    d = int(decimals)
    if v == 0:
        return "–"
    arrow = "↑" if v > 0 else "↓"
    return f"{arrow} {_fmt(v, d)}"


@register.filter
def fmt_metric(value, kind):
    return pct(value) if kind == "pct" else num(value)


@register.filter
def fmt_delta(value, kind):
    return delta(value, 1 if kind == "pct" else 0)


@register.filter
def getitem(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.simple_tag
def icon(name, size=18):
    paths = {
        "home": '<path d="M3 11.5 12 4l9 7.5"/><path d="M5 10v9a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1v-9"/>',
        "compare": '<path d="M8 3 4 7l4 4"/><path d="M4 7h16"/><path d="m16 21 4-4-4-4"/><path d="M20 17H4"/>',
        "trend": '<path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/>',
        "upload": '<path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M5 20h14"/>',
        "archive": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9"/><path d="M10 13h4"/>',
        "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8"/><path d="M18 14.2a6.5 6.5 0 0 1 3.5 5.8"/>',
        "audit": '<path d="M9 4h6l4 4v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"/><path d="M14 4v4h4"/><path d="M9 13h6M9 17h4"/>',
        "download": '<path d="M12 4v12"/><path d="m7 11 5 5 5-5"/><path d="M5 20h14"/>',
        "file": '<path d="M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v5h5"/>',
        "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
        "alert": '<path d="M12 4 2.5 20h19z"/><path d="M12 10v5"/><path d="M12 18h.01"/>',
        "x": '<path d="M6 6l12 12M18 6 6 18"/>',
        "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4"/>',
        "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>',
        "logout": '<path d="M14 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4"/><path d="M10 8l-4 4 4 4"/><path d="M6 12h10"/>',
        "trash": '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
        "plus": '<path d="M12 5v14M5 12h14"/>',
        "key": '<circle cx="8" cy="15" r="4"/><path d="m11 12 8-8"/><path d="m16 7 3 3"/>',
        "pulse": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
        "sliders": '<path d="M4 7h9"/><path d="M17 7h3"/><circle cx="15" cy="7" r="2"/><path d="M4 17h3"/><path d="M11 17h9"/><circle cx="9" cy="17" r="2"/>',
        "send": '<path d="M21 3 10 14"/><path d="M21 3l-7 18-4-7-7-4z"/>',
        "table": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16"/>',
        "chevron": '<path d="m6 9 6 6 6-6"/>',
        "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
        "pin": '<path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.800 12 21 12 21z"/><circle cx="12" cy="9.500" r="2.500"/>',
        "megaphone": '<path d="M3 11v3a1 1 0 0 0 1 1h2l8 4V6L6 10H4a1 1 0 0 0-1 1z"/><path d="M18 9a4 4 0 0 1 0 6"/>',
        "bell": '<path d="M6 17V11a6 6 0 0 1 12 0v6l2 2H4z"/><path d="M10 21h4"/>',
        "install": '<rect x="5" y="2.500" width="14" height="19" rx="3"/><path d="M12 7v7"/><path d="m9 11.500 3 3 3-3"/><path d="M11 18.500h2"/>',
        "shield": '<path d="M12 3 4.500 6v5.500c0 4.500 3 8 7.500 9.500 4.500-1.500 7.500-5 7.500-9.500V6z"/><path d="m9 12 2 2 4-4"/>',
        "eye": '<path d="M2 12s3.500-7 10-7 10 7 10 7-3.500 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
        "cal": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    }
    body = paths.get(name, "")
    return mark_safe(
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{body}</svg>'
    )
