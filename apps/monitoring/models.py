"""Offline monitoring: kunlik holat (snapshot), konsentratorlar, hisoblagichlar, sozlamalar, Telegram jurnali."""
from django.conf import settings
from django.db import models

GROUPS = (("yuridik", "Yuridik"), ("aholi", "Aholi"), ("balans", "Balans"), ("boshqa", "Boshqa"))
GROUP_LABEL = dict(GROUPS)

DEFAULT_KW_YURIDIK = "промышлен, industrial, yuridik, юридич, legal, commercial, коммерч, корпоратив"
DEFAULT_KW_AHOLI = "бытов, household, residential, domestic, aholi, maishiy, населен, физическ"
DEFAULT_KW_BALANS = "баланс, balance"
DEFAULT_OFFLINE = "offline, off-line, офлайн, оффлайн, не в сети"


class Snapshot(models.Model):
    """Bitta hisobot sanasi. Konsentrator va hisoblagich fayllari alohida yuklanishi mumkin."""

    report_date = models.DateField("Hisobot sanasi", unique=True)
    dcu_name = models.CharField(max_length=255, blank=True)
    dcu_uploaded_at = models.DateTimeField(null=True, blank=True)
    dcu_rows = models.PositiveIntegerField("Fayldagi offline konsentratorlar", default=0)
    meter_name = models.CharField(max_length=255, blank=True)
    meter_uploaded_at = models.DateTimeField(null=True, blank=True)
    meter_rows = models.PositiveIntegerField("Fayldagi aloqasiz hisoblagichlar", default=0)
    meter_detail_purged = models.BooleanField("Hisoblagich ro‘yxati tozalangan", default=False)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    warnings = models.JSONField(default=list, blank=True)
    summary = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-report_date"]
        default_permissions = ()
        verbose_name = "Monitoring sanasi"
        verbose_name_plural = "Monitoring sanalari"

    def __str__(self):
        return f"{self.report_date:%d.%m.%Y}"

    @property
    def has_dcu(self):
        return self.dcu_uploaded_at is not None

    @property
    def has_meters(self):
        return self.meter_uploaded_at is not None


class DcuRecord(models.Model):
    snapshot = models.ForeignKey(Snapshot, on_delete=models.CASCADE, related_name="dcus")
    device_no = models.CharField(max_length=40)
    esp = models.CharField(max_length=80, blank=True)
    substation = models.CharField(max_length=120, blank=True)
    feeder = models.CharField(max_length=120, blank=True)
    tp = models.CharField(max_length=60, blank=True)
    operator = models.CharField(max_length=60, blank=True)
    sim = models.CharField(max_length=40, blank=True)
    ip = models.CharField(max_length=45, blank=True)
    status_at = models.DateTimeField(null=True, blank=True)
    lat = models.CharField(max_length=30, blank=True)
    lon = models.CharField(max_length=30, blank=True)
    manager = models.TextField(blank=True)
    manager_phone = models.TextField(blank=True)
    phone = models.CharField(max_length=120, blank=True)
    # Offline davri: konsentrator birinchi marta offline ko‘ringan sana va shundan buyongi kunlar
    offline_since = models.DateField()
    days = models.PositiveIntegerField(default=0)

    class Meta:
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=["snapshot", "device_no"], name="uniq_dcu_snapshot_device")]
        indexes = [models.Index(fields=["snapshot", "days"], name="idx_dcu_snap_days")]


class MeterRecord(models.Model):
    snapshot = models.ForeignKey(Snapshot, on_delete=models.CASCADE, related_name="meters")
    esp = models.CharField(max_length=80, blank=True)
    substation = models.CharField(max_length=120, blank=True)
    feeder = models.CharField(max_length=120, blank=True)
    transformer = models.CharField(max_length=60, blank=True)
    account = models.CharField(max_length=40, blank=True)
    consumer_type = models.CharField(max_length=80, blank=True)
    group = models.CharField(max_length=10, choices=GROUPS, default="boshqa")
    consumer_name = models.CharField(max_length=255, blank=True)
    meter_no = models.CharField(max_length=60, blank=True)
    comm_type = models.CharField(max_length=30, blank=True)
    manager = models.TextField(blank=True)
    manager_phone = models.TextField(blank=True)
    days = models.PositiveIntegerField(default=0)
    gap_time = models.CharField(max_length=20, blank=True)
    gps = models.CharField(max_length=60, blank=True)
    address = models.CharField(max_length=500, blank=True)

    class Meta:
        default_permissions = ()
        indexes = [
            models.Index(fields=["snapshot", "days"], name="idx_mtr_snap_days"),
            models.Index(fields=["snapshot", "group"], name="idx_mtr_snap_group"),
        ]


class MonitorSetting(models.Model):
    """Yagona sozlamalar qatori (pk=1)."""

    dcu_edges = models.CharField("Konsentratorlar chegaralari (kun)", max_length=60, default="2,7,30")
    meter_edges = models.CharField("Hisoblagichlar chegaralari (kun)", max_length=60, default="2,7,30,90")
    kw_yuridik = models.TextField("Yuridik — kalit so‘zlar", default=DEFAULT_KW_YURIDIK)
    kw_aholi = models.TextField("Aholi — kalit so‘zlar", default=DEFAULT_KW_AHOLI)
    kw_balans = models.TextField("Balans — kalit so‘zlar", default=DEFAULT_KW_BALANS)
    offline_keywords = models.TextField("Offline holat so‘zlari", default=DEFAULT_OFFLINE)
    use_file_time = models.BooleanField("Birinchi uchraganda fayldagi vaqtdan foydalanish", default=True)
    telegram_chat_id = models.CharField("Telegram guruh ID", max_length=80, blank=True)
    telegram_token = models.TextField("Telegram bot tokeni (shifrlangan)", blank=True, default="")
    detail_keep_days = models.PositiveSmallIntegerField("Hisoblagich ro‘yxatini saqlash (kun)", default=14)

    class Meta:
        default_permissions = ()
        verbose_name = "Monitoring sozlamalari"
        verbose_name_plural = "Monitoring sozlamalari"


class SendLog(models.Model):
    STATUS = (("running", "Yuborilmoqda"), ("ok", "Yuborildi"), ("error", "Xato"))
    snapshot = models.ForeignKey(Snapshot, on_delete=models.CASCADE, related_name="send_logs")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    username = models.CharField(max_length=150, blank=True)
    chat_id = models.CharField(max_length=80, blank=True)
    kinds = models.JSONField(default=list)
    status = models.CharField(max_length=10, choices=STATUS, default="running")
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        default_permissions = ()
