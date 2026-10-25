#!/usr/bin/env python3
"""Build the animated terminal-style profile art.

Outputs (in assets/):
  hero-dark.svg / hero-light.svg        ASCII portrait + neofetch card
  heatmap-dark.svg / heatmap-light.svg  contribution graph

All animation lives inside the SVGs (CSS keyframes + SMIL), because GitHub
renders README images with <img>, which allows no JavaScript.

Usage:
  python scripts/build.py                       # normal run (used by the Action)
  python scripts/build.py --sample --avatar me.jpg   # offline preview
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import io
import os
import random
import re
import sys
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"

MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', 'DejaVu Sans Mono', monospace"
RAMP = " .,:;i1tfLCG08@"  # sparse -> dense

THEMES = {
    "dark": dict(
        bg="#0d1117", chrome="#161b22", border="#30363d", fg="#e6edf3", dim="#7d8590",
        accent="#7ee787", key="#79c0ff", portrait="#c9d1d9",
        dots=("#ff5f57", "#febc2e", "#28c840"),
        levels=("#161b22", "#1b3a26", "#26623a", "#3fa35a", "#7ee787"),
    ),
    "light": dict(
        bg="#ffffff", chrome="#f6f8fa", border="#d0d7de", fg="#1f2328", dim="#656d76",
        accent="#1a7f37", key="#0550ae", portrait="#1f2328",
        dots=("#ff5f57", "#febc2e", "#28c840"),
        levels=("#ebedf0", "#aceebb", "#4ac26b", "#2da44e", "#116329"),
    ),
}

# ─────────────────────────────── data ────────────────────────────────


def fetch_github(user: str) -> dict:
    import requests

    token = os.environ.get("GITHUB_TOKEN")
    if token:
        try:
            return _fetch_graphql(user, token, requests)
        except Exception as e:  # fall through to scraping
            print(f"GraphQL failed ({e}); falling back to public pages", file=sys.stderr)
    return _fetch_public(user, requests)


def _fetch_graphql(user, token, requests) -> dict:
    q = """
    query($login:String!){ user(login:$login){
      followers{ totalCount }
      repositories(ownerAffiliations:OWNER, privacy:PUBLIC, first:100,
                   orderBy:{field:STARGAZERS, direction:DESC}){
        totalCount nodes{ stargazerCount } }
      contributionsCollection{ contributionCalendar{ totalContributions
        weeks{ contributionDays{ date contributionCount contributionLevel } } } }
    }}"""
    r = requests.post("https://api.github.com/graphql",
                      json={"query": q, "variables": {"login": user}},
                      headers={"Authorization": f"bearer {token}"}, timeout=30)
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"][0].get("message"))
    u = body["data"]["user"]
    lvl = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}
    cal = u["contributionsCollection"]["contributionCalendar"]
    days = [(d["date"], d["contributionCount"], lvl.get(d["contributionLevel"], 0))
            for w in cal["weeks"] for d in w["contributionDays"]]
    return dict(
        repos=u["repositories"]["totalCount"],
        stars=sum(n["stargazerCount"] for n in u["repositories"]["nodes"]),
        followers=u["followers"]["totalCount"],
        total=cal["totalContributions"],
        days=days,
    )


def _fetch_public(user, requests) -> dict:
    page = requests.get(f"https://github.com/users/{user}/contributions", timeout=30).text
    tips = {m[0]: m[1] for m in re.findall(r'<tool-tip[^>]*for="([^"]+)"[^>]*>([^<]*)</tool-tip>', page)}
    days = []
    for attrs in re.findall(r"<td([^>]*data-date[^>]*)>", page):
        date = re.search(r'data-date="([^"]+)"', attrs).group(1)
        level = int(re.search(r'data-level="(\d)"', attrs).group(1))
        cid = re.search(r'id="([^"]+)"', attrs)
        m = re.match(r"\s*(\d[\d,]*)", tips.get(cid.group(1), "") if cid else "")
        days.append((date, int(m.group(1).replace(",", "")) if m else 0, level))
    days.sort()
    total = re.search(r"([\d,]+)\s+contributions?\s+in the last year", page)
    info = requests.get(f"https://api.github.com/users/{user}", timeout=30)
    info = info.json() if info.ok else {}
    return dict(
        repos=info.get("public_repos", 0), stars=None, followers=info.get("followers", 0),
        total=int(total.group(1).replace(",", "")) if total else sum(d[1] for d in days),
        days=days,
    )


def sample_data(seed: int = 7) -> dict:
    rnd = random.Random(seed)
    today = dt.date.today()
    start = today - dt.timedelta(days=364 + (today.weekday() + 1) % 7)
    days = []
    for i in range((today - start).days + 1):
        d = start + dt.timedelta(days=i)
        busy = rnd.random() < (0.35 if d.weekday() >= 5 else 0.75)
        c = rnd.choice([1, 1, 2, 3, 4, 6, 9, 14]) if busy else 0
        days.append((d.isoformat(), c, 0 if c == 0 else 1 if c < 3 else 2 if c < 6 else 3 if c < 10 else 4))
    return dict(repos=23, stars=48, followers=31, total=sum(d[1] for d in days), days=days)


def streaks(days) -> tuple[int, int]:
    counts = [c for _, c, _ in sorted(days)]
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    cur, i = 0, len(counts) - 1
    if i >= 0 and counts[i] == 0:  # today not done yet doesn't break the streak
        i -= 1
    while i >= 0 and counts[i]:
        cur, i = cur + 1, i - 1
    return cur, longest


def load_avatar(user: str, path: str | None) -> Image.Image:
    if path:
        return Image.open(path)
    import requests

    r = requests.get(f"https://github.com/{user}.png?size=460", timeout=30)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content))


# ──────────────────────────── ASCII portrait ─────────────────────────


def ascii_portrait(img: Image.Image, cols: int, contrast: float, gamma: float, invert: bool,
                   remove_bg: bool = True, bg_tolerance: int = 38) -> list[str]:
    img = ImageOps.exif_transpose(img).convert("RGBA")
    white = Image.new("RGBA", img.size, (255, 255, 255, 255))
    img = Image.alpha_composite(white, img).convert("RGB")
    side = min(img.size)
    img = ImageOps.fit(img, (side, side))
    rows = cols // 2  # a character cell is ~2x taller than wide
    small = img.resize((cols, rows), Image.LANCZOS)

    # Background: flood-fill from the edges through similar colours and blank it out.
    bg = set()
    if remove_bg:
        marked = small.copy()
        seeds = [(x, y) for x in range(cols) for y in (0, rows - 1)] + \
                [(x, y) for y in range(rows) for x in (0, cols - 1)]
        for s in seeds:
            if marked.getpixel(s) != (255, 0, 255):
                ImageDraw.floodfill(marked, s, (255, 0, 255), thresh=bg_tolerance)
        bg = {(x, y) for y in range(rows) for x in range(cols) if marked.getpixel((x, y)) == (255, 0, 255)}
        if len(bg) > 0.85 * cols * rows:  # filled almost everything: photo has no clear background
            bg = set()

    gray = small.convert("L")
    gray = ImageOps.autocontrast(gray, cutoff=2,
                                 mask=_mask(cols, rows, bg) if bg else None)
    gray = ImageEnhance.Contrast(gray).enhance(contrast)
    ramp = RAMP[::-1] if invert else RAMP  # dark theme: bright pixel -> dense glyph
    n = len(ramp)
    lines = []
    for y in range(rows):
        row = []
        for x in range(cols):
            if (x, y) in bg:
                row.append(" ")
                continue
            v = (gray.getpixel((x, y)) / 255) ** gamma
            row.append(ramp[min(int(v * n), n - 1)])
        lines.append("".join(row).rstrip() or " ")
    return lines


def _mask(cols, rows, bg) -> Image.Image:
    m = Image.new("L", (cols, rows), 255)
    for p in bg:
        m.putpixel(p, 0)
    return m


# ─────────────────────────────── SVG ────────────────────────────────

esc = lambda s: html.escape(str(s), quote=True)


def window(w: int, h: int, t: dict, title: str) -> str:
    dots = "".join(f'<circle cx="{20 + i * 18}" cy="16" r="5.5" fill="{c}"/>' for i, c in enumerate(t["dots"]))
    return (
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="10" fill="{t["bg"]}" stroke="{t["border"]}"/>'
        f'<path d="M0.5 32 V10.5 a10 10 0 0 1 10 -10 H{w - 10.5} a10 10 0 0 1 10 10 V32 Z" fill="{t["chrome"]}"/>'
        f'<line x1="0.5" y1="32" x2="{w - 0.5}" y2="32" stroke="{t["border"]}"/>{dots}'
        f'<text x="{w / 2}" y="20.5" text-anchor="middle" font-size="12" fill="{t["dim"]}">{esc(title)}</text>'
    )


def hero_svg(cfg: dict, data: dict, portrait: list[str], t: dict, animate: bool = True) -> str:
    user, host = cfg["username"], cfg.get("host", "github")
    cell_w, cell_h = 6, 12
    pcols = cfg["portrait"]["columns"]
    pw, ph = pcols * cell_w, len(portrait) * cell_h
    px, py = 28, 52

    # ---- card lines: list of (tspans_markup, plain_length)
    cur, longest = streaks(data["days"])
    stat_rows = {
        "repos": ("Repos", f'{data["repos"]:,}'),
        "stars": ("Stars", f'{data["stars"]:,}' if data.get("stars") is not None else "—"),
        "followers": ("Followers", f'{data["followers"]:,}'),
        "contributions": ("Contributions", f'{data["total"]:,} in the last year'),
        "streak": ("Streak", f"{cur} day{'s' * (cur != 1)} (best {longest})"),
    }
    K = lambda k, v: (f'<tspan fill="{t["key"]}">{esc(k)}</tspan><tspan fill="{t["dim"]}">: </tspan>'
                      f'<tspan>{esc(v)}</tspan>', len(k) + 2 + len(v))
    header = f"{user}@{host}"
    lines = [
        (f'<tspan fill="{t["accent"]}" font-weight="700">{esc(user)}</tspan><tspan fill="{t["dim"]}">@</tspan>'
         f'<tspan fill="{t["accent"]}" font-weight="700">{esc(host)}</tspan>', len(header)),
        (f'<tspan fill="{t["dim"]}">{"-" * len(header)}</tspan>', len(header)),
    ]
    lines += [K(k, v[:44]) for k, v in cfg.get("card", [])]
    stats = [stat_rows[s] for s in cfg.get("stats", []) if s in stat_rows]
    if stats:
        lines.append(("", 0))
        lines.append((f'<tspan fill="{t["dim"]}">-- live stats ------------</tspan>', 26))
        lines += [K(k, v) for k, v in stats]
    if cfg.get("contact"):
        lines.append(("", 0))
        lines.append(K("Contact", cfg["contact"]))

    fs, lh, ch_w = 14, 21, 8.45
    cx = px + pw + 40
    cy = py + 14
    swatch_y = cy + len(lines) * lh + 4
    w = max(cx + int(max(n for _, n in lines) * ch_w) + 36, 860)
    h = max(py + ph + 28, swatch_y + 18 + 28)

    css, body, defs = [], [], []
    if animate:
        css.append("@keyframes in{to{opacity:1}}@keyframes blink{50%{opacity:0}}"
                   ".p{opacity:0;animation:in .35s ease-out forwards}"
                   ".sw{opacity:0;animation:in .4s ease-out forwards}")

    # ---- portrait (forced width with textLength so alignment never drifts)
    for i, row in enumerate(portrait):
        y = py + (i + 1) * cell_h - 2
        attrs = f'class="p" style="animation-delay:{i * 0.055:.3f}s"' if animate else ""
        body.append(f'<text {attrs} x="{px}" y="{y}" textLength="{len(row) * cell_w}" '
                    f'lengthAdjust="spacingAndGlyphs" xml:space="preserve">{esc(row)}</text>')
    portrait_g = f'<g font-size="10.5" fill="{t["portrait"]}">{"".join(body)}</g>'

    # ---- card, typed line by line with SMIL clip wipes
    card, begin = [], 0.4
    for i, (markup, n) in enumerate(lines):
        y = cy + i * lh
        if not n:
            continue
        width = n * ch_w + 4
        dur = max(0.12, min(0.025 * n, 0.7))
        if animate:
            defs.append(
                f'<clipPath id="c{i}"><rect x="{cx - 2}" y="{y - fs}" width="0" height="{lh}">'
                f'<animate attributeName="width" from="0" to="{width:.1f}" begin="{begin:.2f}s" '
                f'dur="{dur:.2f}s" fill="freeze"/></rect></clipPath>')
            card.append(f'<text x="{cx}" y="{y}" clip-path="url(#c{i})" xml:space="preserve">{markup}</text>')
        else:
            card.append(f'<text x="{cx}" y="{y}" xml:space="preserve">{markup}</text>')
        begin += dur + 0.08
    last_y = cy + (len(lines) - 1) * lh
    last_n = lines[-1][1]

    # cursor after the last line
    cur_x = cx + last_n * ch_w + 4
    if animate:
        css.append(f".cur{{opacity:0;animation:in 0s {begin:.2f}s forwards,blink 1.1s steps(1) {begin:.2f}s infinite}}")
        card.append(f'<rect class="cur" x="{cur_x:.1f}" y="{last_y - fs + 2}" width="8" height="{fs + 2}" fill="{t["accent"]}"/>')
    # neofetch colour swatches
    for i, c in enumerate(list(t["levels"]) + [t["key"], t["accent"], t["fg"]]):
        attrs = f'class="sw" style="animation-delay:{begin + i * 0.06:.2f}s"' if animate else ""
        card.append(f'<rect {attrs} x="{cx + i * 26}" y="{swatch_y}" width="22" height="14" rx="2" fill="{c}"/>')

    card_g = f'<g font-size="{fs}" fill="{t["fg"]}">{"".join(card)}</g>'
    return _svg(w, h, t, f"{user}@{host}: ~ — neofetch", css, defs, portrait_g + card_g,
                f"{user} — ASCII portrait and profile card")


def heatmap_svg(cfg: dict, data: dict, t: dict, animate: bool = True) -> str:
    days = sorted(data["days"])[-371:]
    first = dt.date.fromisoformat(days[0][0])
    offset = (first.weekday() + 1) % 7  # GitHub weeks start on Sunday
    cell, gap = 11, 3
    step = cell + gap
    gx, gy = 58, 92
    ncols = (offset + len(days) + 6) // 7
    w = max(gx + ncols * step + 28, 860)
    h = gy + 7 * step + 64

    css, body = [], []
    if animate:
        css.append("@keyframes pop{0%{opacity:0;transform:scale(.3)}70%{transform:scale(1.15)}100%{opacity:1;transform:scale(1)}}"
                   ".d{opacity:0;transform-box:fill-box;transform-origin:center;animation:pop .4s ease-out forwards}")
        css += [f".w{c}{{animation-delay:{0.25 + c * 0.035:.3f}s}}" for c in range(ncols)]

    months, last_m = [], None
    for i, (date, count, level) in enumerate(days):
        col, row = divmod(offset + i, 7)
        d = dt.date.fromisoformat(date)
        if row == 0 and d.month != last_m and col < ncols - 1:
            months.append((col, d.strftime("%b")))
            last_m = d.month
        cls = f' class="d w{col}"' if animate else ""
        tip = f"{count} contribution{'s' * (count != 1)} on {d:%b %d, %Y}"
        body.append(f'<rect{cls} x="{gx + col * step}" y="{gy + row * step}" width="{cell}" height="{cell}" '
                    f'rx="2" fill="{t["levels"][level]}"><title>{tip}</title></rect>')
    if months and months[0][0] == 0 and len(months) > 1 and months[1][0] < 3:
        months.pop(0)  # avoid overlapping labels at the left edge

    labels = "".join(f'<text x="{gx + c * step}" y="{gy - 10}">{m}</text>' for c, m in months)
    labels += "".join(f'<text x="{gx - 10}" y="{gy + r * step + 9.5}" text-anchor="end">{n}</text>'
                      for r, n in ((1, "Mon"), (3, "Wed"), (5, "Fri")))
    cmd = f'<text x="24" y="54" font-size="13"><tspan fill="{t["accent"]}">$</tspan> <tspan fill="{t["fg"]}">git log --author={esc(cfg["username"])} --since="1 year ago" | heatmap</tspan></text>'
    fy = gy + 7 * step + 30
    foot = (f'<text x="{gx}" y="{fy}" font-size="12.5" fill="{t["fg"]}">'
            f'<tspan font-weight="700">{data["total"]:,}</tspan> contributions in the last year</text>')
    lx = gx + ncols * step - 5 * step - 34
    foot += f'<text x="{lx - 8}" y="{fy}" text-anchor="end">Less</text>'
    foot += "".join(f'<rect x="{lx + i * step}" y="{fy - 10}" width="{cell}" height="{cell}" rx="2" fill="{c}"/>'
                    for i, c in enumerate(t["levels"]))
    foot += f'<text x="{lx + 5 * step + 6}" y="{fy}">More</text>'

    inner = cmd + f'<g font-size="11" fill="{t["dim"]}">{labels}{foot}</g>' + "".join(body)
    return _svg(w, h, t, "contributions", css, [], inner, f"{data['total']} GitHub contributions in the last year")


def _svg(w, h, t, title, css, defs, inner, alt) -> str:
    style = f"<style>{''.join(css)}</style>" if css else ""
    d = f"<defs>{''.join(defs)}</defs>" if defs else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'font-family="{MONO}" role="img" aria-label="{esc(alt)}"><title>{esc(alt)}</title>{style}{d}'
            f"{window(w, h, t, title)}{inner}</svg>\n")


# ─────────────────────────────── main ───────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="use made-up stats (offline preview)")
    ap.add_argument("--avatar", help="local image instead of your GitHub avatar")
    ap.add_argument("--static", action="store_true", help="no animation (for PNG previews)")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "profile.yml").read_text(encoding="utf-8"))
    cfg.setdefault("portrait", {})
    cfg["portrait"].setdefault("columns", 50)
    data = sample_data() if a.sample else fetch_github(cfg["username"])
    avatar = load_avatar(cfg["username"], a.avatar)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    p = cfg["portrait"]
    for name, t in THEMES.items():
        art = ascii_portrait(avatar, p["columns"], p.get("contrast", 1.35), p.get("gamma", 1.0),
                             invert=name == "light", remove_bg=p.get("remove_background", True),
                             bg_tolerance=p.get("background_tolerance", 38))
        (out / f"hero-{name}.svg").write_text(hero_svg(cfg, data, art, t, not a.static), encoding="utf-8")
        (out / f"heatmap-{name}.svg").write_text(heatmap_svg(cfg, data, t, not a.static), encoding="utf-8")
    cur, best = streaks(data["days"])
    print(f"built {out}: {data['total']} contributions, streak {cur}/{best}, repos {data['repos']}")


if __name__ == "__main__":
    main()
