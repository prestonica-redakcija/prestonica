"""Jedno osvežavanje portala. Pokreće se na 15 minuta (GitHub Actions).

  python -m engine.run                      pravi rad (traži API ključeve)
  python -m engine.run --mock               bez Claude API-ja (probni tekstovi)
  python -m engine.run --fixtures f.json    bez interneta, iz probnih podataka
  python -m engine.run --build-only         samo ponovo izgradi sajt
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import shutil
import time
from datetime import timedelta
from zoneinfo import ZoneInfo

from . import build, cluster, config, fetch, rank, store
from .images import Images
from .telegram import Telegram, handle_commands
from .util import age_hours, broken_body, fold, now_utc, paragraphs, parse_iso, quotes, sentence_case_caps, slugify
from .writer import get_writer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("prestonica")


def _unique_slug(base: str, articles: list[dict]) -> str:
    taken = {a["slug"] for a in articles}
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def make_article(out: dict, kind: str, cfg, articles: list[dict], sources: list[dict], published: str) -> dict:
    sec = out.get("rubrika") if out.get("rubrika") in cfg.rubrike else "politika"
    headline = re.sub(r"\s+", " ", out["naslov"]).strip().rstrip(".")
    headline = headline[:1].upper() + headline[1:]
    slug = _unique_slug(slugify(headline), articles)
    tz = ZoneInfo(cfg.portal.get("vremenska_zona", "Europe/Belgrade"))
    stamp = parse_iso(published).astimezone(tz).strftime("%Y%m%d%H%M")
    return {
        "id": f"{stamp}-{hashlib.md5(slug.encode()).hexdigest()[:6]}",
        "slug": slug,
        "kind": kind,
        "section": sec,
        "headline": headline,
        "lead": quotes(out.get("uvod", "").strip()),
        "body": paragraphs(out.get("pasusi", [])),
        "tags": [t.strip() for t in out.get("oznake", []) if t.strip()][:6],
        "people": out.get("osobe", []) or [],
        "places": out.get("mesta", []) or [],
        "importance": max(1, min(10, int(out.get("vaznost", 5)))),
        "published": published,
        "updated": published,
        "sources": sources,
        "source_titles": [s.get("title", "") for s in sources],
        "author": {"vest": "Redakcija Prestonice", "analiza": "Analitički tim Prestonice", "autorski": "Uredništvo"}[kind],
        "hidden": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--fixtures")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--max", type=int, help="najviše novih tekstova u ovom osvežavanju")
    args = ap.parse_args()

    t0 = time.time()
    cfg = config.load()
    state = store.load_state()
    articles = store.load_articles()
    live = not args.mock and bool(cfg.secrets.get("anthropic")) and not args.fixtures
    new_count = 0

    if not args.build_only:
        writer = get_writer(cfg, args.mock or bool(args.fixtures))
        images = Images(cfg, live=live)
        fc = state.setdefault("foto_cache", {})
        for k in [k for k, v in fc.items() if time.time() - v.get("t", 0) > 7 * 86400]:
            del fc[k]
        images.cache = fc
        # potvrđene fotografije ličnosti: samo one smeju uz vest kao "fotografija osobe"
        _lf = state.get("licnosti_foto") or {}
        for p in cfg.licnosti:
            ph = {"src": p["foto"], "credit": p.get("foto_autor", "")} if p.get("foto") else _lf.get(p["ime"])
            if ph and ph.get("src") and (p.get("foto") or ph.get("wd")):
                for k in [p["ime"]] + list(p.get("kljucne", []) or []):
                    images.known[fold(k)] = ph
        for orig, e in (state.get("licnosti_auto") or {}).items():
            if e.get("ok") and e.get("wd") and (e.get("foto") or {}).get("src"):
                images.known[fold(orig)] = e["foto"]
                images.known[fold(e.get("ime") or orig)] = e["foto"]
        images.note_used([a for a in articles if age_hours(a["published"]) < 72])
        # popravka fotografija u vestima iz poslednja 3 dana: strane stock fotografije koje su uklonjene iz arhive
        # i fotografije pogrešne osobe istog prezimena dobijaju novu fotografiju (isti tekst, isti link)
        _lib = {"foto/" + f["fajl"] for f in images.lib}
        _fixed = 0
        for a in articles:
            if _fixed >= 60 or a.get("hidden") or age_hours(a["published"]) > 72:
                continue
            img = a.get("image") or {}
            if (img.get("local") and img.get("src") not in _lib) or images.wrong_person(a):
                a["image"] = images.choose(a, {})
                store.save_article(a)
                _fixed += 1
        if _fixed:
            log.info("Fotografije zamenjene u %d vesti", _fixed)
        now = now_utc()

        # 0. istekle oznake i teme
        state["pins"] = {k: v for k, v in (state.get("pins") or {}).items() if parse_iso(v) > now}
        state["fokus"] = [f for f in (state.get("fokus") or []) if parse_iso(f["do"]) > now]
        fokus = [f["tema"] for f in state["fokus"]]

        # 1. Telegram komande i autorski tekstovi
        tg = Telegram(cfg)
        posts = handle_commands(tg, tg.poll(state), articles, state, store.save_article)

        # 2. Inbox (autorski tekstovi kao .md fajlovi)
        inbox = cfg.root / "inbox"
        for p in sorted(inbox.glob("*.md")):
            if p.name.lower() == "readme.md":
                continue
            posts.append({"text": p.read_text(encoding="utf-8"), "photo": None, "file": p})
        for post in posts:
            out = writer.owner(post["text"])
            if not out:
                continue
            a = make_article(out, "autorski", cfg, articles, [], now.isoformat())
            a["importance"] = max(a["importance"], 8)
            a["image"] = ({"src": "foto/" + post["photo"], "credit": "Prestonica", "ilustracija": False, "local": True}
                          if post.get("photo") else images.choose(a, out))
            store.save_article(a)
            articles.append(a)
            new_count += 1
            if post.get("file"):
                done = inbox / "objavljeno"
                done.mkdir(exist_ok=True)
                shutil.move(str(post["file"]), done / post["file"].name)

        # 3. Izvori
        items = fetch.load_fixtures(args.fixtures) if args.fixtures else fetch.fetch_all(cfg.izvori, cfg.limiti["max_starost_izvora_sati"])
        # 3b. Video arhiva sa YouTube kanala (za plejere na portalu) + oznaka "uživo"
        vids = {v["id"]: v for v in state.get("videos", [])}
        for it in items + fetch.VIDEOS:
            if it.get("video_id"):
                vids.setdefault(it["video_id"], {"id": it["video_id"], "title": it["title"], "kanal": it["source_name"],
                                                 "published": it["published"], "thumb": it.get("thumb", ""), "live": "none",
                                                 "desc": it.get("summary", "")[:700], "spektar": it.get("spektar", "centar")})
        per_kanal: dict[str, int] = {}
        vlist = []
        for v in sorted(vids.values(), key=lambda v: v["published"], reverse=True):
            if per_kanal.get(v["kanal"], 0) < 8 and age_hours(v["published"]) <= 168 * 2:
                per_kanal[v["kanal"]] = per_kanal.get(v["kanal"], 0) + 1
                vlist.append(v)
        vlist = vlist[:140]
        if live and vlist:
            st = fetch.live_status([v["id"] for v in vlist[:50]])
            for v in vlist:
                if v["id"] in st:
                    v["live"] = st[v["id"]]["live"]
                    v["viewers"] = st[v["id"]]["viewers"]
        # Kratki opisi (naslov, rubrika, 2-3 rečenice) za nove snimke, u jednom pozivu
        todo_v = [v for v in vlist if "tekst" not in v and not v.get("skip")][: int(cfg.portal.get("video_opisa_po_osvezavanju", 12))]
        if todo_v and hasattr(writer, "videos"):
            got = {d.get("id"): d for d in writer.videos(todo_v)}
            for v in todo_v:
                d = got.get(v["id"])
                if not d:
                    continue
                if d.get("preskoci"):
                    v["skip"] = True
                    continue
                if "naslov" not in v:
                    v["naslov"] = sentence_case_caps(d.get("naslov", v["title"]))[:100]
                tekst = d.get("tekst") or []
                if isinstance(tekst, str):
                    tekst = [x.strip() for x in tekst.split("\n") if x.strip()]
                v.update({"opis": v.get("opis") or d.get("opis", ""), "tekst": tekst[:5],
                          "rubrika": d.get("rubrika") if d.get("rubrika") in cfg.rubrike else "politika", "oznake": d.get("oznake", [])[:6]})
            log.info("Opisano %d snimaka", sum(1 for v in todo_v if "naslov" in v))
        state["_novi_snimci"] = bool(todo_v)
        for v in vlist:
            if "naslov" in v and not v.get("slug"):
                v["slug"] = (slugify(v["naslov"], 60) + "-" + v["id"].lower().replace("_", "-")).strip("-")
        state["videos"] = vlist

        seen = state.setdefault("seen", {})
        cutoff = (now - timedelta(days=5)).isoformat()
        for k in [k for k, v in seen.items() if v < cutoff]:
            del seen[k]
        fresh = [it for it in items if it["link"] not in seen]

        # 4. Grupisanje i rangiranje
        clusters = cluster.cluster_items(fresh)
        todo, izjave = [], []
        razvoj: dict[str, tuple[dict, list]] = {}  # postojeće vesti koje su dobile nove izvore
        for c in clusters:
            score = rank.cluster_score(c, cfg, fokus)
            existing = cluster.match_existing(c, articles)
            if existing:  # već imamo vest: samo dodaj nove izvore
                have = {s["url"] for s in existing["sources"]}
                added = [it for it in c["items"] if it["link"] not in have]
                for it in added:
                    existing["sources"].append({"name": it["source_name"], "url": it["link"], "title": it["title"], "spektar": it["spektar"]})
                    existing["source_titles"].append(it["title"])
                if added:
                    existing["novo_od_pisanja"] = existing.get("novo_od_pisanja", 0) + len(added)
                    if existing.get("kind") == "vest" and not existing.get("hidden"):
                        razvoj.setdefault(existing["id"], (existing, []))[1].extend(added)
                    existing["updated"] = now.isoformat()
                    existing["boost"] = round(min(1.6, existing.get("boost", 1.0) + 0.08 * len(added)), 2)
                    store.save_article(existing)
                for it in c["items"]:
                    seen[it["link"]] = now.isoformat()
                continue
            izjava = any(it.get("tip") == "izjava" for it in c["items"])
            if score >= float(cfg.limiti["min_skor_za_objavu"]):
                todo.append((score, c))
            elif izjava and score >= float(cfg.limiti.get("min_skor_izjava", 0.8)):
                izjave.append((score, c))  # YouTube/stranke: posebna kvota, da se čuje i šta kažu

        todo.sort(key=lambda x: -x[0])
        izjave.sort(key=lambda x: -x[0])
        todo = todo[: max(0, int(cfg.limiti["max_novih_po_osvezavanju"]) - int(cfg.limiti.get("izjava_po_osvezavanju", 2)))] + izjave[: int(cfg.limiti.get("izjava_po_osvezavanju", 2))] + todo[max(0, int(cfg.limiti["max_novih_po_osvezavanju"]) - int(cfg.limiti.get("izjava_po_osvezavanju", 2))):]

        today = [a for a in articles if age_hours(a["published"]) < 24 and not a.get("hidden")]
        last_h = [a for a in today if age_hours(a["published"]) < 1]
        budget = min(args.max or int(cfg.limiti["max_novih_po_osvezavanju"]),
                     int(cfg.limiti["max_novih_dnevno"]) - len(today),
                     int(cfg.limiti.get("max_novih_po_satu", 999)) - len(last_h))
        log.info("Klastera: %d, kandidata: %d, budžet: %d", len(clusters), len(todo), budget)

        # 5. Pisanje
        errors = 0
        glavni = int(cfg.limiti.get("glavni_model_po_osvezavanju", 999))
        brzi = cfg.portal.get("model_brze_vesti") or None
        for n, (score, c) in enumerate(todo[: max(0, budget)]):
            model = None if (n < glavni or not brzi) else brzi  # najjače priče: glavni model, ostale: brzi
            texts = {}
            if live:
                for it in sorted(c["items"], key=lambda i: -i["poverenje"])[:3]:
                    if it.get("tip") != "izjava":  # YouTube i stranke: koristi se opis, ne stranica
                        texts[it["link"]] = fetch.fulltext(it["link"])
            out = writer.news(c, texts, model)
            if out is None:  # greška API-ja: priča ostaje za sledeće osvežavanje
                errors += 1
                if errors >= 3 and new_count == 0:
                    log.error("Claude API ne odgovara ispravno, prekidam ovo osvežavanje")
                    break
                continue
            for it in c["items"]:
                seen[it["link"]] = now.isoformat()
            if out.get("preskoci"):
                continue
            srcs, names = [], set()
            for it in sorted(c["items"], key=lambda i: -i["poverenje"]):
                srcs.append({"name": it["source_name"], "url": it["link"], "title": it["title"], "spektar": it["spektar"]})
                names.add(it["source_name"])
            pub = now.isoformat() if live else c["latest"]
            a = make_article(out, "vest", cfg, articles, srcs, pub)
            a["score_at_pub"] = score
            a["image"] = images.choose(a, out)
            store.save_article(a)
            articles.append(a)
            new_count += 1

        # 5b. Priča se razvija: postojeća vest sa više novih izvora se dopunjuje (isti link, oznaka "ažurirano")
        if live:
            min_novih = int(cfg.limiti.get("azuriranje_min_novih_izvora", 3))
            kandidati = [(e, add) for e, add in razvoj.values()
                         if e.get("novo_od_pisanja", 0) >= min_novih and 1.0 <= age_hours(e["published"]) <= 24]
            kandidati.sort(key=lambda x: -x[0].get("novo_od_pisanja", 0))
            for e, add in kandidati[: int(cfg.limiti.get("azuriranja_po_osvezavanju", 3))]:
                prev = {"source_name": "Prestonica (dosadašnja verzija vesti)", "spektar": "centar", "poverenje": 1.0, "tip": "vest",
                        "title": e["headline"], "link": e.get("_url", e["slug"]), "published": e["published"],
                        "summary": e["lead"] + "\n" + "\n".join(e["body"])}
                texts = {}
                for it in sorted(add, key=lambda i: -i["poverenje"])[:2]:
                    if it.get("tip") != "izjava":
                        texts[it["link"]] = fetch.fulltext(it["link"])
                pc = {"items": [prev] + add[:5], "sources": e["sources"]}
                out = writer.news(pc, texts)
                if not out or out.get("preskoci") or not out.get("pasusi"):
                    continue
                e["headline"] = (out.get("naslov") or e["headline"]).strip()
                e["headline"] = e["headline"][:1].upper() + e["headline"][1:]
                e["lead"] = out.get("uvod", e["lead"]).strip()
                e["body"] = paragraphs(out["pasusi"])
                e["tags"] = ([t.strip() for t in out.get("oznake", []) if t.strip()] or e.get("tags", []))[:6]
                e["updated"] = now.isoformat()
                e["prepisano"] = now.isoformat()
                e["verzija"] = e.get("verzija", 1) + 1
                e["novo_od_pisanja"] = 0
                store.save_article(e)
                log.info("Ažurirana vest (priča se razvija): %s", e["slug"])

        # 5c. Popravka: vesti čiji je tekst sačuvan slovo po slovo pišu se ponovo iz istih izvora (isti link)
        if live:
            for e in [x for x in articles if broken_body(x.get("body")) and not x.get("hidden")][:4]:
                items, texts = [], {}
                for sct in e.get("sources", [])[:6]:
                    items.append({"source_name": sct.get("name", ""), "spektar": sct.get("spektar", "centar"), "poverenje": 0.8,
                                  "tip": "vest", "title": sct.get("title", ""), "link": sct.get("url", ""),
                                  "published": e["published"], "summary": e.get("lead", "")})
                for it in items[:3]:
                    if it["link"]:
                        texts[it["link"]] = fetch.fulltext(it["link"])
                out = writer.news({"items": items, "sources": e.get("sources", [])}, texts) if items else None
                body = paragraphs((out or {}).get("pasusi", []))
                if not body or broken_body(body):
                    log.warning("Popravka teksta nije uspela: %s", e["slug"])
                    continue
                e["lead"] = quotes((out.get("uvod") or e["lead"]).strip())
                e["body"] = body
                e["updated"] = now.isoformat()
                e["popravljeno"] = now.isoformat()
                store.save_article(e)
                log.info("Popravljen tekst vesti: %s", e["slug"])

        # 6. Analize (desni centar), samo u pravom radu
        an = cfg.portal.get("analize", {})
        local_hour = now.astimezone(ZoneInfo(cfg.portal.get("vremenska_zona", "Europe/Belgrade"))).hour
        done_today = [a for a in articles if a["kind"] == "analiza" and age_hours(a["published"]) < 24]
        if live and local_hour >= int(an.get("od_sata", 8)) and len(done_today) < int(an.get("dnevno", 1)):
            analyzed = {a.get("analysis_of") for a in articles}
            cands = [a for a in articles if a["kind"] == "vest" and not a.get("hidden") and a["id"] not in analyzed
                     and a["section"] in ("politika", "ekonomija") and age_hours(a["published"]) < 18]
            cands.sort(key=lambda a: -rank.article_score(a, cfg, {}))
            if cands:
                base = cands[0]
                rel = [x for x in cands[1:] if set(fold(" ".join(x["tags"])).split()) & set(fold(" ".join(base["tags"])).split())]
                out = writer.analysis(base, rel)
                if out and not out.get("preskoci"):
                    a = make_article(out, "analiza", cfg, articles, base["sources"][:4], now.isoformat())
                    a["analysis_of"] = base["id"]
                    a["image"] = images.choose(a, out)
                    store.save_article(a)
                    articles.append(a)
                    new_count += 1

        # 6b. Mediji pod lupom: jedna priča dnevno koju su pokrili mediji sa obe strane
        md_today = [a for a in articles if a.get("media_of") and age_hours(a["published"]) < 24]
        if live and local_hour >= int(an.get("od_sata", 8)) and len(md_today) < int(an.get("mediji_dnevno", 1)):
            done = {a.get("media_of") for a in articles}
            cands = [a for a in articles if a["kind"] == "vest" and not a.get("hidden") and a["id"] not in done
                     and age_hours(a["published"]) < 24 and len({s["name"] for s in a["sources"]}) >= 4
                     and {"vlast", "opozicija"} <= {s["spektar"] for s in a["sources"]}]
            cands.sort(key=lambda a: -len(a["sources"]))
            if cands:
                out = writer.media(cands[0])
                if out and not out.get("preskoci"):
                    out["rubrika"] = "mediji" if "mediji" in cfg.rubrike else out.get("rubrika")
                    m = make_article(out, "analiza", cfg, articles, cands[0]["sources"][:12], now.isoformat())
                    m["media_of"] = cands[0]["id"]
                    m["image"] = images.choose(m, out)
                    store.save_article(m)
                    articles.append(m)
                    new_count += 1

        # 6b2. Uparivanje snimaka sa vestima (Claude odlučuje; samo kad je snimak baš o toj temi)
        if live and hasattr(writer, "link_videos"):
            vmap = {v["id"]: v for v in state.get("videos", []) if "naslov" in v and not v.get("skip") and age_hours(v["published"]) < 72}
            linked_v = {a.get("video_id") for a in articles if a.get("video_id")}
            free_v = [v for v in vmap.values() if v["id"] not in linked_v and not v.get("link_tried")][:30]
            need = [a for a in articles if a["kind"] == "vest" and not a.get("hidden") and not a.get("video_id")
                    and age_hours(a["published"]) < 48
                    and not any("youtube.com/watch" in src.get("url", "") for src in a["sources"])][:60]
            if free_v and need and (new_count > 0 or state.get("_novi_snimci")):
                pairs = writer.link_videos(free_v, need)
                by_aid = {a["id"]: a for a in need}
                for pr in pairs:
                    a, v = by_aid.get(pr.get("vest")), vmap.get(pr.get("video"))
                    if a and v and not a.get("video_id"):
                        a["video_id"] = v["id"]
                        store.save_article(a)
                log.info("Upareno %d snimaka sa vestima", len(pairs))

        # 6b3. X objave: preuzimanje, ocena i povezivanje sa vestima
        if live:
            try:
                from . import xposts
                xposts.fetch(cfg, state)
                xp = (state.get("x") or {}).get("posts", [])
                todo_x = [p for p in xp if "prikazi" not in p][:40]
                if todo_x and hasattr(writer, "x_posts"):
                    arts = [a for a in articles if a["kind"] == "vest" and not a.get("hidden") and age_hours(a["published"]) < 48]
                    arts.sort(key=lambda a: a["published"], reverse=True)
                    got = {str(d.get("id")): d for d in writer.x_posts(todo_x, arts[:80])}
                    for p in todo_x:
                        d = got.get(p["id"])
                        if d is None:
                            p["pokusaja"] = p.get("pokusaja", 0) + 1
                            if p["pokusaja"] >= 3:
                                p["prikazi"] = False
                            continue
                        p["prikazi"] = bool(d.get("prikazi"))
                        p["rubrika"] = d.get("rubrika", "")
                        p["tema"] = d.get("tema", "")
                        if d.get("vest"):
                            p["vest"] = d["vest"]
                    log.info("X: ocenjeno %d objava", len(got))
            except Exception as e:
                log.warning("X objave: %s", e)

        # 6b4. Objava na nalogu portala na X-u (najviše jedna po osvežavanju)
        if live:
            try:
                from . import objave_x
                objave_x.run(cfg, state, articles, t0)
            except Exception as e:
                log.warning("X objave: %s", e)

        # 6b5. Ankete čitalaca: pitanje dana, ankete redakcije, konačni rezultati
        try:
            from . import ankete
            ankete.run(cfg, state, articles, writer, live)
        except Exception as e:
            log.warning("Ankete: %s", e)

        # 6c. Ličnosti: automatski profili za česta imena iz vesti i prave fotografije (Wikidata, Commons)
        try:
            from . import licnosti
            licnosti.run(cfg, state, articles, writer, live)
        except Exception as e:
            log.warning("Ličnosti: %s", e)

    # 7. Sajt
    build.build(cfg, articles, state, live_widgets=not args.fixtures)
    state.setdefault("runs", []).append({"vreme": now_utc().isoformat(timespec="minutes"), "novih": new_count,
                                         "ukupno": len(articles), "sekundi": round(time.time() - t0, 1)})
    state["runs"] = state["runs"][-60:]
    store.save_state(state)
    log.info("Gotovo: %d novih, %d ukupno, %.1fs", new_count, len(articles), time.time() - t0)


if __name__ == "__main__":
    main()
