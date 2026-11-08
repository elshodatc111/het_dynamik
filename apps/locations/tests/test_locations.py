import shutil
import tempfile
from io import BytesIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from apps.customers import bot
from apps.customers.models import BotUser
from apps.locations import rules, services
from apps.locations.models import LocationSearch, LocationUpload, Place
from apps.monitoring import telegram
from apps.reports import services as rsvc

TMP = tempfile.mkdtemp()
OLD_HEAD = ["Type", "Nomer", "location", "Latude", "Tuman", "Potstansiya", "Fider", "TP", "Hisob raqam", "FIO", "Addres", "Istemolchi turi"]


def row(**kw):
    r = {"kind": "Meter", "number": "121206035571", "lat": "38.86", "lon": "65.79", "district": "Qarshi TETK", "substation": "NS Qarshi 35/10 kV",
         "feeder": "Fider 10 kV", "tp": "293", "account": "350007", "fio": "Aliyev Vali", "address": "Qarshi sh. 1-uy", "consumer_type": "Axoli"}
    r.update(kw)
    return r


def book(rows, head=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "Лист1"
    ws.append(head or OLD_HEAD)
    for r in rows:
        ws.append([r.get(k, "") for k in rules.KEYS])
    b = BytesIO()
    wb.save(b)
    return b.getvalue()


class Rules(TestCase):
    def v(self, **kw):
        return rules.validate_row(row(**kw))

    def test_good_rows(self):
        self.assertEqual(self.v()[1], [])
        self.assertEqual(self.v(kind="meter", number="ex-3068491")[0]["number"], "EX-3068491")        # EX- raqamlar to‘g‘ri
        self.assertEqual(self.v(number="EX162713")[0]["number"], "EX-162713")
        dcu, errs = self.v(kind="DCU", account="-", fio="-", address="-", consumer_type="Konsentrator")
        self.assertEqual(errs, [])
        self.assertEqual((dcu["account"], dcu["fio"], dcu["address"]), ("", "", ""))              # "-" bo‘sh saqlanadi
        self.assertEqual(self.v(kind="Balans", account="", fio="", address="", consumer_type="")[0]["consumer_type"], "Balans")

    def test_errors(self):
        for kw in ({"number": "12345"}, {"kind": "Boshqa"}, {"lat": "36.98", "lon": "62.12"}, {"lat": "abc"}, {"district": "Toshkent"},
                   {"tp": ""}, {"fio": "-"}, {"account": ""}, {"consumer_type": "Konsentrator"}, {"kind": "DCU", "number": "EX-1234567"}):
            self.assertTrue(self.v(**kw)[1], kw)
        # kenglik/uzunlik almashib qolgan bo‘lsa — aniq izoh
        self.assertIn("almashib", " ".join(self.v(lat="65.79", lon="38.86")[1]))

    def test_district_apostrophes(self):
        self.assertEqual(self.v(district="g'uzor tetk")[0]["district"], "G‘uzor TETK")
        self.assertEqual(self.v(district="Yakkabog'  TETK")[0]["district"], "Yakkabog‘ TETK")

    def test_header_aliases_and_missing(self):
        m, err = rules.map_headers(OLD_HEAD)
        self.assertEqual(err, "")
        self.assertEqual({v: k for k, v in m.items()}["lat"], 2)        # «location» — kenglik
        self.assertEqual({v: k for k, v in m.items()}["lon"], 3)        # «Latude» — uzunlik
        self.assertTrue(rules.map_headers(["Type", "Nomer"])[1])

    def test_template_has_validations(self):
        wb = load_workbook(BytesIO(rules.build_template()))
        self.assertEqual(wb.worksheets[0].title, "Лист1")
        self.assertEqual([c.value for c in wb.worksheets[0][1]], [c[1] for c in rules.COLUMNS])
        self.assertGreaterEqual(len(wb.worksheets[0].data_validations.dataValidation), 5)

    def test_query_normalization(self):
        self.assertEqual(rules.normalize_query(" ex 3068491 "), "EX-3068491")
        self.assertEqual(rules.normalize_query("121206035571"), "121206035571")


@override_settings(MEDIA_ROOT=TMP)
class Upload(TestCase):
    def setUp(self):
        rsvc.sync_roles()
        U = get_user_model()
        self.admin = U.objects.create_user("a1", password="x")
        self.admin.groups.add(Group.objects.get(name="Admin"))
        self.client.force_login(self.admin)

    def post(self, data):
        return self.client.post(reverse("location_upload"), {"file": SimpleUploadedFile("loc.xlsx", data)})

    def test_upsert_errors_and_journal(self):
        rows = [row(), row(number="121206035572", kind="DCU", account="-", fio="-", address="-", consumer_type="Konsentrator"),
                row(number="121206035573", tp=""), row(), row(number="EX-3068491")]
        r = self.post(book(rows))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Place.objects.count(), 3)
        up = LocationUpload.objects.get()
        self.assertEqual((up.total, up.created, up.updated, up.bad), (5, 3, 0, 2))
        errs = load_workbook(BytesIO(open(services.base_dir() / up.error_file, "rb").read())).worksheets[0]
        self.assertEqual(errs.max_row, 3)
        self.assertEqual(errs.cell(1, 13).value, "Xatolar")
        self.assertIn("takrorlangan", errs.cell(3, 13).value)
        # qayta yuklash: o‘zgargani yangilanadi, o‘zgarmagani tegilmaydi, yangisi qo‘shiladi
        r = self.post(book([row(fio="Yangi FIO"), row(number="121206035572", kind="DCU", account="-", fio="-", address="-", consumer_type="Konsentrator"),
                            row(number="121206035999")]))
        up = LocationUpload.objects.first()
        self.assertEqual((up.created, up.updated, up.unchanged, up.bad), (1, 1, 1, 0))
        self.assertEqual(Place.objects.get(number="121206035571").fio, "Yangi FIO")
        self.assertEqual(Place.objects.count(), 4)

    def test_old_file_headers_and_big_file_has_no_row_limit(self):
        self.post(book([row(number=f"1212060{i:05d}") for i in range(1, 801)]))
        self.assertEqual(Place.objects.count(), 800)               # Customer'dagi 500 qator chegarasi bu yerda yo‘q

    def test_bad_files(self):
        r = self.client.post(reverse("location_upload"), {"file": SimpleUploadedFile("x.pdf", b"x")})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Place.objects.count(), 0)
        self.post(book([row()], head=["Type", "Nomer"]))
        self.assertEqual(Place.objects.count(), 0)

    def test_pages_and_permissions(self):
        self.post(book([row()]))
        for name in ("location_data", "location_map", "location_uploads", "location_searches", "location_upload"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        pl = Place.objects.get()
        self.assertContains(self.client.get(reverse("location_detail", args=[pl.pk])), "121206035571")
        self.assertEqual(self.client.get(reverse("location_points")).json()["points"][0][4], "121206035571")
        self.assertEqual(load_workbook(BytesIO(self.client.get(reverse("location_export")).content)).worksheets[0].max_row, 2)
        # cheklov: Kuzatuvchi roli location ko‘ra olmaydi, yuklay olmaydi
        viewer = get_user_model().objects.create_user("v1", password="x")
        viewer.groups.add(Group.objects.get(name="Kuzatuvchi"))
        self.client.force_login(viewer)
        for name in ("location_data", "location_upload", "location_searches", "location_export"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.assertEqual(self.client.post(reverse("location_delete", args=[pl.pk])).status_code, 403)


class BotLookup(TestCase):
    def setUp(self):
        self.sent, self.edits, self.loc = [], [], []
        patches = [mock.patch.object(telegram, "send_message", side_effect=lambda c, t, m=None: self.sent.append((c, t, m)) or {"result": {"message_id": 99}}),
                   mock.patch.object(telegram, "edit_text", side_effect=lambda c, mid, t, m=None: self.edits.append(t) or True),
                   mock.patch.object(telegram, "send_action"),
                   mock.patch.object(telegram, "send_location", side_effect=lambda c, la, lo, m=None: self.loc.append((la, lo, m)) or {})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        Place.objects.create(kind="Meter", number="121206035571", lat=38.86, lon=65.79, district="Qarshi TETK", substation="NS 1",
                             feeder="F1", tp="293", account="350007", fio="Aliyev Vali", address="Qarshi", consumer_type="Axoli")
        Place.objects.create(kind="Meter", number="EX-3068491", lat=38.9, lon=65.8, district="Koson TETK", substation="NS 2", feeder="F2", tp="1",
                             account="1", fio="X", address="Y", consumer_type="Yuridik")

    def msg(self, text, uid=7):
        bot.handle_update({"message": {"chat": {"id": uid, "type": "private"}, "from": {"id": uid, "username": "u"}, "text": text}})

    def test_unregistered_cannot_search(self):
        self.msg("📍 Joylashuvni aniqlash", uid=50)
        self.assertEqual(self.loc, [])
        self.assertIn("ro‘yxatdan o‘ting", self.sent[-1][1])
        BotUser.objects.filter(tg_id=50).update(status="pending")
        self.msg("121206035571", uid=50)
        self.assertEqual(self.loc, [])
        self.assertEqual(LocationSearch.objects.count(), 0)

    def test_found_sends_card_map_and_buttons(self):
        BotUser.objects.create(tg_id=7, full_name="Test", status="approved")
        self.msg("📍 Joylashuvni aniqlash")
        self.assertIn("raqamini yuboring", self.sent[-1][1])
        self.msg("121206035571")
        self.assertTrue(any("Topildi" in m[1] and "Aliyev Vali" in m[1] for m in self.sent))       # kartochka progress xabariga yozildi
        lat, lon, markup = self.loc[-1]
        self.assertEqual((lat, lon), (38.86, 65.79))
        rows = markup["inline_keyboard"]
        self.assertEqual([len(r) for r in rows], [2, 2])                                      # bir qatorda ikkita tugma
        self.assertIn("google.com/maps", rows[0][0]["url"])
        self.assertIn("yandex.com/maps", rows[0][1]["url"])
        self.msg("ex 3068491")                                                               # EX- bilan va bo‘shliq bilan ham topiladi
        self.assertEqual(len(self.loc), 2)
        s = LocationSearch.objects.order_by("id")
        self.assertEqual([x.found for x in s], [True, True])

    def test_not_found_and_bad_input_and_cancel(self):
        BotUser.objects.create(tg_id=7, full_name="Test", status="approved")
        self.msg("📍 Joylashuvni aniqlash")
        self.msg("999999999999")
        self.assertTrue(any("topilmadi" in m[1] for m in self.sent))
        self.assertEqual(self.loc, [])
        self.assertFalse(LocationSearch.objects.get().found)
        self.msg("salom!!! ??")
        self.assertTrue(any("noto‘g‘ri" in m[1] for m in self.sent))
        self.msg("❌ Bekor qilish")
        self.assertEqual(BotUser.objects.get(tg_id=7).state, "")
        self.msg("121206035571")                                                              # holat tugagach qidiruv bo‘lmaydi
        self.assertEqual(len(self.loc), 0)
