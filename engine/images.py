"""Fotografije: tvoja arhiva -> Wikimedia Commons (prave fotografije osoba) -> Unsplash -> Pexels -> rezerva.
Nikad ne preuzima fotografije drugih medija (autorska prava)."""
from __future__ import annotations

import logging
import re
import time

import requests

from .config import Config
from .util import age_hours, fold

log = logging.getLogger("prestonica")
UA = "PrestonicaBot/1.0 (+https://prestonica.rs/o-nama.html)"
FREE_LICENSES = ("cc-by", "cc by", "cc0", "public domain", "pd", "cc-by-sa", "cc by-sa")


class Images:
    def __init__(self, cfg: Config, live: bool):
        self.cfg = cfg
        self.live = live
        self.lib = cfg.foto.get("fotografije", []) or []
        self.fallback = cfg.foto.get("rezerva", {}) or {}
        self.used: dict[str, int] = {}
        self.recent: list[dict] = []
        self.cache: dict = {}          # run.py ovde stavlja state["foto_cache"] (rezultati pretrage, 7 dana)
        self.known: dict[str, dict] = {}  # run.py: fold(ime) -> potvrđena fotografija ličnosti (Wikidata identitet ili redakcija)

    def note_used(self, articles: list[dict]) -> None:
        self.recent = list(articles)
        for a in articles:
            src = (a.get("image") or {}).get("src")
            if src:
                self.used[src] = self.used.get(src, 0) + 1

    # --- 0. Isti nastavak priče ne dobija istu fotografiju ---
    def _siblings_used(self, a: dict) -> set[str]:
        tags = {fold(t) for t in a.get("tags", [])} | {fold(p) for p in a.get("people", [])}
        out = set()
        for b in self.recent:
            if b is a or b.get("id") == a.get("id"):
                continue
            bt = {fold(t) for t in b.get("tags", [])} | {fold(p) for p in b.get("people", [])}
            if len(tags & bt) >= 2 or (set(map(fold, a.get("people", []))) & set(map(fold, b.get("people", [])))):
                src = (b.get("image") or {}).get("src")
                if src:
                    out.add(src)
        return out

    # --- 1a. Commons pretraga: tačno poklapanje naziva u imenu fajla, najnovije i najkvalitetnije prvo ---
    _BAD = re.compile(r"signature|potpis|logo|symbol|simbol|coat of arms|\bgrb\b|\bmap\b|mapa|rendering|render|diagram|poster|plakat|stamp|marka|banknote|icon", re.I)

    def _commons_search(self, phrase: str, person: bool) -> list[dict]:
        key = ("o:" if person else "t:") + fold(phrase)
        hit = self.cache.get(key)
        if hit and time.time() - hit.get("t", 0) < 7 * 86400:
            return hit["items"]
        items: list[dict] = []
        try:
            q = (f'intitle:"{phrase}" filetype:bitmap') if person else (f'"{phrase}" filetype:bitmap')
            params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrsearch": q,
                      "gsrlimit": 30, "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size", "iiurlwidth": 1600}
            if person:
                params["gsrsort"] = "create_timestamp_desc"
            r = requests.get("https://commons.wikimedia.org/w/api.php", params=params, headers={"User-Agent": UA}, timeout=15)
            pages = (r.json().get("query") or {}).get("pages", {})
            want = fold(phrase)
            for pg in sorted(pages.values(), key=lambda x: x.get("index", 99)):
                title = pg.get("title", "")[5:]
                ii = (pg.get("imageinfo") or [{}])[0]
                if ii.get("mime") != "image/jpeg" or self._BAD.search(title):
                    continue
                if want not in fold(title):
                    continue
                # zagrada sa drugim zanimanjem (npr. "(pesnik)") znači drugu osobu istog imena
                if person and re.search(r"\((?!cropped|\d)[^)]*[a-zčćšđž]{4,}[^)]*\)", title, re.I):
                    continue
                w, h = ii.get("width", 0), ii.get("height", 0)
                if w < 900 or h < 500:
                    continue
                md = ii.get("extmetadata", {})
                lic = fold(md.get("LicenseShortName", {}).get("value", ""))
                if not any(fold(x) in lic for x in FREE_LICENSES + ("attribution",)):
                    continue
                date = re.sub(r"<[^>]+>", "", md.get("DateTimeOriginal", {}).get("value", "") or md.get("DateTime", {}).get("value", ""))
                ym = re.search(r"(19|20)\d\d", date)
                artist = re.sub(r"<[^>]+>", "", md.get("Artist", {}).get("value", "")).strip()[:60]
                items.append({"src": ii.get("thumburl") or ii.get("url"), "w": w, "h": h, "god": int(ym.group(0)) if ym else 0,
                              "credit": f"Wikimedia Commons / {artist or 'autor'} / {md.get('LicenseShortName', {}).get('value', '')}",
                              "link": ii.get("descriptionurl", ""), "naslov": title})
        except Exception as e:
            log.info("Commons pretraga (%s): %s", phrase, e)
            return []
        self.cache[key] = {"t": time.time(), "items": items[:20]}
        return items[:20]

    def _pick(self, items: list[dict], avoid: set[str]) -> dict | None:
        now_y = time.gmtime().tm_year
        best, best_s = None, -1e9
        for it in items:
            if it["src"] in avoid:
                continue
            s = 0.0
            s += max(0, 6 - (now_y - it["god"])) if it["god"] else 0      # novije je bolje
            s += 2.0 if it["w"] >= it["h"] * 1.2 else 0                       # vodoravna ide uz naslov
            s += 0.5 if it["w"] >= 1600 else 0
            s -= 3.0 * self.used.get(it["src"], 0)                            # ne ponavljaj
            if s > best_s:
                best, best_s = it, s
        if not best:
            return None
        return {"src": best["src"], "credit": best["credit"], "ilustracija": False, "local": False, "link": best["link"]}

    def set_xposts(self, posts: list[dict]) -> None:
        """Objave sa X-a iz ovog ciklusa, kao izvor fotografija."""
        self.xposts = posts or []

    def _x_photo(self, a: dict, avoid: set[str]) -> dict | None:
        """Fotografija iz objave samog aktera na X-u.

        Uzima se samo sa naloga osobe ili institucije koja se u tekstu pominje, pa
        slika ne može da pripada drugom čoveku istog prezimena. Uz sliku uvek ide
        potpis čiji je nalog."""
        if not getattr(self, "xposts", None):
            return None
        trazeni = {fold(str(p)) for p in (a.get("people") or [])}
        trazeni |= {fold(str(t)) for t in (a.get("tags") or []) if len(str(t)) >= 5}
        if not trazeni:
            return None
        kandidati = []
        for p in self.xposts:
            if not p.get("slike") or age_hours(p.get("published", "")) > 72:
                continue
            ime = fold(str(p.get("ime") or ""))
            if not ime:
                continue
            # tačno poklapanje naloga sa osobom ili temom iz teksta
            if ime in trazeni or any(ime == t for t in trazeni):
                kandidati.append(p)
        kandidati.sort(key=lambda p: p.get("published", ""), reverse=True)
        for p in kandidati:
            for src in p["slike"]:
                if src in avoid:
                    continue
                return {"src": src, "credit": f"Foto: @{p.get('x', '')} / X", "ilustracija": False,
                        "local": False, "link": f"https://x.com/{p.get('x', '')}/status/{p.get('id', '')}",
                        "osoba": p.get("ime", ""), "izvor_x": True}
        return None

    def _person_photo(self, name: str, avoid: set[str]) -> dict | None:
        """Samo potvrđen identitet: fotografija iz profila ličnosti (Wikidata zapis ili unos redakcije).
        Nikad slobodna pretraga po imenu: isto prezime, druga osoba (Darko Šarić -> košarkaš Dario Šarić)."""
        if not name:
            return None
        ph = self.known.get(fold(name))
        if not ph or not ph.get("src") or ph["src"] in avoid:
            return None
        return {"src": ph["src"], "credit": ph.get("credit", ""), "ilustracija": False, "local": False,
                "link": ph.get("link", ""), "osoba": name}

    def wrong_person(self, a: dict) -> bool:
        """Postojeća fotografija sa Commons-a čiji naziv nosi prezime osobe iz teksta, a ta osoba nema potvrđenu
        fotografiju: verovatno druga osoba istog prezimena."""
        img = a.get("image") or {}
        if img.get("local") or img.get("ilustracija") or "wikimedia" not in (img.get("src", "") + img.get("link", "")):
            return False
        known_srcs = {v.get("src") for v in self.known.values()}
        if img.get("src") in known_srcs:
            return False
        from urllib.parse import unquote
        ref = fold(unquote(unquote(img.get("src", "") + " " + img.get("link", "")))).replace("_", " ")
        for p in a.get("people", []):
            parts = str(p).split()
            if len(parts) < 2 or fold(p) in self.known:
                continue
            first, sur = fold(parts[0]), fold(parts[-1])
            # prezime u nazivu fajla, a ime nije: druga osoba istog prezimena
            if len(sur) >= 4 and sur in ref and first not in ref:
                return True
        return False

    def _mediji(self) -> set[str]:
        """Imena medija koje portal prati.

        Ona se pojavljuju kao oznake u skoro svakom tekstu, a kao pojam pretrage
        slika su zamka: "Danas" na Wikimediji vraća fotografije poplava od tropske
        oluje Danas sa Filipina, a ne beogradski list."""
        if getattr(self, "_mediji_kes", None) is None:
            # samo cela imena: reč iz imena nije ime. "Srbije" je deo naziva RTS-a,
            # a "Vlada Srbije" je sasvim ispravna tema za fotografiju.
            self._mediji_kes = {fold(str((iz or {}).get("naziv") or "").strip())
                                for iz in (getattr(self.cfg, "izvori", None) or [])
                                if str((iz or {}).get("naziv") or "").strip()}
        return self._mediji_kes

    def _imena_u_tekstu(self, a: dict) -> set[str]:
        """Svi delovi imena osoba iz teksta. Oznaka koja je nečije prezime ne sme
        da ode u slobodnu pretragu: "Tramp" tako vrati sliku skitnica, "Mali" autobus."""
        delovi: set[str] = set()
        for p in (a.get("people") or []):
            for d in str(p).split():
                if len(d) >= 3:
                    delovi.add(fold(d))
            delovi.add(fold(str(p)))
        return delovi

    def pogresna_tema(self, a: dict) -> bool:
        """Postojeća fotografija sa Commons-a čiji naziv nosi ime medija iz teksta.

        Takva slika je skoro sigurno promašaj: "Danas" je vratio poplave od tropske
        oluje Danas, a ne list. Tekst se tada ponovo slika."""
        img = a.get("image") or {}
        if img.get("local") or img.get("ilustracija") or "wikimedia" not in (img.get("src", "") + img.get("link", "")):
            return False
        if img.get("src") in {v.get("src") for v in self.known.values()}:
            return False
        from urllib.parse import unquote

        ref = fold(unquote(unquote(img.get("src", "")))).replace("_", " ")
        mediji = self._mediji()
        for t in a.get("tags", []):
            f = fold(str(t).strip())
            if len(f) >= 4 and f in mediji and f in ref:
                return True
        return False

    def _topic_photo(self, a: dict, avoid: set[str]) -> dict | None:
        imena = self._imena_u_tekstu(a)
        mediji = self._mediji()
        for t in a.get("tags", [])[:5]:
            t = str(t).strip()
            # prezime osobe iz teksta nikad nije pojam za sliku
            if fold(t) in imena or any(fold(w) in imena for w in t.split()):
                continue
            # ime medija nije tema nego izvor
            if fold(t) in mediji:
                continue
            # jedna reč je previše dvosmislena preko jezika: isti niz slova znači
            # drugo na drugom jeziku i vraća sliku sa druge strane sveta
            if len(t.split()) < 2:
                continue
            # samo konkretni pojmovi: vlastita imena ili višečlani nazivi (npr. "hotel Jugoslavija", "Narodna skupština")
            if len(t) < 5 or not (any(w[:1].isupper() for w in t.split()) and (len(t.split()) >= 2 or t[:1].isupper())):
                continue
            img = self._pick(self._commons_search(t, person=False), avoid)
            if img:
                return img
        return None

    # --- 1. tvoja arhiva ---
    def _library(self, text: str, naslov: str = "") -> tuple[dict | None, float]:
        """Fotografija iz arhive po ključnim rečima.

        Pogodak u naslovu vredi dvostruko: naslov kaže o čemu je tekst, dok se u
        telu uvek nađe poneka opšta reč. Bez toga tekst o zabrani emisije
        Informeru dobije fotografiju Skupštine, jer se reči poslanik i sednica
        pojave usput, a informer stoji u naslovu."""
        t = " " + fold(text) + " "
        h = " " + fold(naslov) + " "
        words, hwords = t.split(), h.split()
        best, best_s = None, 0.0
        for f in self.lib:
            s = 0.0
            for k in f.get("kljucne", []):
                k = fold(k)
                if " " in k:
                    if f" {k}" in h:
                        s += 3.0
                    elif f" {k}" in t:
                        s += 1.5
                elif len(k) <= 3:
                    # Kratke ključne reči (eu, tv, k1, rem) traže tačan pogodak:
                    # kao prefiks hvataju svaku reč koja tako počinje, pa je tekst
                    # o Informeru zbog "eu" dobijao fotografiju Pariza.
                    if k in hwords:
                        s += 2.0
                    elif k in words:
                        s += 1.0
                elif any(w.startswith(k) for w in hwords):
                    s += 2.0
                elif any(w.startswith(k) for w in words):
                    s += 1.0
            if s <= 0:
                continue
            # Raznovrsnost: ista fotografija iz arhive gubi prednost, ali kazna je
            # ograničena. Bez granice je tekst o Informeru dobijao fotografiju Pariza,
            # jer je tačna fotografija već bila upotrebljena dva puta u istom prolazu.
            s -= min(1.5, 0.7 * self.used.get("foto/" + f["fajl"], 0))
            if s > best_s:
                best, best_s = f, s
        if not best:
            return None, 0
        return {"src": "foto/" + best["fajl"], "credit": best.get("autor", "Arhiva"), "ilustracija": best.get("ilustracija", True), "local": True}, best_s

    def _fallback(self, section: str) -> dict:
        f = self.fallback.get(section) or self.fallback.get("politika") or self.lib[0]["fajl"]
        meta = next((x for x in self.lib if x["fajl"] == f), {})
        return {"src": "foto/" + f, "credit": meta.get("autor", "Arhiva"), "ilustracija": True, "local": True}

    # --- 2. Wikimedia Commons: prava fotografija osobe pod slobodnom licencom ---
    def _commons(self, person: str) -> dict | None:
        try:
            r = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                    "gsrsearch": f'"{person}" filetype:bitmap', "gsrlimit": 8,
                    "prop": "imageinfo", "iiprop": "url|extmetadata|mime", "iiurlwidth": 1400,
                },
                headers={"User-Agent": UA}, timeout=12,
            )
            pages = (r.json().get("query") or {}).get("pages", {})
            for p in sorted(pages.values(), key=lambda x: x.get("index", 99)):
                ii = (p.get("imageinfo") or [{}])[0]
                if ii.get("mime") not in ("image/jpeg", "image/png"):
                    continue
                md = ii.get("extmetadata", {})
                lic = fold(md.get("LicenseShortName", {}).get("value", ""))
                if not any(fold(x) in lic for x in FREE_LICENSES):
                    continue
                artist = re.sub(r"<[^>]+>", "", md.get("Artist", {}).get("value", "")).strip()[:60]
                return {
                    "src": ii.get("thumburl") or ii.get("url"),
                    "credit": f"Wikimedia Commons / {artist or 'autor'} / {md.get('LicenseShortName', {}).get('value', '')}",
                    "ilustracija": False, "local": False, "link": ii.get("descriptionurl"),
                }
        except Exception as e:
            log.info("Commons nije uspeo: %s", e)
        return None

    # --- 3. Unsplash (besplatno; obavezan potpis autora i prijava preuzimanja) ---
    def _unsplash(self, query: str) -> dict | None:
        key = self.cfg.secrets.get("unsplash")
        if not key or not query:
            return None
        try:
            r = requests.get("https://api.unsplash.com/search/photos",
                             params={"query": query, "per_page": 10, "orientation": "landscape", "content_filter": "high"},
                             headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"}, timeout=12)
            for ph in r.json().get("results", []):
                src = ph["urls"]["raw"] + "&w=1600&q=80&fm=jpg&fit=max"
                if self.used.get(src):
                    continue
                try:  # Unsplash pravila: prijavi korišćenje fotografije
                    requests.get(ph["links"]["download_location"], headers={"Authorization": f"Client-ID {key}"}, timeout=8)
                except Exception:
                    pass
                user = ph.get("user", {})
                return {"src": src, "credit": f"Unsplash / {user.get('name', '')}", "ilustracija": True, "local": False,
                        "alt": ph.get("alt_description") or "", "link": (user.get("links") or {}).get("html", "")}
        except Exception as e:
            log.info("Unsplash nije uspeo: %s", e)
        return None

    # --- 4. Pexels (rezervno, ako imaš stari ključ) ---
    def _pexels(self, query: str) -> dict | None:
        key = self.cfg.secrets.get("pexels")
        if not key or not query:
            return None
        try:
            r = requests.get("https://api.pexels.com/v1/search", params={"query": query, "per_page": 10, "orientation": "landscape"},
                             headers={"Authorization": key}, timeout=12)
            for ph in r.json().get("photos", []):
                src = ph["src"]["large2x"]
                if self.used.get(src):
                    continue
                return {"src": src, "credit": f"Pexels / {ph.get('photographer', '')}", "ilustracija": True, "local": False,
                        "alt": ph.get("alt", "")}
        except Exception as e:
            log.info("Pexels nije uspeo: %s", e)
        return None

    # Poverenje: koliko je dokazano da slika prikazuje ono o cemu tekst govori.
    # Merilo nije lepota nego identifikacija, zato stok nikada ne pobedjuje
    # potvrdjeno lice, ma koliko bolje izgledao.
    P_X = 9.0         # fotografija iz objave samog aktera
    P_OSOBA = 9.0     # potvrdjen identitet: Wikidata zapis ili unos redakcije
    P_TEMA = 5.0      # Commons pretraga po pojmu iz teksta
    P_STOK = 3.0      # Unsplash ili Pexels: ilustracija, nikad dokaz
    PRAG_MREZA = 6.0  # iznad ovoga ne diramo mrezu
    PRAG_STOK = 4.0   # ispod ovoga vredi platiti stok pretragu

    def _sumnjiv_commons(self, img: dict, a: dict) -> bool:
        """Commons kandidat cije ime datoteke nosi prezime osobe iz teksta, ali ne i
        njeno ime: skoro uvek drugi covek istog prezimena. Tako je tekst o sudjenju
        Darku Saricu dobio fotografiju kosarkasa Darija Sarica."""
        if img.get("local") or "wikimedia" not in (img.get("src", "") + img.get("link", "")):
            return False
        from urllib.parse import unquote
        ref = fold(unquote(unquote(img.get("src", "") + " " + img.get("link", "")))).replace("_", " ")
        for p in a.get("people", []):
            delovi = str(p).split()
            if len(delovi) < 2 or fold(p) in self.known:
                continue
            ime, prezime = fold(delovi[0]), fold(delovi[-1])
            if len(prezime) >= 4 and prezime in ref and ime not in ref:
                return True
        return False

    def choose(self, a: dict, hint: dict) -> dict:
        """Skupi kandidate iz svih izvora, boduj ih po pouzdanosti, uzmi najboljeg.

        Ranije je ovo bio lanac u kome je pobedjivao prvi pogodak, pa je slaba
        fotografija sa Commons-a tukla jaku iz arhive samo zato sto je bila ranije
        u redu. Sada se porede.

        Mreza se ne dira dok se ne iscrpi ono sto je besplatno i trenutno: arhiva,
        objave aktera i potvrdjena lica citaju se iz memorije. Commons ide tek ako
        je najbolje do tada slabo, a stok tek ako je i posle Commons-a slabo. Bez
        toga bi Unsplash demo kvota od 50 zahteva na sat pukla u dva ciklusa."""
        text = " ".join([a["headline"], a["lead"], " ".join(a.get("tags", [])), " ".join(a.get("people", []))])
        avoid = self._siblings_used(a)
        kandidati: list[tuple[float, int, dict]] = []

        def dodaj(img: dict | None, bod: float) -> None:
            if img and img.get("src") and img["src"] not in avoid:
                kandidati.append((bod, len(kandidati), img))

        def najbolji() -> float:
            return max(kandidati)[0] if kandidati else 0.0

        # 1. besplatno i trenutno
        lib, lib_s = self._library(text, a.get("headline", ""))
        if lib and lib["src"] in avoid:
            lib, lib_s = None, 0.0
        if lib and lib_s >= 2:
            # jak pogodak arhive vredi vise od nasumicne slike sa interneta
            dodaj(lib, min(7.0, 2.0 + 0.6 * lib_s))
        if self.live:
            dodaj(self._x_photo(a, avoid), self.P_X)
            imena = [hint.get("foto_osoba", "")] + list(a.get("people", [])[:3])
            for n in dict.fromkeys(x for x in imena if x):
                osoba = self._person_photo(n, avoid)
                if osoba:
                    dodaj(osoba, self.P_OSOBA)
                    break

        # 2. mreza, samo ako je potrebno
        if self.live and najbolji() < self.PRAG_MREZA:
            tema = self._topic_photo(a, avoid)
            if tema:
                dodaj(tema, self.P_TEMA - (3.0 if self._sumnjiv_commons(tema, a) else 0.0))
        if self.live and najbolji() < self.PRAG_STOK:
            dodaj(self._unsplash(hint.get("foto_upit_en", "")) or self._pexels(hint.get("foto_upit_en", "")), self.P_STOK)

        img = max(kandidati)[2] if kandidati else (lib or self._fallback("analiza" if a["kind"] == "analiza" else a["section"]))
        img.setdefault("alt", a["headline"])
        self.used[img["src"]] = self.used.get(img["src"], 0) + 1
        return img
