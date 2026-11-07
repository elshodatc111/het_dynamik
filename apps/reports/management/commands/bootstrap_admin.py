"""Birinchi administratorni .env dagi ADMIN_USERNAME / ADMIN_PASSWORD dan yaratadi."""
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from apps.reports.services import sync_catalog, sync_roles


class Command(BaseCommand):
    help = "Katalog va rollarni sinxronlaydi, birinchi administratorni yaratadi (agar yo‘q bo‘lsa)."

    def add_arguments(self, parser):
        parser.add_argument("--username")
        parser.add_argument("--password")
        parser.add_argument("--email", default="")

    def handle(self, *args, **opts):
        sync_catalog()
        sync_roles()
        User = get_user_model()
        username = opts["username"] or os.environ.get("ADMIN_USERNAME", "admin")
        password = opts["password"] or os.environ.get("ADMIN_PASSWORD", "")
        email = opts["email"] or os.environ.get("ADMIN_EMAIL", "")
        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write("Administrator allaqachon mavjud — o‘tkazib yuborildi.")
            return
        if not password:
            self.stderr.write("ADMIN_PASSWORD berilmagan (.env yoki --password).")
            return
        user = User.objects.create_superuser(username=username, email=email, password=password)
        user.groups.add(Group.objects.get(name="Admin"))
        self.stdout.write(self.style.SUCCESS(f"Administrator yaratildi: {username}"))
