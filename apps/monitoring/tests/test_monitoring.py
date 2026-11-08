import shutil
import tempfile
from apps.core.models import get_site
from datetime import date, datetime
from io import BytesIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from apps.monitoring import exports, parsing, sender, services, telegram
from apps.monitoring.models import DcuRecord, MeterRecord, MonitorSetting, SendLog, Snapshot
from apps.reports import services as rsvc

from .helpers import DCU_HEADER, METER_HEADER, break_dimension, dcu_row, dcu_xlsx, meter_row, meters_xlsx

User = get_user_model()
TMP = tempfile.mkdtemp()
D1, D2, D3 = date(2026, 11, 6), date(2026, 11, 7), date(2026, 11, 8)


def xl(name, data):
    return SimpleUploadedFile(name, data, content_type="application/octet-stream")


class EdgeTests(SimpleTestCase):
    def test_bucket_index_default_dcu(self):
        e = [2, 7, 30]
        got = {d: services.bucket_index(d, e) for d in (0, 1, 2, 7, 8, 30, 31, 400)}
        self.assertEqual(got, {0: None, 1: None, 2: 0, 7: 0, 8: 1, 30: 1, 31: 2, 400: 2})

    def test_bucket_index_meters(self):
        e = [2, 7, 30, 90]
        got = {d: services.bucket_index(d, e) for d in (2, 7, 8, 30, 31, 90, 91)}
        self.assertEqual(got, {2: 0, 7: 0, 8: 1, 30: 1, 31: 2, 90: 2, 91: 3})

    def test_labels(self):
        self.assertEqual(services.bucket_labels([2, 7, 30]), ["2–7 kun", "7–30 kun", "30+ kun"])

    def test_parse_edges_validation(self):
        self.assertEqual(services.parse_edges("3, 10 ,40"), [3, 10, 40])
        for bad in ("5", "7, 2", "2, 2, 5", "0, 5", "", "1,2,3,4,5,6,7,8,9"):
            with self.assertRaises(ValueError, msg=bad):
                services.parse_edges(bad)

    def test_classify_mixed_languages(self):
        kw = services.keyword_map(MonitorSetting())
        cases = {"Бытовые": "aholi", "Промышленные": "yuridik", "Индикатор баланса": "balans", "Household": "aholi",
                 "INDUSTRIAL": "yuridik", "Balance indicator": "balans", "Noma’lum": "boshqa", "": "boshqa"}
        for raw, want in cases.items():
            self.assertEqual(services.classify(raw, kw), want, raw)

    def test_date_hint(self):
        d, dt = parsing.date_hint_from_name("Online_Device_Monitroing_Report_08112026_154737.xlsx")
        self.assertEqual((d, dt), (date(2026, 11, 8), datetime(2026, 11, 8, 15, 47, 37)))
        self.assertEqual(parsing.date_hint_from_name("NoMeterData_1.xlsx"), (None, None))
        self.assertEqual(parsing.date_hint_from_name("report 2026-11-05.xlsx")[0], date(2026, 11, 5))


@override_settings(MEDIA_ROOT=TMP, JOBS_SYNC=True)
class ParsingTests(TestCase):
    def parse(self, data, name="x.xlsx"):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.xlsx"
            p.write_bytes(data)
            return parsing.parse_file(p, name, ["offline", "офлайн"])

    def test_dcu_filters_online_dup_and_missing(self):
        data = dcu_xlsx([dcu_row("1"), dcu_row("2", status="Online"), dcu_row("1"), dcu_row("", status="Offline"), dcu_row("3")])
        r = self.parse(data)
        self.assertTrue(r.ok)
        self.assertEqual(r.kind, "dcu")
        self.assertEqual([x["device_no"] for x in r.rows], ["1", "3"])
        self.assertEqual(r.total_rows, 5)
        self.assertEqual(len(r.warnings), 3)

    def test_meters_bad_days_skipped(self):
        r = self.parse(meters_xlsx([meter_row(1, 5), meter_row(2, "x"), meter_row(3, 40.0)]))
        self.assertEqual(r.kind, "meters")
        self.assertEqual([x["days"] for x in r.rows], [5, 40])
        self.assertEqual(r.rows[0]["account"], "0000001")  # bosh nollar saqlanadi

    def test_english_headers(self):
        hdr = ["ESP", "Substation", "Feeder", "Transformer number", "Consumer account", "Consumer type", "Consumer name",
               "Meter number", "Communication type", "Manager name", "Manager phone", "Uncollected period (days)",
               "Uncollected period (time)", "GPS", "Installation address"]
        row = ["Koson TETK", "S", "F", "5", "0001", "Household", "N", "M1", "PLC", "m", "p", 9, "01:00:00", ",", "addr"]
        r = self.parse(meters_xlsx([row], header=hdr))
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.rows[0]["days"], 9)

    def test_wrong_declared_dimension_still_reads_all_rows(self):
        r = self.parse(break_dimension(dcu_xlsx([dcu_row(str(i)) for i in range(50)])))
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(len(r.rows), 50)
        r = self.parse(break_dimension(meters_xlsx([meter_row(i, 5) for i in range(1, 80)])))
        self.assertEqual(len(r.rows), 79)

    def test_unknown_and_wrong_files(self):
        self.assertFalse(self.parse(dcu_xlsx([], header=["a", "b"]), "x.xlsx").ok)
        self.assertIn("xlsx", self.parse(b"hello", "x.csv").errors[0])
        self.assertIn("haqiqiy", self.parse(b"hello", "x.xlsx").errors[0])


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
                   MEDIA_ROOT=TMP, JOBS_SYNC=True, MONITORING_SYNC_SEND=True, TELEGRAM_BOT_TOKEN="123:TEST", TELEGRAM_CHAT_ID="")
class FlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        rsvc.sync_catalog()
        rsvc.sync_roles()
        cls.admin = User.objects.create_superuser("admin", "a@x.uz", "Parol-12345")
        cls.op = User.objects.create_user("op", password="Parol-12345")
        cls.op.groups.add(Group.objects.get(name="Operator"))
        cls.viewer = User.objects.create_user("view", password="Parol-12345")
        cls.viewer.groups.add(Group.objects.get(name="Kuzatuvchi"))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    # --------------------------------------------------------------
    def upload(self, d, dcu=None, meters=None, replace=False, name_dcu="dcu.xlsx", name_mtr="NoMeterData.xlsx"):
        files = []
        if dcu is not None:
            files.append(xl(name_dcu, dcu))
        if meters is not None:
            files.append(xl(name_mtr, meters))
        r = self.client.post(reverse("mon_upload"), {"files": files}, follow=True)
        self.assertEqual(r.status_code, 200, r.content[:300])
        tokens = list(self.client.session["mon_pending"])
        data = {"token": tokens, "report_date": d.isoformat()}
        if replace:
            data["replace"] = "1"
        return self.client.post(reverse("mon_upload_confirm"), data, follow=True)

    def day1(self):
        dcu = dcu_xlsx([dcu_row("A", at="05/11/2026 10:00:00"), dcu_row("B", at="05/11/2026 10:00:00"),
                        dcu_row("C", at="05/11/2026 10:00:00", esp="Ko‘kdala TETK")])
        mtr = meters_xlsx([meter_row(1, 2), meter_row(2, 7, "Промышленные"), meter_row(3, 8, "Индикатор баланса"),
                           meter_row(4, 30, "Промышленные"), meter_row(5, 31), meter_row(6, 90, "Бытовые", esp="Ko‘kdala TETK"),
                           meter_row(7, 91, "Промышленные"), meter_row(8, 1), meter_row(9, 400, "Strange")])
        return dcu, mtr

    def test_summary_numbers_and_buckets(self):
        self.client.force_login(self.admin)
        dcu, mtr = self.day1()
        r = self.upload(D3, dcu, mtr)
        self.assertContains(r, "saqlandi")
        snap = Snapshot.objects.get(report_date=D3)
        s = snap.summary
        self.assertEqual(s["dcu"]["offline_total"], 3)
        self.assertEqual(s["dcu"]["over_min"], 3)
        self.assertEqual(s["meters"]["below_min"], 1)  # 1 kunlik tushmaydi
        self.assertEqual(s["meters"]["over_min"], 8)
        self.assertEqual(s["meters"]["bucket_totals"], [2, 2, 2, 2])  # 2-7: 2,7 | 7-30: 8,30 | 30-90: 31,90 | 90+: 91,400
        self.assertEqual(s["meters"]["groups_total"], {"yuridik": 3, "aholi": 3, "balans": 1, "boshqa": 1})
        self.assertEqual(s["meters"]["by_esp"]["Ko‘kdala TETK"], [0, 0, 1, 0])

    def test_dcu_under_two_days_dropped_and_chain(self):
        self.client.force_login(self.admin)
        # 1-kun: A va B birinchi marta ko‘rinadi, vaqt fayldagi sanadan 3 kun oldin (A) / bugun (B)
        d1 = dcu_xlsx([dcu_row("A", at="03/11/2026 09:00:00"), dcu_row("B", at="06/11/2026 12:00:00")])
        self.upload(D1, d1)
        s1 = Snapshot.objects.get(report_date=D1)
        recs = {r.device_no: r for r in s1.dcus.all()}
        self.assertEqual(recs["A"].days, 3)       # 03.11 -> 06.11
        self.assertEqual(recs["B"].days, 0)       # bugun offline bo‘lgan
        self.assertEqual(s1.summary["dcu"]["over_min"], 1)
        self.assertEqual(s1.summary["dcu"]["below_min"], 1)
        # 2-kun: A, B davom etadi, C yangi
        d2 = dcu_xlsx([dcu_row("A", at="07/11/2026 10:00:00"), dcu_row("B", at="07/11/2026 10:00:00"), dcu_row("C", at="07/11/2026 10:00:00")])
        self.upload(D2, d2)
        s2 = Snapshot.objects.get(report_date=D2)
        r2 = {r.device_no: r.days for r in s2.dcus.all()}
        self.assertEqual(r2, {"A": 4, "B": 1, "C": 0})
        # 3-kun: B tiklandi (yo‘q), A davom, C davom, D yangi, B ertasi yana offline bo‘lsa — yangi hisob
        d3 = dcu_xlsx([dcu_row("A", at="08/11/2026 10:00:00"), dcu_row("C", at="08/11/2026 10:00:00"), dcu_row("D", at="08/11/2026 10:00:00")])
        self.upload(D3, d3)
        s3 = Snapshot.objects.get(report_date=D3)
        r3 = {r.device_no: r.days for r in s3.dcus.all()}
        self.assertEqual(r3, {"A": 5, "C": 1, "D": 0})
        self.assertEqual(s3.summary["dcu"]["new"], 1)
        self.assertEqual(s3.summary["dcu"]["recovered"], 1)
        self.assertEqual(s3.summary["dcu"]["buckets"], [1, 0, 0])  # faqat A (5 kun)

    def test_replace_middle_day_rebuilds_chain(self):
        self.client.force_login(self.admin)
        self.upload(D1, dcu_xlsx([dcu_row("A", at="06/11/2026 10:00:00")]))
        self.upload(D3, dcu_xlsx([dcu_row("A", at="08/11/2026 10:00:00")]))
        self.assertEqual(Snapshot.objects.get(report_date=D3).dcus.get().days, 2)
        # oradagi kun qo‘shiladi, unda A yo‘q (tiklangan) -> D3 da A yangi hisoblanadi
        self.upload(D2, dcu_xlsx([dcu_row("Z", at="07/11/2026 10:00:00")]))
        self.assertEqual(Snapshot.objects.get(report_date=D3).dcus.get().days, 0)

    def test_duplicate_upload_blocked_without_permission(self):
        self.client.force_login(self.op)
        dcu, mtr = self.day1()
        self.upload(D3, dcu, mtr)
        r = self.upload(D3, dcu, replace=True)  # operatorda almashtirish ruxsati yo‘q
        self.assertContains(r, "allaqachon yuklangan")
        self.client.force_login(self.admin)
        r = self.upload(D3, dcu, replace=True)
        self.assertContains(r, "saqlandi")
        self.assertEqual(Snapshot.objects.filter(report_date=D3).count(), 1)
        self.assertEqual(DcuRecord.objects.filter(snapshot__report_date=D3).count(), 3)

    def test_excel_exports_content(self):
        self.client.force_login(self.admin)
        dcu, mtr = self.day1()
        self.upload(D3, dcu, mtr)
        snap = Snapshot.objects.get(report_date=D3)
        wb = load_workbook(BytesIO(exports.svod_xlsx(snap)))
        self.assertEqual(wb.sheetnames, ["Svod"])
        vals = [c.value for row in wb["Svod"].iter_rows() for c in row if c.value is not None]
        for word in ("ESP", "Offline konsentratorlar", "Aloqadan chiqib ketgan hisoblagichlar", "Axoli", "Yuridik", "Balans", "JAMI"):
            self.assertIn(word, vals)
        # JAMI qatori: 3 konsentrator, 8 hisoblagich
        jami = next(r for r in wb["Svod"].iter_rows(values_only=True) if r[0] == "JAMI")
        self.assertEqual(jami[1], 3)
        self.assertEqual(jami[5], 8)

        wb = load_workbook(BytesIO(exports.dcu_list_xlsx(snap)))
        self.assertEqual(len(wb.sheetnames), 4)
        self.assertEqual(wb.worksheets[0].max_row, 4)  # sarlavha + 3

        data, name = exports.meters_bucket_xlsx(snap, 3)  # 90+ : 91 (yuridik), 400 (boshqa)
        self.assertIn("90", name)
        wb = load_workbook(BytesIO(data))
        self.assertEqual([ws.title for ws in wb.worksheets], ["Hammasi (2)", "Yuridik (1)", "Aholi (0)", "Balans (0)", "Boshqa (1)"])
        self.assertEqual(wb["Yuridik (1)"]["F2"].value, "0000007")  # hisob raqami matn bo‘lib qoladi

    def test_download_view_and_permissions(self):
        self.client.force_login(self.admin)
        dcu, mtr = self.day1()
        self.upload(D3, dcu, mtr)
        snap = Snapshot.objects.get(report_date=D3)
        r = self.client.get(reverse("mon_download", args=[snap.pk, "m1"]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(reverse("mon_download", args=[snap.pk, "m9"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("mon_download", args=[snap.pk, "zzz"])).status_code, 404)
        # kuzatuvchi: ko‘radi, lekin yuklay/yubora/sozlay olmaydi
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("mon_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("mon_snapshot", args=[snap.pk])).status_code, 200)
        for name, args in (("mon_upload", []), ("mon_settings", []), ("mon_send", [snap.pk]), ("mon_download", [snap.pk, "svod"])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403, name)

    def test_anonymous_redirected(self):
        for name in ("mon_dashboard", "mon_upload", "mon_archive", "mon_settings"):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)

    def test_nav_groups(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("mon_dashboard"))
        self.assertEqual([g["title"] for g in r.context["nav_groups"]], ["Dinamika", "Offline monitoring", "Customer", "Location", "Postlar", "Tizim"])
        self.assertEqual([i["url"] for i in r.context["nav_top"]], ["dashboard"])
        active = [(g["title"], i["url"]) for g in r.context["nav_groups"] for i in g["items"] if i["active"]]
        self.assertEqual(active, [("Offline monitoring", "mon_dashboard")])
        self.assertEqual([g["has_active"] for g in r.context["nav_groups"]], [False, True, False, False, False, False])
        self.assertContains(r, 'class="nav-toggle')  # bo‘limlar yig‘iladi
        r = self.client.get(reverse("dynamics"))
        self.assertEqual([(g["title"], i["url"]) for g in r.context["nav_groups"] for i in g["items"] if i["active"]], [("Dinamika", "dynamics")])
        r = self.client.get(reverse("dashboard"))
        self.assertEqual([i["url"] for i in r.context["nav_top"] if i["active"]], ["dashboard"])
        self.client.force_login(self.viewer)
        r = self.client.get(reverse("dashboard"))
        self.assertEqual([g["title"] for g in r.context["nav_groups"]], ["Dinamika", "Offline monitoring"])

    def test_home_is_empty(self):
        self.client.force_login(self.admin)
        dcu, mtr = self.day1()
        self.upload(D3, dcu, mtr)
        r = self.client.get(reverse("dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Bosh sahifa tayyorlanmoqda")
        self.assertNotContains(r, "Offline konsentratorlar")
        self.assertNotContains(r, "Aloqasiz hisoblagichlar")

    # --------------------------------------------------------------
    def test_send_requires_confirm_and_sends_files(self):
        self.client.force_login(self.op)
        dcu, mtr = self.day1()
        self.upload(D3, dcu, mtr)
        snap = Snapshot.objects.get(report_date=D3)
        cfg = services.get_settings()
        cfg.telegram_chat_id = "-1001234567890"
        cfg.save()
        calls = []

        def fake(method, fields, files=None, timeout=90):
            if method == "getChat":
                return {"ok": True, "result": {"title": "HETK Offline guruhi"}}
            calls.append((method, fields, ((files or {}).get("document") or (files or {}).get("photo") or (None,))[0]))
            return {"ok": True}

        with mock.patch.object(telegram, "api_call", side_effect=fake):
            # tasdiqsiz — yuborilmaydi
            r = self.client.post(reverse("mon_send", args=[snap.pk]), {"kind": ["dcu"]}, follow=True)
            self.assertContains(r, "tasdiqlang")
            self.assertEqual(calls, [])
            r = self.client.get(reverse("mon_send", args=[snap.pk]))
            self.assertContains(r, "svod.png")
            self.assertContains(r, "HETK Offline guruhi")   # guruh nomi avtomatik olinadi
            self.assertContains(r, "Aloqaga chiqmay qolgan hisoblagichlar")
            # tasdiq bilan
            r = self.client.post(reverse("mon_send", args=[snap.pk]), {"kind": ["svod", "dcu", "m0", "m3"], "confirm": "1"}, follow=True)
            self.assertEqual(r.status_code, 200)
        self.assertEqual([c[0] for c in calls], ["sendPhoto", "sendDocument", "sendDocument", "sendDocument"])
        self.assertTrue(all(c[1]["chat_id"] == "-1001234567890" for c in calls))
        names = [c[2] for c in calls[1:]]
        self.assertTrue(calls[0][2].startswith("svod_2026-11-08") and calls[0][2].endswith(".png"))
        self.assertFalse(any("svod" in (n or "") for n in names))  # svod Excel yuborilmaydi
        self.assertTrue(all(n.startswith(("konsentratorlar", "aloqaga_chiqmay_qolgan_hisoblagichlar")) for n in names))
        log = SendLog.objects.get()
        self.assertEqual(log.status, "ok")

    def test_send_error_is_logged_without_token(self):
        self.client.force_login(self.admin)
        dcu, _ = self.day1()
        self.upload(D3, dcu)
        snap = Snapshot.objects.get(report_date=D3)
        cfg = services.get_settings()
        cfg.telegram_chat_id = "-1001234567890"
        cfg.save()
        with mock.patch.object(telegram, "api_call", side_effect=telegram.TelegramError("Telegram xatosi 400: chat not found")):
            self.client.post(reverse("mon_send", args=[snap.pk]), {"kind": ["dcu"], "confirm": "1"})
        log = SendLog.objects.get()
        self.assertEqual(log.status, "error")
        self.assertIn("chat not found", log.detail)
        self.assertNotIn("TEST", log.detail)

    def test_send_blocked_without_chat_or_token(self):
        self.client.force_login(self.admin)
        dcu, _ = self.day1()
        self.upload(D3, dcu)
        snap = Snapshot.objects.get(report_date=D3)
        r = self.client.post(reverse("mon_send", args=[snap.pk]), {"kind": ["dcu"], "confirm": "1"}, follow=True)
        self.assertContains(r, "guruh ID")
        self.assertEqual(SendLog.objects.count(), 0)
        self.assertTrue(telegram.valid_chat_id("-1001234567890"))
        self.assertTrue(telegram.valid_chat_id("@guruh_nomi"))
        self.assertFalse(telegram.valid_chat_id("abc"))

    # --------------------------------------------------------------
    def test_settings_change_recomputes(self):
        self.client.force_login(self.admin)
        dcu, mtr = self.day1()
        self.upload(D3, dcu, mtr)
        snap = Snapshot.objects.get(report_date=D3)
        self.assertEqual(snap.summary["meters"]["groups_total"]["boshqa"], 1)
        cfg = services.get_settings()
        post = {"dcu_edges": "3, 10, 20", "meter_edges": "5, 50", "kw_yuridik": cfg.kw_yuridik, "kw_aholi": cfg.kw_aholi + ", strange",
                "kw_balans": cfg.kw_balans, "offline_keywords": cfg.offline_keywords, "use_file_time": "1",
                "telegram_chat_id": "-100555555", "detail_keep_days": "30"}
        r = self.client.post(reverse("mon_settings"), post, follow=True)
        self.assertContains(r, "Sozlamalar saqlandi")
        snap.refresh_from_db()
        s = snap.summary
        self.assertEqual(s["meter_edges"], [5, 50])
        self.assertEqual(s["meters"]["groups_total"]["boshqa"], 0)  # «Strange» endi aholi
        self.assertEqual(s["meters"]["bucket_totals"], [4, 3])    # 5..50: 7,8,30,31 | >50: 90,91,400
        # bad input is rejected and nothing changes
        bad = dict(post, meter_edges="9, 3")
        r = self.client.post(reverse("mon_settings"), bad)
        self.assertContains(r, "o‘sish tartibida")
        self.assertEqual(services.get_settings().meter_edges, "5, 50")

    def test_purge_keeps_summary(self):
        self.client.force_login(self.admin)
        dcu, mtr = self.day1()
        self.upload(date(2026, 1, 5), dcu, mtr)  # eski sana: tozalanadi
        snap = Snapshot.objects.get(report_date=date(2026, 1, 5))
        self.assertTrue(snap.meter_detail_purged)
        self.assertEqual(MeterRecord.objects.filter(snapshot=snap).count(), 0)
        self.assertEqual(snap.summary["meters"]["over_min"], 8)
        self.assertEqual(self.client.get(reverse("mon_snapshot", args=[snap.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("mon_download", args=[snap.pk, "m0"])).status_code, 404)

    def test_pages_render(self):
        self.client.force_login(self.admin)
        dcu, mtr = self.day1()
        self.upload(D2, dcu, mtr)
        self.upload(D3, dcu, mtr)
        snap = Snapshot.objects.get(report_date=D3)
        for name, args in (("mon_dashboard", []), ("mon_archive", []), ("mon_snapshot", [snap.pk]), ("mon_dcu", [snap.pk]),
                           ("mon_meters", [snap.pk]), ("mon_send", [snap.pk]), ("mon_settings", []), ("mon_upload", [])):
            r = self.client.get(reverse(name, args=args))
            self.assertEqual(r.status_code, 200, name)
        r = self.client.get(reverse("mon_meters", args=[snap.pk]) + "?b=3&g=yuridik&q=TEST")
        self.assertContains(r, "0000007")
        r = self.client.get(reverse("mon_dcu", args=[snap.pk]) + "?q=Ko‘kdala")
        self.assertContains(r, "Ko‘kdala")
        # kechagiga nisbatan farq sahifada
        r = self.client.get(reverse("mon_dashboard"))
        self.assertContains(r, "07.11")

    def test_delete_requires_manage(self):
        self.client.force_login(self.admin)
        dcu, _ = self.day1()
        self.upload(D3, dcu)
        snap = Snapshot.objects.get(report_date=D3)
        self.client.force_login(self.op)
        self.assertEqual(self.client.post(reverse("mon_delete", args=[snap.pk])).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(reverse("mon_delete", args=[snap.pk]))
        self.assertFalse(Snapshot.objects.exists())

    # -------------------------------------------------------------- token dasturdan kiritiladi
    def settings_post(self, **extra):
        cfg = services.get_settings()
        post = {"dcu_edges": cfg.dcu_edges, "meter_edges": cfg.meter_edges, "kw_yuridik": cfg.kw_yuridik, "kw_aholi": cfg.kw_aholi,
                "kw_balans": cfg.kw_balans, "offline_keywords": cfg.offline_keywords, "use_file_time": "1",
                "telegram_chat_id": "-100777", "detail_keep_days": "14"}
        post.update(extra)
        return self.client.post(reverse("mon_settings"), post, follow=True)

    def token_post(self, **extra):
        return self.client.post(reverse("site_settings"), extra, follow=True)

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_token_entered_in_app_is_encrypted_and_used(self):
        self.client.force_login(self.admin)
        tok = "123456789:AAH" + "x" * 32 + "WXYZ"
        self.assertFalse(telegram.is_configured())
        with mock.patch.object(telegram, "api_call", return_value={"ok": True, "result": {"username": "hetk_bot"}}) as m:
            r = self.token_post(telegram_token=tok)
        self.assertContains(r, "@hetk_bot")
        self.assertEqual(m.call_args_list[0][0][0], "getMe")
        stored = get_site().telegram_token
        self.assertTrue(stored)
        self.assertNotIn("AAH", stored)
        self.assertNotIn(tok, stored)
        self.assertEqual(telegram.token(), tok)
        self.assertTrue(telegram.is_configured())
        # sahifada token qayta ko‘rsatilmaydi, faqat niqoblangan ko‘rinish
        page = self.client.get(reverse("site_settings")).content.decode()
        self.assertNotIn(tok, page)
        self.assertIn("123456789:••••WXYZ", page)
        # bo‘sh qoldirilsa o‘zgarmaydi
        self.token_post(telegram_token="")
        self.assertEqual(telegram.token(), tok)
        # audit jurnaliga token tushmaydi
        from apps.reports.models import AuditLog
        self.assertFalse(AuditLog.objects.filter(detail__contains="AAH").exists())
        # o‘chirish
        self.token_post(clear_token="1")
        self.assertFalse(telegram.is_configured())

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_invalid_token_rejected(self):
        self.client.force_login(self.admin)
        r = self.token_post(telegram_token="bu-token-emas")
        self.assertContains(r, "noto‘g‘ri ko‘rinishda")
        self.assertEqual(get_site().telegram_token, "")

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_token_saved_even_if_telegram_unreachable(self):
        self.client.force_login(self.admin)
        tok = "123456789:AAH" + "y" * 32 + "ABCD"
        with mock.patch.object(telegram, "api_call", side_effect=telegram.TelegramError("Telegramga ulanib bo‘lmadi")):
            r = self.token_post(telegram_token=tok)
        self.assertContains(r, "Token saqlandi, lekin Telegram tasdiqlamadi")
        self.assertEqual(telegram.token(), tok)

    @override_settings(TELEGRAM_BOT_TOKEN="999999:ENVTOKEN" + "z" * 30)
    def test_env_token_is_fallback_and_db_wins(self):
        self.assertEqual(telegram.token_source(), "env")
        cfg = get_site()
        db_tok = "123456789:AAH" + "q" * 32 + "1111"
        cfg.telegram_token = telegram.encrypt_token(db_tok)
        cfg.save()
        self.assertEqual(telegram.token_source(), "db")
        self.assertEqual(telegram.token(), db_tok)

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_token_unreadable_after_secret_key_change(self):
        cfg = get_site()
        cfg.telegram_token = telegram.encrypt_token("123456789:AAH" + "w" * 36)
        cfg.save()
        with override_settings(SECRET_KEY="boshqa-kalit"):
            self.assertFalse(telegram.is_configured())
            self.assertEqual(telegram.token_source(), "")

    def test_operator_cannot_change_token(self):
        self.client.force_login(self.op)
        self.assertEqual(self.client.post(reverse("site_settings"), {"telegram_token": "123456789:" + "a" * 35}).status_code, 403)
        self.assertEqual(self.client.get(reverse("site_settings")).status_code, 403)


    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_legacy_token_still_works_and_moves(self):
        """Eski joyda (MonitorSetting) saqlangan token ishlayveradi."""
        tok = "123456789:AAH" + "l" * 32 + "LEGC"
        cfg = services.get_settings()
        cfg.telegram_token = telegram.encrypt_token(tok)
        cfg.save()
        self.assertEqual(telegram.token(), tok)

    def test_nav_has_both_settings(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("mon_dashboard")).content.decode()
        self.assertIn("Monitoring sozlamalari", page)
        self.assertIn(reverse("site_settings"), page)
        self.assertEqual(self.client.get(reverse("site_settings")).status_code, 200)

    def test_png_and_no_svod_in_send_list(self):
        from apps.monitoring import image
        self.client.force_login(self.admin)
        dcu, meters = self.day1()
        self.upload(D3, dcu, meters)
        snap = Snapshot.objects.get(report_date=D3)
        data = image.svod_png(snap)
        self.assertTrue(data.startswith(b"\x89PNG"))
        self.assertNotIn("svod", [f["kind"] for f in exports.available_files(snap)])
        r = self.client.get(reverse("mon_svod_image", args=[snap.pk]))
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/png"))

    def test_meter_excel_has_all_sheet_first(self):
        self.client.force_login(self.admin)
        dcu, meters = self.day1()
        self.upload(D3, dcu, meters)
        snap = Snapshot.objects.get(report_date=D3)
        data, _ = exports.meters_bucket_xlsx(snap, 0)
        wb = load_workbook(BytesIO(data))
        self.assertTrue(wb.sheetnames[0].startswith("Hammasi"))

    # ------------------------------------------------------------------ svod jadvali, yacheyka ro‘yxatlari, solishtirish
    def test_grid_values_and_cell_links(self):
        self.client.force_login(self.admin)
        dcu, mtr = self.day1()
        self.upload(D3, dcu, mtr)
        snap = Snapshot.objects.get(report_date=D3)
        g = services.grid_for(snap)
        key = lambda c: (c["sec"], c["b"], c["g"])  # noqa: E731
        idx = {key(c): i for i, c in enumerate(g["cols"])}
        row = {r["esp"]: r["vals"] for r in g["rows"]}
        tot = g["total"]["vals"]
        self.assertEqual(tot[idx[("dcu", None, None)]], 3)
        self.assertEqual(tot[idx[("m", None, None)]], 8)
        self.assertEqual(tot[idx[("m", 0, None)]], 2)
        # 90+ (indeks 3): yuridik 91 kun, boshqa 400 kun — boshqa ustuni paydo bo‘ladi
        self.assertIn("boshqa", g["gkeys"])
        self.assertEqual(tot[idx[("m", 3, "yuridik")]], 1)
        self.assertEqual(tot[idx[("m", 3, "boshqa")]], 1)
        kk = row["Ko‘kdala TETK"]
        self.assertEqual(kk[idx[("m", 2, "aholi")]], 1)      # 90 kun, Бытовые -> 30-90 oralig‘i
        self.assertEqual(kk[idx[("dcu", None, None)]], 1)
        # ESP bo‘yicha yig‘indi JAMI qatoriga teng
        for j in range(len(g["cols"])):
            self.assertEqual(sum(r["vals"][j] for r in g["rows"]), tot[j])

        r = self.client.get(reverse("mon_dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Aloqadan chiqib ketgan hisoblagichlar")
        links = [c["url"] for c in r.context["g"]["total_c"]["cells"] if c["url"]]
        self.assertTrue(all(u.startswith(f"/monitoring/{snap.pk}/") for u in links))
        # nol yacheykada havola yo‘q
        zero = [c for c in r.context["g"]["total_c"]["cells"] if c["v"] == 0]
        self.assertTrue(zero and all(c["url"] is None for c in zero))

    def test_cell_list_filters_and_excel(self):
        self.client.force_login(self.admin)
        dcu, mtr = self.day1()
        self.upload(D3, dcu, mtr)
        snap = Snapshot.objects.get(report_date=D3)
        url = reverse("mon_meters", args=[snap.pk])
        # Ko‘kdala, 30–90 kun (b=2), aholi
        r = self.client.get(url, {"esp": "Ko‘kdala TETK", "b": 2, "g": "aholi"})
        self.assertEqual(r.context["page"].paginator.count, 1)
        self.assertContains(r, "Ko‘kdala TETK")
        r = self.client.get(url, {"esp": "Ko‘kdala TETK", "b": 0})
        self.assertEqual(r.context["page"].paginator.count, 0)
        r = self.client.get(url, {"b": 3, "g": "yuridik"})
        self.assertEqual(r.context["page"].paginator.count, 1)
        # Excel: aynan shu ro‘yxat
        r = self.client.get(url, {"esp": "Ko‘kdala TETK", "b": 2, "g": "aholi", "xlsx": 1})
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml", r["Content-Type"])
        wb = load_workbook(BytesIO(r.content))
        self.assertEqual(wb.active.max_row, 2)  # sarlavha + 1
        self.assertIn("filename=", r["Content-Disposition"])
        # barcha hisoblagichlar (JAMI yacheykasi)
        r = self.client.get(url, {"xlsx": 1})
        self.assertEqual(load_workbook(BytesIO(r.content)).active.max_row, 9)  # 8 + sarlavha
        # konsentratorlar
        r = self.client.get(reverse("mon_dcu", args=[snap.pk]), {"esp": "Ko‘kdala TETK", "xlsx": 1})
        self.assertEqual(load_workbook(BytesIO(r.content)).active.max_row, 2)
        # ruxsat: operator yuklay oladi, kuzatuvchi (export_excel yo‘q) — yo‘q
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(url, {"xlsx": 1}).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_previous_snapshots_viewable(self):
        self.client.force_login(self.admin)
        dcu, mtr = self.day1()
        self.upload(D2, dcu, mtr)
        self.upload(D3, dcu, mtr)
        s2, s3 = Snapshot.objects.get(report_date=D2), Snapshot.objects.get(report_date=D3)
        r = self.client.get(reverse("mon_dashboard"))
        self.assertEqual(r.context["snap"].pk, s3.pk)  # eng oxirgisi
        self.assertTrue(r.context["is_latest"])
        r = self.client.get(reverse("mon_snapshot", args=[s2.pk]))
        self.assertEqual(r.context["snap"].pk, s2.pk)
        self.assertFalse(r.context["is_latest"])
        self.assertEqual(r.context["newer"].pk, s3.pk)
        self.assertContains(r, "Arxiv ma’lumoti")

    def test_compare_two_days(self):
        self.client.force_login(self.admin)
        dcu, mtr = self.day1()
        self.upload(D2, dcu, mtr)
        # ikkinchi kun: bitta hisoblagich kam (id=1 yo‘q), bitta yangi Koson da; DCU: C tiklangan, D yangi
        mtr2 = meters_xlsx([meter_row(2, 8, "Промышленные"), meter_row(3, 9, "Индикатор баланса"), meter_row(4, 31, "Промышленные"),
                            meter_row(5, 32), meter_row(6, 91, "Бытовые", esp="Ko‘kdala TETK"), meter_row(7, 92, "Промышленные"),
                            meter_row(9, 401, "Strange"), meter_row(10, 3, esp="Koson TETK")])
        dcu2 = dcu_xlsx([dcu_row("A", at="05/11/2026 10:00:00"), dcu_row("B", at="05/11/2026 10:00:00"),
                         dcu_row("D", at="08/11/2026 10:00:00", esp="Koson TETK")])
        self.upload(D3, dcu2, mtr2)
        s2, s3 = Snapshot.objects.get(report_date=D2), Snapshot.objects.get(report_date=D3)
        cmp = services.compare_grids(s2, s3)
        g = cmp["grid"]
        idx = {(c["sec"], c["b"], c["g"]): i for i, c in enumerate(g["cols"])}
        # JAMI hisoblagichlar: 8 -> 8 (bitta ketdi, bitta qo‘shildi) ; 2-7 kun: id1(2),id2(7) -> id10(3) = 2 -> 1... ni tekshiramiz
        total = {k: cmp["total"]["cells"][i] for k, i in idx.items()}
        self.assertEqual(total[("m", None, None)]["a"], 8)
        self.assertEqual(total[("m", None, None)]["b"], 8)
        self.assertEqual(total[("m", 0, None)]["d"], total[("m", 0, None)]["b"] - total[("m", 0, None)]["a"])
        row = {r["esp"]: {k: r["cells"][i] for k, i in idx.items()} for r in cmp["rows"]}
        self.assertEqual(row["Koson TETK"][("m", 0, None)]["d"], -1)   # 2–7 davr umumiy soni: 7 kunlik 8 ga o‘tdi, yangisi keldi
        self.assertFalse(any(k[2] for k in idx))                       # solishtirishda guruhlar (aholi/yuridik/balans) yo‘q
        self.assertEqual([k for k in idx if k[0] == "dcu"], [("dcu", None, None)])  # konsentratorlar — faqat JAMI
        self.assertEqual(row["Ko‘kdala TETK"][("dcu", None, None)]["d"], -1)   # C tiklandi
        self.assertTrue(any(r["changed"] for r in cmp["rows"]))
        # sahifa va Excel
        r = self.client.get(reverse("mon_compare"))
        self.assertEqual((r.context["a"].pk, r.context["b"].pk), (s2.pk, s3.pk))
        self.assertContains(r, "SOLISHTIRISH")
        self.assertEqual(r.context["new_dcu"]["n"], 1)         # D yangi
        self.assertEqual(r.context["recovered_dcu"]["n"], 1)   # C tiklangan
        r = self.client.get(reverse("mon_compare"), {"a": s2.pk, "b": s3.pk, "xlsx": 1})
        wb = load_workbook(BytesIO(r.content))
        self.assertEqual(len(wb.sheetnames), 1)
        ws = wb.active
        heads = [c.value for c in ws[7] if c.value]   # 3-sarlavha qatori: A | B | Farq
        self.assertEqual(heads[:3], ["07.11", "08.11", "Farq"])
        # PDF yuklab olish
        for url in ({"a": s2.pk, "b": s3.pk, "pdf": 1},):
            r = self.client.get(reverse("mon_compare"), url)
            self.assertEqual(r["Content-Type"], "application/pdf")
            self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertTrue(self.client.get(reverse("mon_download", args=[s3.pk, "svod"]), {"pdf": 1}).content.startswith(b"%PDF"))
        self.assertTrue(self.client.get(reverse("mon_dcu", args=[s3.pk]), {"pdf": 1}).content.startswith(b"%PDF"))
        self.assertTrue(self.client.get(reverse("mon_meters", args=[s3.pk]), {"pdf": 1, "b": 0}).content.startswith(b"%PDF"))
        self.assertContains(self.client.get(reverse("mon_compare")), "pdf=1")
        # kuzatuvchi ko‘ra oladi, Excel — yo‘q
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("mon_compare")).status_code, 200)
        self.assertEqual(self.client.get(reverse("mon_compare"), {"xlsx": 1}).status_code, 403)
        self.assertEqual(self.client.get(reverse("mon_compare"), {"pdf": 1}).status_code, 403)

    def test_compare_needs_two_snapshots(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("mon_compare"))
        self.assertContains(r, "kamida ikki kunlik")
        dcu, mtr = self.day1()
        self.upload(D3, dcu, mtr)
        self.assertContains(self.client.get(reverse("mon_compare")), "kamida ikki kunlik")

    def test_old_summary_without_esp_matrix_is_upgraded(self):
        self.client.force_login(self.admin)
        dcu, mtr = self.day1()
        self.upload(D3, dcu, mtr)
        snap = Snapshot.objects.get(report_date=D3)
        s = snap.summary
        s["meters"].pop("esp_matrix")
        snap.summary = s
        snap.save(update_fields=["summary"])
        r = self.client.get(reverse("mon_dashboard"))
        self.assertEqual(r.status_code, 200)
        snap.refresh_from_db()
        self.assertIn("esp_matrix", snap.summary["meters"])
