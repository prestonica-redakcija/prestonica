"""Brojke sopstvenih objava na nalogu @Prestonicars (X): prikazi, lajkovi, repostovi, odgovori.

Čita se vremenska linija sopstvenog naloga korisničkim ključevima (OAuth 1.0a), istim onim kojima
portal objavljuje. Takvo čitanje X naplaćuje kao "owned read" (oko $0,001 po objavi), pet puta
jeftinije od čitanja tuđih objava. Trošak određuju dva podešavanja u config/portal.yaml (x_statistika):
koliko često se čita i koliko objava po čitanju.

Stanje se čuva u state["x_stat"]: zadnje poznate brojke svake objave ostaju i kad objava ispadne
iz prozora čitanja, pa arhiva pokriva onoliko dana koliko kaže cuvaj_dana.

Na sajt ide fajl x-objave.json sa brojkama koje su na X-u ionako javne. Klikovi na link i na profil
nisu javni, pa ostaju samo u stanju i u njega se ne upisuju na sajt.

Ako ključ nedostaje ili poziv padne, ovaj deo se preskače: statistika nikada ne sme da obori
osvežavanje vesti.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger("prestonica")
API = "https://api.x.com/2"
JAVNA = "public_metrics,created_at,entities"
PUNA = JAVNA + ",non_public_metrics"


def _podesavanja(cfg) -> dict:
    return cfg.portal.get("x_statistika") or {}


def _nalog(cfg) -> str:
    return str((cfg.portal.get("x_objave") or {}).get("nalog")
               or (cfg.portal.get("x") or {}).get("pratilac") or "Prestonicars").lstrip("@")


def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _moj_id(st: dict, auth, nalog: str) -> str:
    """ID naloga se čita jednom i pamti; ključevi moraju pripadati nalogu portala."""
    if st.get("uid") and st.get("ime", "").lower() == nalog.lower():
        return st["uid"]
    r = requests.get(f"{API}/users/me", auth=auth, timeout=20)
    d = (r.json() or {}).get("data") or {} if r.status_code == 200 else {}
    if str(d.get("username", "")).lower() != nalog.lower():
        log.warning("X statistika: ključevi pripadaju nalogu @%s, a ne @%s; preskačem", d.get("username") or "?", nalog)
        return ""
    st["uid"], st["ime"] = str(d["id"]), d["username"]
    return st["uid"]


def _citaj(uid: str, auth, koliko: int, polja: str) -> tuple[list[dict], dict, int, str]:
    """Jedna strana vremenske linije (najviše 100 objava). Vraća (objave, nalog, status, greška)."""
    r = requests.get(
        f"{API}/users/{uid}/tweets", auth=auth, timeout=30,
        params={"max_results": max(5, min(int(koliko), 100)), "exclude": "replies,retweets",
                "tweet.fields": polja, "expansions": "author_id", "user.fields": "public_metrics"})
    try:
        j = r.json()
    except Exception:
        j = {}
    nalog = (((j.get("includes") or {}).get("users") or [{}])[0].get("public_metrics")) or {}
    greska = "" if r.status_code == 200 else str(j.get("title") or j.get("detail") or r.text[:160])
    return j.get("data") or [], nalog, r.status_code, greska


def _zapis(x: dict, stari: dict | None) -> dict:
    m = x.get("public_metrics") or {}
    np = x.get("non_public_metrics") or {}
    z = dict(stari or {})
    z.update({
        "id": str(x.get("id", "")),
        "tekst": " ".join(str(x.get("text") or "").split())[:240],
        "vreme": x.get("created_at", ""),
        "imp": int(m.get("impression_count") or 0),
        "lajk": int(m.get("like_count") or 0),
        "rt": int(m.get("retweet_count") or 0),
        "odg": int(m.get("reply_count") or 0),
        "cit": int(m.get("quote_count") or 0),
        "sacuvano": int(m.get("bookmark_count") or 0),
        "link": bool(((x.get("entities") or {}).get("urls") or []) and not _samo_slika(x)),
        "t": int(time.time()),
    })
    if np:
        z["klik"] = int(np.get("url_link_clicks") or 0)
        z["profil"] = int(np.get("user_profile_clicks") or 0)
    return z


def _samo_slika(x: dict) -> bool:
    """Objava sa karticom ima adresu slike (pic.x.com) u entitetima; to nije link ka sajtu."""
    urls = (x.get("entities") or {}).get("urls") or []
    return all("media_key" in u or "/photo/" in str(u.get("expanded_url", "")) for u in urls)


def osvezi(cfg, state: dict, articles: list[dict]) -> int:
    """Pročita brojke sopstvenih objava ako je prošao zadati razmak. Vraća broj pročitanih objava."""
    p = _podesavanja(cfg)
    if not p.get("ukljuceno", False):
        return 0
    from .objave_x import _auth
    auth = _auth()
    if auth is None:
        return 0
    st = state.setdefault("x_stat", {})
    sada = time.time()
    if sada - float(st.get("citano_t") or 0) < float(p.get("svakih_sati", 4)) * 3600:
        return 0
    # i neuspeo pokušaj pomera sat: greška se ne ponavlja na svakih 15 minuta
    st["citano_t"] = sada

    uid = _moj_id(st, auth, _nalog(cfg))
    if not uid:
        return 0
    koliko = int(p.get("objava_po_citanju", 60))
    # klikovi na link i profil: X ih daje samo vlasniku naloga i samo za objave mlađe od 30 dana.
    # Ako ih nalog ne dobija, to se pamti sedam dana da se ne plaća pokušaj pri svakom čitanju.
    puna = bool(p.get("klikovi", True)) and sada - float(st.get("bez_klikova_t") or 0) > 7 * 86400
    objave, nalog, status, greska = _citaj(uid, auth, koliko, PUNA if puna else JAVNA)
    if puna and (status != 200 or not objave):
        st["bez_klikova_t"] = sada
        objave, nalog, status, greska = _citaj(uid, auth, koliko, JAVNA)
    if status != 200:
        st["greska"] = f"{status} {greska}"[:200]
        log.warning("X statistika: čitanje nije uspelo: %s %s", status, greska)
        return 0
    st.pop("greska", None)

    # veza objave sa vešću: iz dnevnika objavljivanja (objave_x čuva sedam dana) i iz ranije upamćenog
    slug = {a["id"]: a.get("slug", "") for a in articles}
    naslov = {a["id"]: a.get("headline", "") for a in articles}
    veza = {str(h.get("x")): h.get("id", "") for h in (state.get("x_objave") or {}).get("istorija", [])}

    sve = st.setdefault("objave", {})
    for x in objave:
        z = _zapis(x, sve.get(str(x.get("id"))))
        aid = veza.get(z["id"]) or z.get("vest_id", "")
        if aid:
            z["vest_id"] = aid
            if slug.get(aid):
                z["vest"], z["naslov"] = slug[aid], naslov.get(aid, "")
        sve[z["id"]] = z

    granica = sada - float(p.get("cuvaj_dana", 30)) * 86400
    for k in [k for k, z in sve.items() if _ts(z.get("vreme", "")) < granica]:
        del sve[k]

    if nalog:
        st["nalog"] = {"pratioci": int(nalog.get("followers_count") or 0),
                       "prati": int(nalog.get("following_count") or 0),
                       "objava": int(nalog.get("tweet_count") or 0)}
        dan = datetime.now(timezone.utc).date().isoformat()
        tok = [d for d in st.setdefault("pratioci_po_danu", []) if d["dan"] != dan]
        tok.append({"dan": dan, "pratioci": st["nalog"]["pratioci"]})
        st["pratioci_po_danu"] = tok[-90:]
    st["procitano"] = int(st.get("procitano") or 0) + len(objave)
    st["uspeh_t"] = sada
    log.info("X statistika: pročitano %d objava, u arhivi %d", len(objave), len(sve))
    return len(objave)


JAVNA_POLJA = ("id", "tekst", "vreme", "imp", "lajk", "rt", "odg", "cit", "sacuvano", "link", "vest", "naslov")


def izvezi(cfg, state: dict, out: Path) -> None:
    """x-objave.json na sajtu: samo brojke koje su na X-u javne, najnovije objave prve."""
    p = _podesavanja(cfg)
    st = state.get("x_stat") or {}
    if not p.get("ukljuceno", False) or not st.get("objave"):
        return
    domen = cfg.portal.get("domen") or "prestonica.rs"
    ime = st.get("ime") or _nalog(cfg)
    red = sorted(st["objave"].values(), key=lambda z: z.get("vreme", ""), reverse=True)
    objave = []
    for z in red:
        o = {k: z[k] for k in JAVNA_POLJA if k in z and z[k] not in ("", None)}
        o["adresa"] = f"https://x.com/{ime}/status/{z['id']}"
        if z.get("vest"):
            o["vest"] = f"https://{domen}/vesti/{z['vest']}.html"
        objave.append(o)
    podatak = {
        "nalog": {"ime": ime, **(st.get("nalog") or {})},
        "pratioci_po_danu": st.get("pratioci_po_danu") or [],
        "objave": objave,
        "azurirano": datetime.fromtimestamp(float(st.get("uspeh_t") or 0), timezone.utc).isoformat(timespec="minutes"),
        "svakih_sati": float(p.get("svakih_sati", 4)),
    }
    (out / "x-objave.json").write_text(json.dumps(podatak, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
