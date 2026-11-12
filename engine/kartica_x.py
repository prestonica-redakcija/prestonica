"""Grafička kartica 1600 x 900 za objavu vesti na mreži X (PNG).

Naslov velikim serifnim slovima na tamnoplavoj podlozi, rubrika u boji, potpis portala.
Fotografija ide u pozadinu samo kad je iz naše arhive slobodnih fotografija (uploads/foto).
Fontovi se preuzimaju jednom po pokretanju; bez njih kartica koristi sistemski font.
"""
from __future__ import annotations

import io
import logging
import tempfile
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import ROOT

log = logging.getLogger("prestonica")
W, H = 1600, 900
NAVY, NAVY2, RED, WHITE, MUTED = (10, 17, 41), (14, 23, 48), (232, 41, 63), (255, 255, 255), (201, 208, 224)
FONT_URL = {
    "display": "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
    "sans": "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/manrope/Manrope%5Bwght%5D.ttf",
}
MESECI = ["januar", "februar", "mart", "april", "maj", "jun", "jul", "avgust", "septembar", "oktobar", "novembar", "decembar"]
_cache: dict[str, Path | None] = {}


def _font_file(kind: str) -> Path | None:
    if kind in _cache:
        return _cache[kind]
    p = Path(tempfile.gettempdir()) / f"prestonica-{kind}.ttf"
    if not p.exists():
        try:
            r = requests.get(FONT_URL[kind], timeout=40)
            r.raise_for_status()
            p.write_bytes(r.content)
        except Exception as e:
            log.warning("X kartica: font %s nije preuzet: %s", kind, e)
            p = None
    _cache[kind] = p
    return p


def _font(kind: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    p = _font_file(kind)
    if p:
        f = ImageFont.truetype(str(p), size)
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
        return f
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _wrap(d: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _spaced(d, xy, text, font, fill, track):
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + track
    return x


def _spaced_w(d, text, font, track):
    return sum(d.textlength(ch, font=font) + track for ch in text) - track


def _background(a: dict) -> Image.Image:
    img = Image.new("RGB", (W, H), NAVY)
    src = (a.get("image") or {})
    photo = None
    if src.get("local"):
        f = ROOT / "uploads" / src["src"]
        if f.exists():
            try:
                photo = Image.open(f).convert("RGB")
            except Exception:
                photo = None
    if photo:
        s = max(W / photo.width, H / photo.height)
        photo = photo.resize((int(photo.width * s) + 1, int(photo.height * s) + 1), Image.LANCZOS)
        l, t = (photo.width - W) // 2, (photo.height - H) // 2
        img.paste(photo.crop((l, t, l + W, t + H)).filter(ImageFilter.GaussianBlur(1.2)), (0, 0))
    # tamni sloj: s leva jači (tekst), dole jači (potpis)
    sw, sh = 160, 90
    shade = Image.new("L", (sw, sh))
    sp = shade.load()
    for x in range(sw):
        base = 0.97 - 0.42 * (x / sw) if photo else 1.0
        for y in range(sh):
            v = max(base, 0.55 + 0.45 * (y / sh) ** 2) if photo else 1.0
            sp[x, y] = int(255 * min(1.0, v))
    shade = shade.resize((W, H), Image.BILINEAR)
    over = Image.new("RGB", (W, H), NAVY)
    img = Image.composite(over, img, shade)
    if not photo:
        g = ImageDraw.Draw(img)
        for x in range(0, W, 80):
            g.line([(x, 0), (x, H)], fill=(18, 28, 58), width=1)
        for y in range(0, H, 80):
            g.line([(0, y), (W, y)], fill=(18, 28, 58), width=1)
    return img


def render(a: dict, cfg) -> bytes:
    img = _background(a)
    d = ImageDraw.Draw(img)
    M = 96
    # rubrika / analiza
    rub = (cfg.rubrike.get(a.get("section", "")) or {})
    label = "ANALIZA" if a.get("kind") == "analiza" else str(rub.get("naziv", "Vesti")).upper()
    color = RED if a.get("kind") == "analiza" else _hex(rub.get("boja", "#e8293f"))
    tf = _font("sans", 28, 800)
    tw = _spaced_w(d, label, tf, 4)
    d.rounded_rectangle((M, 84, M + tw + 76, 136), radius=26, fill=color)
    d.ellipse((M + 22, 104, M + 34, 116), fill=WHITE)
    _spaced(d, (M + 50, 93), label, tf, WHITE, 4)
    # datum, gore desno
    try:
        dt = datetime.fromisoformat(a["published"])
        from zoneinfo import ZoneInfo
        dt = dt.astimezone(ZoneInfo("Europe/Belgrade"))
        ds = f"{dt.day}. {MESECI[dt.month - 1]} {dt.year}.  {dt:%H:%M}".upper()
    except Exception:
        ds = ""
    df = _font("sans", 24, 700)
    if ds:
        _spaced(d, (W - M - _spaced_w(d, ds, df, 3), 98), ds, df, MUTED, 3)
    # naslov: najveći font pri kom staje u 4 reda
    head = " ".join(str(a.get("headline", "")).split())
    for size in range(96, 50, -4):
        hf = _font("display", size, 900)
        lines = _wrap(d, head, hf, W - 2 * M)
        if len(lines) <= 4:
            break
    lines = lines[:4]
    lh = int(size * 1.14)
    block = lh * len(lines)
    y = max(190, (H - block) // 2 - 10)
    for ln in lines:
        try:
            d.text((M, y), ln, font=hf, fill=WHITE, features=["lnum"])
        except Exception:
            d.text((M, y), ln, font=hf, fill=WHITE)
        y += lh
    # potpisna linija: bela 82%, crvena 18%
    y += 26
    bw = 520
    d.rectangle((M, y, M + int(bw * .82), y + 8), fill=WHITE)
    d.rectangle((M + int(bw * .82), y, M + bw, y + 8), fill=RED)
    # dno: logo levo, adresa desno, crvena traka
    lf = _font("display", 46, 900)
    d.text((M, H - 128), "PRESTONICA", font=lf, fill=WHITE)
    lw = d.textlength("PRESTONICA", font=lf)
    d.text((M + lw + 2, H - 128), ".", font=lf, fill=RED)
    uf = _font("sans", 26, 800)
    u = "PRESTONICA.RS"
    _spaced(d, (W - M - _spaced_w(d, u, uf, 5), H - 112), u, uf, MUTED, 5)
    cr = (a.get("image") or {}).get("credit")
    if cr and (a.get("image") or {}).get("local"):
        cf = _font("sans", 18, 600)
        t = "Ilustracija: " + str(cr)
        d.text((W - M - d.textlength(t, font=cf), H - 66), t, font=cf, fill=(140, 150, 175))
    d.rectangle((0, H - 14, int(W * .22), H), fill=RED)
    d.rectangle((int(W * .22), H - 14, W, H), fill=(40, 50, 80))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
