import logging

from django.core.management.base import BaseCommand

from apps.customers import bot


class Command(BaseCommand):
    help = "Customer Telegram botini ishga tushiradi (doimiy ishlaydi; to‘xtatish: Ctrl+C)."

    def handle(self, *args, **opts):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.stdout.write("Customer boti ishga tushdi (to‘xtatish: Ctrl+C).")
        try:
            bot.run_forever()
        except KeyboardInterrupt:
            self.stdout.write("To‘xtatildi.")
