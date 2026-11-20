"""Stranica Skupština: zvanični prenos sednica i izbor vesti, objava i snimaka o parlamentu.

Prenos: Narodna skupština na svojoj stranici za prenose ugrađuje YouTube snimak koji se menja od sednice do sednice.
Ovde se čita koji je snimak trenutno postavljen, a da li je prenos u toku proverava se preko YouTube API-ja.
"""
from __future__ import annotations

import logging
import re

import requests

from datetime import datetime

from .util import now_utc

log = logging.getLogger("prestonica")

IZVOR = "https://www.videonet.co.rs/nsrsZasedanje1l.htm"
NASLOV = "https://www.videonet.co.rs/nsrsSvi.htm"
UA = {"User-Agent": "Mozilla/5.0 (compatible; PrestonicaBot/1.0; +https://prestonica.rs)"}

_DA = re.compile(r"skup[sš]tin|poslani[kcč]|poslanik|poslanic|parlament", re.I)
_NE = re.compile(r"skup[sš]tin\w*\s+(grada|op[sš]tine|akcionara|stanara|vojvodine|crne gore|kosova|republike srpske)|"
                 r"(gradsk|op[sš]tinsk|generaln|evropsk|pokrajinsk)\w*\s+(skup[sš]tin|parlament)|"
                 r"evropsk\w+\s+parlament|parlament\w*\s+(evropske unije|eu\b)", re.I)
_JAKO = re.compile(r"skup[sš]tin\w*\s+srbije|narodn\w+\s+skup[sš]tin|narodn\w+\s+poslani|poslani[cč]k\w+\s+(grup|klub)|"
                   r"sednic\w+\s+skup[sš]tine|predsedni\w+\s+skup[sš]tine|u\s+skup[sš]tini|dom\w*\s+narodne", re.I)


def _yt(html: str) -> str:
    m = re.search(r"youtube(?:-nocookie)?\.com/embed/([A-Za-z0-9_-]{11})", html or "")
    return m.group(1) if m else ""


def _lat(s: str) -> str:
    cir = "абвгдђежзијклљмнњопрстћуфхцчџшАБВГДЂЕЖЗИЈКЛЉМНЊОПРСТЋУФХЦЧЏШ"
    lat = ["a", "b", "v", "g", "d", "đ", "e", "ž", "z", "i", "j", "k", "l", "lj", "m", "n", "nj", "o", "p", "r", "s", "t", "ć", "u", "f", "h", "c", "č", "dž", "š",
           "A", "B", "V", "G", "D", "Đ", "E", "Ž", "Z", "I", "J", "K", "L", "Lj", "M", "N", "Nj", "O", "P", "R", "S", "T", "Ć", "U", "F", "H", "C", "Č", "Dž", "Š"]
    return "".join(lat[cir.index(c)] if c in cir else c for c in s)


def _mladje(kada: str | None, sati: float) -> bool:
    """Da li je vremenska oznaka mlađa od zadatog broja sati."""
    if not kada:
        return False
    try:
        t = datetime.fromisoformat(str(kada).replace("Z", "+00:00"))
    except Exception:
        return False
    return (now_utc() - t).total_seconds() < sati * 3600


def osvezi(cfg, state: dict) -> None:
    """Upisuje u state['skupstina'] koji snimak Skupština trenutno prenosi i da li je prenos u toku."""
    sk = state.setdefault("skupstina", {})
    try:
        r = requests.get(IZVOR, headers=UA, timeout=15)
        r.raise_for_status()
        yt = _yt(r.text)
    except Exception as e:
        log.warning("Skupština: stranica prenosa nije dostupna: %s", e)
        return
    if not yt:
        return
    naslov = ""
    try:
        t = requests.get(NASLOV, headers=UA, timeout=10)
        t.encoding = t.apparent_encoding or "utf-8"
        m = re.search(r'class="styleZ"[^>]*>(.*?)</', t.text, re.S)
        naslov = _lat(re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip())[:160] if m else ""
    except Exception:
        naslov = ""
    naslov = naslov[:1].upper() + naslov[1:]
    from . import fetch

    # Prenos ume da bude uklonjen ili sakriven posle sednice. Tada ugrađeni plejer
    # pokazuje "Video unavailable", pa se takav snimak ne sme prikazivati.
    dostupan = fetch.video_dostupan(yt)
    ranije = sk.get("live", "none") if sk.get("yt") == yt else "none"
    live = "none"
    if dostupan:
        st = {}
        try:
            st = fetch.live_status([yt])
        except Exception as e:
            log.info("Skupština: status prenosa nije proveren: %s", e)
        if yt in st:
            live = st[yt].get("live") or "none"
        elif ranije == "live" and _mladje(sk.get("uzivo_t"), 2):
            # status se nije mogao potvrditi (nema API ključa ili je poziv pao):
            # prenos važi kao uživo najviše dva sata od poslednje potvrde, pa se gasi sam
            live = "live"
    sk.update({"yt": yt, "live": live, "naslov": naslov, "nedostupan": not dostupan,
               "provereno": now_utc().isoformat()})
    if live == "live":
        sk["uzivo_t"] = now_utc().isoformat()   # kad je prenos poslednji put viđen uživo (naslovna ističe Skupštinu tog dana)
    log.info("Skupština: snimak %s, status %s", yt, live)


def o_skupstini(tekst: str) -> bool:
    t = tekst or ""
    if _JAKO.search(t):
        return True
    return bool(_DA.search(t)) and not _NE.search(t)


def izbor(visible: list[dict], xposts: list[dict], videos: list[dict]) -> dict:
    def vest(a: dict) -> bool:
        t = " ".join([a.get("headline", ""), a.get("lead", ""), " ".join(a.get("tags", []) or [])])
        if _NE.search(a.get("headline", "")):
            return False
        return bool(_JAKO.search(t)) if a.get("section") == "svet" else o_skupstini(t)
    vesti = [a for a in visible if vest(a)]
    objave = [p for p in xposts if o_skupstini(p.get("text", ""))]
    snimci = [v for v in videos if o_skupstini(" ".join([v.get("_h", ""), v.get("title", ""), " ".join(v.get("oznake", []) or [])]))]
    return {"vesti": vesti, "objave": objave, "snimci": snimci}
