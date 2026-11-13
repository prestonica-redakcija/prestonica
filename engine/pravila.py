"""Uređivačka pravila redakcije: imena i teme koje portal ne prenosi.

Spisak se ne drži u repozitorijumu nego u podešavanjima (promenljiva UREDNICKA_PRAVILA,
jedna stavka po redu ili odvojene zarezom). Bez spiska pravilo ne radi ništa.
"""
from __future__ import annotations

import os
import re

from .util import fold

_rx = None


def _rec(w: str, min_len: int) -> str:
    if len(w) >= min_len:
        osnova = w[:-1] if w[-1] in "aeiou" else w
        return re.escape(osnova) + "[a-z]{0,3}"
    return re.escape(w)


def _ucitaj():
    global _rx
    if _rx is None:
        delovi = []
        for red in re.split(r"[\n;,]+", os.environ.get("UREDNICKA_PRAVILA", "")):
            reci = fold(red).split()
            if reci:
                m = 3 if len(reci) > 1 else 4
                delovi.append(" ".join(_rec(w, m) for w in reci))
        _rx = re.compile("(?<![a-z0-9])(?:" + "|".join(delovi) + ")(?![a-z0-9])") if delovi else False
    return _rx


def _tekst(x, d: int = 0) -> str:
    if d > 4:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return " ".join(_tekst(v, d + 1) for k, v in x.items() if not str(k).startswith("_"))
    if isinstance(x, (list, tuple)):
        return " ".join(_tekst(v, d + 1) for v in x)
    return ""


def blokira(*x) -> bool:
    """Da li tekst, vest, snimak, objava ili anketa sadrži nešto sa spiska."""
    rx = _ucitaj()
    if not rx:
        return False
    return bool(rx.search(fold(" ".join(_tekst(v) for v in x))))
