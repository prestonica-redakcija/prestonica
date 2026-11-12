"""Rangiranje: šta je važno, po tvojim prioritetima iz config/."""
from __future__ import annotations

from .config import Config
from .util import age_hours, fold


def keyword_factor(text: str, cfg: Config, fokus: list[str]) -> float:
    words = fold(text).split()
    joined = " " + " ".join(words) + " "
    f = 1.0
    for rule in cfg.teme.get("smanji", []) or []:
        key = fold(rule["rec"])
        if any(w.startswith(key) for w in words) or (" " in key and f" {key}" in joined):
            f *= float(rule["faktor"])
    if f == 0:
        return 0.0
    for rule in cfg.teme.get("pojacaj", []) or []:
        key = fold(rule["rec"])
        if any(w.startswith(key) for w in words):
            f *= float(rule["faktor"])
    # političke ličnosti sa spiska (config/licnosti.yaml): ista blaga prednost za sve, bez izuzetka
    for p in cfg.licnosti:
        if any(any(w.startswith(fold(k)) and len(w) <= len(fold(k)) + 3 for w in words) for k in p.get("kljucne", []) if " " not in k):
            f *= 1.15
            break
    for t in list(cfg.teme.get("fokus", []) or []) + list(fokus or []):
        key = fold(t)
        if key and key in " ".join(words):
            f *= 1.6
    return min(f, 4.0)


def cluster_score(c: dict, cfg: Config, fokus: list[str]) -> float:
    # Svaki dodatni izvor donosi manje (da jedan medij sa 5 tekstova ne "pobedi")
    by_source: dict[str, float] = {}
    for it in c["items"]:
        by_source[it["source_name"]] = max(by_source.get(it["source_name"], 0), it["poverenje"])
    trusts = sorted(by_source.values(), reverse=True)
    coverage = sum(t * (0.6 ** i) for i, t in enumerate(trusts))
    # izveštavaju li o tome obe strane spektra? tada je priča nesporno važna
    spektri = {it["spektar"] for it in c["items"]}
    both = 1.25 if {"vlast", "opozicija"} <= spektri else 1.0
    hints = [it["rubrika_hint"] for it in c["items"]]
    section = max(set(hints), key=hints.count)
    text = " ".join(it["title"] + " " + it.get("summary", "") + " " + " ".join(it.get("categories", [])) for it in c["items"])
    kw = keyword_factor(text, cfg, fokus)
    fresh = 0.5 ** (age_hours(c["latest"]) / float(cfg.limiti["poluzivot_sati"]))
    c["section_hint"] = section
    return round(coverage * both * cfg.weight(section) * kw * (0.4 + 0.6 * fresh) * 3, 3)


def article_score(a: dict, cfg: Config, pins: dict) -> float:
    if a.get("hidden"):
        return -1
    imp = float(a.get("importance", 5)) / 10
    n = len(a.get("sources", [])) or 1
    # svežina se meri od poslednje dopune priče; broj izvora pomaže, ali ne može da drži staru vest na vrhu ceo dan
    fresh = 0.5 ** (age_hours(a.get("prepisano") or a["published"]) / float(cfg.limiti["poluzivot_sati"]))
    s = imp * cfg.weight(a["section"]) * (1 + 0.18 * min(n - 1, 6)) * fresh * a.get("boost", 1.0)
    if a["id"] in pins:
        s += 100
    return s
