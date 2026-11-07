"""Ilova oqimi: kirish, yuklash, saqlash, solishtirish, Excel, ruxsatlar."""
import shutil
import tempfile
from io import BytesIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from apps.reports import services
from apps.reports.models import MetricValue, Upload

FIX = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
User = get_user_model()
TMP = tempfile.mkdtemp()


def pdf(name):
    return SimpleUploadedFile(name, (FIX / name).read_bytes(), content_type="application/pdf")


@override_settings(MEDIA_ROOT=TMP)
class AppFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        services.sync_catalog()
        services.sync_roles()
        cls.admin = User.objects.create_superuser("admin", "a@x.uz", "Parol-12345")
        cls.operator = User.objects.create_user("op", password="Parol-12345")
        cls.operator.groups.add(Group.objects.get(name="Operator"))
        cls.viewer = User.objects.create_user("view", password="Parol-12345")
        cls.viewer.groups.add(Group.objects.get(name="Kuzatuvchi"))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def upload_and_confirm(self, *names, replace=False):
        r = self.client.post(reverse("upload"), {"files": [pdf(n) for n in names]})
        self.assertEqual(r.status_code, 200)
        tokens = list(self.client.session["pending_uploads"])
        data = {"token": tokens}
        if replace:
            data["replace"] = tokens
        return self.client.post(reverse("upload_confirm"), data, follow=True)

    # ------------------------------------------------------------ kirish va ruxsatlar
    def test_login_required(self):
        for url in ("dashboard", "compare", "trend", "upload", "archive", "users", "audit"):
            r = self.client.get(reverse(url))
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/kirish/", r["Location"])

    def test_role_permissions(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("compare")).status_code, 200)
        for url in ("upload", "users", "audit"):
            self.assertEqual(self.client.get(reverse(url)).status_code, 403, url)
        self.assertEqual(self.client.get(reverse("compare_export") + "?a=2026-11-04&b=2026-11-05").status_code, 403)

    def test_inactive_user_cannot_login(self):
        self.viewer.is_active = False
        self.viewer.save()
        self.assertFalse(self.client.login(username="view", password="Parol-12345"))

    # ------------------------------------------------------------ yuklash va saqlash
    def test_full_flow(self):
        self.client.force_login(self.operator)
        r = self.upload_and_confirm("hisobot_2026-11-04.pdf", "hisobot_2026-11-05.pdf")
        self.assertContains(r, "Saqlandi")
        self.assertEqual(Upload.objects.count(), 2)
        self.assertEqual(MetricValue.objects.filter(upload__report_date="2026-11-05", is_prev=True).count() > 0, True)

        # oldingi holat qiymatlari 04.11 o‘z PDF idan olingan — takrorlanmagan
        n = MetricValue.objects.filter(report_date="2026-11-04").count()
        self.assertEqual(n, len({(v.district_id, v.metric_id) for v in MetricValue.objects.filter(report_date="2026-11-04")}))

        r = self.client.get(reverse("compare") + "?a=2026-11-04&b=2026-11-05")
        self.assertContains(r, "Aloqasiz hisoblagichlar")
        self.assertContains(r, "26 627")     # 04.11 jami
        self.assertContains(r, "26 201")     # 05.11 jami
        self.assertContains(r, "↓ -426")

        r = self.client.get(reverse("dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "26 201")

        r = self.client.get(reverse("trend_data"), {"section": "aloqasiz", "metric": "nome_all", "d": ["jami"],
                                                    "from": "2026-11-01", "to": "2026-11-30"})
        data = r.json()
        # 03.11 — 04.11 PDF idagi «oldingi holat» ustunidan olingan nuqta
        self.assertEqual(data["dates"], ["2026-11-03", "2026-11-04", "2026-11-05"])
        self.assertEqual(data["series"][0]["data"], [26810.0, 26627.0, 26201.0])

    def test_duplicate_blocked_then_replaced(self):
        self.client.force_login(self.operator)
        self.upload_and_confirm("hisobot_2026-11-05.pdf")
        r = self.upload_and_confirm("hisobot_2026-11-05.pdf")           # operator almashtira olmaydi
        self.assertContains(r, "tkazib yuborildi")
        self.assertEqual(Upload.objects.count(), 1)
        self.client.force_login(self.admin)
        r = self.upload_and_confirm("hisobot_2026-11-05.pdf", replace=True)
        self.assertContains(r, "Saqlandi")
        self.assertEqual(Upload.objects.count(), 1)

    def test_prev_snapshot_does_not_overwrite_and_is_overridden(self):
        """05.11 avval yuklansa 04.11 «oldingi holat» sifatida to‘ladi; keyin 04.11 PDF i kelsa o‘zi ustun."""
        self.client.force_login(self.admin)
        self.upload_and_confirm("hisobot_2026-11-05.pdf")
        self.assertTrue(MetricValue.objects.filter(report_date="2026-11-04", is_prev=True).exists())
        self.upload_and_confirm("hisobot_2026-11-04.pdf")
        self.assertFalse(MetricValue.objects.filter(report_date="2026-11-04", is_prev=True).exists())
        self.assertTrue(MetricValue.objects.filter(report_date="2026-11-04", is_prev=False).exists())

    def test_rejects_non_pdf(self):
        self.client.force_login(self.operator)
        bad = SimpleUploadedFile("x.pdf", b"hello", content_type="application/pdf")
        r = self.client.post(reverse("upload"), {"files": [bad]})
        self.assertContains(r, "haqiqiy PDF")
        self.assertEqual(Upload.objects.count(), 0)

    # ------------------------------------------------------------ Excel
    def test_excel_export(self):
        self.client.force_login(self.admin)
        self.upload_and_confirm("hisobot_2026-11-04.pdf", "hisobot_2026-11-05.pdf")
        r = self.client.get(reverse("compare_export") + "?a=2026-11-04&b=2026-11-05")
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml", r["Content-Type"])
        wb = load_workbook(BytesIO(r.content))
        self.assertIn("Umumiy", wb.sheetnames)
        self.assertIn("Aloqasiz hisoblagichlar", wb.sheetnames)
        ws = wb["Aloqasiz hisoblagichlar"]
        jami = next(r for r in ws.iter_rows(min_row=1, max_row=15) if r[0].value == "Jami")
        flat = [c.value for c in jami]
        # PDF dizayni: B sanadagi qiymat + A->B farqi ko'rsatiladi
        self.assertIn(26201, flat)
        self.assertIn(-426, flat)
        self.assertTrue(ws.page_setup.orientation == "landscape")

    # ------------------------------------------------------------ foydalanuvchilar
    def test_user_management(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("users_new"), {
            "username": "yangi", "first_name": "Ali", "last_name": "Vali", "email": "", "is_active": "on",
            "role": "Operator", "extra_perms": ["manage_uploads"], "password1": "Kuchli-Parol-77", "password2": "Kuchli-Parol-77",
        })
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(username="yangi")
        self.assertTrue(u.has_perm("reports.upload_pdf"))
        self.assertTrue(u.has_perm("reports.manage_uploads"))
        self.assertFalse(u.has_perm("reports.manage_users"))
        # o‘chirib qo‘yish
        self.client.post(reverse("users_toggle", args=[u.pk]))
        u.refresh_from_db()
        self.assertFalse(u.is_active)
        # o‘zini o‘chira olmaydi
        self.client.post(reverse("users_toggle", args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
