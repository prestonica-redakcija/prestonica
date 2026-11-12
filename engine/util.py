"""Pomoćne funkcije: transliteracija, normalizacija teksta, slug, vreme."""
from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timezone

CYR = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Ђ": "Đ", "Е": "E", "Ж": "Ž", "З": "Z", "И": "I",
    "Ј": "J", "К": "K", "Л": "L", "Љ": "Lj", "М": "M", "Н": "N", "Њ": "Nj", "О": "O", "П": "P", "Р": "R",
    "С": "S", "Т": "T", "Ћ": "Ć", "У": "U", "Ф": "F", "Х": "H", "Ц": "C", "Ч": "Č", "Џ": "Dž", "Ш": "Š",
}
CYR.update({k.lower(): v.lower() for k, v in CYR.items()})

FOLD = str.maketrans({"č": "c", "ć": "c", "š": "s", "ž": "z", "đ": "dj"})

STOP = set(
    """i u na je se da za od sa o po iz do ne su će ce kao a ali ili te to ta taj ova ovaj ovo
    koji koja koje kod pre posle nakon zbog prema bez pod nad među medju kroz još jos već vec
    sve svi bio bila bili biti ima nema može moze treba danas sutra juče juce godine godina
    the of and in to on for vest vesti video foto evo kako zašto zasto šta sta ko gde kada
    nego samo ni li mi vi oni on ona ono njih njegov njen čak cak oko više vise manje""".split()
)


def latin(text: str) -> str:
    """Ćirilica -> latinica (ostali znaci ostaju)."""
    if not text:
        return ""
    return "".join(CYR.get(ch, ch) for ch in text)


def fold(text: str) -> str:
    """Za poređenje: latinica, mala slova, bez kvačica i interpunkcije."""
    t = latin(text or "").lower().translate(FOLD)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def tokens(text: str) -> list[str]:
    return [w for w in fold(text).split() if len(w) > 2 and w not in STOP]


def stem(w: str, n: int = 5) -> str:
    """Grubo korenovanje za srpski: prvih n slova bez završnih samoglasnika (mere/meru/merama -> mer)."""
    s = w[:n].rstrip("aeiou")
    return s if len(s) >= 3 else w[:n]


def stems(text: str, n: int = 5) -> set[str]:
    return {stem(w, n) for w in tokens(text)}


def slugify(text: str, max_len: int = 70) -> str:
    s = fold(text).replace(" ", "-")
    s = re.sub(r"-+", "-", s).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rsplit("-", 1)[0]
    return s or "vest"


def strip_html(text: str) -> str:
    if not text:
        return ""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age_hours(iso: str, now: datetime | None = None) -> float:
    now = now or now_utc()
    return max(0.0, (now - parse_iso(iso)).total_seconds() / 3600)


def sentence_case_caps(title: str) -> str:
    """Pretvara naslove tipa 'VUČIĆ O SKUPŠTINI: Velika bitka' u normalan oblik.
    Koristi se samo u probnom režimu; Claude u pravom radu sam piše naslove."""
    words = title.split()
    caps = [w for w in words if len(w) > 2 and w.isupper()]
    if len(caps) < 2:
        return title
    out = []
    for i, w in enumerate(words):
        if w.isupper() and len(w) > 1:
            core = w.strip("\"'„“”:;,.!?()")
            if len(core) <= 4 and core.isalpha():  # skraćenice: REM, NBS, SNS, EU
                out.append(w)
                continue
            w = w.lower()
        out.append(w)
    s = " ".join(out)
    return s[:1].upper() + s[1:]


SR_MONTHS = ["januar", "februar", "mart", "april", "maj", "jun", "jul", "avgust", "septembar", "oktobar", "novembar", "decembar"]
SR_DAYS = ["ponedeljak", "utorak", "sreda", "četvrtak", "petak", "subota", "nedelja"]


def sr_date(dt: datetime, with_day: bool = False) -> str:
    s = f"{dt.day}. {SR_MONTHS[dt.month - 1]} {dt.year}."
    if with_day:
        s = f"{SR_DAYS[dt.weekday()].capitalize()}, {s}"
    return s


def paragraphs(v) -> list[str]:
    """Pasusi teksta uvek kao lista rečenica-pasusa: model ponekad vrati listu kao JSON tekst,
    a stari zapisi mogu biti rasparčani na pojedinačna slova. Ispravlja i navodnike „...\"."""
    import json as _json
    import re as _re
    if isinstance(v, list) and len(v) > 20 and sum(len(str(x)) <= 1 for x in v) / len(v) > 0.9:
        v = "".join(str(x) for x in v)            # rasparčan tekst: spoji nazad
    if isinstance(v, str):
        t = v.strip()
        if t.startswith("["):
            try:
                v = _json.loads(t)
            except Exception:
                v = [x for x in _re.split(r'"\s*,\s*"', t.strip('[]"')) if x]
        else:
            v = _re.split(r"\n\s*\n|\n", t)
    out = []
    for x in v or []:
        x = " ".join(str(x).split())
        if x:
            out.append(_re.sub(r'„([^"“”\n]{1,200})"', "„\\1“", x))
    return out


def quotes(s: str) -> str:
    import re as _re
    return _re.sub(r'„([^"“”\n]{1,200})"', "„\\1“", s or "")


def broken_body(v) -> bool:
    """Tekst rasparčan na pojedinačna slova (stari zapis, razmaci izgubljeni): mora se ponovo napisati."""
    return isinstance(v, list) and len(v) > 20 and sum(len(str(x)) <= 1 for x in v) / len(v) > 0.9
