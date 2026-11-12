"""Objave portala na nalogu @Prestonicars na mreži X (zvanični X API, korisnički ključevi OAuth 1.0a).

Bez sva četiri ključa (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET) ništa se ne objavljuje.
Pravila (config/portal.yaml, x_objave): najviše jedna objava po osvežavanju, razmak između objava,
dnevni maksimum, tihi sati noću, samo važne vesti i sve autorske analize.
Objava sa linkom košta na X-u znatno više od objave bez linka, pa link dobijaju samo analize i najvažnije vesti;
ostale idu kao grafička kartica sa naslovom i kratkim tekstom.
Objavljuju se samo tekstovi iz ranijih osvežavanja, da bi stranica već bila na sajtu kad X učita pregled linka.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .util import age_hours

log = logging.getLogger("prestonica")
API = "https://api.x.com/2"
KEYS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")


def _auth():
    vals = [os.environ.get(k, "") for k in KEYS]
    if not all(vals):
        return None
    from requests_oauthlib import OAuth1
    return OAuth1(*vals)


def _xlen(s: str) -> int:
    """Dužina po pravilima X-a: linkovi se računaju kao 23 znaka."""
    n = 0
    for w in s.split(" "):
        n += 23 if w.startswith("http") else len(w)
    return n + s.count(" ")


def _cut(s: str, room: int) -> str:
    s = " ".join(str(s).split())
    if len(s) <= room:
        return s
    for end in (". ", "? ", "! "):
        i = s.rfind(end, 0, room)
        if i > room * 0.5:
            return s[:i + 1]
    return s[:room - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def text_for(a: dict, url: str | None) -> str:
    head = " ".join(str(a["headline"]).split())
    if not head.endswith((".", "?", "!", "…")):
        head += "."
    lead = str(a.get("lead", ""))
    parts = [("Analiza | " if a.get("kind") == "analiza" else "") + head]
    room = 275 - _xlen(parts[0]) - (25 if url else 0) - 2
    if room > 60 and lead:
        parts.append(_cut(lead, room))
    if url:
        parts.append(url)
    return "\n\n".join(parts)


def _upload(png: bytes, auth) -> str | None:
    try:
        r = requests.post(f"{API}/media/upload", auth=auth, timeout=60,
                          files={"media": ("kartica.png", png, "image/png")},
                          data={"media_category": "tweet_image", "media_type": "image/png"})
        d = r.json()
        mid = (d.get("data") or {}).get("id") or d.get("media_id_string")
        if not mid:
            log.warning("X objave: slika nije otpremljena (v2): %s %s", r.status_code, str(d)[:300])
            r = requests.post("https://upload.twitter.com/1.1/media/upload.json", auth=auth, timeout=60,
                              files={"media": ("kartica.png", png, "image/png")})
            mid = (r.json() if r.content else {}).get("media_id_string")
            if not mid:
                log.warning("X objave: slika nije otpremljena (v1.1): %s %s", r.status_code, r.text[:200])
        return mid
    except Exception as e:
        log.warning("X objave: slika: %s", e)
        return None



def _account_ok(cfg, st: dict, auth) -> bool:
    """Ključevi moraju pripadati nalogu portala (x.pratilac), nikad nekom drugom nalogu."""
    want = str((cfg.portal.get("x") or {}).get("pratilac", "Prestonicars")).lstrip("@").lower()
    if time.time() - st.get("nalog_t", 0) > 86400 or st.get("nalog", "").lower() != want:
        try:
            r = requests.get(f"{API}/users/me", auth=auth, timeout=20)
            st["nalog"] = ((r.json().get("data") or {}).get("username") or "") if r.status_code == 200 else ""
            st["nalog_t"] = time.time()
        except Exception as e:
            log.warning("X objave: provera naloga: %s", e)
            return False
    if st.get("nalog", "").lower() != want:
        log.warning("X objave: ključevi pripadaju nalogu @%s, a ne @%s; ništa se ne objavljuje", st.get("nalog") or "?", want)
        st["nalog_t"] = 0
        return False
    return True


_STOP = set("i u na za od do sa se je da su o po iz ka kod ili a ali ne nije bi biti će što koji koja koje kao posle pre zbog".split())


def _words(a: dict) -> set[str]:
    from .util import fold
    return {w for w in fold(a.get("headline", "")).replace(":", " ").replace(",", " ").replace(".", " ").split() if len(w) > 3 and w not in _STOP}


def _similar(a: dict, b: dict) -> bool:
    """Ista priča: zajedničke oznake ili ličnosti, ili veliko preklapanje reči u naslovu."""
    from .util import fold
    ta = {fold(t) for t in a.get("tags", [])} | {fold(p) for p in a.get("people", [])}
    tb = {fold(t) for t in b.get("tags", [])} | {fold(p) for p in b.get("people", [])}
    if len(ta & tb) >= 2:
        return True
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.3


def post_poll(cfg, state: dict, p: dict) -> str | None:
    """Ista anketa i na X-u (X anketa: do 4 odgovora, do 25 znakova), sa linkom ka glasanju na portalu."""
    oc = cfg.portal.get("x_objave") or {}
    if not oc.get("ukljuceno", False) or not oc.get("ankete", True):
        return None
    auth = _auth()
    if auth is None:
        return None
    st = state.setdefault("x_objave", {})
    if not _account_ok(cfg, st, auth):
        return None
    done = st.setdefault("ankete", {})
    if p["id"] in done:
        return done[p["id"]]
    opts = [o if len(o) <= 25 else o[:24].rstrip() + "…" for o in p["opcije"][:4]]
    domain = cfg.portal.get("domen") or "prestonica.rs"
    eyebrow = "Pitanje dana" if p.get("izvor") == "dnevna" else "Anketa"
    text = f"{eyebrow}: {p['pitanje']}\n\nGlasajte i na portalu: https://{domain}/ankete.html"
    try:
        from datetime import datetime as _dt
        mins = int((_dt.fromisoformat(p["kraj"]) - _dt.fromisoformat(p["start"])).total_seconds() // 60)
    except Exception:
        mins = 1440
    body = {"text": text, "poll": {"options": opts, "duration_minutes": max(5, min(mins, 10080))}}
    try:
        r = requests.post(f"{API}/tweets", json=body, auth=auth, timeout=30)
        tid = ((r.json() or {}).get("data") or {}).get("id")
    except Exception as e:
        log.warning("X anketa: %s", e)
        return None
    if not tid:
        log.warning("X anketa odbijena: %s %s", r.status_code, r.text[:300])
        return None
    done[p["id"]] = tid
    st.setdefault("istorija", []).append({"t": time.time(), "id": "anketa:" + p["id"], "x": tid, "link": True})
    log.info("X anketa objavljena: %s", tid)
    return tid

def run(cfg, state: dict, articles: list[dict], run_start: float) -> int:
    oc = cfg.portal.get("x_objave") or {}
    if not oc.get("ukljuceno", False):
        return 0
    auth = _auth()
    if auth is None:
        log.info("X objave: nema ključeva naloga, preskačem")
        return 0
    st = state.setdefault("x_objave", {})
    if not _account_ok(cfg, st, auth):
        return 0
    done = set(st.setdefault("objavljeno", []))
    hist = [h for h in st.setdefault("istorija", []) if time.time() - h["t"] < 7 * 86400]
    st["istorija"] = hist
    now = time.time()
    day = [h for h in hist if now - h["t"] < 86400]
    if len(day) >= int(oc.get("dnevno_najvise", 16)):
        return 0
    hour = datetime.now(ZoneInfo("Europe/Belgrade")).hour
    tiho = oc.get("tiho_od_do", [1, 6])
    # udarne vesti (važnost >= hitno_od_vaznosti) idu odmah: bez razmaka između objava i bez tihih sati
    samo_hitne = bool(hist and now - hist[-1]["t"] < float(oc.get("razmak_minuta", 40)) * 60) or bool(tiho and tiho[0] <= hour < tiho[1])
    hitno = int(oc.get("hitno_od_vaznosti", 99))

    prag = int(oc.get("prag_vaznosti", 7))
    max_h = float(oc.get("najstarije_sati", 6))
    domain = cfg.portal.get("domen") or cfg.portal.get("domain") or "prestonica.rs"
    cands = []
    for a in articles:
        if a.get("hidden") or a["id"] in done or age_hours(a["published"]) > max_h:
            continue
        # samo tekstovi iz ranijih osvežavanja: stranica je već objavljena na sajtu
        if age_hours(a["published"]) * 3600 < (time.time() - run_start) + 20 * 60:
            continue
        imp = int(a.get("importance") or 0)
        if samo_hitne and imp < hitno:
            continue
        if a.get("kind") == "analiza" or imp >= prag:
            cands.append(a)
    if not cands:
        return 0
    cands.sort(key=lambda a: a["published"], reverse=True)
    cands.sort(key=lambda a: (a.get("kind") != "analiza", -int(a.get("importance") or 0)))
    # raznovrsnost: ista priča (ili veoma slična) ne ide ponovo u prozoru od razmak_teme_sati
    by_id = {a["id"]: a for a in articles}
    win = float(oc.get("razmak_teme_sati", 8)) * 3600
    recent = [by_id[h["id"]] for h in hist if h["id"] in by_id and time.time() - h["t"] < win]
    left = int(oc.get("dnevno_najvise", 16)) - len(day)
    want_n = max(0, min(int(oc.get("po_osvezavanju", 1)), left))
    n_done = 0
    for a in cands:
        if n_done >= want_n:
            break
        if any(_similar(a, b) for b in recent):
            continue
        if _post_one(a, cfg, oc, st, hist, day, auth, domain):
            n_done += 1
            recent.append(a)
            time.sleep(2)
    return n_done


def _post_one(a: dict, cfg, oc: dict, st: dict, hist: list, day: list, auth, domain: str) -> bool:
    links_today = sum(1 for h in day + hist[-50:] if h.get("link") and time.time() - h["t"] < 86400)
    imp = int(a.get("importance") or 0)
    link = (a.get("kind") == "analiza" or imp >= int(oc.get("link_od_vaznosti", 9))) and links_today < int(oc.get("linkova_dnevno", 4))
    url = f"https://{domain}/vesti/{a['slug']}.html" if link else None

    body = {"text": text_for(a, url)}
    if not link or oc.get("kartica_i_uz_link", False):
        from . import kartica_x
        try:
            mid = _upload(kartica_x.render(a, cfg), auth)
        except Exception as e:
            log.warning("X objave: kartica: %s", e)
            mid = None
        if mid:
            body["media"] = {"media_ids": [mid]}
        elif not link:
            return False           # bez kartice i bez linka objava nema smisla
    try:
        r = requests.post(f"{API}/tweets", json=body, auth=auth, timeout=30)
        d = r.json()
    except Exception as e:
        log.warning("X objave: %s", e)
        return False
    tid = (d.get("data") or {}).get("id")
    if not tid:
        log.warning("X objave: objava odbijena: %s %s", r.status_code, str(d)[:300])
        if r.status_code in (400, 403):
            st["objavljeno"].append(a["id"])      # ne pokušavaj istu vest u krug
        return False
    st["objavljeno"] = (st["objavljeno"] + [a["id"]])[-2000:]
    hist.append({"t": time.time(), "id": a["id"], "x": tid, "link": bool(link)})
    log.info("X objave: objavljeno %s (%s) %s", tid, "link" if link else "kartica", a["headline"][:80])
    return True
