"""Praćenje objavljenih tekstova.

Kad posle objave izađe nova vest o istoj temi, tekst dobija odeljak
"Ažurirano". Postojeće rečenice se ne diraju nikada: ako nova vest
protivreči staroj, to se vidi u dodatku, a ne tako što stara tiho nestane.

Dva koloseka, jer greška ne košta isto svuda. Obične vesti se dopunjuju
same. Autorski i istraživački tekstovi idu u red za odobrenje, jer tamo
jedna pogrešno sročena rečenica ide pod potpis redakcije.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

from .util import fold, age_hours, now_utc

ROOT = Path(__file__).resolve().parent.parent
RED = ROOT / "data" / "azuriranja.json"

NAJVISE_PO_TEKSTU = 5          # ispod udarnog teksta ne sme da se nakupi 20 dodataka
DANA_PRACENJA = 30
SATI_UNAZAD = 8                # koliko stare vesti gledamo u jednom prolazu
PRAG = 2.0
SAM_SE_AZURIRA = {"vest", "izvestaj"}   # sve ostalo ceka odobrenje


def pojmovi(a: dict) -> list[str]:
    """Pojmovi po kojima se prati. Izricito navedeni, inace imena i oznake."""
    rucno = [p for p in (a.get("prati_pojmovi") or []) if p]
    if rucno:
        return rucno
    return [t for t in (a.get("people") or []) + (a.get("tags") or []) if len(str(t)) > 3][:8]


def _pogodak(pjmv: list[str], it: dict) -> float:
    """Naslov nosi dvostruko: on kaze o cemu je vest, dok se u telu nadje
    poneka opsta rec. Ista lekcija kao kod izbora fotografije."""
    nas = " " + fold(it.get("title", "")) + " "
    telo = " " + fold(it.get("summary", "") or "") + " "
    s = 0.0
    for p in pjmv:
        f = fold(p)
        if len(f) < 4:
            continue
        if f in nas:
            s += 2.0
        elif f in telo:
            s += 1.0
    return s


def kandidati(a: dict, items: list[dict]) -> list[dict]:
    """Vesti koje se ticu ovog teksta, a jos nisu u njemu."""
    pjmv = pojmovi(a)
    if not pjmv:
        return []
    vec = {(s.get("url") or "").split("?")[0] for s in (a.get("sources") or [])}
    vec |= set(a.get("prati_videno") or [])
    out = []
    for it in items:
        u = (it.get("url") or "").split("?")[0]
        if not u or u in vec:
            continue
        try:
            if age_hours(it.get("published")) > SATI_UNAZAD:
                continue
        except Exception:
            continue
        s = _pogodak(pjmv, it)
        if s >= PRAG:
            out.append(dict(it, _bod=s))
    out.sort(key=lambda x: -x["_bod"])
    return out[:4]


def adresa_radi(u: str) -> bool:
    """Izvor ciji link ne radi ne ulazi u tekst. Izmisljena adresa koja
    lici na pravu je najtisa greska koja postoji."""
    try:
        r = requests.get(u, timeout=15, allow_redirects=True,
                         headers={"User-Agent": "PrestonicaBot/1.0 (+https://prestonica.rs/o-nama.html)"})
        return r.status_code == 200
    except Exception:
        return False


def _prati_jos(a: dict) -> bool:
    if not a.get("prati"):
        return False
    if len(a.get("azuriranja") or []) >= NAJVISE_PO_TEKSTU:
        return False
    try:
        return age_hours(a.get("published")) <= 24 * DANA_PRACENJA
    except Exception:
        return False


def _ugradi(a: dict, naslov: str, pasusi: list[str], izvori: list[dict]) -> None:
    """Dodatak ide na kraj tela, a naslov ispred njega. Nista se ne prepisuje."""
    body = a.setdefault("body", [])
    umetci = a.setdefault("umetci", [])
    poz = len(body)
    body.extend(pasusi)
    umetci.append({"h": naslov, "posle": poz})
    umetci.sort(key=lambda u: u["posle"])
    for iz in izvori:
        if iz["url"] not in {s.get("url") for s in a.get("sources") or []}:
            a.setdefault("sources", []).append(iz)
            a.setdefault("source_titles", []).append(iz["name"])
    a.setdefault("azuriranja", []).append({"kada": now_utc().isoformat(), "naslov": naslov,
                                           "izvori": [iz["url"] for iz in izvori]})
    a["updated"] = now_utc().isoformat()


def _u_red(a: dict, nacrt: dict) -> None:
    """Autorski tekstovi cekaju odobrenje: nacrt stoji u redu, tekst se ne dira."""
    red = []
    if RED.exists():
        try:
            red = json.loads(RED.read_text(encoding="utf-8"))
        except Exception:
            red = []
    red = [r for r in red if r.get("clanak") != a["id"] or r.get("stanje") != "ceka"]
    red.append({"clanak": a["id"], "slug": a.get("slug", ""), "naslov_clanka": a["headline"],
                "stanje": "ceka", "kada": now_utc().isoformat(), **nacrt})
    RED.write_text(json.dumps(red, ensure_ascii=False, indent=1), encoding="utf-8")


def prodji(cfg, w, articles: list[dict], items: list[dict], cuvaj) -> list[dict]:
    """Jedan prolaz kroz pracene tekstove. Vraca dnevnik za produkcija.json."""
    dnevnik = []
    for a in articles:
        if not _prati_jos(a):
            continue
        nove = kandidati(a, items)
        if not nove:
            continue
        nacrt = w.azuriranje(a, nove)
        if not nacrt or not nacrt.get("pasusi"):
            continue
        izvori = []
        for it in nove:
            u = (it.get("url") or "")
            if u and adresa_radi(u):
                izvori.append({"name": f"{it.get('source_name','Izvor')}: {it.get('title','')[:70]}", "url": u})
        if not izvori:
            dnevnik.append({"clanak": a["id"], "ishod": "izvor nije dostupan"})
            continue
        nacrt["izvori"] = izvori
        a.setdefault("prati_videno", []).extend(iz["url"] for iz in izvori)
        if a.get("kind") in SAM_SE_AZURIRA:
            _ugradi(a, nacrt.get("naslov") or "Ažurirano", nacrt["pasusi"], izvori)
            dnevnik.append({"clanak": a["id"], "ishod": "objavljeno", "pasusa": len(nacrt["pasusi"])})
        else:
            _u_red(a, nacrt)
            dnevnik.append({"clanak": a["id"], "ishod": "ceka odobrenje", "pasusa": len(nacrt["pasusi"])})
        cuvaj(a)
    return dnevnik
