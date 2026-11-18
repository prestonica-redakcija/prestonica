"""Tvoj privatni Telegram kanal ka portalu.

Šalješ botu:
  - duži tekst (300+ znakova)      -> objavljuje se kao autorski tekst
  - fotografiju sa dužim opisom    -> autorski tekst sa tvojom fotografijom
  - /skini <deo naslova>           -> vest se odmah skida sa sajta
  - /vrati <deo naslova>           -> vraća skinutu vest
  - /istakni <deo naslova>         -> vest ide u glavnu (12 sati)
  - /tema <tema>                   -> portal 48h posebno prati temu
  - /status                        -> stanje sistema
  - /pomoc                         -> spisak komandi
Prihvataju se samo poruke sa tvog TELEGRAM_CHAT_ID.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import requests

from .config import ROOT, Config
from .util import fold, now_utc

log = logging.getLogger("prestonica")
API = "https://api.telegram.org/bot{token}/{method}"
HELP = (
    "Prestonica komande:\n"
    "/skini <deo naslova> skida vest\n/vrati <deo naslova> vraća vest\n"
    "/istakni <deo naslova> stavlja vest u glavnu\n/tema <tema> prati temu 48h\n/status stanje\n"
    "/anketa Pitanje? | odgovor 1 | odgovor 2 | ... | 3d  pušta anketu na sajtu i na X-u (2 do 4 odgovora do 25 znakova, trajanje u danima)\n"
    "/ankete aktivne ankete\n"
    "/anonimno <tekst i link> dodaje dojavu u rubriku Anonimno (može i uz fotografiju)\n"
    "/anonimno_skini <deo teksta> uklanja dojavu\n"
    "Duži tekst ili fotografija sa opisom = autorski tekst."
)


class Telegram:
    def __init__(self, cfg: Config):
        self.token = cfg.secrets.get("telegram_token")
        self.chat = str(cfg.secrets.get("telegram_chat") or "")
        self.enabled = bool(self.token and self.chat and cfg.portal.get("telegram", {}).get("ukljucen"))

    def _call(self, method: str, **params):
        r = requests.post(API.format(token=self.token, method=method), data=params, timeout=20)
        return r.json()

    def reply(self, text: str) -> None:
        if self.enabled:
            try:
                self._call("sendMessage", chat_id=self.chat, text=text[:3900])
            except Exception:
                pass

    def poll(self, state: dict) -> list[dict]:
        if not self.enabled:
            return []
        try:
            res = self._call("getUpdates", offset=int(state.get("telegram_offset", 0)) + 1, timeout=0)
        except Exception as e:
            log.warning("Telegram nedostupan: %s", e)
            return []
        out = []
        for u in res.get("result", []):
            state["telegram_offset"] = u["update_id"]
            m = u.get("message") or {}
            if str(m.get("chat", {}).get("id")) != self.chat:
                continue  # ignoriši sve osim vlasnika
            out.append(m)
        return out

    def download_photo(self, m: dict) -> str | None:
        try:
            fid = m["photo"][-1]["file_id"]
            path = self._call("getFile", file_id=fid)["result"]["file_path"]
            data = requests.get(f"https://api.telegram.org/file/bot{self.token}/{path}", timeout=30).content
            name = f"tg-{m['message_id']}.jpg"
            (ROOT / "uploads" / "foto" / name).write_bytes(data)
            return name
        except Exception as e:
            log.warning("Fotografija nije preuzeta: %s", e)
            return None


def find_article(articles: list[dict], query: str) -> dict | None:
    q = fold(query)
    if not q:
        return None
    hits = [a for a in articles if q in fold(a["headline"]) or q == a["slug"]]
    return max(hits, key=lambda a: a["published"]) if hits else None


def handle_commands(tg: Telegram, msgs: list[dict], articles: list[dict], state: dict, save) -> list[dict]:
    """Izvršava komande; vraća listu autorskih priloga za objavu."""
    author_posts = []
    for m in msgs:
        text = (m.get("text") or m.get("caption") or "").strip()
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            cmd = cmd.split("@")[0].lower()
            if cmd in ("/skini", "/vrati"):
                a = find_article(articles, arg)
                if a:
                    a["hidden"] = cmd == "/skini"
                    save(a)
                    tg.reply(("Skinuto: " if a["hidden"] else "Vraćeno: ") + a["headline"])
                else:
                    tg.reply("Nisam našao vest: " + arg)
            elif cmd == "/istakni":
                a = find_article(articles, arg)
                if a:
                    state.setdefault("pins", {})[a["id"]] = (now_utc() + timedelta(hours=12)).isoformat()
                    tg.reply("Istaknuto 12h: " + a["headline"])
                else:
                    tg.reply("Nisam našao vest: " + arg)
            elif cmd == "/tema" and arg:
                state.setdefault("fokus", []).append({"tema": arg, "do": (now_utc() + timedelta(hours=48)).isoformat()})
                tg.reply("Pratim temu 48h: " + arg)
            elif cmd == "/anketa":
                from .ankete import parse_command
                q = parse_command(arg, 3)
                if q:
                    state.setdefault("ankete_red", []).append(q)
                    tg.reply(f"Anketa izlazi u sledećem osvežavanju, traje {q['dana']:g} dana:\n{q['pitanje']}\n- " + "\n- ".join(q["opcije"]))
                else:
                    tg.reply("Format: /anketa Pitanje? | odgovor 1 | odgovor 2 | odgovor 3 | 3d (2 do 5 odgovora)")
            elif cmd == "/ankete":
                from .ankete import active
                act = active(state)
                tg.reply("Aktivne ankete:\n" + "\n".join(f"- {p['pitanje']} (do {p['kraj'][:16].replace('T', ' ')} UTC)" for p in act) if act else "Nema aktivnih anketa.")
            elif cmd == "/anonimno":
                from . import anonimno
                photo = tg.download_photo(m) if m.get("photo") else None
                it = anonimno.dodaj(arg, photo)
                tg.reply("Dodato u Anonimno, izlazi u sledećem osvežavanju." if it else "Format: /anonimno tekst dojave i link (može i uz fotografiju)")
            elif cmd == "/anonimno_skini":
                from . import anonimno
                it = anonimno.ukloni(arg)
                tg.reply("Uklonjeno iz Anonimno: " + (it["tekst"][:80] if it else "") if it else "Nisam našao dojavu: " + arg)
            elif cmd == "/status":
                last = (state.get("runs") or [{}])[-1]
                tg.reply(f"Poslednje osvežavanje: {last.get('vreme', '-')}\nNovih vesti: {last.get('novih', 0)}\n"
                         f"Ukupno u arhivi: {len(articles)}")
            else:
                tg.reply(HELP)
        elif len(text) >= 300:
            photo = tg.download_photo(m) if m.get("photo") else None
            author_posts.append({"text": text, "photo": photo})
            tg.reply("Primljeno. Tekst izlazi u sledećem osvežavanju.")
        elif text:
            tg.reply("Tekst je kratak za objavu (najmanje 300 znakova).\n\n" + HELP)
    return author_posts
