"""O‘zbek kirill → lotin (2021 yil imlosi) o‘girish va matn normallashtirish."""
import re

_LOWER = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "’", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ў": "o‘", "қ": "q", "ғ": "g‘", "ҳ": "h", "ҷ": "j",
}
_MAP = dict(_LOWER)
for k, v in _LOWER.items():
    up = k.upper()
    if v:
        _MAP[up] = v[0].upper() + v[1:]
    else:
        _MAP[up] = ""

_APOS = re.compile(r"[ʻʼ'`´’‘]")


def to_latin(text: str) -> str:
    """Kirill matnni lotinga o‘giradi. Lotin harflari o‘zgarishsiz qoladi."""
    if not text:
        return text
    out = []
    for ch in text:
        out.append(_MAP.get(ch, ch))
    return "".join(out)


def norm_apostrophes(text: str) -> str:
    """Barcha apostrof turlarini bitta ‘ ga keltiradi (G‘uzor, G'uzor, Gʻuzor ...)."""
    return _APOS.sub("‘", text)


def fold(text: str) -> str:
    """Taqqoslash uchun: lotinlashtirish, kichik harf, faqat harf va raqam."""
    t = to_latin(text or "").lower()
    t = _APOS.sub("", t)
    return re.sub(r"[^a-z0-9]+", "", t)
