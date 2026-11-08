"""Post yuborish (fon ishi), tasdiqlash, eslatma va o‘chirish."""
from __future__ import annotations

import logging
import mimetypes
import time

from django.urls import reverse
from django.utils import timezone

from apps.core.jobs import JobError
from apps.customers.models import BotUser
from apps.monitoring import telegram

from .models import Post, Recipient

log = logging.getLogger("broadcasts")
TEXT_MAX, CAPTION_MAX = 4096, 1024
PHOTO_MAX, FILE_MAX = 10 * 1024 * 1024, 50 * 1024 * 1024
SEND_STEPS = [("Qabul qiluvchilar tayyorlanmoqda", 1), ("Telegramga yuborilmoqda", 8), ("Natija saqlanmoqda", 1)]
PAUSE = 0.05            # sekundiga ~20 ta xabar (Telegram chegarasi ~30)


def detect_kind(upload) -> str:
    ctype = (getattr(upload, "content_type", "") or mimetypes.guess_type(upload.name)[0] or "").lower()
    if ctype in ("image/jpeg", "image/png", "image/webp"):
        return "photo"
    if ctype in ("video/mp4", "video/quicktime", "video/webm"):
        return "video"
    return "document"


def validate(text: str, upload) -> str:
    """Xato matni yoki ''."""
    text = (text or "").strip()
    if not text and upload is None:
        return "Post matnini yozing yoki fayl biriktiring."
    if upload is None:
        return f"Matn {TEXT_MAX} belgidan oshmasin." if len(text) > TEXT_MAX else ""
    kind = detect_kind(upload)
    if len(text) > CAPTION_MAX:
        return f"Rasm/video/fayl bilan birga matn {CAPTION_MAX} belgidan oshmasligi kerak (hozir {len(text)})."
    if kind == "photo" and upload.size > PHOTO_MAX:
        return "Rasm hajmi 10 MB dan oshmasin (katta rasmni fayl sifatida yuboring)."
    if upload.size > FILE_MAX:
        return "Fayl hajmi 50 MB dan oshmasligi kerak (Telegram cheklovi)."
    return ""


def ack_markup(rec: Recipient) -> dict:
    return {"inline_keyboard": [[{"text": "✅ Tanishdim", "callback_data": f"ack:{rec.pk}"}]]}


def _send_one(post: Post, rec: Recipient) -> dict:
    chat = rec.user.tg_id
    markup = ack_markup(rec) if post.confirm_required else None
    text = post.text
    if post.confirm_required and post.deadline:
        text = (text + "\n\n" if text else "") + f"⏰ Tanishish muddati: {timezone.localtime(post.deadline):%d.%m.%Y %H:%M}"
    if post.kind == "text":
        return telegram.send_message(chat, text, markup)
    ref = post.tg_file_id
    if ref:
        return telegram.send_media(chat, post.kind, ref, caption=text, markup=markup)
    post.file.open("rb")
    try:
        data = post.file.read()
    finally:
        post.file.close()
    res = telegram.send_media(chat, post.kind, None, caption=text, markup=markup, filename=post.file_name or "fayl", data=data)
    fid = telegram.extract_file_id(res.get("result", {}), post.kind)
    if fid:
        post.tg_file_id = fid
        post.save(update_fields=["tg_file_id"])
    return res


def run_send(job, post_id: int):
    """Fon ishi: har bir qabul qiluvchiga yuboradi. Bitta xato qolganlarni to‘xtatmaydi."""
    post = Post.objects.get(pk=post_id)
    job.step(0, "Qabul qiluvchilar tayyorlanmoqda…")
    recs = list(post.recipients.select_related("user").filter(status="pending"))
    total = len(recs)
    if not total:
        raise JobError("Yuboriladigan foydalanuvchi topilmadi.")
    job.step(1, f"Yuborilmoqda: 0 / {total}")
    ok = fail = 0
    for n, rec in enumerate(recs, 1):
        try:
            res = _send_one(post, rec)
            rec.status, rec.message_id, rec.sent_at, rec.error = "sent", res.get("result", {}).get("message_id"), timezone.now(), ""
            ok += 1
        except telegram.TelegramError as exc:
            msg = str(exc)
            rec.status = "failed"
            rec.error = ("Foydalanuvchi botni bloklagan" if "blocked" in msg or "deactivated" in msg or "chat not found" in msg else msg)[:200]
            fail += 1
        rec.save(update_fields=["status", "message_id", "sent_at", "error"])
        job.progress(n / total, f"Yuborilmoqda: {n} / {total} · yetdi {ok}, yetmadi {fail}")
        time.sleep(PAUSE)
    job.step(2, "Natija saqlanmoqda…")
    job.done(redirect_to=reverse("post_detail", args=[post.pk]))


def run_remind(job, post_id: int):
    post = Post.objects.get(pk=post_id)
    job.step(0, "Tanishmaganlar aniqlanmoqda…")
    recs = list(post.recipients.select_related("user").filter(status="sent", confirmed_at__isnull=True, removed=False))
    if not recs:
        raise JobError("Hamma tanishib chiqqan — eslatma yuboradigan odam yo‘q.")
    job.step(1, f"Eslatma: 0 / {len(recs)}")
    for n, rec in enumerate(recs, 1):
        head = (post.title or "post")[:70]
        try:
            telegram.send_message(rec.user.tg_id, f"🔔 Eslatma: «{head}» postiga tanishganingizni tasdiqlang.", ack_markup(rec))
            rec.reminded_at = timezone.now()
            rec.save(update_fields=["reminded_at"])
        except telegram.TelegramError as exc:
            log.warning("eslatma yetmadi: %s", exc)
        job.progress(n / len(recs), f"Eslatma yuborilmoqda: {n} / {len(recs)}")
        time.sleep(PAUSE)
    job.step(2, "Tayyor")
    job.done(redirect_to=reverse("post_detail", args=[post.pk]))


def run_delete(job, post_id: int):
    post = Post.objects.get(pk=post_id)
    job.step(0, "Xabarlar aniqlanmoqda…")
    recs = list(post.recipients.select_related("user").filter(status="sent", removed=False, message_id__isnull=False))
    job.step(1, f"O‘chirilmoqda: 0 / {len(recs)}")
    for n, rec in enumerate(recs, 1):
        try:
            telegram.delete_message(rec.user.tg_id, rec.message_id)
        except telegram.TelegramError as exc:
            log.warning("o‘chirilmadi: %s", exc)
        rec.removed = True
        rec.save(update_fields=["removed"])
        job.progress(n / max(len(recs), 1), f"O‘chirilmoqda: {n} / {len(recs)}")
        time.sleep(PAUSE)
    post.deleted_at = timezone.now()
    post.save(update_fields=["deleted_at"])
    job.step(2, "Tayyor")
    job.done(redirect_to=reverse("post_detail", args=[post.pk]))


def confirm(rec_id: int, tg_user_id: int):
    """«Tanishdim» bosildi. Qaytaradi: (Recipient | None, yangi_tasdiq_bo‘ldimi)."""
    rec = Recipient.objects.select_related("post", "user").filter(pk=rec_id).first()
    if rec is None or rec.user.tg_id != tg_user_id:
        return None, False
    if rec.confirmed_at:
        return rec, False
    rec.confirmed_at = timezone.now()
    rec.save(update_fields=["confirmed_at"])
    return rec, True


def audience_users(mode: str, districts: list[str], ids: list[int]):
    qs = BotUser.objects.filter(status="approved")
    if mode == "district":
        qs = qs.filter(district__in=districts)
    elif mode == "selected":
        qs = qs.filter(pk__in=ids)
    return qs.order_by("full_name")
