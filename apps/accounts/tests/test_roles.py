import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.reports import services as rsvc
from apps.reports.perms import PERMS


class Roles(TestCase):
    def setUp(self):
        rsvc.sync_roles()
        U = get_user_model()
        self.admin = U.objects.create_user("adm", password="x")
        self.admin.groups.add(Group.objects.get(name="Admin"))
        self.client.force_login(self.admin)

    def test_admin_has_every_permission_including_new_ones(self):
        for code in PERMS:
            self.assertTrue(self.admin.has_perm(f"reports.{code}"), code)
        self.assertEqual(len(PERMS), 25)

    def test_custom_role_limits_user_precisely(self):
        r = self.client.post(reverse("role_new"), {"name": "Dispetcher", "perms": ["view_location", "view_posts"]})
        self.assertEqual(r.status_code, 302)
        g = Group.objects.get(name="Dispetcher")
        U = get_user_model()
        self.client.post(reverse("users_new"), {"username": "disp", "password1": "Parol-12345", "password2": "Parol-12345", "is_active": "on", "role": "Dispetcher"})
        u = U.objects.get(username="disp")
        self.assertEqual(list(u.groups.all()), [g])
        self.assertTrue(u.has_perm("reports.view_location"))
        self.assertFalse(u.has_perm("reports.upload_location"))
        self.assertFalse(u.has_perm("reports.send_posts"))
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("location_data")).status_code, 200)
        self.assertEqual(self.client.get(reverse("location_upload")).status_code, 403)
        self.assertEqual(self.client.get(reverse("post_new")).status_code, 403)
        self.assertEqual(self.client.get(reverse("users")).status_code, 403)
        self.assertEqual(self.client.get(reverse("roles")).status_code, 403)
        self.assertEqual(self.client.get(reverse("system_status")).status_code, 403)
        self.assertEqual(self.client.get(reverse("site_settings")).status_code, 403)

    def test_extra_perms_and_effective_list(self):
        U = get_user_model()
        self.client.post(reverse("users_new"), {"username": "x1", "password1": "Parol-12345", "password2": "Parol-12345", "is_active": "on",
                                                "role": "", "extra_perms": ["view_status", "view_location_log"]})
        u = U.objects.get(username="x1")
        self.assertTrue(u.has_perm("reports.view_status"))
        self.assertFalse(u.has_perm("reports.manage_settings"))
        page = self.client.get(reverse("users_edit", args=[u.pk]))
        self.assertContains(page, "Hozir amalda bo‘lgan ruxsatlar")
        self.assertContains(page, 'value="view_status" checked')

    def test_builtin_roles_protected_and_custom_deletable(self):
        self.assertEqual(self.client.post(reverse("role_delete", args=[Group.objects.get(name="Operator").pk])).status_code, 302)
        self.assertTrue(Group.objects.filter(name="Operator").exists())
        self.assertEqual(self.client.get(reverse("role_edit", args=[Group.objects.get(name="Admin").pk])).status_code, 302)   # Admin tahrirlanmaydi
        self.client.post(reverse("role_edit", args=[Group.objects.get(name="Tahlilchi").pk]), {"name": "boshqa nom", "perms": ["view_dashboard"]})
        g = Group.objects.get(name="Tahlilchi")
        self.assertEqual(list(g.permissions.values_list("codename", flat=True)), ["view_dashboard"])
        self.client.post(reverse("role_new"), {"name": "Vaqtincha", "perms": []})
        self.client.post(reverse("role_delete", args=[Group.objects.get(name="Vaqtincha").pk]))
        self.assertFalse(Group.objects.filter(name="Vaqtincha").exists())

    def test_cannot_lock_out_last_admin(self):
        self.client.post(reverse("users_toggle", args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)


class Pwa(TestCase):
    def test_manifest_sw_offline_public(self):
        m = self.client.get(reverse("pwa_manifest"))
        d = json.loads(m.content)
        self.assertEqual((d["display"], d["start_url"], d["scope"]), ("standalone", "/", "/"))
        self.assertTrue(any(i["sizes"] == "512x512" and i["purpose"] == "maskable" for i in d["icons"]))
        sw = self.client.get(reverse("pwa_sw"))
        self.assertEqual(sw["Service-Worker-Allowed"], "/")
        self.assertIn("fetch", sw.content.decode())
        self.assertEqual(self.client.get(reverse("pwa_offline")).status_code, 200)

    def test_pages_link_manifest_and_install_bar(self):
        r = self.client.get(reverse("login"))
        self.assertContains(r, 'rel="manifest"')
        self.assertContains(r, "installBar()")
