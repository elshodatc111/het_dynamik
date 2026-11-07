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
        self.assertEqual(len(r.sections), 8)
        self.assertEqual(r.skipped_pages, [5])         # diagramma sahifasi
        # «5 8» + «60» = 5860 kabi bo‘lingan raqamlar
        self.assertEqual(int(r.sections["smartapp"].cur["qarshi_sh"]["no_obxod"]), 5860)
        self.assertEqual(int(r.sections["billing_tafovut"].cur["jami"]["bill_total"]), 2984)

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
