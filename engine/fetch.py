"""Preuzimanje RSS izvora i punog teksta (uz poštovanje robots.txt)."""
from __future__ import annotations

import json
import re
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from .util import age_hours, latin, now_utc, strip_html

log = logging.getLogger("prestonica")
UA = "PrestonicaBot/1.0 (+https://prestonica.rs/o-nama.html)"
_robots: dict[str, RobotFileParser | None] = {}


def _get(url: str, timeout: int = 15) -> requests.Response:
    return requests.get(url, headers={"User-Agent": UA, "Accept-Language": "sr,en;q=0.5"}, timeout=timeout)


def allowed(url: str) -> bool:
    host = urlparse(url).scheme + "://" + urlparse(url).netloc
    if host not in _robots:
        rp = RobotFileParser()
        try:
            r = _get(host + "/robots.txt", timeout=8)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            _robots[host] = rp
        except Exception:
            _robots[host] = None
    rp = _robots[host]
    return True if rp is None else rp.can_fetch(UA, url)


PATH_HINTS = [("/biznis/", "ekonomija"), ("/ekonomija/", "ekonomija"), ("/svet/", "svet"), ("/planeta/", "svet"), ("/region/", "svet"),
              ("/kultura/", "kultura"), ("/sport/", "sport"), ("/hronika/", "hronika"), ("/drustvo/", "drustvo"), ("/magazin/", "kultura")]
CAT_HINTS = {"svet": "svet", "region": "svet", "ekonomija": "ekonomija", "biznis": "ekonomija", "kultura": "kultura", "sport": "sport",
             "hronika": "hronika", "drustvo": "drustvo", "zivot": "drustvo", "zdravlje": "drustvo", "politika": "politika"}


def section_hint(link: str, categories: list[str], default: str) -> str:
    """Rubrika iz putanje linka i kategorija izvora; Claude je u pravom radu ionako sam određuje."""
    from .util import fold as _f
    for frag, sec in PATH_HINTS:
        if frag in link:
            return sec
    for c in categories[:2]:
        if _f(c) in CAT_HINTS:
            return CAT_HINTS[_f(c)]
    return default


def _date(entry) -> str | None:
    for key in ("published", "updated", "pubDate"):
        v = entry.get(key)
        if v:
            try:
                dt = parsedate_to_datetime(v)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat()
            except Exception:
                pass
    return None


import threading

_RESOLVE_LOCK = threading.Lock()


def _resolve_channel(name: str, key: str) -> str:
    with _RESOLVE_LOCK:
        return _resolve_channel_locked(name, key)


def _resolve_channel_locked(name: str, key: str) -> str:
    """Pronalazi zvanični kanal po imenu (jednom), čuva u data/youtube_kanali.json."""
    from .config import ROOT
    from .util import fold

    path = ROOT / "data" / "youtube_kanali.json"
    try:
        cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        cache = {}
    if name in cache:
        return cache[name] or ""
    cid = ""
    if name.startswith("@"):
        try:
            r = requests.get("https://www.googleapis.com/youtube/v3/channels",
                             params={"part": "snippet", "forHandle": name, "key": key}, timeout=15)
            its = r.json().get("items", [])
            if its:
                cid = its[0]["id"]
                cache[name + "#naslov"] = its[0]["snippet"].get("title", "")
        except Exception as e:
            log.warning("YouTube handle '%s' nije pronađen: %s", name, e)
        cache[name] = cid
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        return cid
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/search",
                         params={"part": "snippet", "type": "channel", "q": name, "maxResults": 5, "key": key}, timeout=15)
        want = set(fold(name).split())
        for it in r.json().get("items", []):
            title = fold(it["snippet"].get("channelTitle", ""))
            if want and want <= set(title.split()):
                cid = it["snippet"]["channelId"]
                cache[name + "#naslov"] = it["snippet"].get("channelTitle", "")
                break
    except Exception as e:
        log.warning("YouTube pretraga '%s' nije uspela: %s", name, e)
    cache[name] = cid
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cid


def fetch_youtube(src: dict) -> list[dict]:
    """YouTube kanal preko zvaničnog YouTube Data API-ja (naslovi i opisi videa)."""
    import os

    key = os.environ.get("YOUTUBE_API_KEY", "")
    cid = src.get("youtube_kanal", "")
    if key and not cid.startswith("UC") and src.get("youtube_handle"):
        cid = _resolve_channel(src["youtube_handle"], key)
    if key and not cid.startswith("UC") and src.get("youtube_trazi"):
        cid = _resolve_channel(src["youtube_trazi"], key)
    if not key or not cid.startswith("UC"):
        return []
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/playlistItems",
                         params={"part": "snippet", "playlistId": "UU" + cid[2:], "maxResults": 15, "key": key}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.warning("YouTube %s nedostupan: %s", src["id"], e)
        return []
    out = []
    for it in r.json().get("items", []):
        sn = it.get("snippet", {})
        vid = (sn.get("resourceId") or {}).get("videoId")
        if not vid:
            continue
        out.append({
            "source": src["id"], "source_name": src["naziv"], "spektar": src.get("spektar", "centar"),
            "poverenje": float(src.get("poverenje", 0.5)), "rubrika_hint": src.get("rubrika", "politika"),
            "title": latin(sn.get("title", "")), "link": f"https://www.youtube.com/watch?v={vid}",
            "published": sn.get("publishedAt", now_utc().isoformat()).replace("Z", "+00:00"),
            "summary": latin(sn.get("description", ""))[:900], "categories": ["YouTube"], "tip": "izjava",
            "video_id": vid, "samo_video": bool(src.get("samo_video")), "thumb": ((sn.get("thumbnails") or {}).get("high") or {}).get("url", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"),
        })
    return out


def live_status(ids: list[str]) -> dict[str, dict]:
    """Za listu video ID-jeva vraća da li je snimak uživo (1 jedinica kvote za do 50 videa)."""
    import os

    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not key or not ids:
        return {}
    out = {}
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                         params={"part": "snippet,liveStreamingDetails", "id": ",".join(ids[:50]), "key": key}, timeout=15)
        for v in r.json().get("items", []):
            out[v["id"]] = {"live": v["snippet"].get("liveBroadcastContent", "none"),
                            "viewers": (v.get("liveStreamingDetails") or {}).get("concurrentViewers")}
    except Exception as e:
        log.info("YouTube status nije uspeo: %s", e)
    return out


def fetch_source(src: dict) -> list[dict]:
    import feedparser

    if src.get("youtube_kanal") or src.get("youtube_trazi") or src.get("youtube_handle"):
        return fetch_youtube(src)
    if src.get("sitemap"):
        return fetch_sitemap(src)

    try:
        r = _get(src["url"])
        r.raise_for_status()
        feed = feedparser.parse(r.content)
    except Exception as e:  # izvor privremeno ne radi: preskoči, ne ruši sistem
        log.warning("Izvor %s nedostupan: %s", src["id"], e)
        return []
    items = []
    for e in feed.entries[:60]:
        link = e.get("link")
        title = latin(strip_html(e.get("title", "")))
        if not link or not title:
            continue
        items.append(
            {
                "source": src["id"],
                "source_name": src["naziv"],
                "spektar": src.get("spektar", "centar"),
                "poverenje": float(src.get("poverenje", 0.7)),
                "rubrika_hint": src.get("rubrika", "politika"),  # dopunjava se ispod
                "title": title,
                "link": link,
                "published": _date(e) or now_utc().isoformat(),
                "summary": latin(strip_html(e.get("summary", "") or e.get("media_description", "")))[:700],
                "tip": src.get("tip", "vest"),
                "categories": [latin(t.get("term", "")) for t in e.get("tags", [])][:8],
            }
        )
    return items


def fetch_sitemap(src: dict, max_age_h: float = 24, limit: int = 8) -> list[dict]:
    """Izvor bez RSS-a: najnoviji tekstovi iz mape sajta, naslov i opis iz same strane (og: oznake)."""
    import html as _html
    from .util import age_hours

    try:
        r = _get(src["sitemap"])
        r.raise_for_status()
    except Exception as e:
        log.warning("Izvor %s nedostupan: %s", src["id"], e)
        return []
    entries = re.findall(r"<url>\s*<loc>([^<]+)</loc>\s*<lastmod>([^<]+)</lastmod>", r.text)
    out = []
    for link, mod in entries[:40]:
        if age_hours(mod) > max_age_h or not allowed(link):
            continue
        try:
            page = _get(link).text
        except Exception:
            continue

        def meta(prop: str) -> str:
            m = re.search(r'<meta[^>]+(?:property|name)=["\']' + prop + r'["\'][^>]+content=["\']([^"\']*)', page)
            return _html.unescape(m.group(1)).strip() if m else ""

        title = meta("og:title") or link.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        title = re.sub(r"\s*[|\-–]\s*BIRN.*$", "", title)
        pub = meta("article:published_time") or mod
        out.append({
            "source": src["id"], "source_name": src["naziv"], "spektar": src.get("spektar", "centar"),
            "poverenje": float(src.get("poverenje", 0.7)), "rubrika_hint": src.get("rubrika", "politika"),
            "title": latin(title), "link": link, "published": pub.replace("Z", "+00:00"),
            "summary": latin(meta("og:description"))[:700], "tip": src.get("tip", "vest"),
            "categories": [latin(meta("article:section"))] if meta("article:section") else [],
        })
        if len(out) >= limit:
            break
    return out


VIDEOS: list[dict] = []


def fetch_all(sources: list[dict], max_age_h: float) -> list[dict]:
    with ThreadPoolExecutor(max_workers=8) as ex:
        batches = list(ex.map(fetch_source, sources))
    items = [it for b in batches for it in b if age_hours(it["published"]) <= max_age_h and not it.get("samo_video")]
    # Video arhiva ne zavisi od svežine vesti: poslednjih 7 dana sa svakog kanala
    VIDEOS[:] = [it for b in batches for it in b if it.get("video_id") and age_hours(it["published"]) <= 168]
    for it in items:
        it["rubrika_hint"] = section_hint(it["link"], it.get("categories", []), it["rubrika_hint"])
    log.info("Preuzeto %d svežih stavki iz %d izvora", len(items), len(sources))
    return items


def load_fixtures(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    for it in items:
        it["rubrika_hint"] = section_hint(it["link"], it.get("categories", []), it["rubrika_hint"])
    return items


def fulltext(url: str, limit: int = 6000) -> str:
    """Pun tekst članka iz izvora, samo za čitanje i prepričavanje (ne objavljuje se)."""
    try:
        if not allowed(url):
            return ""
        import trafilatura

        r = _get(url, timeout=15)
        if r.status_code != 200:
            return ""
        text = trafilatura.extract(r.text, include_comments=False, include_tables=False) or ""
        return latin(text)[:limit]
    except Exception:
        return ""
