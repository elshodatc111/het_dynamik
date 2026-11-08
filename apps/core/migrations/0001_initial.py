from django.db import migrations, models


def copy_token(apps, schema_editor):
    """Avval token Monitoring sozlamasida saqlangan edi — umumiy sozlamaga ko‘chiramiz."""
    Old = apps.get_model("monitoring", "MonitorSetting")
    New = apps.get_model("core", "SiteSetting")
    old = Old.objects.filter(pk=1).first()
    if old and old.telegram_token:
        New.objects.update_or_create(pk=1, defaults={"telegram_token": old.telegram_token})


class Migration(migrations.Migration):
    initial = True
    dependencies = [("monitoring", "0002_monitorsetting_telegram_token")]

    operations = [
        migrations.CreateModel(
            name="SiteSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("telegram_token", models.TextField(blank=True, default="", verbose_name="Telegram bot tokeni (shifrlangan)")),
            ],
            options={"verbose_name": "Loyiha sozlamasi", "verbose_name_plural": "Loyiha sozlamalari", "default_permissions": ()},
        ),
        migrations.RunPython(copy_token, migrations.RunPython.noop),
    ]
