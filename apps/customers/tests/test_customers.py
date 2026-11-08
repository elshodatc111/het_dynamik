import shutil
import tempfile
from io import BytesIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from apps.customers import bot, rules, services
from apps.customers.models import BotUser, Customer, Submission
from apps.monitoring import telegram
from apps.reports import services as rsvc

TMP = tempfile.mkdtemp()


def good(n=1, **kw):
    row = {"etk_code": "10207", "trans_code": "01328", "trans_name": "TP-1", "consumer_name": "Ruziyev Anvar",
           "consumer_code": "00550484", "meter_type": "EX18", "meter_no": f"{121207107200 + n}", "address": "MUSTAQILLIK",
           "phase": "1", "ct_ratio": "1/1", "consumer_type": "1", "comm_type": "PLC", "vt_ratio": "1/1"}
    row.update(kw)
    return row


def xlsx(rows, sheet="Лист1", headers=True, extra_sheet=None):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(list(range(rules.NCOLS)))
    ws.append([c[1] for c in rules.COLUMNS] if headers else ["x"] * rules.NCOLS)
    for i, r in enumerate(rows, 1):
        ws.append([i] + [r.get(k, "") for k in rules.KEYS[1:]])
    if extra_sheet:
        wb.create_sheet(extra_sheet).append(["bu varaq o‘qilmaydi"])
    b = BytesIO()
    wb.save(b)
    return b.getvalue()


class Rules(TestCase):
    def check(self, **kw):
        return rules.validate_row(good(**kw))

    def test_good_row(self):
        vals, errs = self.check()
        self.assertEqual(errs, [])
        self.assertEqual(vals["trans_code"], "01328")      # boshidagi nol saqlangan
        self.assertEqual(vals["consumer_code"], "00550484")

    def test_each_rule(self):
        cases = {
            "etk_code": "10208", "trans_code": "1328", "meter_type": "EX99", "meter_no": "12345678901",
            "phase": "2", "consumer_type": "3", "comm_type": "WIFI", "ct_ratio": "200", "vt_ratio": "0/5",
        }
        for k, v in cases.items():
            self.assertTrue(self.check(**{k: v})[1], k)
        for k in ("trans_name", "consumer_name", "consumer_code", "address", "ct_ratio", "vt_ratio", "meter_type", "meter_no"):
            self.assertTrue(self.check(**{k: ""})[1], f"{k} bo‘sh bo‘lsa xato")

    def test_normalization_and_numeric_cells(self):
        vals, errs = self.check(meter_type="ex518  100a", comm_type="rs485", etk_code=10207, phase=1, meter_no=121207107209, ct_ratio="200 / 5")
        self.assertEqual(errs, [])
        self.assertEqual((vals["meter_type"], vals["comm_type"], vals["ct_ratio"]), ("EX518 100A", "RS485", "200/5"))
        # raqam turida kelgan 1328 — nol yo‘qolgan: tushunarli izoh
        _v, errs = self.check(trans_code=1328)
        self.assertIn("Matn", errs[0])
        self.assertEqual(self.check(trans_code=12345)[1], [])

    def test_all_17_etk_and_6_meter_types(self):
        for e in rules.ETK_CODES:
            self.assertEqual(self.check(etk_code=e)[1], [])
        for t in rules.METER_TYPES:
            self.assertEqual(self.check(meter_type=t)[1], [])
        self.assertEqual(len(rules.ETK_CODES), 17)

    def test_template_file(self):
        wb = load_workbook(BytesIO(rules.build_template()))
        ws = wb["Лист1"]
        self.assertEqual(ws["B2"].value, rules.COLUMNS[1][1])
        self.assertEqual(ws["C3"].number_format, "@")
        self.assertGreaterEqual(len(ws.data_validations.dataValidation), 7)
        self.assertEqual(rules.check_headers([c[1] for c in rules.COLUMNS]), "")
        self.assertTrue(rules.check_headers(["x"] * 14))


@override_settings(MEDIA_ROOT=TMP)
class Intake(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        self.u = BotUser.objects.create(tg_id=1, full_name="Test Foydalanuvchi", status="approved", district="Qarshi")

    def test_process_saves_good_rejects_bad_and_duplicates(self):
        data = xlsx([good(1), good(2, meter_no="bad"), good(1, address="boshqa"), good(3)], extra_sheet="Lист9")
        res = services.process(self.u, "a.xlsx", data)
        self.assertEqual((res["total"], res["ok"], res["bad"]), (4, 2, 2))
        self.assertEqual(Customer.objects.count(), 2)
        # xatolar fayli: faqat xatoli qatorlar + «Xatolar» ustuni, fayldagi takror 3-qator (birinchisi qabul qilingan)
        ws = load_workbook(BytesIO(res["error_file"]))["Лист1"]
        self.assertEqual(ws.cell(row=2, column=15).value, "Xatolar")
        self.assertEqual(sum(1 for r in range(3, 20) if ws.cell(row=r, column=15).value), 2)
        self.assertIn("takrorlangan", ws.cell(row=4, column=15).value)
        self.assertIn("12 xonali", ws.cell(row=3, column=15).value)
        # oldin yuklangan raqam qayta kelsa — rad
        res2 = services.process(self.u, "b.xlsx", xlsx([good(1), good(9)]))
        self.assertEqual((res2["ok"], res2["bad"]), (1, 1))
        self.assertIn("avval yuklangan", load_workbook(BytesIO(res2["error_file"]))["Лист1"].cell(row=3, column=15).value)
        self.assertEqual(Submission.objects.count(), 2)

    def test_file_rejections_are_journaled(self):
        for data, part in ((xlsx([good()], sheet="Boshqa"), "Лист1"), (xlsx([good()], headers=False), "shablonga mos emas"),
                           (b"bu excel emas", "o‘qilmadi"), (xlsx([good(i) for i in range(501)]), "500")):
            with self.assertRaises(services.FileRejected) as cm:
                services.process(self.u, "x.xlsx", data)
            self.assertIn(part, str(cm.exception))
        self.assertEqual(Submission.objects.filter(rejected__gt="").count(), 4)
        self.assertEqual(Customer.objects.count(), 0)
        with self.assertRaises(services.FileRejected):
            services.process(self.u, "big.xlsx", b"0" * (3 * 1024 * 1024 + 1))

    def test_500_rows_ok(self):
        res = services.process(self.u, "f.xlsx", xlsx([good(i) for i in range(500)]))
        self.assertEqual((res["ok"], res["bad"]), (500, 0))
        self.assertIsNone(res["error_file"])


@override_settings(MEDIA_ROOT=TMP)
class BotFlow(TestCase):
    def setUp(self):
        self.sent = []
        patches = [mock.patch.object(telegram, "send_message", side_effect=lambda c, t, m=None: self.sent.append((c, t))),
                   mock.patch.object(telegram, "send_document", side_effect=lambda c, n, d, cap="": self.sent.append((c, f"DOC:{n}:{cap}")) or {})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def msg(self, text=None, uid=5, **extra):
        m = {"message": {"chat": {"id": uid, "type": "private"}, "from": {"id": uid, "username": "u5"}, **extra}}
        if text is not None:
            m["message"]["text"] = text
        bot.handle_update(m)
        return self.sent[-1][1] if self.sent else ""

    def register(self):
        self.msg("/start")
        self.msg("Aliyev Vali Karimovich")
        self.msg("Qarshi shahar")
        self.msg(None, contact={"phone_number": "998901234567", "user_id": 5})
        return self.msg("Muhandis")

    def test_registration_and_approval_flow(self):
        out = self.register()
        u = BotUser.objects.get(tg_id=5)
        self.assertEqual((u.status, u.phone, u.position), ("pending", "+998901234567", "Muhandis"))
        self.assertIn("qabul qilindi", out)
        self.assertIn("ko‘rib chiqilmoqda", self.msg("Customer shablon"))
        # admin bekor qiladi -> sabab bilan xabar -> qayta ro‘yxatdan o‘tish
        admin = get_user_model().objects.create_superuser("adm", "a@a.uz", "pw")
        self.client.force_login(admin)
        r = self.client.post(reverse("customer_app_action", args=[u.pk]), {"action": "reject", "reason": ""}, follow=True)
        self.assertContains(r, "sababini yozing")
        self.client.post(reverse("customer_app_action", args=[u.pk]), {"action": "reject", "reason": "FIO to‘liq emas"})
        u.refresh_from_db()
        self.assertEqual(u.status, "rejected")
        self.assertIn("FIO to‘liq emas", self.sent[-1][1])
        self.assertIn("/start", self.sent[-1][1])
        self.register()
        u.refresh_from_db()
        self.assertEqual(u.status, "pending")
        self.assertEqual(u.reject_reason, "")
        # tasdiqlash: ma’lumotlarni to‘g‘rilab saqlaydi, xabar boradi
        self.client.post(reverse("customer_app_action", args=[u.pk]), {"action": "approve", "full_name": "Aliyev Vali Karimovich", "district": "Qarshi sh.",
                                                                       "phone": "+998901234567", "position": "Bosh muhandis"})
        u.refresh_from_db()
        self.assertEqual((u.status, u.district, u.position), ("approved", "Qarshi sh.", "Bosh muhandis"))
        self.assertIn("tasdiqlandi", self.sent[-1][1])

    def test_bad_phone_and_foreign_contact(self):
        self.msg("/start"); self.msg("Aliyev Vali Karimovich"); self.msg("Qarshi")
        self.assertIn("noto‘g‘ri", self.msg("12345"))
        self.assertIn("o‘zingizning", self.msg(None, contact={"phone_number": "998901234567", "user_id": 999}))
        self.assertEqual(self.msg("90 123 45 67").split()[0], "Lavozimingizni")

    def approved(self):
        return BotUser.objects.create(tg_id=7, full_name="Tasdiqlangan", status="approved")

    def test_template_and_send_buttons(self):
        self.approved()
        self.assertIn("DOC:CUSTOMER_SHABLON.xlsx", self.msg("Customer shablon", uid=7))
        self.assertIn("jo‘nating", self.msg("Customer jo'natish", uid=7))   # oddiy apostrof ham ishlaydi

    def test_document_flow_returns_errors_file(self):
        self.approved()
        data = xlsx([good(1), good(2, comm_type="X")])
        with mock.patch.object(telegram, "download_file", return_value=data):
            self.msg(None, uid=7, document={"file_id": "f", "file_name": "m.xlsx", "file_size": len(data)})
        texts = [t for _c, t in self.sent]
        self.assertTrue(any("Jami qator: 2" in t and "Xato: 1" in t for t in texts))
        self.assertTrue(any(t.startswith("DOC:xatolar_") for t in texts))
        self.assertEqual(Customer.objects.count(), 1)

    def test_non_excel_and_big_files_rejected(self):
        self.approved()
        self.assertIn("xlsx", self.msg(None, uid=7, document={"file_id": "f", "file_name": "m.pdf", "file_size": 10}))
        self.assertIn("3 MB", self.msg(None, uid=7, document={"file_id": "f", "file_name": "m.xlsx", "file_size": 4 * 1024 * 1024}))
        self.assertEqual(Customer.objects.count(), 0)

    def test_blocked_user_and_groups_ignored(self):
        BotUser.objects.create(tg_id=8, status="blocked")
        self.assertIn("cheklangan", self.msg("Customer shablon", uid=8))
        n = len(self.sent)
        bot.handle_update({"message": {"chat": {"id": -100, "type": "group"}, "from": {"id": 9}, "text": "/start"}})
        self.assertEqual(len(self.sent), n)


@override_settings(MEDIA_ROOT=TMP)
class Web(TestCase):
    def setUp(self):
        shutil.rmtree(bot.state_dir(), ignore_errors=True)
        rsvc.sync_roles()
        U = get_user_model()
        self.admin = U.objects.create_user("admin1", password="x")
        self.admin.groups.add(Group.objects.get(name="Admin"))
        self.op = U.objects.create_user("op1", password="x")
        self.op.groups.add(Group.objects.get(name="Operator"))
        self.bu = BotUser.objects.create(tg_id=1, full_name="Test", status="approved")
        services.process(self.bu, "a.xlsx", xlsx([good(i) for i in range(1, 4)]))
        services.process(self.bu, "b.xlsx", xlsx([good(1), good(4, comm_type="X")]))

    def test_only_admin(self):
        self.client.force_login(self.op)
        for name in ("customer_data", "customer_journal", "customer_apps", "system_status"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.assertEqual(self.client.post(reverse("customer_export"), {"delete": "1"}).status_code, 403)
        self.client.force_login(self.admin)
        for name in ("customer_data", "customer_journal", "customer_apps", "system_status"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("customer_template")).status_code, 200)

    def test_journal_counts(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("customer_journal"))
        self.assertEqual((r.context["sent"], r.context["rows"], r.context["ok"], r.context["bad"]), (2, 5, 3, 2))
        self.assertContains(r, "Test")

    def test_export_without_delete_keeps_data(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("customer_export"), {"delete": "0"})
        ws = load_workbook(BytesIO(r.content))["Лист1"]
        self.assertEqual(ws.max_row, 5)                       # 2 sarlavha + 3 qator
        self.assertEqual(ws["C3"].value, "01328")
        self.assertEqual(Customer.objects.count(), 3)

    def test_export_with_delete_removes_exported_only(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("customer_export"), {"delete": "1", "etk": "10207"})
        self.assertEqual(load_workbook(BytesIO(r.content))["Лист1"].max_row, 5)
        self.assertEqual(Customer.objects.count(), 0)
        # filtr: boshqa ETK dagilar tegilmaydi
        services.process(self.bu, "c.xlsx", xlsx([good(10, etk_code="10212"), good(11)]))
        self.client.post(reverse("customer_export"), {"delete": "1", "etk": "10212"})
        self.assertEqual(Customer.objects.count(), 1)
        # bo‘sh bo‘lsa — fayl emas, xabar
        r = self.client.post(reverse("customer_export"), {"delete": "1", "etk": "10401"}, follow=True)
        self.assertContains(r, "ma’lumot yo‘q")

    def test_data_page_has_confirm_choices(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("customer_data")).content.decode()
        self.assertIn("O‘chirilsin", page)
        self.assertIn("O‘chirilmasin", page)

    def test_status_page_reports_bot_down(self):
        self.client.force_login(self.admin)
        with mock.patch.object(telegram, "get_me", return_value={"username": "x_bot"}), override_settings(TELEGRAM_BOT_TOKEN="1:" + "a" * 35):
            r = self.client.get(reverse("system_status") + "?tg=1")
        items = {i["key"]: i for i in r.context["res"]["items"]}
        self.assertEqual(items["db"]["state"], "ok")
        self.assertEqual(items["tg"]["state"], "ok")
        self.assertEqual(items["bot"]["state"], "err")
        bot.heartbeat()
        self.assertEqual(self.client.get(reverse("system_status")).context["res"]["items"][4]["state"], "ok")


@override_settings(MEDIA_ROOT=TMP)
class Loop(TestCase):
    def test_run_forever_processes_updates_and_survives_errors(self):
        sent = []
        calls = {"n": 0}

        def fake_updates(offset, wait):
            calls["n"] += 1
            if calls["n"] == 1:
                raise telegram.TelegramError("tarmoq xatosi")
            if calls["n"] == 2:
                return [{"update_id": 10, "message": {"chat": {"id": 3, "type": "private"}, "from": {"id": 3}, "text": "/start"}},
                        {"update_id": 11, "message": None}]
            return []

        with mock.patch.object(telegram, "is_configured", return_value=True), \
                mock.patch.object(telegram, "delete_webhook"), \
                mock.patch.object(telegram, "get_updates", side_effect=fake_updates), \
                mock.patch.object(telegram, "send_message", side_effect=lambda c, t, m=None: sent.append(t)), \
                mock.patch("apps.customers.bot.time.sleep"):
            bot.run_forever(stop=lambda: calls["n"] >= 3)
        self.assertTrue(any("ro‘yxatdan o‘ting" in t for t in sent))
        self.assertEqual(bot._read_json("state.json")["offset"], 12)
        self.assertEqual(bot.read_heartbeat()["error"], "")


@override_settings(MEDIA_ROOT=TMP)
class Stats(TestCase):
    def setUp(self):
        rsvc.sync_roles()
        U = get_user_model()
        self.admin = U.objects.create_user("adm2", password="x")
        self.admin.groups.add(Group.objects.get(name="Admin"))
        self.bu = BotUser.objects.create(tg_id=1, full_name="Test", status="approved")

    def test_counts_per_etk_survive_deletion(self):
        from datetime import date

        from apps.customers.models import EtkStat
        rows = [good(1, etk_code="10207"), good(2, etk_code="10207", comm_type="X"), good(3, etk_code="10212"),
                good(4, etk_code="99999"), good(1, etk_code="10207", address="takror")]
        res = services.process(self.bu, "a.xlsx", xlsx(rows))
        self.assertEqual((res["ok"], res["bad"]), (2, 3))
        st = {s.etk_code: (s.sent, s.ok, s.bad) for s in EtkStat.objects.all()}
        self.assertEqual(st["10207"], (3, 1, 2))     # 1 ta to‘g‘ri; noto‘g‘ri tur + takrorlangan raqam
        self.assertEqual(st["10212"], (1, 1, 0))
        self.assertEqual(st[""], (1, 0, 1))          # noto‘g‘ri ETK kodi — «noma’lum»
        # ikkinchi yuborish shu kunga qo‘shiladi, mijozlar o‘chirilsa ham statistika qoladi
        services.process(self.bu, "b.xlsx", xlsx([good(7, etk_code="10212")]))
        Customer.objects.all().delete()
        self.assertEqual(EtkStat.objects.get(etk_code="10212").sent, 2)
        self.assertEqual(EtkStat.objects.get(etk_code="10212", date=date.today()).ok, 2)

    def test_stats_page_day_and_month(self):
        services.process(self.bu, "a.xlsx", xlsx([good(1, etk_code="10207"), good(2, etk_code="10207", comm_type="X")]))
        self.client.force_login(self.admin)
        r = self.client.get(reverse("customer_stats"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.context["tot"]["sent"], r.context["tot"]["ok"], r.context["tot"]["bad"]), (2, 1, 1))
        self.assertEqual(len(r.context["rows"]), 17)
        self.assertEqual(r.context["tot"]["pct"], 50.0)
        r = self.client.get(reverse("customer_stats") + "?mode=month")
        self.assertEqual(r.context["tot"]["sent"], 2)
        self.assertEqual(len(r.context["trend"]), 12)
        self.assertEqual(len(r.context["days"]), 1)
        self.assertEqual(self.client.get(reverse("customer_stats") + "?mode=day&date=2020-01-01").context["tot"]["sent"], 0)
        self.assertEqual(self.client.get(reverse("customer_stats") + "?mode=month&month=xx").status_code, 200)
