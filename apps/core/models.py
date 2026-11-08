from django.db import models


class SiteSetting(models.Model):
    """Butun loyiha uchun umumiy sozlamalar (bitta yozuv, pk=1). Telegram bot tokeni barcha bo‘limlarda ishlatiladi."""

    telegram_token = models.TextField("Telegram bot tokeni (shifrlangan)", blank=True, default="")

    class Meta:
        default_permissions = ()
        verbose_name = "Loyiha sozlamasi"
        verbose_name_plural = "Loyiha sozlamalari"


def get_site() -> "SiteSetting":
    obj, _ = SiteSetting.objects.get_or_create(pk=1)
    return obj
