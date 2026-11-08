"""Barcha shablonlar uchun umumiy kontekst: yig‘iladigan bo‘limlarga ajratilgan menyu va ruxsatlar.

Yangi bo‘lim qo‘shish: NAV_GROUPS ga yangi guruh qo‘shing. Element:
(url_name, nomi, ikonka, ruxsat, qo‘shimcha_faol_url_nomlari)
NAV_TOP — bo‘limsiz, doim ko‘rinadigan yuqori elementlar (Bosh sahifa).
"""

NAV_TOP = (
    ("dashboard", "Bosh sahifa", "home", None, ()),
)

NAV_GROUPS = (
    ("Dinamika", (
        ("dynamics", "Umumiy ko‘rinish", "chart", "reports.view_dashboard", ()),
        ("compare", "Solishtirish", "compare", "reports.view_dashboard", ()),
        ("trend", "Trend", "trend", "reports.view_dashboard", ()),
        ("upload", "PDF yuklash", "upload", "reports.upload_pdf", ("upload_confirm",)),
        ("archive", "Arxiv", "archive", "reports.view_dashboard", ()),
    )),
    ("Offline monitoring", (
        ("mon_dashboard", "Umumiy svod", "pulse", "reports.view_monitoring", ("mon_snapshot", "mon_dcu", "mon_meters")),
        ("mon_compare", "Solishtirish", "compare", "reports.view_monitoring", ()),
        ("mon_upload", "Fayl yuklash", "upload", "reports.upload_monitoring", ("mon_upload_confirm",)),
        ("mon_archive", "Arxiv", "archive", "reports.view_monitoring", ("mon_send",)),
        ("mon_settings", "Monitoring sozlamalari", "sliders", "reports.manage_monitoring", ()),
    )),
    ("Customer", (
        ("customer_data", "Qabul qilingan ma’lumotlar", "table", "reports.view_customers", ()),
        ("customer_stats", "Statistika", "chart", "reports.view_customer_stats", ()),
        ("customer_journal", "Jurnal", "audit", "reports.view_customers", ()),
        ("customer_apps", "Bot foydalanuvchilari", "users", "reports.approve_customers", ()),
    )),
    ("Location", (
        ("location_data", "Ma’lumotlar", "table", "reports.view_location", ("location_detail",)),
        ("location_map", "Xarita", "pin", "reports.view_location", ()),
        ("location_upload", "Fayl yuklash", "upload", "reports.upload_location", ()),
        ("location_uploads", "Yuklash jurnali", "audit", "reports.view_location", ()),
        ("location_searches", "Qidiruv jurnali", "eye", "reports.view_location_log", ()),
    )),
    ("Postlar", (
        ("post_new", "Yangi post", "megaphone", "reports.send_posts", ()),
        ("posts", "Yuborilgan postlar", "send", "reports.view_posts", ("post_detail",)),
    )),
    ("Tizim", (
        ("system_status", "Tizim holati", "pulse", "reports.view_status", ()),
        ("site_settings", "Sozlamalar", "sliders", "reports.manage_settings", ()),
        ("users", "Foydalanuvchilar", "users", "reports.manage_users", ()),
        ("roles", "Rollar va ruxsatlar", "shield", "reports.manage_roles", ("role_new", "role_edit")),
        ("audit", "Audit jurnali", "audit", "reports.view_audit", ()),
    )),
)


def _asset_version() -> str:
    """CSS/JS o‘zgarganda brauzer eski nusxani keshdan olmasligi uchun: fayl o‘zgargan vaqti."""
    from django.conf import settings

    latest = 0
    for sub in ("js/app.js", "css/app.css"):
        for base in getattr(settings, "STATICFILES_DIRS", []):
            try:
                latest = max(latest, int((base / sub).stat().st_mtime))
            except OSError:
                pass
    return str(latest)


def _is_active(current: str, name: str, extra) -> bool:
    return current == name or current in extra or current.startswith(name + "_")


def app_context(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"ASSET_V": _asset_version()}
    match = getattr(request, "resolver_match", None)
    current = match.url_name if match else ""
    top = [{"url": name, "label": label, "icon": icon, "group": "", "active": current == name}
           for name, label, icon, perm, extra in NAV_TOP if perm is None or user.has_perm(perm)]
    groups, flat = [], list(top)
    for title, entries in NAV_GROUPS:
        items = [
            {"url": name, "label": label, "icon": icon, "group": title, "active": _is_active(current, name, extra)}
            for name, label, icon, perm, extra in entries
            if user.has_perm(perm)
        ]
        if items:
            groups.append({"title": title, "items": items, "has_active": any(i["active"] for i in items)})
            flat += items
    return {"nav_top": top, "nav_groups": groups, "nav_items": flat, "APP_NAME": "Dinamika", "ASSET_V": _asset_version()}
