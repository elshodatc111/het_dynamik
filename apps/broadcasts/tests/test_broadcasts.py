import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.broadcasts import services
from apps.broadcasts.models import Post, Recipient
from apps.customers import bot
from apps.customers.models import BotUser
from apps.monitoring import telegram
from apps.reports import services as rsvc

TMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TMP)
class Posts(TestCase):
    def setUp(self):
        rsvc.sync_roles()
        U = get_user_model()
        self.admin = U.objects.create_user("a1", password="x")
        self.admin.groups.add(Group.objects.get(name="Admin"))
        self.client.force_login(self.admin)
        self.u1 = BotUser.objects.create(tg_id=11, full_name="Bir", district="Qarshi", status="approved")
        self.u2 = BotUser.objects.create(tg_id=12, full_name="Ikki", district="Koson", status="approved")
        self.u3 = BotUser.objects.create(tg_id=13, full_name="Uch", district="Qarshi", status="blocked")
        self.calls = []

        def send_msg(c, t, m=None):
            self.calls.append(("msg", c, t, m))
            if c == 12:
                raise telegram.TelegramError("Telegram xatosi 403: Forbidden: bot was blocked by the user")
            return {"result": {"message_id": 500 + c}}

        def send_media(c, kind, ref, caption="", markup=None, filename="", data=None):
            self.calls.append((kind, c, ref, markup))
            return {"result": {"message_id": 700 + c, "photo": [{"file_id": "small"}, {"file_id": "BIG"}]}}

        for p in (mock.patch.object(telegram, "send_message", side_effect=send_msg), mock.patch.object(telegram, "send_media", side_effect=send_media),
                  mock.patch.object(telegram, "delete_message")):
            p.start()
            self.addCleanup(p.stop)

    def create(self, **data):
        base = {"text": "Diqqat! Yangi topshiriq", "mode": "all", "confirm": "1"}
        base.update(data)
        return self.client.post(reverse("post_new"), base)

    def test_text_post_all_with_button_and_failure_report(self):
        r = self.create()
        self.assertEqual(r.status_code, 302)
        post = Post.objects.get()
        sent = {rec.user_id: rec for rec in post.recipients.all()}
        self.assertEqual(len(sent), 2)                                                       # bloklangan foydalanuvchi olmaydi
        self.assertEqual(sent[self.u1.pk].status, "sent")
        self.assertEqual(sent[self.u2.pk].status, "failed")
        self.assertIn("bloklagan", sent[self.u2.pk].error)
        markup = next(c for c in self.calls if c[1] == 11)[3]
        self.assertEqual(markup["inline_keyboard"][0][0]["callback_data"], f"ack:{sent[self.u1.pk].pk}")

    def test_without_confirm_button(self):
        self.client.post(reverse("post_new"), {"text": "Oddiy xabar", "mode": "all"})
        self.assertIsNone(next(c for c in self.calls if c[1] == 11)[3])
        self.assertFalse(Post.objects.get().confirm_required)

    def test_selected_and_district_audience(self):
        self.create(mode="selected", users=[str(self.u2.pk)])
        self.assertEqual([r.user_id for r in Post.objects.get().recipients.all()], [self.u2.pk])
        Post.objects.all().delete()
        self.create(mode="district", districts=["Qarshi"])
        self.assertEqual([r.user_id for r in Post.objects.get().recipients.all()], [self.u1.pk])

    def test_photo_post_uploads_once_then_reuses_file_id(self):
        self.u2.status = "approved"
        BotUser.objects.create(tg_id=14, full_name="To", status="approved")
        r = self.client.post(reverse("post_new"), {"text": "Rasm", "mode": "all", "confirm": "1", "file": SimpleUploadedFile("a.png", b"\x89PNG", content_type="image/png")})
        self.assertEqual(r.status_code, 302)
        post = Post.objects.get()
        self.assertEqual(post.kind, "photo")
        self.assertEqual(post.tg_file_id, "BIG")
        photo_calls = [c for c in self.calls if c[0] == "photo"]
        self.assertEqual(len(photo_calls), 3)
        self.assertIsNone(photo_calls[0][2])                                                 # birinchi marta fayl yuklandi
        self.assertEqual({c[2] for c in photo_calls[1:]}, {"BIG"})                           # keyingilariga file_id

    def test_validation(self):
        for data in ({"text": "", "mode": "all"}, {"text": "x" * 5000, "mode": "all"}, {"text": "x", "mode": "selected"}):
            self.client.post(reverse("post_new"), data)
        self.client.post(reverse("post_new"), {"text": "x" * 1100, "mode": "all", "file": SimpleUploadedFile("a.pdf", b"x")})
        self.assertEqual(Post.objects.count(), 0)

    def test_confirmation_flow_and_report(self):
        self.create()
        rec = Recipient.objects.get(user=self.u1)
        answers = []
        with mock.patch.object(telegram, "answer_callback", side_effect=lambda i, t="", alert=False: answers.append(t)), \
                mock.patch.object(telegram, "edit_markup") as em:
            bot.handle_update({"callback_query": {"id": "1", "from": {"id": 99}, "data": f"ack:{rec.pk}", "message": {"chat": {"id": 99}, "message_id": 5}}})
            rec.refresh_from_db()
            self.assertIsNone(rec.confirmed_at)                                              # begona odam tasdiqlay olmaydi
            bot.handle_update({"callback_query": {"id": "2", "from": {"id": 11}, "data": f"ack:{rec.pk}", "message": {"chat": {"id": 11}, "message_id": 5}}})
            bot.handle_update({"callback_query": {"id": "3", "from": {"id": 11}, "data": f"ack:{rec.pk}", "message": {"chat": {"id": 11}, "message_id": 5}}})
        rec.refresh_from_db()
        self.assertIsNotNone(rec.confirmed_at)
        self.assertIn("Tasdiqlandi", answers[1])
        self.assertIn("allaqachon", answers[2])
        self.assertEqual(em.call_count, 2)
        post = Post.objects.get()
        page = self.client.get(reverse("post_detail", args=[post.pk]))
        self.assertEqual(page.context["st"]["confirmed"], 1)
        self.assertEqual(self.client.get(reverse("post_export", args=[post.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("posts")).status_code, 200)

    def test_remind_only_unconfirmed_and_delete(self):
        BotUser.objects.create(tg_id=15, full_name="Besh", status="approved")
        self.create()
        post = Post.objects.get()
        Recipient.objects.filter(post=post, user=self.u1).update(confirmed_at="2026-01-01T00:00:00Z")
        self.calls.clear()
        self.client.post(reverse("post_remind", args=[post.pk]))
        self.assertEqual([c[1] for c in self.calls if c[0] == "msg"], [15])                   # faqat yetib borgan va tanishmagan
        self.client.post(reverse("post_delete", args=[post.pk]))
        post.refresh_from_db()
        self.assertIsNotNone(post.deleted_at)
        self.assertEqual(telegram.delete_message.call_count, 2)

    def test_permissions(self):
        viewer = get_user_model().objects.create_user("v1", password="x")
        viewer.groups.add(Group.objects.get(name="Kuzatuvchi"))
        self.client.force_login(viewer)
        for name in ("posts", "post_new"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
