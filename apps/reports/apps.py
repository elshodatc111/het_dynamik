from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _seed(sender, **kwargs):
    from .services import sync_catalog, sync_roles

    sync_catalog()
    sync_roles()


class ReportsConfig(AppConfig):
    name = "apps.reports"
    verbose_name = "Hisobotlar"

    def ready(self):
        post_migrate.connect(_seed, sender=self)
        from . import signals  # noqa: F401
