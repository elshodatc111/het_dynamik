from django.conf import settings
from django.db import models


class District(models.Model):
    key = models.SlugField(max_length=40, unique=True)
    name = models.CharField("Nomi", max_length=80)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_total = models.BooleanField("Jami qatori", default=False)

    class Meta:
        ordering = ["sort_order"]
        verbose_name = "Hudud"
        verbose_name_plural = "Hududlar"

    def __str__(self):
        return self.name


class Section(models.Model):
    key = models.SlugField(max_length=40, unique=True)
    title = models.CharField(max_length=200)
    short = models.CharField(max_length=80)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]
        verbose_name = "Bo‘lim"
        verbose_name_plural = "Bo‘limlar"

    def __str__(self):
        return self.short


class Metric(models.Model):
    KIND = (("count", "Son"), ("pct", "Foiz"))
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="metrics")
    key = models.SlugField(max_length=40)
    label = models.CharField(max_length=200)
    group = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=10, choices=KIND, default="count")
    additive = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["section__sort_order", "sort_order"]
        constraints = [models.UniqueConstraint(fields=["section", "key"], name="uniq_metric_section_key")]
        verbose_name = "Ko‘rsatkich"
        verbose_name_plural = "Ko‘rsatkichlar"

    def __str__(self):
        return f"{self.section.short}: {self.label}"


class Upload(models.Model):
    """Bitta kunlik PDF. Har bir hisobot sanasi uchun faqat bitta yuklash bo‘ladi."""

    report_date = models.DateField("Hisobot sanasi", unique=True)
    prev_date = models.DateField("Oldingi sana")
    variant = models.CharField("Ko‘rinish", max_length=20, blank=True)
    pages = models.PositiveSmallIntegerField(default=0)
    original_name = models.CharField(max_length=255)
    file = models.FileField(upload_to="pdfs/%Y/%m/")
    file_hash = models.CharField(max_length=64, db_index=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    warnings = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-report_date"]
        default_permissions = ()
        permissions = [
            ("view_dashboard", "Bosh sahifa, solishtirish va trendni ko‘rish"),
            ("export_excel", "Excel va asl PDF ni yuklab olish"),
            ("upload_pdf", "PDF yuklash"),
            ("manage_uploads", "Yuklangan sanani almashtirish yoki o‘chirish"),
            ("manage_users", "Foydalanuvchi va rollarni boshqarish"),
            ("view_audit", "Audit jurnalini ko‘rish"),
        ]
        verbose_name = "Yuklangan hisobot"
        verbose_name_plural = "Yuklangan hisobotlar"

    def __str__(self):
        return f"{self.report_date:%d.%m.%Y}"


class MetricValue(models.Model):
    upload = models.ForeignKey(Upload, on_delete=models.CASCADE, related_name="values")
    report_date = models.DateField(db_index=True)
    district = models.ForeignKey(District, on_delete=models.PROTECT, related_name="+")
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="+")
    value = models.DecimalField(max_digits=20, decimal_places=4)
    # True: qiymat o‘sha sananing o‘z PDF idan emas, keyingi kun PDF idagi «oldingi holat» ustunidan olingan
    is_prev = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["report_date", "district", "metric"], name="uniq_value_date_district_metric")
        ]
        indexes = [models.Index(fields=["metric", "district", "report_date"], name="idx_value_trend")]
        default_permissions = ()


class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=40, db_index=True)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        default_permissions = ()
        verbose_name = "Audit yozuvi"
        verbose_name_plural = "Audit jurnali"
