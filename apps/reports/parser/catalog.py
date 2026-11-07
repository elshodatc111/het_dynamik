"""Hisobot bo‘limlari va ko‘rsatkichlar katalogi (hammasi lotin alifbosida).

Katalog bazaga `sync_catalog()` orqali yoziladi. Yangi ko‘rsatkich qo‘shish uchun
shu yerga qo‘shing va `layouts.py` dagi shablonda uni ishlating.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    group: str = ""          # Excel / jadval sarlavhasidagi yuqori daraja
    kind: str = "count"      # count | pct
    additive: bool = True    # hududlar yig‘indisi "Jami" ga teng bo‘lishi kerakmi


@dataclass(frozen=True)
class SectionDef:
    key: str
    title: str
    short: str
    metrics: tuple


def C(key, label, group="", additive=True):
    return MetricDef(key, label, group, "count", additive)


def P(key, label, group=""):
    return MetricDef(key, label, group, "pct", False)


_ALL_SECTIONS = (
    SectionDef(
        "konsentratorlar",
        "CAS dasturidagi konsentratorlar holati",
        "Konsentratorlar",
        (
            C("dcu_program", "Dasturda mavjud", "Konsentratorlar"),
            C("dcu_cas", "CAS ga yuklangan", "Konsentratorlar"),
            C("dcu_online", "Online", "Konsentratorlar"),
            C("dcu_offline", "Offline (jami)", "Konsentratorlar"),
            C("dcu_offline_3d", "Offline, 3 kundan ortiq", "Konsentratorlar"),
            C("dcu_p0", "0%", "Obxod olish darajasi bo‘yicha"),
            C("dcu_p1_30", "1–30%", "Obxod olish darajasi bo‘yicha"),
            C("dcu_p30_50", "30–50%", "Obxod olish darajasi bo‘yicha"),
            C("dcu_p50_80", "50–80%", "Obxod olish darajasi bo‘yicha"),
            C("dcu_p80_100", "80–100%", "Obxod olish darajasi bo‘yicha"),
            C("dcu_no_meters", "Iste’molchi biriktirilmagan", "Konsentratorlar"),
        ),
    ),
    SectionDef(
        "aloqasiz",
        "CAS dasturida aloqaga chiqmagan hisoblagichlar",
        "Aloqasiz hisoblagichlar",
        (
            C("commissioned", "Aloqaga chiqqan hisoblagichlar", "Umumiy"),
            C("nome_all", "Jami", "Aloqaga chiqmaganlar"),
            P("nome_pct", "Ulushi, %", "Aloqaga chiqmaganlar"),
            C("nome_res", "Aholi", "Iste’molchi turi kesimida"),
            C("nome_ind", "Yuridik / ulgurji", "Iste’molchi turi kesimida"),
            C("nome_bal", "Balans", "Iste’molchi turi kesimida"),
            C("nome_7_30", "7–30 kun", "Vaqt kesimida"),
            C("nome_30_90", "1–3 oy", "Vaqt kesimida"),
            C("nome_90p", "3 oydan ortiq", "Vaqt kesimida"),
        ),
    ),
    SectionDef(
        "uzilgan",
        "CAS dasturida tarmoqdan uzilgan hisoblagichlar",
        "Uzilgan hisoblagichlar",
        (
            C("commissioned", "Aloqaga chiqqan hisoblagichlar", "Umumiy"),
            C("reloff_all", "Jami", "Tarmoqdan uzilganlar"),
            P("reloff_pct", "Ulushi, %", "Tarmoqdan uzilganlar"),
            C("reloff_4_7", "4–7 kun", "Shundan"),
            C("reloff_7_30", "7–30 kun", "Shundan"),
            C("reloff_30p", "30 kundan ortiq", "Shundan"),
            C("direct_conn", "To‘g‘ridan-to‘g‘ri ulanishlar (peremichka)", "Boshqa"),
        ),
    ),
    SectionDef(
        "obxod48",
        "48 soat ichida obxod bergan hisoblagichlar",
        "48 soatlik obxod",
        (
            C("all_total", "Jami hisoblagichlar", "Jami"),
            C("all_obxod", "Obxod olgan", "Jami"),
            P("all_pct", "Ulushi, %", "Jami"),
            C("res_total", "Aholi hisoblagichlari", "Aholi iste’molchilari"),
            C("res_obxod", "Obxod olgan", "Aholi iste’molchilari"),
            P("res_pct", "Ulushi, %", "Aholi iste’molchilari"),
            C("ind_total", "Ulgurji hisoblagichlar", "Ulgurji iste’molchilar"),
            C("ind_obxod", "Obxod olgan", "Ulgurji iste’molchilar"),
            P("ind_pct", "Ulushi, %", "Ulgurji iste’molchilar"),
            C("bal_total", "Balans hisoblagichlar", "Balans hisoblagichlar"),
            C("bal_obxod", "Obxod olgan", "Balans hisoblagichlar"),
            P("bal_pct", "Ulushi, %", "Balans hisoblagichlar"),
        ),
    ),
    SectionDef(
        "balans_yuklama",
        "Balans hisoblagichlar va transformatorlar yuklamasi",
        "Balans va yuklama",
        (
            C("project", "Loyiha doirasida", "Balans hisoblagichlar"),
            C("cas", "CAS ga yuklangan", "Balans hisoblagichlar"),
            C("obxod_once", "Bir marta obxod olgan", "Obxod olish ko‘rsatkichi"),
            C("obxod_none", "Umuman obxod olmagan", "Obxod olish ko‘rsatkichi"),
            C("obxod_3d", "Obxodsiz 3 kundan ortiq", "Obxod olish ko‘rsatkichi"),
            C("problem_tp", "Muammoli transformatorlar", "Muammoli transformatorlar"),
            C("no_quvvat", "Quvvati yuklanmagan", "Shundan"),
            C("no_tt", "TT nominali noto‘g‘ri", "Shundan"),
            C("sxema_xato", "Sxemasi xato", "Shundan"),
            C("bal_nodata", "Balans hisoblagich aloqaga chiqmagan", "Shundan"),
            C("tp_load_0", "TP yuklamasi 0%", "Shundan"),
            C("tp_load_150", "TP yuklamasi 150%", "Shundan"),
        ),
    ),
    SectionDef(
        "tafovut_balans",
        "Balans va iste’molchilar hisoblagichlari o‘rtasidagi tafovut",
        "Balans tafovuti",
        (
            C("tr_count", "CAS dasturidagi transformatorlar", "Transformatorlar"),
            C("tp_reserve", "Shundan rezervda", "Transformatorlar"),
            C("bal_installed", "Balans hisoblagich o‘rnatilgan TPlar", "Transformatorlar"),
            C("loss_minus", "Manfiy", "Balans va iste’molchilar o‘rtasidagi farq"),
            C("loss_0", "0%", "Balans va iste’molchilar o‘rtasidagi farq"),
            C("loss_0_10", "1–10%", "Balans va iste’molchilar o‘rtasidagi farq"),
            C("loss_10_20", "10–20%", "Balans va iste’molchilar o‘rtasidagi farq"),
            C("loss_20_50", "20–50%", "Balans va iste’molchilar o‘rtasidagi farq"),
            C("loss_50_100", "50–100%", "Balans va iste’molchilar o‘rtasidagi farq"),
        ),
    ),
    SectionDef(
        "billing_tafovut",
        "Billing va CAS dasturlaridagi tafovut",
        "Billing tafovuti",
        (
            C("bill_total", "Billing bilan ma’lumot almashmayotganlar", "Billing bilan almashinuv"),
            C("bill_account", "Hisob raqami xato", "Shundan"),
            C("bill_type", "Iste’molchi turi xato", "Shundan"),
            C("bill_not_in_cas", "CAS dasturida mavjud emas", "Shundan"),
            C("bill_not_askue", "ASKUE ga mos emas (jami)", "Billingda mavjud"),
            C("bill_not_askue_res", "Aholi", "Billingda mavjud"),
            C("bill_not_askue_ind", "Ulgurji", "Billingda mavjud"),
            C("bill_dup", "Dublikat hisoblagichlar", "Billingda mavjud"),
            C("cas_not_billing", "CAS da bor, Billingga kiritilmagan", "CAS da mavjud"),
            C("cas_nb_3d", "Shundan 3 kun ichida obxod olgan", "CAS da mavjud"),
            C("cas_nb_30d", "Shundan 30 kun ichida obxod olgan", "CAS da mavjud"),
        ),
    ),
    SectionDef(
        "smartapp",
        "SmartApp orqali aloqaga chiqmagan hisoblagich ma’lumotlari",
        "SmartApp",
        (
            C("no_obxod", "Obxod olmagan hisoblagichlar", "Umumiy"),
            C("sa_total", "SmartApp orqali yuborilgan (jami)", "SmartApp"),
            C("sa_month", "Oy boshidan", "SmartApp"),
            C("sa_d0", "Hisobot kuni", "Oxirgi 7 kunda"),
            C("sa_d1", "1 kun oldin", "Oxirgi 7 kunda"),
            C("sa_d2", "2 kun oldin", "Oxirgi 7 kunda"),
            C("sa_d3", "3 kun oldin", "Oxirgi 7 kunda"),
            C("sa_d4", "4 kun oldin", "Oxirgi 7 kunda"),
            C("sa_d5", "5 kun oldin", "Oxirgi 7 kunda"),
            C("sa_d6", "6 kun oldin", "Oxirgi 7 kunda"),
            C("sa_prev_month", "O‘tgan oyda", "SmartApp"),
        ),
    ),
)

# Faqat shu bo‘limlar o‘qiladi va saqlanadi (to‘liq hisobotning 1, 2, 3, 4, 6, 7, 8-betlari).
# SmartApp (9-bet) kerak emas — o‘tkazib yuboriladi.
# Qaytarish uchun kalitni shu ro‘yxatga qo‘shing.
ENABLED_SECTIONS = (
    "konsentratorlar", "aloqasiz", "uzilgan", "balans_yuklama", "obxod48", "tafovut_balans", "billing_tafovut",
)
SECTIONS = tuple(s for s in _ALL_SECTIONS if s.key in ENABLED_SECTIONS)

SECTION_BY_KEY = {s.key: s for s in _ALL_SECTIONS}
METRIC_BY_KEY = {(s.key, m.key): m for s in _ALL_SECTIONS for m in s.metrics}
