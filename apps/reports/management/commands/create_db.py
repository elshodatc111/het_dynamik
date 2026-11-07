"""MySQL bazasini (agar yo‘q bo‘lsa) yaratadi. SQLite uchun hech narsa qilmaydi."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    requires_system_checks = []  # baza hali yo‘q — tekshiruv unga ulanmasin
    help = "Sozlamalardagi MySQL bazasini yaratadi (mavjud bo‘lsa, tegmaydi)."

    def handle(self, *args, **opts):
        db = settings.DATABASES["default"]
        if "sqlite" in db["ENGINE"]:
            self.stdout.write("SQLite — baza yaratish shart emas.")
            return
        import pymysql

        name = db["NAME"]
        if not name.replace("_", "").isalnum():
            raise CommandError(f"Baza nomi noto‘g‘ri: {name!r} (faqat harf, raqam va _).")
        try:
            conn = pymysql.connect(
                host=db["HOST"], port=int(db["PORT"] or 3306), user=db["USER"], password=db["PASSWORD"], charset="utf8mb4"
            )
        except pymysql.err.OperationalError as exc:
            raise CommandError(
                f"MySQL ga ulanib bo‘lmadi ({exc.args[0]}: {exc.args[1]}). "
                "Server ishlayotganini va .env dagi DB_HOST / DB_USER / DB_PASSWORD ni tekshiring."
            )
        with conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                    )
                except pymysql.err.OperationalError as exc:
                    raise CommandError(
                        f"Bazani yaratishga ruxsat yo‘q ({exc.args[1]}). MySQL da root bilan quyidagini bajaring:\n"
                        f"  CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
                        f"  GRANT ALL ON `{name}`.* TO '{db['USER']}'@'%';"
                    )
        self.stdout.write(self.style.SUCCESS(f"Baza tayyor: {name}"))
