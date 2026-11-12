"""X (Twitter): javne objave sa liste naloga, preko zvaničnog X API-ja (plaća se po pročitanoj objavi).

Bez ključa X_BEARER_TOKEN ništa se ne preuzima i portal radi kao i ranije.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import timedelta

import requests
import yaml

from .config import ROOT
from .util import age_hours, now_utc

log = logging.getLogger("prestonica")
API = "https://api.x.com/2"


def _yaml() -> dict:
    path = ROOT / "config" / "x_nalozi.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def excluded() -> set[str]:
    """Nalozi koji se nikad ne prikazuju na stranici Mreže (mediji: njihove vesti već stižu preko RSS-a)."""
    y = _yaml()
    out = {str(h).lstrip("@").strip().lower() for h in (y.get("iskljuci") or [])}
    for a in y.get("nalozi") or []:
        if a.get("grupa") == "mediji" or a.get("ukljucen") is False:
            out.add(str(a.get("x", "")).lstrip("@").strip().lower())
    return out


def accounts(cfg, state: dict | None = None) -> list[dict]:
    """Nalozi iz config/x_nalozi.yaml, ličnosti sa poljem x i svi koje prati nalog portala na X-u (bez medija)."""
    out, seen = [], set(excluded())
    for a in _yaml().get("nalozi") or []:
        if True:
            h = str(a.get("x", "")).lstrip("@").strip()
            if h and a.get("ukljucen", True) and h.lower() not in seen:
                seen.add(h.lower())
                out.append({**a, "x": h})
    for p in cfg.licnosti:
        h = str(p.get("x", "")).lstrip("@").strip()
        if h and h.lower() not in seen:
            seen.add(h.lower())
            out.append({"x": h, "ime": p["ime"], "grupa": p.get("grupa", ""), "uloga": p.get("uloga", "")})
    for a in ((state or {}).get("x") or {}).get("pratim", []):
        if a["x"].lower() not in seen:
            seen.add(a["x"].lower())
            out.append(a)
    return out


def _following(cfg, state: dict, hdr: dict) -> None:
    """Spisak naloga koje prati nalog portala (x.pratilac): uredništvo bira koga pratimo direktno na X-u."""
    xs = cfg.portal.get("x") or {}
    handle = str(xs.get("pratilac", "")).lstrip("@").strip()
    st = state.setdefault("x", {})
    if not handle or time.time() - st.get("pratim_t", 0) < float(xs.get("pratilac_svakih_sati", 24)) * 3600:
        return
    try:
        r = requests.get(f"{API}/users/by/username/{handle}", headers=hdr, timeout=20)
        uid = (r.json().get("data") or {}).get("id")
        if not uid:
            log.warning("X: nalog portala @%s nije pronađen: %s", handle, r.text[:200])
            return
        out, token = [], None
        for _ in range(5):
            params = {"max_results": 1000, "user.fields": "name"}
            if token:
                params["pagination_token"] = token
            r = requests.get(f"{API}/users/{uid}/following", params=params, headers=hdr, timeout=30)
            data = r.json()
            if r.status_code != 200:
                log.warning("X: lista praćenih %s %s", r.status_code, str(data)[:200])
                return
            for u in data.get("data") or []:
                out.append({"x": u["username"], "ime": u.get("name") or u["username"], "grupa": "", "uloga": ""})
                st.setdefault("ids", {})[u["username"].lower()] = u["id"]
            token = (data.get("meta") or {}).get("next_token")
            if not token:
                break
        st["pratim"] = out
        st["pratim_t"] = time.time()
        log.info("X: @%s prati %d naloga", handle, len(out))
    except Exception as e:
        log.warning("X: lista praćenih: %s", e)


def _set_profile(st: dict, u: dict) -> None:
    img = str(u.get("profile_image_url") or "")
    if not img:
        return
    for small in ("_normal.", "_bigger.", "_mini."):
        img = img.replace(small, "_200x200.")
    st.setdefault("profil", {})[u["username"].lower()] = {"img": img, "ime_x": u.get("name", ""), "t": time.time()}


def _profiles(st: dict, accs: list[dict], ids: dict, hdr: dict, every_h: float) -> None:
    prof = st.setdefault("profil", {})
    now = time.time()
    due = [a["x"] for a in accs if ids.get(a["x"].lower()) and now - (prof.get(a["x"].lower()) or {}).get("t", 0) > every_h * 3600]
    for i in range(0, len(due), 100):
        try:
            r = requests.get(f"{API}/users/by", params={"usernames": ",".join(due[i:i + 100]),
                             "user.fields": "profile_image_url,name"}, headers=hdr, timeout=20)
            if r.status_code != 200:
                log.warning("X profili: %s %s", r.status_code, r.text[:200])
                return
            for u in r.json().get("data") or []:
                _set_profile(st, u)
        except Exception as e:
            log.warning("X profili: %s", e)
            return
    if due:
        log.info("X: osvežene slike profila za %d naloga", len(due))


def fetch(cfg, state: dict) -> int:
    token = os.environ.get("X_BEARER_TOKEN", "")
    xs = cfg.portal.get("x") or {}
    if not token or not xs.get("ukljuceno", True):
        return 0
    hdr = {"Authorization": f"Bearer {token}"}
    _following(cfg, state, hdr)
    accs = accounts(cfg, state)
    if not accs:
        return 0
    st = state.setdefault("x", {})
    ids = st.setdefault("ids", {})            # nalog -> X id (prazno = ne postoji)
    last = st.setdefault("poslednji", {})     # nalog -> {since_id, t}
    posts = st.setdefault("posts", [])

    # 1. X id-jevi naloga, jednom (plaća se po nalogu)
    need = [a["x"] for a in accs if a["x"].lower() not in ids]
    for i in range(0, len(need), 100):
        try:
            r = requests.get(f"{API}/users/by", params={"usernames": ",".join(need[i:i + 100]),
                             "user.fields": "profile_image_url,name"}, headers=hdr, timeout=20)
            data = r.json()
            for u in data.get("data") or []:
                ids[u["username"].lower()] = u["id"]
                _set_profile(st, u)
            for e in data.get("errors") or []:
                if e.get("value"):
                    ids[str(e["value"]).lower()] = ""
            if r.status_code != 200:
                log.warning("X nalozi: %s %s", r.status_code, str(data)[:300])
                return 0
        except Exception as e:
            log.warning("X nalozi: %s", e)
            return 0

    # 1b. Aktuelne slike profila: osvežavaju se na profil_svakih_sati (X naplaćuje čitanje naloga)
    _profiles(st, accs, ids, hdr, float(xs.get("profil_svakih_sati", 72)))

    # 2. Nove objave: svaki nalog najviše jednom u interval_minuta, najviše naloga_po_osvezavanju naloga po krugu
    interval = float(xs.get("interval_minuta", 30)) * 60
    per_run = int(xs.get("naloga_po_osvezavanju", 40))
    now = time.time()
    due = [a for a in accs if ids.get(a["x"].lower())]
    due.sort(key=lambda a: (last.get(a["x"].lower()) or {}).get("t", 0))
    # nalog može imati svoj interval (npr. mediji ređe, jer objavljuju mnogo i to već imamo preko RSS-a)
    due = [a for a in due if now - (last.get(a["x"].lower()) or {}).get("t", 0) >= float(a.get("interval_minuta") or interval / 60) * 60][:per_run]
    have = {p["id"] for p in posts}
    novih = 0
    for a in due:
        h = a["x"].lower()
        prev = last.get(h) or {}
        params = {"max_results": max(5, int(a.get("max_po_nalogu") or xs.get("max_po_nalogu", 5))), "exclude": "retweets,replies",
                  "tweet.fields": "created_at,public_metrics,lang"}
        if prev.get("since_id"):
            params["since_id"] = prev["since_id"]
        else:
            params["start_time"] = (now_utc() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            r = requests.get(f"{API}/users/{ids[h]}/tweets", params=params, headers=hdr, timeout=20)
            if r.status_code in (402, 403, 429):
                log.warning("X: %s %s", r.status_code, r.text[:200])
                break
            data = r.json()
        except Exception as e:
            log.warning("X %s: %s", a["x"], e)
            continue
        last[h] = {"since_id": (data.get("meta") or {}).get("newest_id") or prev.get("since_id"), "t": now}
        for t in data.get("data") or []:
            if t["id"] in have:
                continue
            posts.append({"id": t["id"], "x": a["x"], "ime": a.get("ime") or a["x"], "grupa": a.get("grupa", ""),
                          "uloga": a.get("uloga", ""), "text": t.get("text", ""),
                          "published": t.get("created_at", now_utc().isoformat()).replace("Z", "+00:00"),
                          "lajk": (t.get("public_metrics") or {}).get("like_count", 0)})
            have.add(t["id"])
            novih += 1
    keep_h = float(xs.get("cuvaj_dana", 7)) * 24
    skip = excluded()
    st["posts"] = sorted((p for p in posts if age_hours(p["published"]) <= keep_h and p["x"].lower() not in skip),
                         key=lambda p: p["published"], reverse=True)[:800]
    log.info("X: %d novih objava, provereno %d naloga", novih, len(due))
    return novih
