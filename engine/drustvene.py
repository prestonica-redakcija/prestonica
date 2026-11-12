"""Kartice za deljenje (og:image) 1200 x 630, na našem domenu.

X, Facebook, Viber i WhatsApp pregled linka prave iz og:image. Ranije je to bio direktan link na fotografiju
sa Wikimedia ili Pexels servera (male, ponekad nedostupne robotima), pa je pregled znao da ostane siv.
Sada svaka nova vest dobija sopstvenu karticu: fotografija iz vesti desno (oštra, lice gore), tamnoplava
površina levo sa rubrikom, naslovom i znakom portala. Bez fotografije: tamnoplava kartica sa mrežom.

Kartice se čuvaju u .og-cache (keš GitHub Actions-a), pa se svaka pravi samo jednom.
"""
from __future__ import annotations

import hashlib
import io
import logging
import shutil
import time
from pathlib import Path
from urllib.parse import quote, unquote

import requests
from PIL import Image, ImageDraw, ImageOps

from . import kartica_x as kx
from .config import ROOT
from .util import age_hours

log = logging.getLogger("prestonica")
W, H = 1200, 630
CACHE = ROOT / ".og-cache"
VERZIJA = "3"
UA = {"User-Agent": "PrestonicaBot/1.0 (+https://prestonica.rs/o-nama.html; redakcija@prestonica.rs)"}
NAVY, LINE, RED, WHITE, MUTED = (13, 23, 48), (22, 34, 66), (211, 38, 58), (255, 255, 255), (190, 199, 218)


def _photo_url(img: dict) -> str:
    """Za Wikimedia fotografije uzima se standardna veličina 1280 px (ne original, ne sitna sličica)."""
    src = img.get("src", "")
    link = img.get("link", "")
    if "commons.wikimedia.org/wiki/File:" in link:
        name = unquote(link.split("File:", 1)[1])
        return "https://commons.wikimedia.org/wiki/Special:FilePath/" + quote(name) + "?width=1280"
    return src


def _photo(a: dict) -> Image.Image | None:
    img = a.get("image") or {}
    if not img.get("src"):
        return None
    try:
        if img.get("local"):
            f = ROOT / "uploads" / img["src"]
            return Image.open(f).convert("RGB") if f.exists() else None
        url = _photo_url(img)
        key = hashlib.md5(url.encode()).hexdigest()
        cp = CACHE / "src" / f"{key}.jpg"
        if cp.exists():
            return Image.open(cp).convert("RGB")
        r = requests.get(url, headers=UA, timeout=25)
        if r.status_code != 200 or len(r.content) < 3000:
            r = requests.get(img["src"], headers=UA, timeout=25)
        r.raise_for_status()
        ph = ImageOps.exif_transpose(Image.open(io.BytesIO(r.content))).convert("RGB")
        ph.thumbnail((1600, 1600))
        cp.parent.mkdir(parents=True, exist_ok=True)
        ph.save(cp, quality=86)
        return ph
    except Exception as e:
        log.info("Kartica: fotografija nije preuzeta (%s): %s", a.get("slug", "")[:40], e)
        return None


def _fit(ph: Image.Image, w: int, h: int) -> Image.Image:
    """Popuni okvir; kod uspravnih fotografija (portreti) zadrži gornji deo, gde je lice."""
    s = max(w / ph.width, h / ph.height)
    ph = ph.resize((max(w, int(ph.width * s + 1)), max(h, int(ph.height * s + 1))), Image.LANCZOS)
    left = (ph.width - w) // 2
    top = int((ph.height - h) * (0.18 if ph.height > ph.width * 1.05 else 0.4))
    return ph.crop((left, top, left + w, top + h))


def card(a: dict, cfg) -> bytes:
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    ph = _photo(a)
    PW = 520 if ph else 0
    if ph:
        img.paste(_fit(ph, PW, H), (W - PW, 0))
        # meki prelaz sa tamnoplave površine na fotografiju
        g = Image.new("L", (90, 1))
        for x in range(90):
            g.putpixel((x, 0), int(255 * (1 - x / 89) ** 1.6))
        img.paste(Image.new("RGB", (90, H), NAVY), (W - PW, 0), g.resize((90, H)))
    else:
        for x in range(0, W, 60):
            d.line([(x, 0), (x, H)], fill=LINE)
        for y in range(0, H, 60):
            d.line([(0, y), (W, y)], fill=LINE)
    M, TW = 64, W - PW - 64 - (40 if ph else 64)
    # rubrika
    rub = cfg.rubrike.get(a.get("section", "")) or {}
    label = "ANALIZA" if a.get("kind") in ("analiza", "autorski") else str(rub.get("naziv", "Vesti")).upper()
    color = RED if a.get("kind") in ("analiza", "autorski") else kx._hex(rub.get("boja", "#d3263a"))
    tf = kx._font("sans", 20, 800)
    lw = kx._spaced_w(d, label, tf, 3)
    d.rounded_rectangle((M, 56, M + lw + 52, 94), radius=19, fill=color)
    d.ellipse((M + 16, 70, M + 26, 80), fill=WHITE)
    kx._spaced(d, (M + 36, 63), label, tf, WHITE, 3)
    # naslov: najveći font pri kom staje u 5 redova
    head = " ".join(str(a.get("headline", "")).split())
    for size in range(64, 30, -2):
        hf = kx._font("display", size, 800)
        lines = kx._wrap(d, head, hf, TW)
        if len(lines) <= 5:
            break
    if len(lines) > 5:
        lines = lines[:5]
        lines[-1] = lines[-1].rstrip(".,;:") + "…"
    lh = int(size * 1.17)
    y = 128 + max(0, (H - 128 - 120 - lh * len(lines)) // 2)
    for ln in lines:
        try:
            d.text((M, y), ln, font=hf, fill=WHITE, features=["lnum"])
        except Exception:
            d.text((M, y), ln, font=hf, fill=WHITE)
        y += lh
    # dno: znak portala i adresa
    lf = kx._font("display", 34, 900)
    d.text((M, H - 92), "PRESTONICA", font=lf, fill=WHITE)
    d.text((M + d.textlength("PRESTONICA", font=lf) + 1, H - 92), ".", font=lf, fill=RED)
    uf = kx._font("sans", 17, 700)
    kx._spaced(d, (M, H - 46), "PRESTONICA.RS", uf, MUTED, 4)
    cr = (a.get("image") or {}).get("credit", "") if ph else ""
    if cr:
        cf = kx._font("sans", 13, 600)
        t = "Foto: " + str(cr)[:70]
        tw = d.textlength(t, font=cf)
        d.rectangle((W - tw - 26, H - 30, W, H), fill=(0, 0, 0))
        d.text((W - tw - 14, H - 24), t, font=cf, fill=(215, 220, 232))
    d.rectangle((0, 0, int(W * .26), 6), fill=RED)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=84, optimize=True, progressive=True)
    return buf.getvalue()


def brand_card(cfg) -> bytes:
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    for x in range(0, W, 60):
        d.line([(x, 0), (x, H)], fill=LINE)
    for y in range(0, H, 60):
        d.line([(0, y), (W, y)], fill=LINE)
    lf = kx._font("display", 128, 900)
    t = "PRESTONICA"
    tw = d.textlength(t, font=lf) + d.textlength(".", font=lf)
    x = (W - tw) / 2
    d.text((x, 200), t, font=lf, fill=WHITE)
    d.text((x + d.textlength(t, font=lf) + 2, 200), ".", font=lf, fill=RED)
    sf = kx._font("sans", 28, 600)
    s = str(cfg.portal.get("slogan", ""))
    d.text(((W - d.textlength(s, font=sf)) / 2, 390), s, font=sf, fill=MUTED)
    d.rectangle((0, H - 10, int(W * .3), H), fill=RED)
    d.rectangle((int(W * .3), H - 10, W, H), fill=LINE)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue()


def logo(size: int = 512) -> bytes:
    """Znak portala (krug sa P.) za Google, X i strukturisane podatke."""
    img = Image.new("RGB", (size, size), NAVY)
    d = ImageDraw.Draw(img)
    f = kx._font("display", int(size * .62), 900)
    t = "P"
    w = d.textlength(t, font=f)
    d.text(((size - w) / 2 - size * .06, size * .1), t, font=f, fill=WHITE)
    r = size * .075
    cx, cy = size * .72, size * .7
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=RED)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def signature_banner(cfg) -> bytes:
    """Baner za potpis u mejlovima redakcije: 1200 x 240 (prikaz 600 x 120), tamnoplavo, znak portala i slogan."""
    w, h = 1200, 240
    img = Image.new("RGB", (w, h), NAVY)
    d = ImageDraw.Draw(img)
    for x in range(0, w, 48):
        d.line([(x, 0), (x, h)], fill=LINE)
    for y in range(0, h, 48):
        d.line([(0, y), (w, y)], fill=LINE)
    lf = kx._font("display", 92, 900)
    t = "PRESTONICA"
    x0, y0 = 56, 52
    d.text((x0, y0), t, font=lf, fill=WHITE)
    tw = d.textlength(t, font=lf)
    d.text((x0 + tw + 2, y0), ".", font=lf, fill=RED)
    full = tw + d.textlength(".", font=lf)
    ly = y0 + 112
    d.rectangle((x0, ly, x0 + full * .84, ly + 4), fill=WHITE)
    d.rectangle((x0 + full * .84, ly, x0 + full, ly + 4), fill=RED)
    sf = kx._font("sans", 26, 600)
    s1 = "Najvažnije vesti iz Srbije,"
    s2 = "bez buke. Bez kompromisa."
    rx = w - 56 - max(d.textlength(s1, font=sf), d.textlength(s2, font=sf))
    d.text((rx, 84), s1, font=sf, fill=MUTED)
    d.text((rx, 120), s2, font=sf, fill=MUTED)
    d.rectangle((0, h - 8, int(w * .3), h), fill=RED)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def build_cards(cfg, arts: list[dict], out: Path) -> int:
    oc = cfg.portal.get("kartice_za_deljenje") or {}
    if not oc.get("ukljuceno", True):
        return 0
    days = float(oc.get("dana", 4))
    CACHE.mkdir(exist_ok=True)
    (out / "og").mkdir(parents=True, exist_ok=True)
    (out / "og" / "prestonica.jpg").write_bytes(brand_card(cfg))
    (out / "og" / "logo.png").write_bytes(logo())
    (out / "og" / "potpis.png").write_bytes(signature_banner(cfg))
    n, t0 = 0, time.time()
    for a in arts:
        if age_hours(a["published"]) > days * 24:
            continue
        key = hashlib.md5("|".join([VERZIJA, a["id"], a.get("headline", ""), (a.get("image") or {}).get("src", "")]).encode()).hexdigest()[:16]
        cf = CACHE / f"{a['id']}-{key}.jpg"
        if not cf.exists():
            if time.time() - t0 > float(oc.get("najvise_sekundi", 240)):
                continue                       # ostale u sledećem osvežavanju
            try:
                cf.write_bytes(card(a, cfg))
                n += 1
            except Exception as e:
                log.warning("Kartica %s: %s", a["id"], e)
                continue
        shutil.copyfile(cf, out / "og" / f"{a['id']}.jpg")
        a["_og"] = f"og/{a['id']}.jpg"
    # čišćenje keša: starije od 8 dana
    for f in list(CACHE.glob("*.jpg")) + list((CACHE / "src").glob("*.jpg")):
        if time.time() - f.stat().st_mtime > 8 * 86400:
            f.unlink(missing_ok=True)
    if n:
        log.info("Kartice za deljenje: %d novih", n)
    return n
