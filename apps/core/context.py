"""Barcha shablonlar uchun umumiy kontekst: menyu va ruxsatlar."""

NAV = (
    ("dashboard", "Bosh sahifa", "home", "reports.view_dashboard"),
    ("compare", "Solishtirish", "compare", "reports.view_dashboard"),
    ("trend", "Trend", "trend", "reports.view_dashboard"),
    ("upload", "PDF yuklash", "upload", "reports.upload_pdf"),
    ("archive", "Arxiv", "archive", "reports.view_dashboard"),
    ("users", "Foydalanuvchilar", "users", "reports.manage_users"),
    ("audit", "Audit jurnali", "audit", "reports.view_audit"),
)


def app_context(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    match = getattr(request, "resolver_match", None)
    current = match.url_name if match else ""
    items = [
        {"url": name, "label": label, "icon": icon, "active": current == name or current.startswith(name + "_")}
        for name, label, icon, perm in NAV
        if user.has_perm(perm)
    ]
    return {"nav_items": items, "APP_NAME": "Dinamika"}
