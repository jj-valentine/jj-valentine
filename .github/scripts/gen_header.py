#!/usr/bin/env python3
"""
gen_header.py — render the profile terminal-window header (SVG -> PNG).

Faithful to assets/masthead-context.html (the v6 mock): a terminal WINDOW =
  chrome (rounded, traffic lights) + title bar (james@valentine path + live ✳ summary)
  + body (the pre-baked masthead PNG + positioning tagline + kicker).

The ✳ commit summary is the only moving part — passed as argv[1] (or env SUMMARY),
so CI re-renders just this PNG. The ✳ is drawn as an SVG shape (not a font glyph) so it
renders identically everywhere and can never tofu. Output: assets/header.png at 2x.

Usage:  python3 .github/scripts/gen_header.py "feat(cerebellum|recall): hybrid rerank · perf(ops|gate): model routing"
"""
import sys, os, base64, struct, subprocess, html

ROOT     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
ASSETS   = os.path.join(ROOT, "assets")
MASTHEAD = os.path.join(ASSETS, "masthead-perletter.png")
OUT_SVG  = os.path.join(ASSETS, "header.svg")
OUT_PNG  = os.path.join(ASSETS, "header.png")

# --- content -----------------------------------------------------------------
PATH    = "james@valentine: ~/Seattle/{dev,dogs}"
DEFAULT = "feat(cerebellum|recall): hybrid rerank · perf(intero|status): git cache"
TAGLINE = "studying intelligent systems and shaping their behavior"   # placeholder (Track B)
KICKER  = "full-stack · agentic systems · harness & CLI tooling · memory"  # placeholder (Track B)

summary = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SUMMARY", "")).strip() or DEFAULT

# --- palette / geometry (from the mock CSS) ----------------------------------
WIN_W   = 1040
RADIUS  = 12
C_WINBG, C_BORDER, C_BARBG = "#16130f", "#2a241e", "#241f1b"
LIGHTS  = ["#dd6964", "#ffd080", "#baffc9"]                  # coral / amber / mint
C_PATH, C_STAR, C_SUM      = "#ffd080", "#baffc9", "#9a9488"
C_TAG, C_KICK              = "#ffd080", "#7d756a"
FONT    = "Intero Mono, Menlo, monospace"

BAR_H        = 36
BAR_PAD_X    = 16
LIGHT_R, LIGHT_GAP = 6.5, 9
TITLE_FS     = 13
CHAR_W       = TITLE_FS * 0.6        # Intero/Menlo are monospace (~0.6em advance)
GAP_PATH_STAR, GAP_STAR_SUM = 24, 17
STAR_R, STAR_TH = 6.5, 2.0

BODY_PAD_X, BODY_PAD_TOP, BODY_PAD_BOT = 30, 34, 30
TAG_FS, KICK_FS = 19, 12
TAG_MT,  KICK_MT = 22, 11


def png_size(path):
    """(width, height) from a PNG's IHDR header — big-endian uint32 at bytes 16/20."""
    with open(path, "rb") as f:
        return struct.unpack(">II", f.read(24)[16:24])


def data_uri(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")


def star(cx, cy, r, th, color):
    """✳ eight-spoked asterisk = 4 rounded bars through center at 0/45/90/135°."""
    bar = ('<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{rx:.2f}" '
           'fill="{c}" transform="rotate({a} {cx:.2f} {cy:.2f})"/>')
    return "".join(
        bar.format(x=cx - r, y=cy - th / 2, w=2 * r, h=th, rx=th / 2, c=color, a=a, cx=cx, cy=cy)
        for a in (0, 45, 90, 135)
    )


# --- layout ------------------------------------------------------------------
mh_w, mh_h = png_size(MASTHEAD)
disp_w = WIN_W - 2 * BODY_PAD_X
disp_h = disp_w * mh_h / mh_w
mh_y   = BAR_H + BODY_PAD_TOP

tag_y  = mh_y + disp_h + TAG_MT + TAG_FS            # tagline baseline
kick_y = tag_y + KICK_MT + KICK_FS                  # kicker baseline
TOTAL_H = int(round(kick_y + KICK_FS * 0.3 + BODY_PAD_BOT))

cy = BAR_H / 2                                       # vertical center: lights, star, title
title_base = cy + TITLE_FS * 0.35

# traffic lights
lights = "".join(
    '<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{c}"/>'.format(
        cx=BAR_PAD_X + LIGHT_R + i * (2 * LIGHT_R + LIGHT_GAP), cy=cy, r=LIGHT_R, c=c)
    for i, c in enumerate(LIGHTS)
)

# title bar text: path (amber) — drawn ✳ — summary (muted)
title_x  = BAR_PAD_X + LIGHT_R + 2 * (2 * LIGHT_R + LIGHT_GAP) + LIGHT_R + 16
path_w   = len(PATH) * CHAR_W
star_cx  = title_x + path_w + GAP_PATH_STAR + STAR_R
sum_x    = star_cx + STAR_R + GAP_STAR_SUM

def txt(x, y, fill, fs, s, anchor="start", ls=0.2):
    return ('<text x="{x:.1f}" y="{y:.1f}" font-family="{f}" font-size="{fs}" '
            'fill="{fill}" letter-spacing="{ls}" text-anchor="{a}" '
            'style="white-space:pre">{s}</text>').format(
        x=x, y=y, f=FONT, fs=fs, fill=fill, ls=ls, a=anchor, s=html.escape(s))

title_bar = (
    '<path d="M0 {bh:.0f} L0 {r} Q0 0 {r} 0 L{wr:.0f} 0 Q{w} 0 {w} {r} L{w} {bh:.0f} Z" fill="{bg}"/>'
    '<line x1="0" y1="{bh:.0f}" x2="{w}" y2="{bh:.0f}" stroke="{bd}" stroke-width="1"/>'
).format(bh=BAR_H, r=RADIUS, wr=WIN_W - RADIUS, w=WIN_W, bg=C_BARBG, bd=C_BORDER)

cx_center = WIN_W / 2.0
svg = (
    '<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">'
    '<rect width="{w}" height="{h}" rx="{r}" fill="{winbg}"/>'                 # window body
    '{bar}{lights}'                                                            # bar + lights
    '{path}{star}{summary}'                                                    # title contents
    '<image x="{mx}" y="{my:.1f}" width="{mw}" height="{mh:.1f}" href="{img}"/>'  # masthead
    '{tag}{kick}'                                                              # positioning
    '<rect x="0.5" y="0.5" width="{w1:.0f}" height="{h1:.0f}" rx="{r1}" fill="none" '
    'stroke="{bd}" stroke-width="1"/>'                                         # crisp border on top
    '</svg>'
).format(
    w=WIN_W, h=TOTAL_H, r=RADIUS, winbg=C_WINBG,
    bar=title_bar, lights=lights,
    path=txt(title_x, title_base, C_PATH, TITLE_FS, PATH),
    star=star(star_cx, cy, STAR_R, STAR_TH, C_STAR),
    summary=txt(sum_x, title_base, C_SUM, TITLE_FS, summary),
    mx=BODY_PAD_X, my=mh_y, mw=disp_w, mh=disp_h, img=data_uri(MASTHEAD),
    tag=txt(cx_center, tag_y, C_TAG, TAG_FS, TAGLINE, anchor="middle", ls=0.2),
    kick=txt(cx_center, kick_y, C_KICK, KICK_FS, KICKER, anchor="middle", ls=2),
    w1=WIN_W - 1, h1=TOTAL_H - 1, r1=RADIUS - 0.5, bd=C_BORDER,
)

with open(OUT_SVG, "w") as f:
    f.write(svg)
subprocess.run(["rsvg-convert", "-w", str(WIN_W * 2), OUT_SVG, "-o", OUT_PNG], check=True)
print("header: {}x{}  (masthead {}x{}, disp {:.0f}x{:.0f})".format(
    WIN_W, TOTAL_H, mh_w, mh_h, disp_w, disp_h))
