"""Podaci za bočne module: kursna lista NBS i vreme u Beogradu (javni, besplatni API-ji)."""
from __future__ import annotations

import requests

WMO = {0: "Vedro", 1: "Pretežno vedro", 2: "Delimično oblačno", 3: "Oblačno", 45: "Magla", 48: "Magla", 51: "Rosulja", 53: "Rosulja",
       55: "Rosulja", 61: "Slaba kiša", 63: "Kiša", 65: "Jaka kiša", 71: "Slab sneg", 73: "Sneg", 75: "Jak sneg", 80: "Pljuskovi",
       81: "Pljuskovi", 82: "Jaki pljuskovi", 95: "Grmljavina"}


def kurs() -> list[dict]:
    out = []
    for code in ("eur", "usd", "chf"):
        try:
            r = requests.get(f"https://kurs.resenje.org/api/v1/currencies/{code}/rates/today", timeout=8)
            j = r.json()
            out.append({"code": code.upper(), "rate": float(j["exchange_middle"]), "date": j.get("date", "")})
        except Exception:
            pass
    return out


def vreme() -> dict | None:
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast",
                         params={"latitude": 44.82, "longitude": 20.46, "current": "temperature_2m,weather_code",
                                 "daily": "temperature_2m_max,temperature_2m_min", "timezone": "Europe/Belgrade", "forecast_days": 1},
                         timeout=8)
        j = r.json()
        return {"temp": round(j["current"]["temperature_2m"]), "opis": WMO.get(j["current"]["weather_code"], ""),
                "max": round(j["daily"]["temperature_2m_max"][0]), "min": round(j["daily"]["temperature_2m_min"][0])}
    except Exception:
        return None


GORIVO_URL = "https://nafta.hr/sr/cene-goriva-srbija/"
GORIVA = (("Evrodizel", "Eurodizel"), ("BMB 95", "BMB 95 Benzin"), ("Autoplin", "Autoplin (Tečni Naftni Gas)"))


def _gorivo_parse(page: str) -> dict | None:
    import html as _h
    import re
    from collections import Counter
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    t = re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", t)))
    heads = list(re.finditer(r"(Eurodizel Premium|Eurodizel|BMB 95 Premium Benzin|BMB 95 Benzin|BMB 100 Benzin|Autoplin \(Tečni Naftni Gas\)) Kompanija EUR DIN", t))
    out = []
    for naziv, kljuc in GORIVA:
        for i, m in enumerate(heads):
            if m.group(1) != kljuc:
                continue
            kraj = heads[i + 1].start() if i + 1 < len(heads) else m.end() + 700
            cene = [float(x.replace(",", ".")) for x in re.findall(r"(\d{2,3},\d\d) RSD", t[m.end():kraj])]
            if cene:
                najcesca = Counter(cene).most_common(1)[0][0]
                out.append({"naziv": naziv, "cena": round(najcesca), "min": round(min(cene))})
            break
    if len(out) < 2:
        return None
    do = re.search(r"važiti do (\d{1,2})\.(\d{1,2})\.\d{4}", t)
    return {"cene": out, "do": f"{int(do.group(1))}.{int(do.group(2))}." if do else ""}


def gorivo() -> dict | None:
    """Cene goriva u Srbiji (menjaju se petkom u 15h). Keš 6 sati, da se izvor ne opterećuje."""
    import json
    import time
    from .config import ROOT
    cf = ROOT / ".og-cache" / "gorivo.json"
    try:
        if cf.exists() and time.time() - cf.stat().st_mtime < 6 * 3600:
            return json.loads(cf.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        r = requests.get(GORIVO_URL, timeout=12, headers={"User-Agent": "Mozilla/5.0 (compatible; PrestonicaBot/1.0; +https://prestonica.rs)"})
        r.raise_for_status()
        d = _gorivo_parse(r.text)
        if d:
            cf.parent.mkdir(parents=True, exist_ok=True)
            cf.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            return d
    except Exception:
        pass
    try:
        return json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else None
    except Exception:
        return None
