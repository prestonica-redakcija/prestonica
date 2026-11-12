"""Učitavanje konfiguracije iz config/."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _yaml(name: str) -> dict:
    with open(ROOT / "config" / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Config:
    portal: dict
    izvori: list
    teme: dict
    politika: str
    foto: dict
    licnosti: list = field(default_factory=list)
    root: Path = ROOT
    secrets: dict = field(default_factory=dict)

    @property
    def rubrike(self) -> dict:
        return self.portal["rubrike"]

    @property
    def limiti(self) -> dict:
        return self.portal["limiti"]

    def weight(self, rubrika: str) -> float:
        return float(self.rubrike.get(rubrika, {}).get("tezina", 0.3))


def load() -> Config:
    foto_path = ROOT / "uploads" / "index.yaml"
    foto = yaml.safe_load(foto_path.read_text(encoding="utf-8")) if foto_path.exists() else {}
    return Config(
        portal=_yaml("portal.yaml"),
        izvori=[s for s in _yaml("izvori.yaml").get("izvori", []) if s.get("ukljucen", True)],
        teme=_yaml("teme.yaml"),
        politika=(ROOT / "config" / "urednicka-politika.md").read_text(encoding="utf-8"),
        foto=foto or {},
        licnosti=(_yaml("licnosti.yaml").get("licnosti", []) if (ROOT / "config" / "licnosti.yaml").exists() else []),
        secrets={
            "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
            "pexels": os.environ.get("PEXELS_API_KEY", ""),
            "unsplash": os.environ.get("UNSPLASH_ACCESS_KEY", ""),
            "telegram_token": os.environ.get("TELEGRAM_TOKEN", ""),
            "telegram_chat": os.environ.get("TELEGRAM_CHAT_ID", ""),
        },
    )
