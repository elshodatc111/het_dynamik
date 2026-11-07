"""Qashqadaryo hududlari: rasmiy nomlar va variantlar."""
from .translit import fold

TOTAL_KEY = "jami"
TOTAL_NAME = "Jami"

# (kalit, ko‘rsatiladigan nom, qo‘shimcha variantlar)
DISTRICTS = (
    ("baxoriston", "Baxoriston ETK", ("baxoristonetk", "baxoriston")),
    ("guzor", "G‘uzor t.", ("guzort", "guzor", "guzortumani")),
    ("dehqonobod", "Dehqonobod t.", ("dehqonobodt", "dehqonobod")),
    ("qamashi", "Qamashi t.", ("qamashit", "qamashi")),
    ("qarshi_sh", "Qarshi sh.", ("qarshish", "qarshishahri")),
    ("qarshi_t", "Qarshi t.", ("qarshit", "qarshitumani")),
    ("koson", "Koson t.", ("kosont", "koson")),
    ("kasbi", "Kasbi t.", ("kasbit", "kasbi")),
    ("kitob", "Kitob t.", ("kitobt", "kitob")),
    ("mirishkor", "Mirishkor t.", ("mirishkort", "mirishkor")),
    ("muborak", "Muborak t.", ("muborakt", "muborak")),
    ("nishon", "Nishon t.", ("nishont", "nishon")),
    ("chiroqchi", "Chiroqchi t.", ("chiroqchit", "chiroqchi")),
    ("shahrisabz_sh", "Shahrisabz sh.", ("shahrisabzsh", "shahrisabzshahri")),
    ("shahrisabz_t", "Shahrisabz t.", ("shahrisabzt", "shahrisabztumani")),
    ("yakkabogh", "Yakkabog‘ t.", ("yakkabogt", "yakkabog")),
    ("kokdala", "Ko‘kdala t.", ("kokdalat", "kokdala")),
)

DISTRICT_KEYS = tuple(d[0] for d in DISTRICTS)

_ALIAS = {}
for key, name, aliases in DISTRICTS:
    _ALIAS[fold(name)] = key
    for a in aliases:
        _ALIAS[fold(a)] = key
for a in ("jami", "жами", "всего", "итого", "total"):
    _ALIAS[fold(a)] = TOTAL_KEY


def district_key(text: str):
    """Hudud nomi (kirill yoki lotin, istalgan apostrof) -> kalit yoki None."""
    return _ALIAS.get(fold(text or ""))
