from django.conf import settings
from django.db import models


class Post(models.Model):
    """Bot orqali foydalanuvchilarga yuboriladigan post (matn / rasm / video / fayl + matn)."""

    KINDS = (("text", "Matn"), ("photo", "Rasm + matn"), ("video", "Video + matn"), ("document", "Fayl + matn"))
    kind = models.CharField(max_length=10, choices=KINDS)
    text = models.TextField(blank=True)
    file = models.FileField(upload_to="posts/%Y/%m/", blank=True)
    file_name = models.CharField(max_length=200, blank=True)
    tg_file_id = models.CharField(max_length=300, blank=True)       # birinchi yuborishdan keyin Telegram file_id si saqlanadi
    confirm_required = models.BooleanField("«Tanishdim» tugmasi", default=True)
    deadline = models.DateTimeField("Tanishish muddati", null=True, blank=True)
    audience = models.CharField("Qabul qiluvchilar", max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        default_permissions = ()

    @property
    def title(self):
        t = (self.text or self.file_name or "").strip().splitlines()
        return (t[0] if t else "(matnsiz)")[:90]


class Recipient(models.Model):
    STATUS = (("pending", "Navbatda"), ("sent", "Yuborildi"), ("failed", "Yetmadi"))
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="recipients")
    user = models.ForeignKey("customers.BotUser", on_delete=models.CASCADE, related_name="post_recipients")
    status = models.CharField(max_length=8, choices=STATUS, default="pending", db_index=True)
    error = models.CharField(max_length=200, blank=True)
    message_id = models.BigIntegerField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    reminded_at = models.DateTimeField(null=True, blank=True)
    removed = models.BooleanField(default=False)

    class Meta:
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=["post", "user"], name="uniq_post_user")]
        ordering = ["user__full_name"]
