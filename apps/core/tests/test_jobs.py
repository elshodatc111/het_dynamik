import os
import shutil
import tempfile
import time
from datetime import date

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.core import jobs
from apps.monitoring.tests.helpers import dcu_row, dcu_xlsx
from apps.reports import services as rsvc

TMP = tempfile.mkdtemp()


class _U:
    pk = 7


@override_settings(MEDIA_ROOT=TMP)
class JobCoreTests(SimpleTestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def test_progress_and_percent(self):
        job = jobs.create("t", _U(), [("a", 1), ("b", 3)])
        job.step(1)
        job.progress(0.5, "yarmi")
        job._flush(force=True)
        d = jobs.load(job.id)
        self.assertEqual((d["step"], d["label"]), (1, "yarmi"))
        self.assertAlmostEqual(d["pct"], 62.5)  # (1 + 3*0.5) / 4
        job.done("/x/")
        d = jobs.load(job.id)
        self.assertEqual((d["status"], d["pct"], d["redirect"]), ("done", 100.0, "/x/"))

    def test_owner_only_and_bad_id(self):
        job = jobs.create("t", _U(), [("a", 1)])
        self.assertIsNotNone(jobs.load(job.id, _U()))
        other = _U()
        other.pk = 8
        self.assertIsNone(jobs.load(job.id, other))
        self.assertIsNone(jobs.load("../../etc/passwd"))
        self.assertIsNone(jobs.load("z" * 32))

    def test_stale_job_becomes_error(self):
        job = jobs.create("t", _U(), [("a", 1)])
        p = jobs.jobs_dir() / f"{job.id}.json"
        old = time.time() - jobs.STALE_SECONDS - 5
        os.utime(p, (old, old))
        d = jobs.load(job.id)
        self.assertEqual(d["status"], "error")
        self.assertIn("to‘xtab", d["error"])
        self.assertIsNone(jobs.active("k"))

    def test_active_key(self):
        job = jobs.create("t", _U(), [("a", 1)], key="send:1")
        self.assertEqual(jobs.active("send:1")["id"], job.id)
        job.done()
        self.assertIsNone(jobs.active("send:1"))

    def test_heavy_lock_serialises_and_recovers_from_stale(self):
        job = jobs.create("t", _U(), [("a", 1)])
        lock = jobs.jobs_dir() / "heavy.lock"
        lock.write_text("dead")
        old = time.time() - jobs.STALE_SECONDS - 5
        os.utime(lock, (old, old))
        with jobs.heavy_lock(job):
            self.assertTrue(lock.exists())
        self.assertFalse(lock.exists())

    def test_failure_is_reported(self):
        job = jobs.create("t", _U(), [("a", 1)])

        def boom(j):
            raise jobs.JobError("Yomon fayl")

        jobs._execute(job, boom, (), {}, True, False)
        d = jobs.load(job.id)
        self.assertEqual((d["status"], d["error"]), ("error", "Yomon fayl"))

        job2 = jobs.create("t", _U(), [("a", 1)])
        jobs._execute(job2, lambda j: 1 / 0, (), {}, False, False)
        self.assertIn("ZeroDivisionError", jobs.load(job2.id)["error"])
        self.assertFalse((jobs.jobs_dir() / "heavy.lock").exists())


@override_settings(MEDIA_ROOT=TMP, JOBS_SYNC=True)
class AjaxFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        rsvc.sync_catalog()
        rsvc.sync_roles()
        cls.admin = User.objects.create_superuser("admin", "a@x.uz", "Parol-12345")
        cls.other = User.objects.create_superuser("other", "o@x.uz", "Parol-12345")

    def setUp(self):
        self.client.login(username="admin", password="Parol-12345")

    def test_ajax_upload_gives_job_and_status_json(self):
        f = SimpleUploadedFile("dcu.xlsx", dcu_xlsx([dcu_row(f"D{i}") for i in range(30)]))
        r = self.client.post(reverse("mon_upload"), {"files": [f]}, headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status_code, 200)
        j = r.json()
        st = self.client.get(j["status_url"], headers={"X-Requested-With": "XMLHttpRequest"}).json()
        self.assertEqual(st["status"], "done")
        self.assertEqual(st["pct"], 100.0)
        self.assertTrue(st["redirect"].startswith("/monitoring/yuklash/natija/"))
        self.assertGreaterEqual(len(st["steps"]), 3)
        page = self.client.get(st["redirect"])
        self.assertContains(page, "30 ta qator")
        # boshqa foydalanuvchi bu ishni ko‘ra olmaydi
        self.client.logout()
        self.client.login(username="other", password="Parol-12345")
        self.assertEqual(self.client.get(j["status_url"]).status_code, 404)

    def test_ajax_validation_error_is_json(self):
        r = self.client.post(reverse("mon_upload"), {}, headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Excel fayl", r.json()["error"])

    def test_status_requires_login(self):
        self.client.logout()
        r = self.client.get(reverse("job_status", args=["a" * 32]))
        self.assertEqual(r.status_code, 302)

    def test_save_job_progress_and_redirect(self):
        f = SimpleUploadedFile("dcu.xlsx", dcu_xlsx([dcu_row(f"D{i}") for i in range(10)]))
        r = self.client.post(reverse("mon_upload"), {"files": [f]}, headers={"X-Requested-With": "XMLHttpRequest"})
        self.client.get(self.client.get(r.json()["status_url"]).json()["redirect"])
        token = list(self.client.session["mon_pending"])
        r = self.client.post(reverse("mon_upload_confirm"), {"token": token, "report_date": date(2026, 11, 8).isoformat()},
                             headers={"X-Requested-With": "XMLHttpRequest"})
        st = self.client.get(r.json()["status_url"]).json()
        self.assertEqual(st["status"], "done", st)
        self.assertIn("Konsentratorlar", " ".join(s[0] for s in st["steps"]))
        self.assertNotIn("Hisoblagichlar", " ".join(s[0] for s in st["steps"]))
        self.assertTrue(st["redirect"].endswith("?saved=1"))
        self.assertEqual(self.client.get(st["redirect"], follow=True).status_code, 200)

    def test_stuck_send_log_is_reaped(self):
        from apps.monitoring import sender
        from apps.monitoring.models import SendLog, Snapshot

        snap = Snapshot.objects.create(report_date=date(2026, 11, 7))
        SendLog.objects.create(snapshot=snap, status="running")
        self.assertIsNone(sender.running_job(snap))
        self.assertEqual(SendLog.objects.get().status, "error")
