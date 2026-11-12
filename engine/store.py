"""Skladište: svaki članak je JSON fajl u data/articles/. Git je arhiva."""
from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT

ART_DIR = ROOT / "data" / "articles"
STATE = ROOT / "data" / "state.json"


def load_articles() -> list[dict]:
    ART_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(ART_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def save_article(a: dict) -> None:
    ART_DIR.mkdir(parents=True, exist_ok=True)
    (ART_DIR / f"{a['id']}.json").write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"seen": {}, "telegram_offset": 0, "pins": {}, "fokus": [], "runs": []}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
