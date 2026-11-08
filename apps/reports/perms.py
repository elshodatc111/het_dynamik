"""Ruxsatlar katalogi (bo‘limlar bo‘yicha guruhlangan). Modellarni import qilmaydi — models.py shundan foydalanadi."""

PERM_GROUPS = (
    ("Dinamika", (
        ("view_dashboard", "Bosh sahifa, solishtirish va trendni ko‘rish"),
        ("export_excel", "Excel va asl PDF ni yuklab olish"),
        ("upload_pdf", "PDF yuklash"),
        ("manage_uploads", "Yuklangan sanani almashtirish yoki o‘chirish"),
    )),
    ("Offline monitoring", (
        ("view_monitoring", "Offline monitoringni ko‘rish"),
        ("upload_monitoring", "Offline monitoring fayllarini yuklash"),
        ("send_monitoring", "Monitoring hisobotini Telegramga yuborish"),
        ("manage_monitoring", "Monitoring sozlamalari va sanalarni o‘chirish"),
    )),
    ("Customer", (
        ("view_customers", "Customer ma’lumotlari va jurnalini ko‘rish"),
        ("view_customer_stats", "Customer statistikasini ko‘rish"),
        ("manage_customers", "Customer: Excelga yuklab olish va o‘chirish"),
        ("approve_customers", "Bot foydalanuvchilari: tasdiqlash, bekor qilish, bloklash"),
    )),
    ("Location", (
        ("view_location", "Location ma’lumotlari va xaritani ko‘rish"),
        ("upload_location", "Location faylini yuklash"),
        ("export_location", "Location ma’lumotlarini Excelga yuklab olish"),
        ("manage_location", "Location yozuvlarini tahrirlash va o‘chirish"),
        ("view_location_log", "Location qidiruv jurnalini ko‘rish"),
    )),
    ("Postlar", (
        ("view_posts", "Yuborilgan postlar va hisobotlarni ko‘rish"),
        ("send_posts", "Bot orqali post yuborish"),
        ("manage_posts", "Postlarni o‘chirish va eslatma yuborish"),
    )),
    ("Tizim", (
        ("manage_users", "Foydalanuvchilarni boshqarish"),
        ("manage_roles", "Rollar va ruxsatlarni sozlash"),
        ("view_audit", "Audit jurnalini ko‘rish"),
        ("view_status", "Tizim holatini ko‘rish"),
        ("manage_settings", "Umumiy sozlamalar (Telegram bot tokeni)"),
    )),
)

PERMS = {code: label for _g, items in PERM_GROUPS for code, label in items}
