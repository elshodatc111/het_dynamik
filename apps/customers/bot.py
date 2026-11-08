"""Customer boti: ro‘yxatdan o‘tish, shablon berish va faylni qabul qilish. Telegram long polling (`manage.py run_bot`)."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError, close_old_connections
from django.utils import timezone

from apps.monitoring import telegram

from . import rules, services
from .models import BotUser, Submission

log = logging.getLogger("customers.bot")

BTN_LOCATION = "📍 Joylashuvni aniqlash"
BTN_TEMPLATE = "📄 Customer shablon"
BTN_SEND = "📤 Customer jo‘natish"
BTN_HELP = "❓ Yordam"
BTN_CANCEL = "❌ Bekor qilish"
MENU = {"keyboard": [[{"text": BTN_LOCATION}], [{"text": BTN_TEMPLATE}, {"text": BTN_SEND}], [{"text": BTN_HELP}]],
        "resize_keyboard": True, "input_field_placeholder": "Kerakli tugmani tanlang"}
CANCEL_KB = {"keyboard": [[{"text": BTN_CANCEL}]], "resize_keyboard": True, "input_field_placeholder": "Raqamni yozing…"}
CONTACT_KB = {"keyboard": [[{"text": "📱 Raqamimni yuborish", "request_contact": True}]], "resize_keyboard": True, "one_time_keyboard": True}
NO_KB = {"remove_keyboard": True}
REG_STATES = ("name", "district", "phone", "position")
ALIVE_SECONDS = 90


def _norm(t: str) -> str:
    """Tugma matnini solishtirish uchun: emoji, bo‘sh joy va apostroflarsiz, kichik harfda."""
    return re.sub(r"[^0-9a-zа-я]+", "", (t or "").lower().replace("‘", "").replace("’", "").replace("ʻ", "").replace("'", ""))


# ------------------------------------------------------------------ holat fayllari (monitoring uchun)
def state_dir() -> Path:
    p = Path(settings.MEDIA_ROOT) / "bot"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_json(name: str, data: dict):
    p = state_dir() / name
    tmp = p.with_name(f"{p.stem}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for i in range(5):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:   # Windows
            time.sleep(0.05 * (i + 1))
    tmp.unlink(missing_ok=True)


def _read_json(name: str) -> dict:
    try:
        return json.loads((state_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def heartbeat(**extra):
    cur = _read_json("heartbeat.json")
    cur.update(extra, ts=time.time(), pid=os.getpid())
    _write_json("heartbeat.json", cur)


def read_heartbeat() -> dict:
    return _read_json("heartbeat.json")


# ------------------------------------------------------------------ yuborish yordamchilari
def say(chat_id, text: str, markup: dict | None = None):
    try:
        telegram.send_message(chat_id, text, markup)
    except telegram.TelegramError as exc:
        log.warning("xabar yuborilmadi: %s", exc)


class Progress:
    """Foydalanuvchi kutib zerikmasligi uchun: bitta xabar bosqichma-bosqich yangilanib boradi (jarayon qaerda ekani ko‘rinadi)."""

    def __init__(self, chat, total: int, first: str):
        self.chat, self.total, self.n, self.mid = chat, total, 0, None
        try:
            self.mid = ((telegram.send_message(chat, self._fmt(first, 0)) or {}).get("result") or {}).get("message_id")
        except telegram.TelegramError as exc:
            log.warning("progress xabari yuborilmadi: %s", exc)

    def _fmt(self, text: str, n: int) -> str:
        bar = "▰" * n + "▱" * (self.total - n)
        return f"{bar}  {n}/{self.total}\n{text}"

    def step(self, text: str):
        self.n += 1
        if self.mid:
            telegram.edit_text(self.chat, self.mid, self._fmt(text, self.n))

    def finish(self, text: str, markup: dict | None = None):
        """Oxirgi natijani shu xabarga yozadi (yozib bo‘lmasa — yangi xabar)."""
        inline = markup if markup and "inline_keyboard" in markup else None   # yozuv o‘zgartirilganda faqat inline tugmalar qo‘yiladi
        if self.mid and telegram.edit_text(self.chat, self.mid, text, inline):
            return
        say(self.chat, text, markup)


def notify(user: BotUser, text: str, markup: dict | None = None) -> bool:
    """Veb panelidan (tasdiqlash/bekor qilish) foydalanuvchiga xabar. Yetib bormasa False."""
    try:
        telegram.send_message(user.tg_id, text, markup)
        return True
    except telegram.TelegramError as exc:
        log.warning("xabar yuborilmadi: %s", exc)
        return False


def normalize_phone(text: str) -> str:
    d = re.sub(r"\D", "", text or "")
    if len(d) == 9:
        d = "998" + d
    return "+" + d if re.fullmatch(r"998\d{9}", d) else ""


# ------------------------------------------------------------------ suhbat
def on_start(user: BotUser, chat):
    if user.status == "approved":
        say(chat, f"Assalomu alaykum, {user.full_name}! Kerakli tugmani tanlang.", MENU)
    elif user.status == "pending":
        say(chat, "Arizangiz ko‘rib chiqilmoqda. Natija shu yerga yuboriladi.", NO_KB)
    elif user.status == "blocked":
        say(chat, "Sizga botdan foydalanish cheklangan. Administrator bilan bog‘laning.", NO_KB)
    else:   # new yoki rejected — ro‘yxatdan o‘tishni (qayta) boshlash
        user.status, user.state, user.draft = "new", "name", {}
        user.save(update_fields=["status", "state", "draft"])
        say(chat, "Assalomu alaykum! Botdan foydalanish uchun ro‘yxatdan o‘ting.\n\nIsmingiz, familiyangiz va otangizning ismini to‘liq yozing (FIO):", NO_KB)


def registration_step(user: BotUser, chat, msg: dict):
    text = (msg.get("text") or "").strip()
    d = user.draft or {}
    if user.state == "name":
        if len(text) < 5 or len(text) > 150 or len(text.split()) < 2:
            return say(chat, "FIO ni to‘liq yozing: familiya, ism va otangizning ismi.")
        d["full_name"], user.state = text, "district"
        say(chat, "Qaysi tuman yoki shaharda ishlaysiz? Yozing:")
    elif user.state == "district":
        if len(text) < 2 or len(text) > 120:
            return say(chat, "Tuman yoki shahar nomini yozing.")
        d["district"], user.state = text, "phone"
        say(chat, "Telefon raqamingizni yuboring: pastdagi tugmani bosing yoki +998901234567 ko‘rinishida yozing.", CONTACT_KB)
    elif user.state == "phone":
        contact = msg.get("contact")
        if contact:
            if contact.get("user_id") and int(contact["user_id"]) != user.tg_id:
                return say(chat, "Faqat o‘zingizning raqamingizni yuboring.", CONTACT_KB)
            phone = normalize_phone(contact.get("phone_number", ""))
        else:
            phone = normalize_phone(text)
        if not phone:
            return say(chat, "Raqam noto‘g‘ri. +998901234567 ko‘rinishida yozing yoki tugmani bosing.", CONTACT_KB)
        d["phone"], user.state = phone, "position"
        say(chat, "Lavozimingizni yozing:", NO_KB)
    elif user.state == "position":
        if len(text) < 2 or len(text) > 120:
            return say(chat, "Lavozimingizni yozing.")
        user.full_name, user.district, user.phone, user.position = d["full_name"], d["district"], d["phone"], text
        user.status, user.state, user.draft, user.reject_reason = "pending", "", {}, ""
        user.applied_at = timezone.now()
        user.save()
        return say(chat, "Arizangiz qabul qilindi. Administrator ko‘rib chiqadi, natija shu yerga yuboriladi.", NO_KB)
    user.draft = d
    user.save(update_fields=["state", "draft"])


def _reject_file(user: BotUser, chat, reason: str):
    Submission.objects.create(user=user, filename="", rejected=reason[:300])
    say(chat, "❌ " + reason, MENU)


def handle_document(user: BotUser, chat, doc: dict):
    name = doc.get("file_name") or "fayl.xlsx"
    if not name.lower().endswith(".xlsx"):
        return _reject_file(user, chat, "Faqat Excel (.xlsx) fayl qabul qilinadi.")
    if int(doc.get("file_size") or 0) > rules.MAX_BYTES:
        return _reject_file(user, chat, "Fayl hajmi 3 MB dan oshmasligi kerak.")
    prog = Progress(chat, 3, "📥 Fayl qabul qilindi, yuklab olinmoqda…")
    try:
        data = telegram.download_file(doc["file_id"], rules.MAX_BYTES)
        prog.step("🔍 Qatorlar tekshirilmoqda…")
        res = services.process(user, name, data, on_step=lambda t: prog.step(t))
    except services.FileRejected as exc:
        return prog.finish("❌ " + str(exc), MENU)
    except telegram.TelegramError as exc:
        log.warning("fayl yuklanmadi: %s", exc)
        return prog.finish("Faylni olib bo‘lmadi. Birozdan keyin qayta yuboring.", MENU)
    except DatabaseError:
        raise
    except Exception:   # noqa: BLE001 — kutilmagan xato foydalanuvchiga tushunarli aytiladi
        log.exception("fayl tekshiruvida xato")
        return prog.finish("Faylni tekshirishda kutilmagan xatolik yuz berdi. Keyinroq qayta urinib ko‘ring.", MENU)
    user.state = ""
    user.save(update_fields=["state"])
    lines = [f"Jami qator: {res['total']}", f"✅ Qabul qilindi: {res['ok']}", f"❌ Xato: {res['bad']}"]
    if not res["bad"]:
        return prog.finish("\n".join(lines) + "\n\n✅ Barcha qatorlar qabul qilindi. Rahmat!", MENU)
    prog.finish("\n".join(lines))
    say(chat, "Xatolar jadvali tayyorlanmoqda…", MENU)
    sample = "\n".join(res["samples"][:3])
    caption = ("Xatolar jadvali. Xatolarni tuzatib, faqat shu qatorlarni qayta yuboring (qabul qilinganlarni qayta yubormang)."
               + (f"\n\n{sample}" if sample else ""))
    try:
        telegram.send_document(chat, f"xatolar_{timezone.localtime():%d.%m.%Y_%H%M}.xlsx", res["error_file"], caption)
    except telegram.TelegramError as exc:
        log.warning("xatolar fayli yuborilmadi: %s", exc)
        say(chat, "Xatolar faylini yuborib bo‘lmadi. Qayta «Customer jo‘natish» tugmasini bosib urinib ko‘ring.")


HELP_TEXT = ("ℹ️ Bot imkoniyatlari:\n\n"
             "📍 Joylashuvni aniqlash — konsentrator yoki hisoblagich raqamini yuboring, joylashuvi xaritada chiqadi.\n"
             "📄 Customer shablon — Excel shablonni oling.\n"
             "📤 Customer jo‘natish — to‘ldirilgan shablonni yuboring.\n\n"
             "Muammo bo‘lsa, administratorga murojaat qiling.")


def location_lookup(user: BotUser, chat, text: str):
    """Raqam bo‘yicha joylashuvni topadi: jarayon xabari → ma’lumot kartochkasi → xarita nuqtasi (+ Google/Yandex tugmalari)."""
    from apps.locations import services as loc

    place, query, err = loc.find_place(text)
    if err:
        return say(chat, "⚠️ " + err, CANCEL_KB)
    loc.log_search(user, query, place)
    if place is None:
        return say(chat, f"❌ Joylashuv haqida ma’lumot topilmadi.\n\nQidirilgan raqam: {query}\n"
                           "Raqamni tekshirib qayta yuboring yoki «Bekor qilish» ni bosing.", CANCEL_KB)
    say(chat, loc.place_card(place), CANCEL_KB)
    try:
        telegram.send_location(chat, place.lat, place.lon, loc.maps_buttons(place))
    except telegram.TelegramError as exc:
        log.warning("joylashuv yuborilmadi: %s", exc)
        say(chat, f"📍 {place.lat:.6f}, {place.lon:.6f}", loc.maps_buttons(place))
    say(chat, "Yana raqam yuborishingiz mumkin yoki «Bekor qilish» ni bosing.", CANCEL_KB)


def handle_callback(cb: dict):
    """Inline tugmalar: «Tanishdim»."""
    data = cb.get("data") or ""
    frm = cb.get("from") or {}
    if data.startswith("ack:") and data[4:].isdigit():
        from apps.broadcasts import services as post_svc

        rec, fresh = post_svc.confirm(int(data[4:]), frm.get("id"))
        if rec is None:
            return telegram.answer_callback(cb["id"], "Bu tugma sizga tegishli emas.", alert=True)
        msg = cb.get("message") or {}
        telegram.edit_markup(msg.get("chat", {}).get("id", frm.get("id")), msg.get("message_id"),
                             {"inline_keyboard": [[{"text": "✅ Tanishganingiz qayd etildi", "callback_data": "noop"}]]})
        return telegram.answer_callback(cb["id"], "Tasdiqlandi ✅" if fresh else "Siz allaqachon tanishgansiz ✅")
    telegram.answer_callback(cb["id"])


def handle_update(upd: dict):
    if upd.get("callback_query"):
        return handle_callback(upd["callback_query"])
    msg = upd.get("message")
    if not msg or (msg.get("chat") or {}).get("type") != "private":
        return
    frm = msg.get("from") or {}
    if not frm.get("id") or frm.get("is_bot"):
        return
    chat = msg["chat"]["id"]
    user, _ = BotUser.objects.get_or_create(tg_id=frm["id"])
    if user.tg_username != (frm.get("username") or ""):
        user.tg_username = frm.get("username") or ""
        user.save(update_fields=["tg_username"])
    text = (msg.get("text") or "").strip()
    if text.startswith(("/start", "/help")):
        return on_start(user, chat)
    if user.status == "new" and user.state in REG_STATES:
        return registration_step(user, chat, msg)
    if user.status == "new":
        return on_start(user, chat)
    if user.status == "pending":
        return say(chat, "Arizangiz ko‘rib chiqilmoqda. Natija shu yerga yuboriladi.")
    if user.status == "rejected":
        reason = f"\n\nSabab: {user.reject_reason}" if user.reject_reason else ""
        return say(chat, f"Arizangiz bekor qilingan.{reason}\n\nQayta ro‘yxatdan o‘tish uchun /start ni bosing.")
    if user.status == "blocked":
        return say(chat, "Sizga botdan foydalanish cheklangan. Administrator bilan bog‘laning.")
    # tasdiqlangan foydalanuvchi
    n = _norm(text)
    if n == _norm(BTN_CANCEL):
        user.state = ""
        user.save(update_fields=["state"])
        return say(chat, "Bekor qilindi. Kerakli tugmani tanlang.", MENU)
    if n == _norm(BTN_HELP):
        return say(chat, HELP_TEXT, MENU)
    if n == _norm(BTN_LOCATION):
        user.state = "await_loc"
        user.save(update_fields=["state"])
        return say(chat, "📍 Konsentrator yoki hisoblagich raqamini yuboring.\n\nMasalan: 121206035571 yoki EX-3068491", CANCEL_KB)
    if _norm(text) == _norm(BTN_TEMPLATE):
        if user.state:
            user.state = ""
            user.save(update_fields=["state"])
        try:
            telegram.send_document(chat, "CUSTOMER_SHABLON.xlsx", rules.build_template(),
                                   "Shablonni to‘ldiring: B–N ustunlar «Matn» formatida, ro‘yxatli kataklardan tanlang. "
                                   f"Bir faylda {rules.MAX_ROWS} tagacha qator, faqat «Лист1».")
        except telegram.TelegramError as exc:
            log.warning("shablon yuborilmadi: %s", exc)
            say(chat, "Shablonni yuborib bo‘lmadi, keyinroq urinib ko‘ring.")
        return
    if _norm(text) == _norm(BTN_SEND):
        user.state = "await_file"
        user.save(update_fields=["state"])
        return say(chat, f"Tayyorlagan customeringizni jo‘nating.\n\nFaqat Excel (.xlsx) fayl, {rules.MAX_ROWS} qatordan va 3 MB dan oshmasin.", MENU)
    if msg.get("document"):
        return handle_document(user, chat, msg["document"])
    if user.state == "await_loc" and text:
        return location_lookup(user, chat, text)
    say(chat, "Quyidagi tugmalardan foydalaning.", MENU)


# ------------------------------------------------------------------ asosiy sikl
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bot")
_chat_locks: dict = {}
_locks_guard = threading.Lock()


def _chat_of(u: dict):
    m = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
    return (m.get("chat") or {}).get("id")


def _work(u: dict):
    chat = _chat_of(u)
    with _locks_guard:
        lock = _chat_locks.setdefault(chat, threading.Lock())
    try:
        with lock:      # bir foydalanuvchining xabarlari tartib bilan, boshqalar parallel
            handle_update(u)
    except Exception:   # noqa: BLE001 — bitta xabar botni to‘xtatmasin
        log.exception("xabarni qayta ishlashda xato (update %s)", u.get("update_id"))
        heartbeat(error="Xabarni qayta ishlashda xato (jurnalga qarang)")
    finally:
        close_old_connections()


def _dispatch(u: dict):
    """Xabarlar parallel ishlanadi: uzoq Excel yuklash boshqa foydalanuvchilar javobini kechiktirmaydi."""
    if os.environ.get("JOBS_SYNC") == "1":
        return _work(u)
    _pool.submit(_work, u)


def run_forever(stop=lambda: False, poll_wait: int = 25):
    """Telegramdan xabarlarni oladi. Xato bo‘lsa to‘xtamaydi — kutib, qayta urinadi."""
    state = _read_json("state.json")
    offset = int(state.get("offset") or 0) or None
    heartbeat(started=time.time(), error="", token=telegram.is_configured())
    try:
        telegram.delete_webhook()
    except telegram.TelegramError:
        pass
    backoff = 3
    while not stop():
        close_old_connections()
        try:
            if not telegram.is_configured():
                heartbeat(error="Bot tokeni kiritilmagan (Sozlamalar → Telegram bot)", token=False)
                time.sleep(10)
                continue
            updates = telegram.get_updates(offset, poll_wait)
            heartbeat(error="", token=True)
            backoff = 3
            for u in updates:
                _dispatch(u)
                offset = u["update_id"] + 1
                _write_json("state.json", {"offset": offset})
            if updates:
                heartbeat(last_update=time.time())
        except telegram.TelegramError as exc:
            heartbeat(error=str(exc)[:300])
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except DatabaseError as exc:
            heartbeat(error=f"Baza xatosi: {str(exc)[:200]}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
