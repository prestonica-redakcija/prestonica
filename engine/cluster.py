"""Grupisanje: ista priča iz više izvora postaje jedna vest."""
from __future__ import annotations

from .util import age_hours, stems


COMMON = {"vucic", "srbij", "stude", "list", "vlad", "beogr", "rekao", "izjav", "novi", "nova", "predse", "pred", "posl"}


def _sim(a: set, b: set, df: dict | None = None) -> tuple[float, float]:
    """Preklapanje korena; česte reči (Vučić, Srbija, vlada) nose pola težine."""
    if not a or not b:
        return 0.0, 0.0
    shared = a & b
    w = sum(0.5 if (s in COMMON or (df and df.get(s, 0) > 4)) else 1.0 for s in shared)
    return w / min(len(a), len(b)), w


def cluster_items(items: list[dict], threshold: float = 0.34) -> list[dict]:
    items = sorted(items, key=lambda x: x["published"])
    df: dict[str, int] = {}
    for it in items:
        for s in stems(it["title"]):
            df[s] = df.get(s, 0) + 1
    clusters: list[dict] = []
    for it in items:
        st = stems(it["title"])
        best, best_s = None, 0.0
        for c in clusters:
            s, inter = _sim(st, c["title_stems"], df)
            if inter >= 1.8 and s >= threshold and s > best_s:
                best, best_s = c, s
        if best:
            # isti izvor dva puta u klasteru ne povećava značaj
            best["items"].append(it)
            best["stems"] |= st
            best["title_stems"] |= st
        else:
            clusters.append({"items": [it], "stems": set(st), "title_stems": set(st)})
    for c in clusters:
        c["sources"] = sorted({i["source_name"] for i in c["items"]})
        c["latest"] = max(i["published"] for i in c["items"])
    return clusters


def match_existing(cluster: dict, articles: list[dict], hours: float = 36) -> dict | None:
    """Da li već imamo vest o ovome? Ako da, vraća je (dodaju se novi izvori)."""
    best, best_s = None, 0.0
    for a in articles:
        if a.get("kind") != "vest" or a.get("hidden") or age_hours(a["published"]) > hours:
            continue
        s, inter = _sim(cluster["stems"], stems(a["headline"] + " " + " ".join(a.get("source_titles", []))))
        if inter >= 2 and s >= 0.45 and s > best_s:
            best, best_s = a, s
    return best
