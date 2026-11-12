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
