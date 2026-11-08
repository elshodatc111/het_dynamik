from django.conf import settings
from django.db import models


class Place(models.Model):
    """Konsentrator (DCU), balans yoki aholi/yuridik hisoblagichning joylashuvi. Nomer noyob."""

    KINDS = (("Meter", "Hisoblagich"), ("DCU", "Konsentrator"), ("Balans", "Balans hisoblagich"))
    kind = models.CharField("Turi", max_length=6, choices=KINDS, db_index=True)
    number = models.CharField("Nomer", max_length=20, unique=True)
    lat = models.FloatField("Kenglik")
    lon = models.FloatField("Uzunlik")
    district = models.CharField("Tuman", max_length=60, db_index=True)
    substation = models.CharField("Podstansiya", max_length=150)
    feeder = models.CharField("Fider", max_length=150)
    tp = models.CharField("TP", max_length=60)
    account = models.CharField("Hisob raqam", max_length=60, blank=True)
    fio = models.CharField("FIO", max_length=250, blank=True)
    address = models.CharField("Manzil", max_length=500, blank=True)
    consumer_type = models.CharField("Iste’molchi turi", max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["number"]
        default_permissions = ()

    def __str__(self):
        return f"{self.kind} {self.number}"


class LocationUpload(models.Model):
    """Location faylini yuklash jurnali."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    username = models.CharField(max_length=150, blank=True)
    filename = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    total = models.PositiveIntegerField(default=0)
    created = models.PositiveIntegerField(default=0)
    updated = models.PositiveIntegerField(default=0)
    unchanged = models.PositiveIntegerField(default=0)
    bad = models.PositiveIntegerField(default=0)
    error_file = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        default_permissions = ()


class LocationSearch(models.Model):
    """Bot orqali qilingan qidiruv jurnali."""

    bot_user = models.ForeignKey("customers.BotUser", null=True, on_delete=models.SET_NULL, related_name="location_searches")
    query = models.CharField(max_length=60)
    found = models.BooleanField(default=False, db_index=True)
    place = models.ForeignKey(Place, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        default_permissions = ()
