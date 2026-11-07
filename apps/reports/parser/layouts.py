"""PDF jadval tuzilmalari (shablonlar).

Har bir shablon — jadval qatoridagi (hudud nomi ustunidan boshlab) har bir katak
nimani anglatishining ro‘yxati:

    "district"   hudud nomi
    None         e’tiborsiz qoldiriladigan katak
    "kalit"      joriy sana (B) qiymati
    "kalit@prev" oldingi sana (A) holatidagi qiymat
    "%kalit"     foiz qiymati (joriy sana)
    "D"          oldingi qiymat ustuniga tegishli o‘zgarish (↑↓) — faqat tekshiruv uchun

Yangi hisobot ko‘rinishi kelsa: shu yerga yangi shablon qo‘shing (kalit — (bo‘lim, ustunlar soni)).
"""

D = "D"


def _pairs(*keys):
    """["a","b"] -> ["a","D","b","D"]"""
    out = []
    for k in keys:
        out += [k, D]
    return out


# ---------------------------------------------------------------- konsentratorlar
_CONC_LEVELS = _pairs("dcu_p0", "dcu_p1_30", "dcu_p30_50", "dcu_p50_80", "dcu_p80_100")

CONC_A = ["district", "dcu_program@prev", "dcu_cas", "dcu_online", "dcu_offline", "dcu_offline_3d", D] + _CONC_LEVELS
CONC_B = ["district", "dcu_program@prev", "dcu_cas", None, "dcu_online", "dcu_offline", "dcu_offline_3d", D] + _CONC_LEVELS
CONC_C = ["district", "dcu_program", "dcu_cas", "dcu_online", "dcu_offline", "dcu_offline_3d", D] + _CONC_LEVELS + ["dcu_no_meters"]

# ---------------------------------------------------------------- aloqasiz
NOME_A = [
    "district", "commissioned", "nome_all@prev", "nome_all", "%nome_pct", D,
] + _pairs("nome_res", "nome_ind", "nome_7_30", "nome_30_90", "nome_90p")
NOME_C = [
    "district", "commissioned", "nome_all", "%nome_pct", D,
] + _pairs("nome_res", "nome_ind", "nome_bal", "nome_7_30", "nome_30_90", "nome_90p")

# ---------------------------------------------------------------- uzilgan
OFF_A = [
    "district", "commissioned", "reloff_all@prev", "reloff_all", "%reloff_pct", D,
] + _pairs("reloff_4_7", "reloff_7_30", "reloff_30p", "direct_conn")
OFF_C = [
    "district", "commissioned", "reloff_all", "%reloff_pct", D,
] + _pairs("reloff_4_7", "reloff_7_30", "reloff_30p", "direct_conn")

# ---------------------------------------------------------------- 48 soatlik obxod
OB48 = ["district"]
for g in ("all", "res", "ind", "bal"):
    OB48 += [f"{g}_total", f"{g}_obxod", f"%{g}_pct", D]

# ---------------------------------------------------------------- balans / yuklama (faqat HETK ko‘rinishi)
BALYUK = [
    "district", "project", "cas", "obxod_once", "obxod_none", "obxod_3d", D,
    "problem_tp", D, "no_quvvat", "no_tt", "sxema_xato", "bal_nodata", "tp_load_0", "tp_load_150",
]

# ---------------------------------------------------------------- balans tafovuti
TAFOVUT = ["district", "tr_count", "tp_reserve", "bal_installed"] + _pairs(
    "loss_minus", "loss_0", "loss_0_10", "loss_10_20", "loss_20_50", "loss_50_100"
)

# ---------------------------------------------------------------- billing tafovuti
BILL_A = [
    "district", "bill_total", D, "bill_account", D, "bill_type", D, "bill_not_in_cas", D,
    "bill_not_askue", D, "bill_not_askue_res", "bill_not_askue_ind", "bill_dup", D,
    "cas_not_billing", D, "cas_nb_3d", "cas_nb_30d",
]
BILL_C = [
    "district", "bill_total", None, D, "bill_account", D, "bill_type", D, "bill_not_in_cas", D,
    "bill_not_askue", D, "bill_not_askue_res", "bill_not_askue_ind", "bill_dup", D,
    "cas_not_billing", D, "cas_nb_3d", "cas_nb_30d",
]

# ---------------------------------------------------------------- SmartApp
SMART = (
    ["district", "no_obxod"]              # 1–7-ustunlar birlashtirilgan katak: qiymat shu oraliqdan yig‘iladi
    + [None] * 6
    + ["sa_total", "sa_month"]            # oy boshidan: 9–14-ustunlar oralig‘idan
    + [None] * 5
    + ["sa_d0", "sa_d1", "sa_d2", "sa_d3", "sa_d4", "sa_d5", "sa_d6", "sa_prev_month"]
)
# Excelda birlashtirilgan kataklar PDF da turli ustunlarga tushadi (hatto «5 860» -> «5 8» + «60» bo‘lib qoladi).
# Shu kalitlarning qiymati ko‘rsatilgan ustunlardagi barcha bo‘sh bo‘lmagan matnlarni birlashtirib olinadi.
SMART_COALESCE = {"no_obxod": tuple(range(1, 8)), "sa_month": tuple(range(9, 15))}
BILL_C_COALESCE = {"bill_total": (1, 2)}

# (bo‘lim, ustunlar soni) -> (variant, shablon, qo‘shimcha parametrlar)
LAYOUTS = {
    ("konsentratorlar", 17): ("A", CONC_A, {}),
    ("konsentratorlar", 18, "B"): ("B", CONC_B, {}),
    ("konsentratorlar", 18, "C"): ("C", CONC_C, {}),
    ("aloqasiz", 16): ("A", NOME_A, {}),
    ("aloqasiz", 17): ("C", NOME_C, {}),
    ("uzilgan", 14): ("A", OFF_A, {}),
    ("uzilgan", 13): ("C", OFF_C, {}),
    ("obxod48", 17): ("A", OB48, {}),
    ("balans_yuklama", 15): ("C", BALYUK, {}),
    ("tafovut_balans", 16): ("C", TAFOVUT, {}),
    ("billing_tafovut", 19): ("A", BILL_A, {}),
    ("billing_tafovut", 20): ("C", BILL_C, {"coalesce": BILL_C_COALESCE}),
    ("smartapp", 23): ("C", SMART, {"coalesce": SMART_COALESCE}),
}


def find_layout(section: str, ncols: int, sample_cells=()):
    """Bo‘lim va ustunlar soni bo‘yicha shablonni topadi.

    Konsentratorlar jadvalida 18 ustunli ikki xil ko‘rinish bor:
    B — 3-ustunda "Farqi" (strelkali) qiymat bor, C — HETK ko‘rinishi (3-ustun Online soni).
    """
    if section == "konsentratorlar" and ncols == 18:
        cell3 = (sample_cells[3] if len(sample_cells) > 3 else "") or ""
        variant = "B" if ("↑" in cell3 or "↓" in cell3) else "C"
        return LAYOUTS.get((section, 18, variant))
    return LAYOUTS.get((section, ncols))
