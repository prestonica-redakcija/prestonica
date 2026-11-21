"""Slanje podataka o YouTube kanalu i X nalogu internoj aplikaciji (Komanda).

Ključevi za YouTube i X već postoje u GitHub Actions, pa se ne dupliraju u
Cloudflare: portal pri svakom osvežavanju pročita stanje svojih naloga i pošalje
ga servisu statistike. Aplikacija ga samo prikazuje.

Ako ključ nedostaje ili poziv padne, taj deo se preskače bez greške: statistika
portala nikada ne sme da obori osvežavanje vesti.
"""

from __future__ import annotations

import logging
import os

import requests

from .util import now_utc

log = logging.getLogger("prestonica")
UA = {"User-Agent": "PrestonicaBot/1.0 (+https://prestonica.rs/o-nama.html)"}


def _youtube(kanal: str) -> dict | None:
    kljuc = os.environ.get("YOUTUBE_API_KEY", "")
    if not kljuc or not kanal:
        return None

    def api(put: str) -> dict:
        r = requests.get(f"https://www.googleapis.com/youtube/v3/{put}&key={kljuc}", headers=UA, timeout=20)
        r.raise_for_status()
        return r.json()

    k = api(f"channels?part=snippet,statistics,contentDetails&id={kanal}")
    stavke = k.get("items") or []
    if not stavke:
        log.info("Komanda: YouTube kanal %s nije nađen", kanal)
        return None
    c = stavke[0]
    st = c.get("statistics") or {}
    lista = ((c.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    videi: list[dict] = []
    if lista:
        pl = api(f"playlistItems?part=snippet&maxResults=25&playlistId={lista}")
        ids = [((x.get("snippet") or {}).get("resourceId") or {}).get("videoId") for x in pl.get("items") or []]
        ids = [i for i in ids if i]
        if ids:
            vv = api(f"videos?part=snippet,statistics,contentDetails&id={','.join(ids)}")
            for x in vv.get("items") or []:
                s, stat, cd = x.get("snippet") or {}, x.get("statistics") or {}, x.get("contentDetails") or {}
                videi.append({
                    "id": x.get("id", ""), "naslov": s.get("title", ""),
                    "objavljen": s.get("publishedAt", ""),
                    "pregledi": int(stat.get("viewCount") or 0),
                    "lajkovi": int(stat.get("likeCount") or 0),
                    "komentari": int(stat.get("commentCount") or 0),
                    "trajanje": cd.get("duration", ""),
                })
            videi.sort(key=lambda v: v["objavljen"], reverse=True)
    return {
        "kanal": {
            "ime": (c.get("snippet") or {}).get("title", ""),
            "pretplatnici": int(st.get("subscriberCount") or 0),
            "pregledi": int(st.get("viewCount") or 0),
            "videa": int(st.get("videoCount") or 0),
            "skriveni": st.get("hiddenSubscriberCount") is True,
        },
        "videi": videi,
        "generisano": int(now_utc().timestamp() * 1000),
    }


def _x(ime: str) -> dict | None:
    token = os.environ.get("X_BEARER_TOKEN", "")
    if not token or not ime:
        return None
    glava = {"Authorization": "Bearer " + token, **UA}

    def api(put: str) -> dict:
        r = requests.get(f"https://api.x.com/2/{put}", headers=glava, timeout=20)
        if r.status_code == 429:
            raise RuntimeError("kvota X API-ja privremeno potrošena")
        r.raise_for_status()
        return r.json()

    u = api(f"users/by/username/{ime}?user.fields=public_metrics,created_at,description")
    nalog = u.get("data") or {}
    if not nalog.get("id"):
        return None
    pm = nalog.get("public_metrics") or {}
    objave: list[dict] = []
    try:
        t = api(f"users/{nalog['id']}/tweets?max_results=50&exclude=replies"
                "&tweet.fields=public_metrics,created_at,entities")
        for x in t.get("data") or []:
            m = x.get("public_metrics") or {}
            objave.append({
                "id": x.get("id", ""), "tekst": (x.get("text") or "")[:200],
                "vreme": x.get("created_at", ""),
                "imp": m.get("impression_count", 0), "lajk": m.get("like_count", 0),
                "rt": m.get("retweet_count", 0), "odg": m.get("reply_count", 0),
                "cit": m.get("quote_count", 0),
                "linkKlik": bool(((x.get("entities") or {}).get("urls") or [])),
            })
    except Exception as e:
        log.info("Komanda: objave sa X-a nisu preuzete: %s", e)
    return {
        "nalog": {
            "ime": nalog.get("username", ime), "prikaz": nalog.get("name", ""),
            "pratioci": pm.get("followers_count", 0), "prati": pm.get("following_count", 0),
            "objava": pm.get("tweet_count", 0), "od": nalog.get("created_at", ""),
        },
        "objave": objave,
        "generisano": int(now_utc().timestamp() * 1000),
    }


def posalji(cfg) -> None:
    """Pročita stanje YouTube kanala i X naloga i pošalje ga servisu statistike."""
    upis = os.environ.get("STAT_UPIS", "")
    if not upis:
        return
    # Servis statistike ima sopstvene ključeve za YouTube i X, pa portal ništa ne šalje
    # osim ako se izričito ne traži. X se naplaćuje po pozivu i duplo čitanje je čist trošak.
    trazeni = {x.strip() for x in os.environ.get("KOMANDA_IZVORI", "").split(",") if x.strip()}
    if not trazeni:
        return
    domen = cfg.portal.get("domen") or "prestonica.rs"
    nas = cfg.portal.get("nas_kanal") or {}
    telo: dict = {}
    try:
        yt = _youtube(nas.get("youtube", "")) if "youtube" in trazeni else None
        if yt:
            telo["youtube"] = yt
    except Exception as e:
        log.info("Komanda: YouTube nije preuzet: %s", e)
    try:
        xx = _x((cfg.portal.get("x_objave") or {}).get("nalog") or "Prestonicars") if "x" in trazeni else None
        if xx:
            telo["x"] = xx
    except Exception as e:
        log.info("Komanda: X nije preuzet: %s", e)
    if not telo:
        return
    try:
        r = requests.post(f"https://{domen}/api/stat/izvori", json=telo,
                          headers={"X-Upis": upis, **UA}, timeout=25)
        if r.status_code >= 300:
            log.info("Komanda: servis statistike odbio podatke: %s %s", r.status_code, r.text[:160])
        else:
            log.info("Komanda: poslato (%s)", ", ".join(sorted(telo)))
    except Exception as e:
        log.info("Komanda: slanje nije uspelo: %s", e)
