from django.conf import settings
from django.db import models


class BotUser(models.Model):
    """Telegram bot foydalanuvchisi (ro‘yxatdan o‘tish arizasi)."""

    STATUS = (("new", "Ro‘yxatdan o‘tmoqda"), ("pending", "Kutilmoqda"), ("approved", "Tasdiqlangan"),
              ("rejected", "Bekor qilingan"), ("blocked", "Bloklangan"))
    tg_id = models.BigIntegerField("Telegram ID", unique=True)
    tg_username = models.CharField(max_length=80, blank=True)
    full_name = models.CharField("FIO", max_length=150, blank=True)
    district = models.CharField("Tuman / shahar", max_length=120, blank=True)
    phone = models.CharField("Telefon", max_length=20, blank=True)
    position = models.CharField("Lavozim", max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="new", db_index=True)
    state = models.CharField(max_length=20, blank=True, default="")   # bot suhbat bosqichi
    draft = models.JSONField(default=dict, blank=True)
    reject_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-applied_at", "-id"]
        default_permissions = ()

    def __str__(self):
        return self.full_name or f"tg:{self.tg_id}"


class Submission(models.Model):
    """Bitta yuborilgan fayl (jurnal yozuvi)."""

    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="submissions")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    filename = models.CharField(max_length=200, blank=True)
    total = models.PositiveIntegerField(default=0)
    ok = models.PositiveIntegerField(default=0)
    bad = models.PositiveIntegerField(default=0)
    rejected = models.CharField("Fayl rad etilgan sababi", max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        default_permissions = ()


class Customer(models.Model):
    """Tekshiruvdan o‘tgan istemolchi (CAS ga yuklanadi)."""

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="customers")
    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="customers")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    etk_code = models.CharField(max_length=5, db_index=True)
    trans_code = models.CharField(max_length=5)
    trans_name = models.CharField(max_length=250)
    consumer_name = models.CharField(max_length=250)
    consumer_code = models.CharField(max_length=100)
    meter_type = models.CharField(max_length=20)
    meter_no = models.CharField(max_length=12, unique=True)
    address = models.CharField(max_length=500)
    phase = models.CharField(max_length=1)
    ct_ratio = models.CharField(max_length=30)
    consumer_type = models.CharField(max_length=1)
    comm_type = models.CharField(max_length=10)
    vt_ratio = models.CharField(max_length=30)

    class Meta:
        ordering = ["id"]
        default_permissions = ()


class EtkStat(models.Model):
    """Kunlik statistika: ETK kodi bo‘yicha yuborilgan / to‘g‘ri / xato qatorlar. Customer yozuvlari o‘chirilsa ham saqlanadi.
    etk_code bo‘sh — ETK kodi noto‘g‘ri yoki kiritilmagan qatorlar."""

    date = models.DateField(db_index=True)
    etk_code = models.CharField(max_length=5, blank=True, default="")
    sent = models.PositiveIntegerField(default=0)
    ok = models.PositiveIntegerField(default=0)
    bad = models.PositiveIntegerField(default=0)

    class Meta:
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=["date", "etk_code"], name="uniq_etk_stat_day")]
