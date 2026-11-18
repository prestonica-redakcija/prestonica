"""Pisanje tekstova: Claude (pravi rad) ili probni režim bez API-ja."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from .config import Config
from .util import fold, sentence_case_caps, sr_date

log = logging.getLogger("prestonica")

TOOL = {
    "name": "objavi_tekst",
    "description": "Predaje gotov tekst za objavljivanje na portalu Prestonica.",
    "input_schema": {
        "type": "object",
        "properties": {
            "preskoci": {"type": "boolean", "description": "true ako tema nije za ozbiljan portal (zabava, reklama, trivijalno) ili izvori nisu dovoljni"},
            "razlog": {"type": "string"},
            "rubrika": {"type": "string"},
            "naslov": {"type": "string", "description": "do 90 znakova, bez velikih slova i uzvičnika"},
            "uvod": {"type": "string", "description": "1-2 rečenice"},
            "pasusi": {"type": "array", "items": {"type": "string"}, "description": "3-6 kratkih pasusa"},
            "oznake": {"type": "array", "items": {"type": "string"}, "description": "3-6 tema/ključnih pojmova"},
            "osobe": {"type": "array", "items": {"type": "string"}, "description": "puna imena ključnih osoba"},
            "mesta": {"type": "array", "items": {"type": "string"}},
            "vaznost": {"type": "integer", "description": "1-10, koliko je vest važna za čitaoca u Srbiji"},
            "foto_upit_en": {"type": "string", "description": "2-5 engleskih reči za stock fotografiju (scena, ne osoba)"},
            "foto_osoba": {"type": "string", "description": "ime osobe čija prava fotografija najbolje ide uz tekst, ili prazno"},
        },
        "required": ["preskoci", "rubrika", "naslov", "uvod", "pasusi", "oznake", "vaznost", "foto_upit_en"],
    },
}

RULES = """
TEHNIČKA PRAVILA (obavezna):
- Piši isključivo latinicom, ekavicom.
- Koristi SAMO činjenice iz priloženih izvora. Ne dodaj brojke, imena, datume ni citate kojih nema u izvorima.
- Ne prepisuj rečenice iz izvora; piši svojim rečima. Doslovan citat najviše jedna rečenica, pod navodnicima, uz ime onoga ko je rekao.
- Kada izvori različitih orijentacija (spektar: vlast / opozicija / centar / desnica) izveštavaju različito, navedi obe verzije i koji medij šta tvrdi.
- U tekstu pomeni medije iz kojih je informacija ("kako prenosi Tanjug", "navodi N1").
- Nikada ne piši "prema našim izvorima", "saznajemo" niti tvrdi da neko "nije odgovorio" ako to izričito ne piše u izvorima. Nemamo sopstvene izvore; prenosimo samo ono što su objavili navedeni mediji.
- Naslov i uvod počinju velikim slovom.
- rubrika mora biti jedna od: {rubrike}.
- Ako je tema zabavna, reklamna, trivijalna ili nema dovoljno činjenica: preskoci=true.
- Odgovori isključivo pozivom alata objavi_tekst.
"""


class ClaudeWriter:
    def __init__(self, cfg: Config):
        import anthropic

        self.cfg = cfg
        self.client = anthropic.Anthropic(api_key=cfg.secrets["anthropic"])
        self.system = cfg.politika + "\n\n" + RULES.format(rubrike=", ".join(cfg.rubrike))

    def _call(self, prompt: str, model: str | None = None) -> dict | None:
        try:
            msg = self.client.messages.create(
                model=model or self.cfg.portal["model"],
                max_tokens=int(self.cfg.portal.get("model_maks_tokena", 2200)),
                # urednička pravila su ista u svakom pozivu: keširana, posle prvog poziva plaćaju se 5% cene
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                tools=[TOOL],
                tool_choice={"type": "auto"},
                messages=[{"role": "user", "content": prompt + "\n\nOdgovori ISKLJUČIVO pozivom alata objavi_tekst."}],
            )
            for block in msg.content:
                if block.type == "tool_use":
                    return dict(block.input)
            # rezerva: JSON u tekstu
            for block in msg.content:
                if block.type == "text":
                    m = re.search(r"\{[\s\S]*\}", block.text)
                    if m:
                        try:
                            return json.loads(m.group(0))
                        except Exception:
                            pass
            log.error("Claude nije vratio tekst u očekivanom obliku")
        except Exception as e:
            log.error("Claude greška: %s", e)
        return None

    def news(self, cluster: dict, texts: dict[str, str], model: str | None = None) -> dict | None:
        parts = []
        for it in cluster["items"][:6]:
            body = texts.get(it["link"]) or it.get("summary", "")
            kind = " | TIP: IZJAVA (YouTube/stranka; prenosi se kao izjava, ne kao činjenica)" if it.get("tip") == "izjava" else ""
            parts.append(
                f"### Izvor: {it['source_name']} | spektar: {it['spektar']}{kind} | objavljeno: {it['published']}\n"
                f"Naslov: {it['title']}\nLink: {it['link']}\nTekst: {body[:4500]}"
            )
        update = bool(cluster["items"]) and cluster["items"][0].get("source_name", "").startswith("Prestonica (")
        uvod = (f"Danas je {sr_date(datetime.now(), True)} AŽURIRAJ našu postojeću vest: prvi izvor ispod je dosadašnja verzija, "
                "ostali su NOVI izveštaji o istoj priči. Napiši celu vest ponovo: najnovije činjenice idu u naslov i uvod, "
                "zadrži proverene činjenice iz dosadašnje verzije, ne ponavljaj isto dva puta. preskoci=true samo ako novi izvori ne donose ništa novo."
                if update else
                f"Danas je {sr_date(datetime.now(), True)} Napiši VEST za Prestonicu na osnovu sledećih izvora "
                f"(ista priča iz {len(cluster['sources'])} medija).")
        prompt = uvod + "\n\n" + "\n\n".join(parts)
        return self._call(prompt, model)

    def analysis(self, base: dict, related: list[dict]) -> dict | None:
        ctx = "\n\n".join(
            f"### {a['headline']}\n{a['lead']}\n" + "\n".join(a["body"]) + "\nIzvori: " + ", ".join(s["name"] for s in a["sources"])
            for a in [base] + related[:3]
        )
        prompt = (
            f"Danas je {sr_date(datetime.now(), True)} Napiši ANALIZU (perspektiva desnice, po uređivačkoj politici) "
            f"o temi prve vesti ispod. Koristi samo činjenice iz ovih tekstova. 5-7 pasusa. Naslov mora biti drugačiji od naslova vesti.\n\n{ctx}"
        )
        return self._call(prompt, self.cfg.portal.get("model_analize") or None)

    def media(self, a: dict) -> dict | None:
        heads = "\n".join(f"- {s['name']} ({s['spektar']}): „{s.get('title', '')}“" for s in a["sources"][:20])
        prompt = (
            f"Danas je {sr_date(datetime.now(), True)} Napiši tekst za rubriku MEDIJI POD LUPOM (rubrika: mediji). "
            "Tema: kako su različiti mediji naslovili i predstavili istu priču. Uporedi naslove ispod: šta ko ističe, šta prećutkuje, "
            "koje reči bira i šta to govori o agendi medija, kritički prema mainstream medijima sa obe strane (bliskim bivšoj vlasti i "
            "onima koji se predstavljaju kao nezavisni). Oslanjaj se ISKLJUČIVO na naslove i činjenice ispod; ne pripisuj medijima ništa "
            "što ne piše. Naslov neka počne sa 'Mediji pod lupom:'. 4-6 pasusa.\n\n"
            f"PRIČA: {a['headline']}\n{a['lead']}\n" + "\n".join(a["body"]) + f"\n\nNASLOVI PO MEDIJIMA:\n{heads}"
        )
        return self._call(prompt, self.cfg.portal.get("model_analize") or None)

    def videos(self, vids: list[dict]) -> list[dict]:
        listing = "\n\n".join(f"### id: {v['id']} | kanal: {v['kanal']} | objavljeno: {v['published']}\nNaslov: {v['title']}\nOpis: {v.get('desc', '')[:700]}" for v in vids)
        try:
            msg = self.client.messages.create(
                model=self.cfg.portal["model"], max_tokens=9000,
                system=VIDEO_RULES.format(rubrike=", ".join(self.cfg.rubrike)),
                tools=[VIDEO_TOOL], tool_choice={"type": "auto"},
                messages=[{"role": "user", "content": "Opiši sledeće snimke.\n\n" + listing}],
            )
            for block in msg.content:
                if block.type == "tool_use":
                    got = block.input.get("videi", [])
                    if isinstance(got, str):
                        got = json.loads(got)
                    log.info("Video opisi: stop=%s, stavki=%d, primer=%s", msg.stop_reason, len(got), str(got[:1])[:300])
                    out = []
                    for i, d in enumerate(got):
                        if isinstance(d, dict):
                            vid = str(d.get("id", "")).replace("id:", "").strip()
                            if vid not in {v["id"] for v in vids} and i < len(vids):
                                vid = vids[i]["id"]
                            d["id"] = vid
                            out.append(d)
                    return out
            log.error("Video opisi: neočekivan odgovor: %s", str(msg.content)[:400])
        except Exception as e:
            log.error("Claude greška (video): %s", e)
        return []

    def link_videos(self, vids: list[dict], arts: list[dict]) -> list[dict]:
        tool = {"name": "upari", "description": "Parovi snimak-vest.", "input_schema": {"type": "object", "properties": {
            "parovi": {"type": "array", "items": {"type": "object", "properties": {"video": {"type": "string"}, "vest": {"type": "string"}},
                       "required": ["video", "vest"]}}}, "required": ["parovi"]}}
        vl = "\n".join(f"{v['id']} | {v['naslov']} | {v.get('opis', '')[:160]}" for v in vids)
        al = "\n".join(f"{a['id']} | {a['headline']}" for a in arts)
        prompt = ("Upari snimke sa vestima. Par napravi kada se snimak tiče istog konkretnog slučaja, događaja ili odluke kao vest, "
                  "tako da bi čitaocu te vesti snimak bio direktno koristan (npr. vest o hapšenju u slučaju X i snimak izjave porodice u slučaju X). "
                  "Ne uparuj samo zbog iste šire teme (npr. 'politika', 'izbori' uopšte) ili iste osobe u različitim povodima. Jedan snimak najviše uz jednu vest, "
                  "jedna vest najviše jedan snimak. Prazna lista je sasvim u redu.\n\nSNIMCI (id | naslov | opis):\n" + vl +
                  "\n\nVESTI (id | naslov):\n" + al)
        try:
            msg = self.client.messages.create(model=self.cfg.portal["model"], max_tokens=1200, tools=[tool], tool_choice={"type": "auto"},
                                              messages=[{"role": "user", "content": prompt + "\n\nOdgovori isključivo pozivom alata upari."}])
            for block in msg.content:
                if block.type == "tool_use":
                    got = block.input.get("parovi", [])
                    return json.loads(got) if isinstance(got, str) else list(got)
        except Exception as e:
            log.error("Claude greška (uparivanje): %s", e)
        return []

    def x_posts(self, posts: list[dict], arts: list[dict]) -> list[dict]:
        """Ocena X objava: šta je od javnog značaja i uz koju vest ide."""
        tool = {"name": "oceni_objave", "description": "Ocena X objava za portal.", "input_schema": {"type": "object", "properties": {
            "objave": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"},
                "prikazi": {"type": "boolean", "description": "true samo za objave od javnog značaja: politika, društvo, ekonomija, institucije, protesti, izbori, mediji"},
                "rubrika": {"type": "string"},
                "tema": {"type": "string", "description": "o čemu je objava, do 70 znakova, latinica"},
                "vest": {"type": "string", "description": "id vesti o istom konkretnom događaju ili slučaju, ili prazno"}},
                "required": ["id", "prikazi"]}}}, "required": ["objave"]}}
        pl = "\n".join(f"{p['id']} | @{p['x']} ({p.get('ime', '')}) | " + " ".join(p["text"][:400].split()) for p in posts)
        al = "\n".join(f"{a['id']} | {a['headline']}" for a in arts)
        prompt = ("Oceni objave sa mreže X za srpski informativni portal. prikazi=false za: privatne i porodične objave, čestitke bez sadržaja, "
                  "reklame, sport bez javnog značaja, objave sa uvredama, pretnjama ili govorom mržnje, i objave bez jasnog smisla. "
                  "Stav autora nije razlog za odbacivanje: prikazuju se i vlast i opozicija. "
                  f"rubrika: jedna od {', '.join(self.cfg.rubrike)}. "
                  "vest: id vesti samo ako je objava o istom konkretnom događaju, odluci ili slučaju kao vest (reakcija, izjava, dopuna); inače prazno.\n\n"
                  "OBJAVE (id | nalog | tekst):\n" + pl + "\n\nVESTI (id | naslov):\n" + al)
        try:
            msg = self.client.messages.create(model=self.cfg.portal["model"], max_tokens=4000, tools=[tool], tool_choice={"type": "auto"},
                                              messages=[{"role": "user", "content": prompt + "\n\nOdgovori isključivo pozivom alata oceni_objave."}])
            for block in msg.content:
                if block.type == "tool_use":
                    got = block.input.get("objave", [])
                    return json.loads(got) if isinstance(got, str) else list(got)
        except Exception as e:
            log.error("Claude greška (X objave): %s", e)
        return []

    def resolve_people(self, batch: list[dict]) -> list[dict]:
        """Ko je osoba iz vesti: pravi Wikidata zapis (ili nijedan), uloga i oblast, za profil na stranici Ličnosti."""
        tool = {"name": "osobe", "description": "Identifikacija osoba iz vesti.", "input_schema": {"type": "object", "properties": {
            "osobe": {"type": "array", "items": {"type": "object", "properties": {
                "ime": {"type": "string", "description": "ime tačno kako je dato"},
                "wd": {"type": "string", "description": "id kandidata koji je sigurno ta osoba iz vesti, ili prazno ako nijedan nije sigurno ta osoba"},
                "prikaz": {"type": "string", "description": "puno ime za prikaz, latinica, srpski pravopis (npr. Donald Tramp, Novak Đoković)"},
                "uloga": {"type": "string", "description": "funkcija ili zanimanje u 2-6 reči, latinica (npr. predsednik SAD, teniser, tužilac u Beogradu)"},
                "oblast": {"type": "string", "enum": ["politika", "svet", "ekonomija", "drustvo", "hronika", "sport", "kultura"]},
                "profil": {"type": "boolean", "description": "false za privatna lica, žrtve, maloletnike, osumnjičene koji nisu javne ličnosti i za nejasna imena"}},
                "required": ["ime", "wd", "prikaz", "uloga", "oblast", "profil"]}}}, "required": ["osobe"]}}
        lines = []
        for b in batch:
            c = "; ".join(f"{x['id']}: {x.get('naziv', '')} ({x.get('opis', '') or 'bez opisa'})" for x in b["kandidati"]) or "nema kandidata"
            lines.append(f"IME: {b['ime']}\nVESTI: " + " | ".join(b["vesti"]) + f"\nKANDIDATI: {c}")
        prompt = ("Za svaku osobu iz vesti srpskog portala odredi ko je. wd postavi samo ako si siguran da je kandidat baš ta osoba "
                  "(uloga iz vesti se poklapa sa opisom kandidata); kod čestih imena bez jasnog poklapanja ostavi prazno. "
                  "profil=false za privatna lica, žrtve, maloletnike i osumnjičene ili optužene koji nisu javne ličnosti.\n\n" + "\n\n".join(lines))
        try:
            msg = self.client.messages.create(model=self.cfg.portal["model"], max_tokens=3000, tools=[tool], tool_choice={"type": "auto"},
                                              messages=[{"role": "user", "content": prompt + "\n\nOdgovori isključivo pozivom alata osobe."}])
            for block in msg.content:
                if block.type == "tool_use":
                    got = block.input.get("osobe", [])
                    return json.loads(got) if isinstance(got, str) else list(got)
        except Exception as e:
            log.error("Claude greška (osobe): %s", e)
        return []

    def poll(self, arts: list[dict], recent: list[str]) -> dict | None:
        """Pitanje dana za čitaoce: neutralno, uravnoteženo, bez pitanja o krivici pojedinaca."""
        tool = {"name": "anketa", "description": "Anketa čitalaca.", "input_schema": {"type": "object", "properties": {
            "pitanje": {"type": "string", "description": "pitanje do 110 znakova, latinica, završava se upitnikom"},
            "opcije": {"type": "array", "items": {"type": "string"}, "description": "3 ili 4 kratka odgovora (najviše 25 znakova svaki, zbog ankete na X-u), međusobno isključiva, uravnotežena"},
            "vest": {"type": "string", "description": "id vesti na koju se pitanje odnosi"},
            "tema": {"type": "string", "description": "tema u 2-4 reči"}}, "required": ["pitanje", "opcije", "vest", "tema"]}}
        al = "\n".join(f"{a['id']} | važnost {a.get('importance')} | {a['headline']} | {a['lead'][:200]}" for a in arts)
        prompt = ("Sastavi 'Pitanje dana' za čitaoce srpskog informativnog portala, o jednoj od najvažnijih tema dana sa spiska. "
                  "Pravila: pitanje traži mišljenje čitaoca o javnoj temi ili politici (npr. odluka, predlog, mera, događaj), neutralno i bez sugestije; "
                  "odgovori pokrivaju ceo raspon stavova i ne vrede nijednu stranu; jedan odgovor može biti 'Nemam stav' ili 'Ne znam'. "
                  "Zabranjeno: pitanja o krivici ili nevinosti konkretnih osoba, o istragama i suđenjima u toku, o privatnim licima, o zdravlju, "
                  "veri ili nacionalnosti, i pitanja koja vređaju. Ne ponavljaj nedavna pitanja.\n\n"
                  "NEDAVNA PITANJA:\n" + ("\n".join(recent) or "-") + "\n\nVESTI (id | važnost | naslov | uvod):\n" + al)
        try:
            msg = self.client.messages.create(model=self.cfg.portal["model"], max_tokens=800, tools=[tool], tool_choice={"type": "auto"},
                                              messages=[{"role": "user", "content": prompt + "\n\nOdgovori isključivo pozivom alata anketa."}])
            for block in msg.content:
                if block.type == "tool_use":
                    d = dict(block.input)
                    if isinstance(d.get("opcije"), str):
                        d["opcije"] = json.loads(d["opcije"])
                    return d
        except Exception as e:
            log.error("Claude greška (anketa): %s", e)
        return None

    def owner(self, text: str) -> dict | None:
        prompt = (
            "Vlasnik portala je poslao autorski tekst. ZADRŽI njegov glas, stav i stil. Ispravi samo pravopis i gramatiku, "
            "podeli u pasuse i predloži naslov i uvod ako ih nema (ako prvi red izgleda kao naslov, koristi ga). "
            "Ne dodaj sadržaj, ne ublažavaj stav. rubrika: najbliža tema. Nikad ne preskači.\n\n" + text
        )
        return self._call(prompt)


VIDEO_TOOL = {
    "name": "opisi_videe",
    "description": "Predaje kratke opise video snimaka za video stranu portala.",
    "input_schema": {
        "type": "object",
        "properties": {"videi": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"},
            "preskoci": {"type": "boolean", "description": "true za zabavu, reklame, najave bez sadržaja, muziku, nevažne teme"},
            "rubrika": {"type": "string"},
            "naslov": {"type": "string", "description": "informativan naslov do 80 znakova, bez velikih slova, uzvičnika i | znakova"},
            "opis": {"type": "string", "description": "2-3 rečenice: ko govori, o čemu i povod; samo iz naslova i opisa snimka"},
            "tekst": {"type": "array", "items": {"type": "string"}, "description": "2-4 kratka pasusa za stranu snimka: šta se u snimku dešava, ko učestvuje, koje teme se otvaraju i kontekst iz opisa; samo činjenice iz naslova i opisa, bez ponavljanja opisa od reči do reči. Ako opis snimka nema dovoljno podataka, napiši 1 pasus."},
            "oznake": {"type": "array", "items": {"type": "string"}, "description": "3-5 tema, osoba, institucija"},
        }, "required": ["id", "preskoci", "rubrika", "naslov", "opis", "tekst", "oznake"]}}},
        "required": ["videi"],
    },
}

VIDEO_RULES = """Pišeš kratke opise video snimaka za informativnu video stranu portala Prestonica.
- Latinica, ekavica, neutralan novinarski ton. Naslov i opis počinju velikim slovom.
- Koristi SAMO ono što piše u naslovu i opisu snimka. Ne izmišljaj imena, brojke ni tvrdnje.
- Stav iz snimka prenesi kao izjavu ("X u snimku tvrdi da..."), nikad kao utvrđenu činjenicu.
- Ako je snimak stranački ili emisija, to prirodno navedi u opisu (npr. "Na konferenciji stranke...", "U emisiji...").
- Bez reklamnih poziva (lajk, pretplata, donacije), bez linkova i heštegova.
- rubrika mora biti jedna od: {rubrike}.
- Odgovori isključivo pozivom alata opisi_videe."""


GENERIC_TAGS = {"vesti", "srbija", "svet", "biznis", "ekonomija", "politika", "drustvo", "news", "video", "region", "vest", "info"}


class MockWriter:
    """Probni režim bez API ključa: slaže tekst iz sažetaka izvora."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def news(self, cluster: dict, texts: dict[str, str], model: str | None = None) -> dict | None:
        items = sorted(cluster["items"], key=lambda i: (-(i["spektar"] == "centar"), -i["poverenje"]))
        title = sentence_case_caps(items[0]["title"]).replace("\"", "„", 1).replace("\"", "“", 1)
        with_sum = [i for i in items if len(i.get("summary", "")) > 40]
        if with_sum:
            lead = _first_sentences(with_sum[0]["summary"], 2)
            paras = []
            for i in with_sum[1:4]:
                paras.append(f"{_first_sentences(i['summary'], 3)} ({i['source_name']})")
        else:
            lead = f"{title}, prenosi {items[0]['source_name']}."
            paras = []
        others = [i["title"] for i in items[1:4] if i["title"] != items[0]["title"]]
        for t in others:
            if len(paras) < 3:
                paras.append(f"{items[[x['title'] for x in items].index(t)]['source_name']} izveštava: {sentence_case_caps(t)}.")
        srcs = cluster["sources"]
        paras.append("O ovoj temi izveštavali su: " + ", ".join(srcs) + ".")
        cats = []
        for i in items:
            for c in i.get("categories", []):
                if fold(c) not in GENERIC_TAGS and c not in cats and len(c) < 32:
                    cats.append(c[:1].upper() + c[1:])
        imp = min(10, 4 + len(srcs) + (1 if cluster.get("section_hint") in ("politika", "ekonomija") else 0))
        return {
            "preskoci": False, "rubrika": cluster.get("section_hint", "politika"), "naslov": title, "uvod": lead,
            "pasusi": paras, "oznake": cats[:5], "osobe": [], "mesta": [], "vaznost": imp, "foto_upit_en": "", "foto_osoba": "",
        }

    def analysis(self, base: dict, related: list[dict]) -> dict | None:
        return None

    def media(self, a: dict) -> dict | None:
        return None

    def videos(self, vids: list[dict]) -> list[dict]:
        out = []
        for v in vids:
            t = re.sub(r"\s*[|#].*$", "", v["title"]).strip() or v["title"]
            out.append({"id": v["id"], "preskoci": False, "rubrika": "politika", "naslov": sentence_case_caps(t)[:90],
                        "opis": _first_sentences(v.get("desc", ""), 2)[:300], "oznake": []})
        return out

    def owner(self, text: str) -> dict | None:
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        head = lines[0] if lines and len(lines[0]) < 120 else "Autorski tekst"
        rest = lines[1:] if head != "Autorski tekst" else lines
        return {"preskoci": False, "rubrika": "politika", "naslov": head, "uvod": rest[0] if rest else "", "pasusi": rest[1:] or rest,
                "oznake": [], "osobe": [], "mesta": [], "vaznost": 7, "foto_upit_en": "", "foto_osoba": ""}


def _first_sentences(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZČĆŠŽĐ„\"])", text)
    s = " ".join(parts[:n]).strip()
    if s and s[-1] not in ".!?“\"":
        s = s.rsplit(" ", 1)[0] + "…" if len(s) > 200 else s + "."
    return s


def get_writer(cfg: Config, mock: bool):
    if mock or not cfg.secrets.get("anthropic"):
        if not mock:
            log.warning("Nema ANTHROPIC_API_KEY: radim u probnom režimu")
        return MockWriter(cfg)
    return ClaudeWriter(cfg)
