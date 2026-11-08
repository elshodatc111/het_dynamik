"""Tizim holati: qaysi qism ishlayotgani va qaysi biri to‘xtab qolgani. Har bir tekshiruv — bitta qator."""
from __future__ import annotations

import platform
import shutil
import sys
import time
from datetime import timedelta
from pathlib import Path

import django
from django.conf import settings
from django.db import connection
from django.utils import timezone

OK, WARN, ERR = "ok", "warn", "err"
_tg_cache = {"t": 0.0, "res": None}


def _item(key, title, state, detail, hint=""):
    return {"key": key, "title": title, "state": state, "detail": detail, "hint": hint}


def check_database():
    t = time.perf_counter()
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        ms = (time.perf_counter() - t) * 1000
    except Exception as exc:  # noqa: BLE001
        return _item("db", "Ma’lumotlar bazasi", ERR, f"Ulanib bo‘lmadi: {str(exc)[:160]}",
                     "MySQL ishlayotganini va .env dagi DB_HOST/DB_USER/DB_PASSWORD ni tekshiring.")
    engine = connection.vendor
    try:
        from django.db.migrations.executor import MigrationExecutor
        pending = len(MigrationExecutor(connection).migration_plan(MigrationExecutor(connection).loader.graph.leaf_nodes()))
    except Exception:  # noqa: BLE001
        pending = 0
    if pending:
        return _item("db", "Ma’lumotlar bazasi", WARN, f"{engine}, ulanish {ms:.0f} ms · bajarilmagan migratsiyalar: {pending}",
                     "`python manage.py migrate` ni ishga tushiring (Docker'da konteynerni qayta ishga tushirsangiz o‘zi bajariladi).")
    return _item("db", "Ma’lumotlar bazasi", OK if ms < 500 else WARN, f"{engine}, ulanish {ms:.0f} ms, migratsiyalar to‘liq")


def check_storage():
    root = Path(settings.MEDIA_ROOT)
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".status_probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return _item("media", "Fayllar papkasi", ERR, f"Yozib bo‘lmadi: {exc}", "MEDIA_ROOT papkasiga yozish huquqini tekshiring.")
    total, used, free = shutil.disk_usage(root)
    gb = free / 1024**3
    state = ERR if gb < 0.2 else WARN if gb < 1 else OK
    return _item("media", "Fayllar papkasi va disk", state, f"Yozish mumkin · bo‘sh joy: {gb:.1f} GB ({free / total * 100:.0f}%)",
                 "Disk to‘lib qolmoqda — eski fayllarni tozalang." if state != OK else "")


def check_telegram(force=False):
    from apps.monitoring import telegram

    if not telegram.is_configured():
        return _item("tg", "Telegram bot tokeni", ERR, "Token kiritilmagan", "Sozlamalar → Telegram bot bo‘limida tokenni kiriting.")
    now = time.time()
    if not force and _tg_cache["res"] and now - _tg_cache["t"] < 60:
        return _tg_cache["res"]
    try:
        me = telegram.get_me()
        res = _item("tg", "Telegram bot tokeni", OK, f"@{me.get('username', '?')} — Telegram javob beryapti")
    except telegram.TelegramError as exc:
        res = _item("tg", "Telegram bot tokeni", ERR, str(exc)[:200], "Internet aloqasini va token to‘g‘riligini tekshiring.")
    _tg_cache.update(t=now, res=res)
    return res


def check_bot():
    from apps.customers import bot
    from apps.customers.models import BotUser

    hb = bot.read_heartbeat()
    pending = BotUser.objects.filter(status="pending").count()
    if not hb:
        return _item("bot", "Customer boti (run_bot)", ERR, "Jarayon hali ishga tushmagan",
                     "Alohida oynada `python manage.py run_bot` ni ishga tushiring (Docker'da `bot` xizmati).")
    age = time.time() - hb.get("ts", 0)
    last = hb.get("last_update")
    tail = f" · oxirgi xabar: {timezone.datetime.fromtimestamp(last, timezone.get_current_timezone()):%d.%m.%Y %H:%M}" if last else ""
    tail += f" · kutayotgan arizalar: {pending}" if pending else ""
    if age > bot.ALIVE_SECONDS:
        return _item("bot", "Customer boti (run_bot)", ERR, f"Jarayon {int(age // 60)} daqiqadan beri javob bermayapti{tail}",
                     "`python manage.py run_bot` jarayoni to‘xtagan bo‘lishi mumkin — qayta ishga tushiring.")
    if hb.get("error"):
        return _item("bot", "Customer boti (run_bot)", WARN, f"Ishlayapti, lekin xato: {hb['error']}{tail}")
    return _item("bot", "Customer boti (run_bot)", OK, f"Ishlayapti (PID {hb.get('pid')}){tail}")


def check_jobs():
    from apps.core import jobs

    d = jobs.jobs_dir()
    running = stale = 0
    now = time.time()
    import json
    for p in d.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("status") == "running":
            if now - p.stat().st_mtime > jobs.STALE_SECONDS:
                stale += 1
            else:
                running += 1
    lock = d / "heavy.lock"
    lock_note = ""
    if lock.exists():
        age = now - lock.stat().st_mtime
        lock_note = f" · og‘ir ish navbati band ({int(age)} s)"
    if stale:
        return _item("jobs", "Fon ishlari (yuklash, yuborish)", WARN, f"Qotib qolgan ish: {stale}, ishlayotgan: {running}{lock_note}",
                     "Server qayta ishga tushganda to‘xtab qolgan ishlar 1 daqiqadan keyin o‘zi «xato» bo‘ladi; shundan so‘ng qayta urinib ko‘ring.")
    return _item("jobs", "Fon ishlari (yuklash, yuborish)", OK, f"Ishlayotgan: {running}{lock_note}")


def check_sending():
    from apps.monitoring.models import SendLog

    since = timezone.now() - timedelta(hours=24)
    stuck = SendLog.objects.filter(status="running", created_at__lt=timezone.now() - timedelta(minutes=15)).count()
    errs = SendLog.objects.filter(status="error", created_at__gte=since).count()
    last = SendLog.objects.order_by("-created_at").first()
    tail = f" · oxirgisi: {timezone.localtime(last.created_at):%d.%m.%Y %H:%M} ({last.get_status_display()})" if last else " · hali yuborilmagan"
    if stuck:
        return _item("send", "Monitoring: Telegramga yuborish", WARN, f"Qotib qolgan yuborish: {stuck}{tail}", "Yuborish sahifasidan qayta yuboring.")
    if errs:
        return _item("send", "Monitoring: Telegramga yuborish", WARN, f"Oxirgi 24 soatda xato bilan tugagan: {errs}{tail}",
                     "Jurnal va guruh ID/bot huquqlarini tekshiring (bot guruhda administrator bo‘lsin).")
    return _item("send", "Monitoring: Telegramga yuborish", OK, f"Xato yo‘q{tail}")


def check_server():
    dbg = settings.DEBUG
    detail = (f"Python {platform.python_version()} · Django {django.get_version()} · server vaqti "
              f"{timezone.localtime():%d.%m.%Y %H:%M:%S} ({settings.TIME_ZONE})")
    if dbg:
        return _item("server", "Server", WARN, detail + " · DEBUG yoqilgan", "Ishchi serverda DJANGO_DEBUG=0 bo‘lsin.")
    return _item("server", "Server", OK, detail)


def run_checks(force_tg=False) -> dict:
    items = [check_database(), check_storage(), check_server(), check_telegram(force_tg), check_bot(), check_jobs(), check_sending()]
    states = {i["state"] for i in items}
    overall = ERR if ERR in states else WARN if WARN in states else OK
    return {"items": items, "overall": overall, "problems": sum(1 for i in items if i["state"] != OK),
            "checked": timezone.localtime()}
