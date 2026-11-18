"""Rubrika Anonimno: dojave i informacije iz nezvaničnih izvora koje redakcija ručno odabere.

Svaka stavka je JSON fajl u data/anonimno/. Na sajtu svaka stavka uvek nosi oznaku
"Neprovereno", stranica nije u pretraživačima, RSS-u ni u objavama na mrežama.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from . import pravila
from .config import ROOT
from .util import fold, now_utc

DIR = ROOT / "data" / "anonimno"
_URL = re.compile(r"https?://\S+")
_MES = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "avg", "sep", "okt", "nov", "dec"]


def dodaj(tekst: str, foto: str | None = None, izvor: str = "") -> dict | None:
    tekst = (tekst or "").strip()
    link = ""
    m = _URL.search(tekst)
    if m:
        link = m.group(0).rstrip(").,;")
        tekst = (tekst[:m.start()] + tekst[m.end():]).strip()
    if not tekst and not link and not foto:
        return None
    t = now_utc()
    sid = t.astimezone(ZoneInfo("Europe/Belgrade")).strftime("%Y%m%d%H%M") + "-" + hashlib.md5((tekst + link + str(foto)).encode()).hexdigest()[:5]
    it = {"id": sid, "tekst": tekst[:1500], "link": link, "foto": foto or "", "izvor": izvor[:60], "vreme": t.isoformat()}
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{sid}.json").write_text(json.dumps(it, ensure_ascii=False, indent=1), encoding="utf-8")
    return it


def ukloni(upit: str) -> dict | None:
    q = fold(upit)
    if not q or not DIR.exists():
        return None
    for p in sorted(DIR.glob("*.json"), reverse=True):
        it = json.loads(p.read_text(encoding="utf-8"))
        if q == fold(it["id"]) or q in fold(it.get("tekst", "")):
            p.unlink()
            return it
    return None


def ucitaj() -> list[dict]:
    if not DIR.exists():
        return []
    out = []
    tz = ZoneInfo("Europe/Belgrade")
    for p in DIR.glob("*.json"):
        try:
            it = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if pravila.blokira(it):
            continue
        dt = datetime.fromisoformat(it["vreme"]).astimezone(tz)
        it["_time"] = f"{dt.day}. {_MES[dt.month - 1]} {dt:%H:%M}"
        it["_pasusi"] = [x.strip() for x in re.split(r"\n\s*\n|\n", it.get("tekst", "")) if x.strip()]
        it["_domen"] = urlparse(it["link"]).netloc.removeprefix("www.") if it.get("link") else ""
        out.append(it)
    out.sort(key=lambda x: x["vreme"], reverse=True)
    return out


def slike(out: Path, items: list[dict]) -> None:
    from PIL import Image
    (out / "foto" / "a").mkdir(parents=True, exist_ok=True)
    for it in items:
        n = it.get("foto")
        src = ROOT / "uploads" / "foto" / n if n else None
        if not src or not src.exists():
            it["foto"] = ""
            continue
        im = Image.open(src).convert("RGB")
        im.thumbnail((1100, 1100))
        im.save(out / "foto" / "a" / n, quality=80, optimize=True, progressive=True)
