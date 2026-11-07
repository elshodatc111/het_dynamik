"""Parser testlari: haqiqiy PDF namunalar bilan (tests/fixtures)."""
from pathlib import Path

from django.test import SimpleTestCase

from apps.reports.parser.extract import parse_pdf
from apps.reports.parser.translit import to_latin
from apps.reports.parser.validate import validate

FIX = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


class TranslitTests(SimpleTestCase):
    def test_cyrillic_to_latin(self):
        self.assertEqual(to_latin("Ҳудуд номи"), "Hudud nomi")
        self.assertEqual(to_latin("обход олиш"), "obxod olish")
        self.assertEqual(to_latin("Қашқадарё"), "Qashqadaryo")
        self.assertEqual(to_latin("ўчирилган"), "o‘chirilgan")
        self.assertEqual(to_latin("Ko‘kdala t."), "Ko‘kdala t.")


class ParserTests(SimpleTestCase):
    def parse(self, name):
        return validate(parse_pdf(FIX / name))

    def test_five_page_report(self):
        r = self.parse("hisobot_2026-11-05.pdf")
        self.assertEqual((r.prev_date.isoformat(), r.report_date.isoformat()), ("2026-11-04", "2026-11-05"))
        self.assertEqual(r.errors, [])
        self.assertEqual(r.warnings, [])
        self.assertEqual(len(r.sections), 5)
        self.assertEqual(int(r.sections["aloqasiz"].cur["jami"]["nome_all"]), 26201)
        self.assertEqual(int(r.sections["aloqasiz"].prev["jami"]["nome_all"]), 26627)

    def test_hetk_nine_page_report(self):
        r = self.parse("hisobot_2026-11-01_06_hetk.pdf")
        self.assertEqual(r.report_date.isoformat(), "2026-11-06")
        self.assertEqual(r.errors, [])
        self.assertEqual(r.warnings, [], r.warnings)   # birlashtirilgan kataklar to‘g‘ri yig‘ilishi kerak
        # faqat 1, 2, 3, 4, 6, 7, 8-betlar; 5 (diagramma) va 9 (SmartApp) o‘tkazib yuboriladi
        self.assertEqual(sorted(r.sections), sorted(
            ["konsentratorlar", "aloqasiz", "uzilgan", "balans_yuklama", "obxod48", "tafovut_balans", "billing_tafovut"]))
        self.assertEqual([r.sections[k].page for k in r.sections], [1, 2, 3, 4, 6, 7, 8])
        self.assertEqual(r.skipped_pages, [5, 9])
        self.assertEqual(int(r.sections["billing_tafovut"].cur["jami"]["bill_total"]), 2984)

    def test_hetk_billing_21_columns(self):
        """Billing jadvali 21 ustunli ko‘rinishda (jami qiymatdan keyin qo‘shimcha bo‘sh ustun)."""
        r = self.parse("hisobot_2026-11-04_hetk21.pdf")
        self.assertEqual(r.report_date.isoformat(), "2026-11-04")
        self.assertEqual(r.errors, [])
        self.assertEqual(r.warnings, [])
        self.assertEqual(len(r.sections), 7)
        b = r.sections["billing_tafovut"]
        self.assertEqual(b.page, 8)
        self.assertEqual(int(b.cur["jami"]["bill_total"]), 2402)
        self.assertEqual(int(b.cur["jami"]["bill_not_askue"]), 11478)
        self.assertEqual(int(b.cur["guzor"]["cas_not_billing"]), 21)   # «2 1» -> 21
        self.assertEqual(int(b.cur["guzor"]["cas_nb_3d"]), 19)         # «1 9» -> 19

    def test_farqi_variant(self):
        r = self.parse("hisobot_2026-11-04.pdf")
        self.assertEqual(r.errors, [])
        self.assertEqual(r.sections["konsentratorlar"].variant, "B")
        self.assertEqual(int(r.sections["konsentratorlar"].cur["jami"]["dcu_cas"]), 11322)

    def test_consecutive_days_match(self):
        """05.11 dagi «oldingi holat» ustunlari 04.11 hisobotiga, strelkalar farqqa mos."""
        r4, r5 = self.parse("hisobot_2026-11-04.pdf"), self.parse("hisobot_2026-11-05.pdf")
        checked = bad = 0
        for sk, s5 in r5.sections.items():
            s4 = r4.sections[sk]
            for d, pv in s5.prev.items():
                for m, v in pv.items():
                    if m in s4.cur[d]:   # «dcu_program» 04.11 hisobotida faqat 03.11 holati sifatida bor
                        self.assertEqual(s4.cur[d][m], v, (sk, d, m))
            for d, deltas in s5.pdf_delta.items():
                if d == "jami":
                    continue
                for m, dv in deltas.items():
                    if m in s5.cur[d] and m in s4.cur[d]:
                        checked += 1
                        bad += (s5.cur[d][m] - s4.cur[d][m]) != dv
        self.assertGreater(checked, 400)
        self.assertEqual(bad, 0)

    def test_bad_file(self):
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile(suffix=".pdf") as fh:
            fh.write(b"%PDF-1.4 not really")
            fh.flush()
            r = parse_pdf(fh.name)
        self.assertFalse(r.ok)
