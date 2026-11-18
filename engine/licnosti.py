"""Ličnosti: automatski profili za sve koji se često pojavljuju u vestima (političari, sportisti, umetnici...),
i prava fotografija za svaku ličnost.

Kako: imena iz tekstova (polje people) -> ko ima dovoljno tekstova u poslednjih N dana dobija profil.
Ko je ta osoba utvrđuje se preko Wikidata, a za dvosmislena imena Claude bira pravu osobu iz konteksta vesti
(ili nijednu: tada profil ostaje bez pogrešne fotografije). Fotografija: Wikidata slika osobe (P18, Wikimedia Commons,
slobodna licenca) -> najnovija fotografija sa Commons-a sa punim imenom u nazivu -> slika profila sa X-a -> inicijali.
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from urllib.parse import quote

import requests

from .util import age_hours, fold

log = logging.getLogger("prestonica")
UA = "PrestonicaBot/1.0 (+https://prestonica.rs/o-nama.html)"
WD = "https://www.wikidata.org/w/api.php"
CM = "https://commons.wikimedia.org/w/api.php"
FREE = ("cc-by", "cc by", "cc0", "public domain", "pd", "attribution")


def _get(url: str, params: dict) -> dict:
    r = requests.get(url, params={**params, "format": "json"}, headers={"User-Agent": UA}, timeout=15)
    return r.json()


def _candidates(name: str) -> list[dict]:
    out, seen = [], set()
    for lang in ("sr-el", "sr", "en"):
        try:
            s = _get(WD, {"action": "wbsearchentities", "type": "item", "limit": 6, "language": lang, "uselang": "sr-el", "search": name})
        except Exception:
            continue
        for x in s.get("search") or []:
            if x["id"] not in seen:
                seen.add(x["id"])
                out.append({"id": x["id"], "opis": (x.get("description") or "")[:90], "naziv": x.get("label", "")})
        if out:
            break
    if not out:
        return []
    try:
        e = _get(WD, {"action": "wbgetentities", "ids": "|".join(c["id"] for c in out), "props": "claims|labels", "languages": "en|sr-el|sr"})
    except Exception:
        return []
    res = []
    for c in out:
        ent = (e.get("entities") or {}).get(c["id"]) or {}
        cl = ent.get("claims") or {}
        if not any((s.get("mainsnak", {}).get("datavalue", {}).get("value", {}) or {}).get("id") == "Q5" for s in cl.get("P31", [])):
            continue                              # samo ljudi
        img = next((s["mainsnak"]["datavalue"]["value"] for s in cl.get("P18", []) if s.get("mainsnak", {}).get("datavalue")), "")
        en = ((ent.get("labels") or {}).get("en") or {}).get("value", "")
        res.append({**c, "slika": img, "en": en})
    return res


def _file_photo(filename: str) -> dict | None:
    try:
        d = _get(CM, {"action": "query", "titles": "File:" + filename, "prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlwidth": 480})
        pg = next(iter((d.get("query") or {}).get("pages", {}).values()), {})
        ii = (pg.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata", {})
        lic = md.get("LicenseShortName", {}).get("value", "")
        if not any(x in fold(lic) for x in FREE):
            return None
        artist = re.sub(r"<[^>]+>", "", md.get("Artist", {}).get("value", "")).strip()[:60]
        return {"src": ii.get("thumburl") or ii.get("url"), "credit": f"Wikimedia Commons / {artist or 'autor'} / {lic}",
                "link": ii.get("descriptionurl", ""), "w": ii.get("width", 0), "h": ii.get("height", 0)}
    except Exception as e:
        log.info("Commons fajl %s: %s", filename, e)
        return None


def _search_photo(full_name: str) -> dict | None:
    """Najnovija portretna fotografija sa punim imenom u nazivu fajla."""
    try:
        d = _get(CM, {"action": "query", "generator": "search", "gsrnamespace": 6, "gsrlimit": 25, "gsrsort": "create_timestamp_desc",
                      "gsrsearch": f'intitle:"{full_name}" filetype:bitmap', "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size", "iiurlwidth": 480})
    except Exception:
        return None
    want = fold(full_name)
    best, best_s = None, -1.0
    for pg in (d.get("query") or {}).get("pages", {}).values():
        title = pg.get("title", "")[5:]
        ii = (pg.get("imageinfo") or [{}])[0]
        if ii.get("mime") != "image/jpeg" or want not in fold(title):
            continue
        if re.search(r"signature|potpis|logo|grb|poster|&| i | and |with|meeting|sastanak", title, re.I):
            continue                              # samo ta osoba, ne grupne fotografije
        md = ii.get("extmetadata", {})
        lic = md.get("LicenseShortName", {}).get("value", "")
        if not any(x in fold(lic) for x in FREE):
            continue
        w, h = ii.get("width", 0), ii.get("height", 0)
        if min(w, h) < 300:
            continue
        s = (2.0 if "cropped" in title.lower() else 0) + (1.0 if h >= w else 0) + min(w, h) / 2000
        if s > best_s:
            artist = re.sub(r"<[^>]+>", "", md.get("Artist", {}).get("value", "")).strip()[:60]
            best, best_s = {"src": ii.get("thumburl") or ii.get("url"), "credit": f"Wikimedia Commons / {artist or 'autor'} / {lic}",
                            "link": ii.get("descriptionurl", "")}, s
    return best


def photo_for(name: str, slika: str = "", en: str = "") -> dict | None:
    img = _file_photo(slika) if slika else None
    if not img:
        for n in dict.fromkeys(x for x in (en, name) if x):
            img = _search_photo(n)
            if img:
                break
    return img


def run(cfg, state: dict, articles: list[dict], writer, live: bool) -> None:
    lc = cfg.portal.get("licnosti_auto") or {}
    if not lc.get("ukljuceno", True) or not live:
        return
    auto = state.setdefault("licnosti_auto", {})
    fotos = state.setdefault("licnosti_foto", {})
    days = float(lc.get("dana", 30))
    min_n = int(lc.get("min_tekstova", 4))
    known = {fold(p["ime"]) for p in cfg.licnosti}
    cnt: Counter = Counter()
    ctx: dict[str, list[str]] = {}
    for a in articles:
        if a.get("hidden") or age_hours(a["published"]) > days * 24:
            continue
        for p in set(a.get("people", [])):
            p = " ".join(str(p).split())
            if len(p.split()) < 1 or len(p) < 4:
                continue
            cnt[p] += 1
            ctx.setdefault(p, []).append(a["headline"])
    # 1. nove osobe sa dovoljno tekstova i stalne ličnosti kojima treba (bolja) fotografija; najviše 14 po osvežavanju
    new = [n for n, c in cnt.most_common() if c >= min_n and fold(n) not in known and n not in auto][:10]
    stale = [p for p in cfg.licnosti if not (isinstance(fotos.get(p["ime"]), dict) and fotos[p["ime"]].get("v") == 2 and "wd" in fotos[p["ime"]]
                                              and time.time() - fotos[p["ime"]].get("t", 0) < 30 * 86400)][:14 - len(new)]
    if (new or stale) and hasattr(writer, "resolve_people"):
        batch = [{"ime": n, "vesti": ctx[n][:3], "kandidati": _candidates(n), "stalna": None} for n in new]
        batch += [{"ime": p["ime"], "vesti": [p.get("uloga", "")] + ctx.get(p["ime"], [])[:2], "kandidati": _candidates(p["ime"]), "stalna": p}
                  for p in stale]
        got = {d.get("ime"): d for d in writer.resolve_people(batch) or []}
        for b in batch:
            d = got.get(b["ime"]) or {}
            c = next((x for x in b["kandidati"] if x["id"] == d.get("wd")), None)
            # fotografija samo kad je identitet potvrđen (Wikidata zapis); bez potvrde nikad pretraga po imenu (isto ime, druga osoba)
            img = photo_for(b["ime"], c.get("slika", ""), c.get("en", "")) if c else None
            if b["stalna"] is not None:
                fotos[b["ime"]] = {**(img or {"src": ""}), "v": 2, "t": time.time(), "wd": c["id"] if c else ""}
                continue
            entry = {"ime": d.get("prikaz") or b["ime"], "uloga": d.get("uloga", ""), "oblast": d.get("oblast", "drustvo"),
                     "wd": c["id"] if c else "", "t": time.time(), "ok": bool(d.get("profil", True)), "foto": img}
            auto[b["ime"]] = entry
            log.info("Ličnost: %s (%s) %s", entry["ime"], entry["uloga"], "sa fotografijom" if img else "bez fotografije")
    # 2b. stari zapisi bez potvrđenog identiteta ne smeju imati fotografiju
    for e in auto.values():
        if not e.get("wd") and e.get("foto"):
            e["foto"] = None

    # 3. osobe sa automatskim profilom koje još nemaju fotografiju: pokušaj ponovo jednom nedeljno
    for n, e in list(auto.items())[:200]:
        if e.get("ok") and e.get("wd") and not e.get("foto") and time.time() - e.get("foto_t", 0) > 7 * 86400:
            cands = [c for c in _candidates(n) if c["id"] == e["wd"]]
            e["foto"] = photo_for(n, cands[0].get("slika", "") if cands else "", cands[0].get("en", "") if cands else "")
            e["foto_t"] = time.time()


def initials(name: str) -> str:
    parts = [w for w in re.split(r"\s+", name) if w and w[0].isalpha()]
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper() if parts else "?"

