"""Telegram Bot API bilan ishlash (faqat standart kutubxona). Token hech qachon jurnalga yozilmaydi."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import http.client
import logging
import threading
import time
import uuid
from urllib import error, request

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import DatabaseError

API = "https://api.telegram.org"
CHAT_RE = re.compile(r"^(-?\d{5,20}|@[A-Za-z][A-Za-z0-9_]{4,63})$")


class TelegramError(Exception):
    pass


TOKEN_RE = re.compile(r"^\d{6,14}:[A-Za-z0-9_-]{30,}$")
NO_TOKEN_MSG = "Bot tokeni kiritilmagan (Sozlamalar → Telegram bot)."


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(f"{settings.SECRET_KEY}:monitoring-telegram".encode()).digest())
    return Fernet(key)


def encrypt_token(plain: str) -> str:
    """Token bazada ochiq matn bo‘lib yotmasligi uchun (kalit — DJANGO_SECRET_KEY dan olinadi)."""
    return _fernet().encrypt(plain.strip().encode()).decode()


def decrypt_token(stored: str) -> str:
    try:
        return _fernet().decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError):
        return ""  # DJANGO_SECRET_KEY o‘zgargan yoki yozuv buzilgan — token qayta kiritilsin


def valid_token(value: str) -> bool:
    return bool(TOKEN_RE.match((value or "").strip()))


def _stored() -> str:
    """Token butun loyiha sozlamasida (core.SiteSetting). Eski o‘rni (MonitorSetting) — zaxira."""
    from apps.core.models import SiteSetting

    from .models import MonitorSetting

    try:
        val = SiteSetting.objects.filter(pk=1).values_list("telegram_token", flat=True).first()
        if val:
            return val
        return MonitorSetting.objects.filter(pk=1).values_list("telegram_token", flat=True).first() or ""
    except DatabaseError:
        return ""


def token_source() -> str:
    """«db» — dasturda kiritilgan, «env» — .env dagi zaxira, «» — yo‘q."""
    stored = _stored()
    if stored and decrypt_token(stored):
        return "db"
    if (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip():
        return "env"
    return ""


def token() -> str:
    stored = _stored()
    if stored:
        plain = decrypt_token(stored)
        if plain:
            return plain
    return (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()


def mask_token(value: str | None = None) -> str:
    t = value if value is not None else token()
    if not t or ":" not in t:
        return ""
    bot_id, secret = t.split(":", 1)
    return f"{bot_id}:••••{secret[-4:]}"


def is_configured() -> bool:
    return bool(token())


def get_me() -> dict:
    """Token to‘g‘riligini tekshiradi; bot ma’lumotini qaytaradi."""
    return api_call("getMe", {}, timeout=20)["result"]


def valid_chat_id(chat_id: str) -> bool:
    return bool(CHAT_RE.match((chat_id or "").strip()))


def _scrub(text: str) -> str:
    t = token()
    return text.replace(t, "***") if t else text


def _multipart(fields: dict, files: dict):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fname, data, ctype) in files.items():
        safe_name = fname.replace('"', "")
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{safe_name}"\r\n'
                      f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


_local = threading.local()
_log = logging.getLogger(__name__)


def _use_proxy() -> bool:
    px = request.getproxies()
    return bool(px.get("https") or px.get("all"))


def _post(url: str, body: bytes, ctype: str, timeout: int):
    """Ulanishni qayta ishlatadi (keep-alive): har chaqiruvda yangi TLS qo‘l siqish bo‘lmaydi — bot ancha tez javob beradi.
    Proksi bo‘lsa yoki ulanish uzilsa — oddiy urllib ga o‘tadi."""
    t0 = time.monotonic()
    try:
        if not _use_proxy():
            path = url[len(API):]
            for fresh in (False, True):
                conn = getattr(_local, "conn", None)
                if conn is None or fresh:
                    if conn is not None:
                        conn.close()
                    conn = _local.conn = http.client.HTTPSConnection("api.telegram.org", timeout=timeout)
                conn.timeout = timeout
                if conn.sock:
                    conn.sock.settimeout(timeout)
                try:
                    conn.request("POST", path, body=body, headers={"Content-Type": ctype, "Connection": "keep-alive"})
                    resp = conn.getresponse()
                    raw = resp.read()
                    return json.loads(raw.decode("utf-8"))
                except (http.client.HTTPException, ConnectionError, OSError) as exc:
                    _local.conn = None
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
                    if fresh or isinstance(exc, TimeoutError):
                        break   # yangi ulanishda ham xato — urllib bilan urinib ko‘ramiz
        req = request.Request(url, data=body, headers={"Content-Type": ctype}, method="POST")
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                raise TelegramError(f"Telegram javobi o‘qilmadi (HTTP {exc.code}).") from None
    finally:
        dt = time.monotonic() - t0
        if dt > 3 and not url.endswith("/getUpdates"):
            _log.warning("Telegram sekin javob berdi: %s %.1f s", url.rsplit("/", 1)[-1], dt)


def api_call(method: str, fields: dict, files: dict | None = None, timeout: int = 90) -> dict:
    """Bot API chaqiruvi. 429 bo‘lsa, ko‘rsatilgan vaqt kutib qayta urinadi (3 martagacha)."""
    if not token():
        raise TelegramError(NO_TOKEN_MSG)
    url = f"{API}/bot{token()}/{method}"
    for attempt in range(3):
        if files:
            body, ctype = _multipart(fields, files)
        else:
            body, ctype = json.dumps(fields).encode(), "application/json"
        try:
            data = _post(url, body, ctype, timeout)
        except (error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise TelegramError(_scrub(f"Telegramga ulanib bo‘lmadi: {exc}")) from None
        if data.get("ok"):
            return data
        params = data.get("parameters") or {}
        if data.get("error_code") == 429 and attempt < 2:
            time.sleep(min(int(params.get("retry_after", 5)), 30) + 1)
            continue
        raise TelegramError(_scrub(f"Telegram xatosi {data.get('error_code', '')}: {data.get('description', 'noma’lum')}"))
    raise TelegramError("Telegram band — keyinroq urinib ko‘ring.")


def send_message(chat_id, text: str, reply_markup: dict | None = None) -> dict:
    fields = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_markup is not None:
        fields["reply_markup"] = reply_markup
    return api_call("sendMessage", fields)


def get_updates(offset: int | None, wait: int = 25) -> list[dict]:
    """Long polling: yangi xabar kelguncha Telegram javobni ushlab turadi — server yuklamasi minimal."""
    fields = {"timeout": wait, "allowed_updates": ["message", "callback_query"]}
    if offset:
        fields["offset"] = offset
    return api_call("getUpdates", fields, timeout=wait + 15)["result"]


def delete_webhook() -> None:
    api_call("deleteWebhook", {"drop_pending_updates": False}, timeout=20)


def download_file(file_id: str, max_bytes: int) -> bytes:
    """Foydalanuvchi yuborgan faylni yuklab oladi (Bot API: 20 MB gacha)."""
    info = api_call("getFile", {"file_id": file_id}, timeout=20)["result"]
    if int(info.get("file_size") or 0) > max_bytes:
        raise TelegramError("Fayl juda katta.")
    url = f"{API}/file/bot{token()}/{info['file_path']}"
    try:
        with request.urlopen(url, timeout=60) as resp:
            data = resp.read(max_bytes + 1)
    except (error.URLError, TimeoutError, OSError) as exc:
        raise TelegramError(_scrub(f"Faylni yuklab bo‘lmadi: {exc}")) from None
    if len(data) > max_bytes:
        raise TelegramError("Fayl juda katta.")
    return data


def send_document(chat_id, filename: str, data: bytes, caption: str = "") -> dict:
    ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    fields = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption[:1000]
    return api_call("sendDocument", fields, {"document": (filename, data, ctype)}, timeout=180)


def send_photo(chat_id: str, filename: str, data: bytes, caption: str = "") -> dict:
    fields = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption[:1000]
    return api_call("sendPhoto", fields, {"photo": (filename, data, "image/png")}, timeout=120)


def chat_title(chat_id: str) -> str:
    """Guruh nomini Telegramdan oladi (getChat). Natija 1 soatga keshlanadi; xato bo‘lsa — bo‘sh satr."""
    from django.core.cache import cache

    chat_id = (chat_id or "").strip()
    if not is_configured() or not valid_chat_id(chat_id):
        return ""
    key = f"mon_chat_title:{chat_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    try:
        chat = api_call("getChat", {"chat_id": chat_id}, timeout=8)["result"]
        title = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username", "")
    except Exception:  # noqa: BLE001
        cache.set(key, "", 60)  # vaqtincha xato — bir daqiqadan keyin qayta urinadi
        return ""
    cache.set(key, title, 3600)
    return title


# ------------------------------------------------------------------ bot uchun qo‘shimcha usullar
_MEDIA = {"photo": ("sendPhoto", "photo", "image/jpeg"), "video": ("sendVideo", "video", "video/mp4"),
          "document": ("sendDocument", "document", "application/octet-stream")}


def send_media(chat_id, kind: str, file_ref: str | None, caption: str = "", markup: dict | None = None,
               filename: str = "fayl", data: bytes | None = None) -> dict:
    """Rasm / video / fayl yuboradi. file_ref (Telegram file_id) bo‘lsa, qayta yuklanmaydi; bo‘lmasa data yuboriladi."""
    method, field, ctype = _MEDIA[kind]
    fields: dict = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption[:1024]
    if kind == "video":
        fields["supports_streaming"] = "true" if data is not None else True
    if file_ref:
        if markup is not None:
            fields["reply_markup"] = markup
        fields[field] = file_ref
        return api_call(method, fields, timeout=120)
    if markup is not None:
        fields["reply_markup"] = json.dumps(markup)
    guessed = __import__("mimetypes").guess_type(filename)[0] or ctype
    return api_call(method, fields, {field: (filename, data or b"", guessed)}, timeout=600)


def extract_file_id(message: dict, kind: str) -> str:
    if kind == "photo":
        photos = message.get("photo") or []
        return photos[-1]["file_id"] if photos else ""
    return (message.get(kind) or {}).get("file_id", "")


def delete_message(chat_id, message_id) -> None:
    api_call("deleteMessage", {"chat_id": chat_id, "message_id": message_id}, timeout=20)


def answer_callback(callback_id: str, text: str = "", alert: bool = False) -> None:
    try:
        api_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190], "show_alert": alert}, timeout=15)
    except TelegramError:
        pass


def edit_markup(chat_id, message_id, markup: dict | None) -> None:
    try:
        api_call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": markup or {"inline_keyboard": []}}, timeout=20)
    except TelegramError:
        pass


def edit_text(chat_id, message_id, text: str, markup: dict | None = None) -> bool:
    fields = {"chat_id": chat_id, "message_id": message_id, "text": text, "disable_web_page_preview": True}
    if markup is not None:
        fields["reply_markup"] = markup
    try:
        api_call("editMessageText", fields, timeout=20)
        return True
    except TelegramError:
        return False


def send_location(chat_id, lat: float, lon: float, markup: dict | None = None) -> dict:
    fields = {"chat_id": chat_id, "latitude": lat, "longitude": lon}
    if markup is not None:
        fields["reply_markup"] = markup
    return api_call("sendLocation", fields, timeout=30)


def send_action(chat_id, action: str = "typing") -> None:
    try:
        api_call("sendChatAction", {"chat_id": chat_id, "action": action}, timeout=10)
    except TelegramError:
        pass
