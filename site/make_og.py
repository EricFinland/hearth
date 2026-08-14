#!/usr/bin/env python3
"""Render the Open Graph card as a PNG.

    python site/make_og.py

Why this exists rather than an SVG: X, Slack, Discord, LinkedIn and iMessage
do not rasterise SVG for link previews. An og:image pointing at a .svg shows
no card at all, which is the single most expensive twenty-minute bug a launch
can carry. Measured on the live site before this was written: both docs sites
served og.svg as image/svg+xml, and both meta tags pointed at it.

The flame is the real logo path, sampled from its beziers rather than redrawn,
so the card cannot drift away from the favicon.
"""

import io
import os
import sys
import re
import urllib.request

from PIL import Image, ImageDraw, ImageFont

#: Deliberately empty. fonts.googleapis.com content-negotiates on User-Agent:
#: modern browsers get woff2 and old ones get woff, neither of which Pillow
#: reads. Sending no User-Agent at all is the one case that still returns a
#: plain TTF. Verified by checking the magic bytes of each variant.
NO_UA = {}
W, H = 1200, 630

THEMES = {
    "windows": {
        "bg": "#131922", "fg": "#f2f6fa", "dim": "#b6c2d0",
        "accent": "#4a9eff", "accent_hi": "#a8d0ff",
        "title": "hearth", "sub": "for Windows",
        "line": ["Local LLMs and an autonomous coding agent,",
                 "on your own machine."],
        "url": "ericfinland.github.io/hearth-windows",
    },
    "linux": {
        "bg": "#1c1813", "fg": "#faf7f2", "dim": "#c6c0b6",
        "accent": "#e8822e", "accent_hi": "#f7c08a",
        "title": "hearth", "sub": "for NixOS",
        "line": ["Security-first local LLMs and sandboxed",
                 "agents, on hardware you control."],
        "url": "ericfinland.github.io/hearth",
    },
}


def cubic(p0, p1, p2, p3, n=24):
    """Sample a cubic bezier. Pillow has no path support, so the logo's curves
    are flattened here rather than eyeballed as a polygon."""
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((
            u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
            u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
        ))
    return out


def flame_outer():
    """The outer flame, matching .github/assets/hero.svg on a 32x32 grid."""
    pts = [(16, 3)]
    pts += cubic((16, 3), (11, 10), (8, 13), (8, 19))[1:]
    # a8 8 0 0 0 16 0 -- a半 semicircle from (8,19) to (24,19), sweeping down.
    import math
    for i in range(1, 33):
        a = math.pi - (math.pi * i / 32)
        pts.append((16 - 8 * math.cos(a + math.pi), 19 + 8 * math.sin(a)))
    pts += cubic((24, 19), (24, 15), (21, 12), (19, 10))[1:]
    pts += cubic((19, 10), (19, 12), (18, 13), (17, 13))[1:]
    pts += cubic((17, 13), (18, 9), (16, 4), (16, 3))[1:]
    return pts


def flame_inner():
    pts = [(16, 12)]
    pts += cubic((16, 12), (14, 15), (13, 16), (13, 19.5))[1:]
    import math
    for i in range(1, 25):
        a = math.pi - (math.pi * i / 24)
        pts.append((16 - 3 * math.cos(a + math.pi), 19.5 + 3 * math.sin(a)))
    pts += cubic((19, 19.5), (19, 17.5), (18, 16.5), (17, 15.5))[1:]
    pts += cubic((17, 15.5), (17, 16.5), (16, 17.5), (15, 16.5))[1:]
    return pts


def font(spec, size):
    """Fetch a TTF for `spec` (a css2 family query) and load it at `size`.

    See NO_UA above for why the request carries no User-Agent. Pinning raw
    GitHub paths instead was tried and 404'd, because google/fonts moves its
    static/ directories around.
    """
    css = urllib.request.urlopen(urllib.request.Request(
        "https://fonts.googleapis.com/css2?" + spec, headers=NO_UA), timeout=60).read().decode()
    urls = re.findall(r"url\((https://[^)]+)\)", css)
    if not urls:
        raise SystemExit("no font url for " + spec)
    raw = urllib.request.urlopen(urllib.request.Request(urls[0], headers=NO_UA), timeout=60).read()
    return ImageFont.truetype(io.BytesIO(raw), size)


def main(outdir_win, outdir_nix):
    # Static TTFs, so Pillow gets a real weight rather than a variable default.
    news = "family=Newsreader:wght@500"
    inter_r = "family=Inter:wght@400"
    inter_m = "family=Inter:wght@500"

    f_title = font(news, 118)
    f_sub = font(inter_m, 44)
    f_line = font(inter_r, 31)
    f_url = font(inter_m, 25)

    for name, t, outdir in (("windows", THEMES["windows"], outdir_win),
                            ("linux", THEMES["linux"], outdir_nix)):
        if not outdir:
            continue
        img = Image.new("RGB", (W, H), t["bg"])
        d = ImageDraw.Draw(img)

        d.rectangle([0, 0, W, 8], fill=t["accent"])

        # Flame, scaled off the 32-unit grid the SVG uses.
        s, ox, oy = 11.0, 92, 214
        d.polygon([(ox + x * s, oy + y * s) for x, y in flame_outer()], fill=t["accent"])
        d.polygon([(ox + x * s, oy + y * s) for x, y in flame_inner()], fill=t["accent_hi"])

        # x is set by the longest line, not by eye: the tagline needs 676px and
        # the guard below enforces it. Moving the column left is the right
        # lever here because the flame has slack and the type does not.
        x, right_margin = 428, 64
        avail = W - x - right_margin

        def put(xy, text, fnt, fill):
            """Draw, and refuse to draw past the edge.

            The banner this card is derived from shipped with its tagline
            running 138px past an opaque panel, invisibly clipped. Measuring
            here means that failure is a build error rather than something
            discovered in a screenshot after publishing."""
            w = d.textlength(text, font=fnt)
            if w > avail:
                raise SystemExit(
                    "og card: %r is %.0fpx wide with %.0fpx of room at x=%d. "
                    "Shorten the string or reduce the size; do not let it clip."
                    % (text, w, avail, xy[0]))
            d.text(xy, text, font=fnt, fill=fill)

        put((x, 178), t["title"], f_title, t["fg"])
        put((x + 6, 312), t["sub"], f_sub, t["accent"])
        # Two lines rather than one shrunk to fit. The card has vertical room
        # and no horizontal room, so the break is the honest solution.
        for i, line in enumerate(t["line"]):
            put((x + 6, 388 + i * 42), line, f_line, t["dim"])
        put((x + 6, 500), t["url"], f_url, t["accent"])

        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, "og.png")
        img.save(path, "PNG", optimize=True)
        print("  wrote %s  (%dx%d, %d bytes)" % (path, W, H, os.path.getsize(path)))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None,
         sys.argv[2] if len(sys.argv) > 2 else None)
