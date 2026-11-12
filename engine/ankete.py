"""Ankete čitalaca ("Pitanje dana").

Pitanje dana bira Claude iz najvažnijih tema dana (neutralno, uravnoteženi odgovori), a redakcija može
pustiti svoju anketu porukom na Telegramu: /anketa Pitanje? | odgovor 1 | odgovor 2 | ... | 3d
Glasove čuva mali servis na Cloudflare-u (cloudflare/ankete-worker.js), sajt samo prikazuje.
Kad anketa istekne, konačni rezultati se upisuju u arhivu (state.json), za trendove.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

import requests

from .util import age_hours, now_utc, parse_iso

log = logging.getLogger("prestonica")


def _new(pitanje: str, opcije: list[str], dana: float, izvor: str, vest: str = "", tema: str = "") -> dict:
    now = now_utc()
    return {"id": "a" + now.strftime("%Y%m%d%H%M"), "pitanje": pitanje.strip(), "opcije": [o.strip() for o in opcije if o.strip()][:4],
            "start": now.isoformat(timespec="minutes"), "kraj": (now + timedelta(days=dana)).isoformat(timespec="minutes"),
            "izvor": izvor, "vest": vest, "tema": tema}


def parse_command(arg: str, default_days: float) -> dict | None:
    """'/anketa Pitanje? | A | B | C | 3d' -> anketa (bez id-ja), ili None ako nije ispravna."""
    parts = [p.strip() for p in arg.split("|") if p.strip()]
    days = default_days
    if parts and re.fullmatch(r"\d{1,2}\s*d", parts[-1], re.I):
        days = float(re.sub(r"\D", "", parts.pop()))
    if len(parts) < 3 or len(parts) > 5:
        return None
    return {"pitanje": parts[0], "opcije": parts[1:], "dana": max(1.0, min(days, 30.0))}


def active(state: dict) -> list[dict]:
    now = now_utc()
    return [p for p in state.get("ankete", []) if parse_iso(p["kraj"]) > now]


def run(cfg, state: dict, articles: list[dict], writer, live: bool) -> None:
    ac = cfg.portal.get("ankete") or {}
    if not ac.get("ukljuceno", False):
        return
    if not str(ac.get("api_url", "")).strip():
        return                     # bez servisa za glasove ankete se ne puštaju
    polls = state.setdefault("ankete", [])
    days = float(ac.get("trajanje_dana", 3))
    have = {p["id"] for p in polls}

    # 1. ankete redakcije (Telegram), uvek imaju prednost
    for q in state.pop("ankete_red", []) or []:
        p = _new(q["pitanje"], q["opcije"], q.get("dana", days), "redakcija")
        while p["id"] in have:
            p["id"] += "x"
        polls.append(p)
        have.add(p["id"])
        log.info("Anketa redakcije: %s", p["pitanje"])

    # 2. pitanje dana: jednom u nova_svakih_sati, posle 7 ujutru
    last = max((parse_iso(p["start"]) for p in polls), default=None)
    hour = now_utc().astimezone(ZoneInfo("Europe/Belgrade")).hour
    due = last is None or (now_utc() - last).total_seconds() > float(ac.get("nova_svakih_sati", 24)) * 3600
    if live and due and hour >= int(ac.get("od_sata", 7)) and hasattr(writer, "poll"):
        arts = [a for a in articles if a["kind"] == "vest" and not a.get("hidden") and age_hours(a["published"]) < 24]
        arts.sort(key=lambda a: (-int(a.get("importance") or 0), a["published"]))
        recent_q = [p["pitanje"] for p in polls[-10:]]
        got = writer.poll(arts[:25], recent_q) if arts else None
        if got and got.get("pitanje") and 2 <= len(got.get("opcije") or []) <= 4:
            p = _new(got["pitanje"], got["opcije"], days, "dnevna", got.get("vest", ""), got.get("tema", ""))
            if p["id"] not in have:
                polls.append(p)
                log.info("Pitanje dana: %s", p["pitanje"])

    # 2b. ista anketa i na X-u (nalog @Prestonicars), jednom po anketi
    try:
        from . import objave_x
        for p in polls[-3:]:
            if live and parse_iso(p["kraj"]) > now_utc():
                objave_x.post_poll(cfg, state, p)
    except Exception as e:
        log.warning("X anketa: %s", e)

    # 3. konačni rezultati isteklih anketa (za arhivu i trendove)
    api = str(ac.get("api_url", "")).rstrip("/")
    if api and live:
        if api.startswith("/"):
            api = f"https://{cfg.portal.get('domen', 'prestonica.rs')}{api}"
        now = now_utc()
        todo = [p for p in polls if "rezultat" not in p and parse_iso(p["kraj"]) < now][:40]
        if todo:
            try:
                r = requests.get(f"{api}/rezultati", params={"ids": ",".join(p["id"] for p in todo)}, timeout=15)
                data = r.json() if r.status_code == 200 else {}
                for p in todo:
                    counts = (data.get("rezultati") or {}).get(p["id"])
                    if counts is not None:
                        p["rezultat"] = [int(counts.get(str(i), 0)) for i in range(len(p["opcije"]))]
            except Exception as e:
                log.warning("Ankete: rezultati: %s", e)
    state["ankete"] = polls[-300:]
