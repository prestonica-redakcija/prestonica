"""Gradi kompletan statički sajt u site/: naslovna, rubrike, članci, pretraga, RSS, sitemap."""
from __future__ import annotations

import json
import logging
import shutil
from collections import Counter
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

from . import rank, widgets
from .config import Config
from .cluster import COMMON as _COMMON
from .util import age_hours, fold, now_utc, broken_body, paragraphs, parse_iso, quotes, slugify, sr_date, stems

log = logging.getLogger("prestonica")
SR_MON_SHORT = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "avg", "sep", "okt", "nov", "dec"]


def _x_home(xposts: list[dict], n: int = 3) -> list[dict]:
    """Za naslovnu: najnovije objave, najviše jedna po nalogu, mediji tek ako nema drugih."""
    out, seen = [], set()
    for p in [x for x in xposts if x.get("grupa") != "mediji"] + [x for x in xposts if x.get("grupa") == "mediji"]:
        if p["x"].lower() in seen:
            continue
        seen.add(p["x"].lower())
        out.append(p)
        if len(out) >= n:
            break
    return out


def build(cfg: Config, articles: list[dict], state: dict, live_widgets: bool = True) -> Path:
    root = cfg.root
    out = root / "site"
    if out.exists():
        shutil.rmtree(out)
    (out / "vesti").mkdir(parents=True)
    tz = ZoneInfo(cfg.portal.get("vremenska_zona", "Europe/Belgrade"))
    now = datetime.now(tz)
    pins = state.get("pins") or {}

    visible = [a for a in articles if not a.get("hidden")]
    for a in visible:
        a["body"] = [] if broken_body(a.get("body")) else paragraphs(a.get("body"))   # tekst uvek kao pasusi, nikad slovo po slovo
        a["lead"] = quotes(a.get("lead", ""))
        a["headline"] = quotes(a.get("headline", ""))
        a["_score"] = rank.article_score(a, cfg, pins)
        a["_url"] = f"vesti/{a['slug']}.html"
        dt = parse_iso(a["published"]).astimezone(tz)
        a["_time"] = f"{dt.day}. {SR_MON_SHORT[dt.month - 1]} {dt:%H:%M}"
        a["_date_long"] = sr_date(dt) + f" u {dt:%H:%M}"
        a["_minutes"] = max(1, round(sum(len(p.split()) for p in [a["lead"]] + a["body"]) / 200))

    # stalne ličnosti (config/licnosti.yaml) + automatski profili za česta imena iz vesti (engine/licnosti.py)
    _known = {fold(p["ime"]) for p in cfg.licnosti}
    _auto = []
    for orig, e in (state.get("licnosti_auto") or {}).items():
        if e.get("ok") and fold(e.get("ime", orig)) not in _known and fold(orig) not in _known:
            _known.add(fold(e.get("ime", orig)))
            _auto.append({"ime": e.get("ime") or orig, "uloga": e.get("uloga", ""), "oblast": e.get("oblast", "drustvo"),
                          "kljucne": list(dict.fromkeys([fold(orig), fold(e.get("ime") or orig)])), "auto": True, "_foto": e.get("foto")})
    people, match_people = _person_matcher(list(cfg.licnosti) + _auto)
    for a in visible:
        a["_people"] = match_people(a)

    by_score = sorted(visible, key=lambda a: -a["_score"])
    by_date = sorted(visible, key=lambda a: a["published"], reverse=True)
    window = float(cfg.limiti.get("dana_na_naslovnoj", 2)) * 24
    recent = [a for a in by_score if age_hours(a["published"]) <= window] or by_score

    # --- naslovna ---
    used: set[str] = set()
    news_first = [a for a in recent if a["kind"] == "vest" or a["id"] in pins]
    max_h = float(cfg.limiti.get("naslovna_max_sati", 8))
    svez = lambda a: age_hours(a.get("prepisano") or a["published"])  # noqa: E731
    hot = [a for a in news_first if svez(a) <= max_h or a["id"] in pins] or news_first
    hero = hot[0] if hot else (recent[0] if recent else None)
    if hero:
        used.add(hero["id"])
    top = []
    for a in [x for x in hot if x["kind"] == "vest"] + recent:
        if a["id"] not in used and a["kind"] == "vest" and len(top) < 4:
            top.append(a)
            used.add(a["id"])
    sections = []
    for key, meta in cfg.rubrike.items():
        items = [a for a in recent if a["section"] == key and a["id"] not in used and a["kind"] == "vest"][: int(meta.get("na_naslovnoj", 4))]
        if items:
            used.update(a["id"] for a in items)
            sections.append({"key": key, "meta": meta, "lead": items[0], "rest": items[1:]})
    opinion = [a for a in by_date if a["kind"] in ("analiza", "autorski")][:3]
    tag_counter: Counter = Counter()
    for a in visible:
        if age_hours(a["published"]) < 36:
            for t in a.get("tags", []):
                tag_counter[t] += 1 + a["_score"]
    teme = [t for t, _ in tag_counter.most_common(12)]

    w = {"kurs": widgets.kurs() if live_widgets else [], "vreme": widgets.vreme() if live_widgets else None}
    videos = [dict(v) for v in (state.get("videos") or []) if not v.get("skip") and v.get("live") != "upcoming"]
    if any("naslov" in v for v in videos):
        videos = [v for v in videos if "naslov" in v]
    today_d = now.date()
    for v in videos:
        dt = parse_iso(v["published"]).astimezone(tz)
        v["_time"] = f"{dt.day}. {SR_MON_SHORT[dt.month - 1]} {dt:%H:%M}"
        dd = (today_d - dt.date()).days
        v["_dan"] = "Danas" if dd == 0 else "Juče" if dd == 1 else sr_date(dt)
        v["_h"] = v.get("naslov") or v["title"]
        v["_st"] = stems(" ".join([v["_h"], v["title"], " ".join(v.get("oznake", []))]))
        v["_article"] = None
        v["_url"] = "video/" + (v.get("slug") or (slugify(v["_h"], 60) + "-" + v["id"].lower().replace("_", "-"))) + ".html"
    live_now = [v for v in videos if v.get("live") == "live"]

    # Povezivanje: svaka vest dobija snimak na istu temu (i snimak dobija link na vest)
    by_id = {v["id"]: v for v in videos}
    best_for_video: dict[str, float] = {}
    for a in visible:
        own = next((src["url"].split("v=")[1][:11] for src in a["sources"] if "youtube.com/watch?v=" in src.get("url", "")), None)
        vid_id = a.get("video_id") or own
        a["_video"] = None
        if vid_id:
            a["_video"] = by_id.get(vid_id) or {"id": vid_id, "_h": a["headline"], "live": "none",
                                                "kanal": next((x["name"] for x in a["sources"] if vid_id in x.get("url", "")), "")}
        score = 9.0 if a.get("video_id") else 5.0
        v = a["_video"]
        if v and v.get("id") in by_id and score > best_for_video.get(v["id"], 0) and a["kind"] == "vest":
            best_for_video[v["id"]] = score
            by_id[v["id"]]["_article"] = a["_url"]

    # --- X objave (samo one koje je Claude ocenio kao javno značajne) ---
    xgrupe = (cfg.portal.get("x") or {}).get("grupe", {})
    from .xposts import excluded as _x_excluded
    _xskip = _x_excluded()
    _xprof = (state.get("x") or {}).get("profil", {})
    xposts = [dict(p) for p in (state.get("x") or {}).get("posts", []) if p.get("prikazi") and p["x"].lower() not in _xskip]
    xposts.sort(key=lambda p: p["published"], reverse=True)
    for p in xposts:
        p["img"] = (_xprof.get(p["x"].lower()) or {}).get("img", "")
        p["_time"] = parse_iso(p["published"]).astimezone(tz).strftime("%H:%M")
        p["_grupa"] = xgrupe.get(p.get("grupa", ""), "")
    x_by_art: dict[str, list] = {}
    for p in xposts:
        if p.get("vest"):
            x_by_art.setdefault(p["vest"], []).append(p)
    for a in visible:
        a["_x"] = x_by_art.get(a["id"], [])[:3]

    env = Environment(loader=FileSystemLoader(root / "templates"), autoescape=select_autoescape(["html", "xml"]))
    env.filters["img"] = lambda a, root_rel, size="m": _img_url(a, root_rel, size)
    env.filters["xtext"] = _xtext
    css = (root / "static" / "style.css").read_text(encoding="utf-8") + _section_css(cfg)
    js = (root / "static" / "app.js").read_text(encoding="utf-8")
    base_ctx = {
        "portal": cfg.portal, "rubrike": cfg.rubrike, "css": css, "js": js, "now": now,
        "today": sr_date(now, True), "w": w, "updated": now.strftime("%H:%M"),
        "newsletter": (cfg.portal.get("newsletter") or {}).get("akcija_url", ""),
        "ticker": [a for a in by_date if a["kind"] == "vest"][:6], "domain": cfg.portal.get("domen", ""),
        "has_people": bool(people), "live_now": live_now, "has_videos": bool(videos), "has_x": bool(xposts),
    }

    # --- ankete čitalaca ---
    ac = cfg.portal.get("ankete") or {}
    api = str(ac.get("api_url", "")).rstrip("/") if ac.get("ukljuceno") else ""
    polls = sorted((dict(p) for p in state.get("ankete", [])), key=lambda p: p["start"], reverse=True)
    for p in polls:
        p["_otvorena"] = parse_iso(p["kraj"]) > now_utc()
        p["_dan"] = sr_date(parse_iso(p["start"]).astimezone(tz), False)
        if p.get("rezultat"):
            tot = sum(p["rezultat"]) or 1
            p["_pct"] = [round(100 * n / tot) for n in p["rezultat"]]
            p["_ukupno"] = sum(p["rezultat"])
    poll_now = next((p for p in polls if p["_otvorena"]), None) if api else None
    base_ctx["anketa"] = poll_now
    base_ctx["ankete_api"] = api
    base_ctx["has_polls"] = bool(api and polls)

    def render(tpl: str, path: str, **ctx):
        depth = path.count("/")
        html = env.get_template(tpl).render(**base_ctx, root="../" * depth, page_path=path, **ctx)
        (out / path).write_text(html, encoding="utf-8")

    render("index.html", "index.html", hero=hero, top=top, sections=sections, opinion=opinion,
           latest=by_date[:12], teme=teme, active="naslovna", title=None, videos=videos[:4], xnaslovna=_x_home(xposts))
    if videos:
        days: list[tuple[str, list]] = []
        for v in videos[1:]:
            if not days or days[-1][0] != v["_dan"]:
                days.append((v["_dan"], []))
            days[-1][1].append(v)
        render("video.html", "video.html", lead=videos[0], days=days, active="video", title="Video",
               vrubrike=[k for k in cfg.rubrike if any(v.get("rubrika") == k for v in videos)])

    (out / "video").mkdir(exist_ok=True)
    art_by_url = {a["_url"]: a for a in visible}
    for v in videos:
        more = [x for x in videos if x is not v and x.get("rubrika") == v.get("rubrika")][:6]
        more += [x for x in videos if x is not v and x not in more][: 6 - len(more)]
        render("video_page.html", v["_url"], v=v, more=more, art=art_by_url.get(v["_article"]), active="video", title=v["_h"])

    for key, meta in cfg.rubrike.items():
        items = [a for a in by_date if a["section"] == key][:48]
        lead = max((a for a in items if age_hours(a["published"]) < 30), key=lambda a: a["_score"], default=items[0] if items else None)
        render("section.html", f"{key}.html", key=key, meta=meta, lead=lead, items=[a for a in items if a is not lead],
               active=key, title=meta["naziv"], latest=by_date[:8])

    an = [a for a in by_date if a["kind"] in ("analiza", "autorski")][:48]
    render("section.html", "analize.html", key="analiza", meta={"naziv": "Analize i stavovi", "boja": "#b98a1e"},
           lead=an[0] if an else None, items=an[1:], active="analize", title="Analize", latest=by_date[:8])
    render("list.html", "najnovije.html", items=by_date[:120], active="najnovije", title="Najnovije vesti")
    if xposts:
        art_by_id = {a["id"]: a for a in visible}
        for p in xposts:
            p["_art"] = art_by_id.get(p.get("vest", ""))
        render("x.html", "mreze.html", posts=xposts[:150], active="mreze", title="Na mrežama",
               xgrupe=[(k, v) for k, v in xgrupe.items() if any(p.get("grupa") == k for p in xposts)])
    if api and polls:
        render("ankete.html", "ankete.html", polls=polls[:120], active="ankete", title="Ankete čitalaca")
    (out / "ankete.json").write_text(json.dumps([{"id": p["id"], "opcije": len(p["opcije"]), "kraj": p["kraj"]} for p in polls[:60]],
                                                separators=(",", ":")), encoding="utf-8")
    render("search.html", "pretraga.html", active="pretraga", title="Pretraga", teme=teme)
    render("page.html", "o-nama.html", page="o-nama", active="", title="O nama")
    render("page.html", "impresum.html", page="impresum", active="", title="Impresum", imp=cfg.portal.get("impresum", {}))
    render("page.html", "404.html", page="404", active="", title="Stranica nije pronađena")

    # --- ličnosti ---
    if people:
        cards = []
        for p in people:
            items = [a for a in by_date if any(x["slug"] == p["slug"] for x in a["_people"])]
            recent_n = sum(1 for a in items if age_hours(a["published"]) < 24 * 7)
            p_img = p.get("_foto") if p.get("auto") else (state.get("licnosti_foto") or {}).get(p["ime"])
            if p.get("foto"):   # fotografija koju redakcija sama upiše u config/licnosti.yaml (adresa slike na koju imamo pravo)
                p_img = {"src": p["foto"], "credit": p.get("foto_autor", "")}
            if not (p_img and p_img.get("src")) and p.get("x"):
                xi = ((state.get("x") or {}).get("profil") or {}).get(str(p["x"]).lstrip("@").lower(), {}).get("img")
                p_img = {"src": xi, "credit": "Slika profila sa mreže X"} if xi else None
            if not (p_img and p_img.get("src")):
                p_img = None
            p["_ini"] = "".join(w[0] for w in p["ime"].replace("-", " ").split() if w[:1].isalpha())[:1] + (p["ime"].split()[-1][:1] if len(p["ime"].split()) > 1 else "")
            if p.get("auto") and not items:
                continue
            cards.append({**p, "n": len(items), "n7": recent_n, "last": items[0] if items else None, "img": p_img})
            px = [x for x in xposts if p.get("x") and x["x"].lower() == str(p["x"]).lstrip("@").lower()][:6]
            render("person.html", f"{p['slug']}.html", p={**p, "img": p_img}, items=items[:80], xposts=px, active="licnosti", title=p["ime"])
        cards.sort(key=lambda c: (bool(c.get("auto")), -c["n7"], -c["n"]))
        lim = int((cfg.portal.get("licnosti_auto") or {}).get("najvise_na_stranici", 120))
        cards = [c for c in cards if not c.get("auto")] + [c for c in cards if c.get("auto")][:lim]
        cards.sort(key=lambda c: (-c["n7"], -c["n"]))
        obl = {"politika": "Politika", "svet": "Svet", "ekonomija": "Ekonomija", "drustvo": "Društvo", "hronika": "Hronika", "sport": "Sport", "kultura": "Kultura"}
        for c in cards:
            c["_obl"] = c.get("oblast") or "politika"
        render("persons.html", "licnosti.html", cards=cards, active="licnosti", title="Ličnosti",
               oblasti=[(k, v) for k, v in obl.items() if any(c["_obl"] == k for c in cards)])

    # kartice za deljenje (og:image 1200 x 630 na našem domenu): pregled linka na X-u i drugim mrežama nikad sivo
    try:
        from . import drustvene
        drustvene.build_cards(cfg, visible, out)
    except Exception as e:
        log.warning("Kartice za deljenje: %s", e)

    for a in visible:
        tags = set(fold(" ".join(a.get("tags", []))).split())
        rel = sorted((x for x in visible if x is not a),
                     key=lambda x: (-len(tags & set(fold(" ".join(x.get("tags", []))).split())), x["section"] != a["section"],
                                    -parse_iso(x["published"]).timestamp()))[:4]
        render("article.html", a["_url"], a=a, related=rel, active=a["section"], title=a["headline"], vid=a.get("_video"))

    # --- slike iz arhive (dve veličine) ---
    _copy_photos(root, out, visible)

    # --- indeks za pretragu ---
    idx = [{
        "u": a["_url"], "t": a["headline"], "l": a["lead"], "b": " ".join(a["body"])[:900], "s": a["section"],
        "k": a["kind"], "g": a.get("tags", []), "p": sorted(set(a.get("people", [])) | {x["ime"] for x in a.get("_people", [])}), "src": sorted({s["name"] for s in a["sources"]}),
        "d": a["published"], "i": _img_url(a, "", "s"),
    } for a in by_date] + [{
        "u": v["_url"], "t": v["_h"], "l": v.get("opis", ""), "b": " ".join(v.get("tekst", []))[:600], "s": v.get("rubrika", "politika"),
        "k": "video", "g": v.get("oznake", []), "p": [], "src": [v["kanal"]], "d": v["published"],
        "i": f"https://i.ytimg.com/vi/{v['id']}/mqdefault.jpg",
    } for v in videos if v.get("naslov")]
    (out / "search.json").write_text(json.dumps(idx, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    _feeds(cfg, out, by_date, [v["_url"] for v in videos])
    if cfg.portal.get("domen"):
        (out / "CNAME").write_text(cfg.portal["domen"] + "\n")
    (out / ".nojekyll").write_text("")
    log.info("Sajt izgrađen: %d članaka", len(visible))
    return out


def _person_matcher(licnosti: list[dict]):
    """Prepoznaje osobu po prezimenu u svim padežima (Vučić, Vučića, Vučiću), ali ne po dužim prezimenima (Vučićević)."""
    from .util import slugify
    people = []
    for p in licnosti:
        keys = [fold(k) for k in p.get("kljucne", [])] or [fold(p["ime"].split()[-1])]
        people.append({**p, "slug": "licnost-" + slugify(p["ime"]), "_keys": keys})

    def match(a: dict) -> list[dict]:
        words = fold(" ".join([a["headline"], a["lead"], " ".join(a["body"]), " ".join(a.get("people", []))])).split()
        text = " " + " ".join(words) + " "
        hits = []
        for p in people:
            for k in p["_keys"]:
                if " " in k:
                    ok = f" {k}" in text
                else:
                    ok = any(w.startswith(k) and len(w) <= len(k) + 3 for w in words)
                if ok:
                    hits.append(p)
                    break
        return hits

    return people, match


def _xtext(text: str):
    """Tekst objave sa X-a: linkovi, @nalozi i #teme kao linkovi; sve ostalo escapeovano."""
    import html as _h
    import re
    from markupsafe import Markup
    out = []
    for tok in re.split(r"(https?://\S+|@\w{1,15}|#\w+)", text or ""):
        if not tok:
            continue
        if tok.startswith("http"):
            out.append(f'<a href="{_h.escape(tok)}" target="_blank" rel="noopener nofollow" class="xc-link">link</a>')
        elif tok.startswith("@") and len(tok) > 1:
            out.append(f'<a href="https://x.com/{_h.escape(tok[1:])}" target="_blank" rel="noopener nofollow">{_h.escape(tok)}</a>')
        elif tok.startswith("#") and len(tok) > 1:
            out.append(f'<a href="https://x.com/hashtag/{_h.escape(tok[1:])}" target="_blank" rel="noopener nofollow">{_h.escape(tok)}</a>')
        else:
            out.append(_h.escape(tok).replace("\n", "<br>"))
    return Markup("".join(out))


def _img_url(a: dict, root_rel: str, size: str = "m") -> str:
    img = a.get("image") or {}
    src = img.get("src", "")
    if img.get("local"):
        name = src.split("/", 1)[1]
        return f"{root_rel}foto/{'' if size == 'l' else size + '/'}{name}"
    return src


def _copy_photos(root: Path, out: Path, arts: list[dict]) -> None:
    names = {a["image"]["src"].split("/", 1)[1] for a in arts if (a.get("image") or {}).get("local")}
    for sub, width in (("", 1400), ("m/", 720), ("s/", 240)):
        (out / "foto" / sub).mkdir(parents=True, exist_ok=True)
        for n in names:
            srcp = root / "uploads" / "foto" / n
            if not srcp.exists():
                continue
            im = Image.open(srcp).convert("RGB")
            im.thumbnail((width, width))
            im.save(out / "foto" / sub / n, quality=78 if width > 300 else 72, optimize=True, progressive=True)


def _section_css(cfg: Config) -> str:
    light = "".join(f".r-{k}{{--c:{v['boja']}}}" for k, v in cfg.rubrike.items())
    dark = "".join(f".r-{k}{{--c:{v.get('boja_tamna', v['boja'])}}}" for k, v in cfg.rubrike.items())
    return (f"\n{light}.r-analiza,.r-autorski{{--c:var(--gold)}}"
            f"\n@media (prefers-color-scheme: dark){{:root:not([data-theme=\"light\"]) {dark.replace('.r-', ':root:not([data-theme=\"light\"]) .r-')}}}"
            f"\n{dark.replace('.r-', ':root[data-theme=\"dark\"] .r-')}")


def _feeds(cfg: Config, out: Path, arts: list[dict], extra: list[str] | None = None) -> None:
    base = f"https://{cfg.portal.get('domen', 'example.org')}/"
    items = []
    for a in arts[:50]:
        items.append(
            f"<item><title>{escape(a['headline'])}</title><link>{base}{a['_url']}</link><guid>{base}{a['_url']}</guid>"
            f"<pubDate>{format_datetime(parse_iso(a['published']))}</pubDate><category>{escape(cfg.rubrike.get(a['section'], {}).get('naziv', ''))}</category>"
            f"<description>{escape(a['lead'])}</description></item>"
        )
    (out / "feed.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{escape(cfg.portal["ime"])}</title>'
        f"<link>{base}</link><description>{escape(cfg.portal.get('slogan', ''))}</description><language>sr</language>{''.join(items)}</channel></rss>",
        encoding="utf-8",
    )
    urls = ["", "najnovije.html", "analize.html", "licnosti.html", "video.html"] + [f"{k}.html" for k in cfg.rubrike] + [a["_url"] for a in arts] + (extra or [])
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{base}{u}</loc></url>" for u in urls) + "</urlset>", encoding="utf-8")
    news = [a for a in arts if age_hours(a["published"]) < 48][:1000]
    (out / "news-sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
        + "".join(f"<url><loc>{base}{a['_url']}</loc><news:news><news:publication><news:name>{escape(cfg.portal['ime'])}</news:name>"
                  f"<news:language>sr</news:language></news:publication><news:publication_date>{a['published']}</news:publication_date>"
                  f"<news:title>{escape(a['headline'])}</news:title></news:news></url>" for a in news)
        + "</urlset>", encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nDisallow: /statistika.html\nSitemap: {base}sitemap.xml\nSitemap: {base}news-sitemap.xml\n")
    stat = Path(__file__).resolve().parent.parent / "static" / "statistika.html"
    if stat.exists():
        (out / "statistika.html").write_text(stat.read_text(encoding="utf-8"), encoding="utf-8")
