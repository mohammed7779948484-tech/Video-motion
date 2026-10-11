#!/usr/bin/env python3
"""Build the SYRAZEN wordmark (proposed original treatment) as vector outlines.

    python3 tools/build_wordmark.py            # writes assets/svg/wordmark.svg
    python3 tools/build_wordmark.py --inline   # also refreshes the inline copy in compositions/lockup.html

Type: Archivo variable (assets/fonts/Archivo-Variable.ttf, SIL OFL), instance wdth 125 / wght 800,
all caps. uharfbuzz shapes the word (GPOS kerning applied at that instance) and draws each glyph's
outline at the same variation coordinates, so the SVG is real letterforms, not live text.

Spacing: +0.02em base tracking (frame.md "wordmark" spec) plus per-pair optical corrections, tuned
by measuring the white area between each pair's facing profiles (cap band, depth-limited) and then
by eye at display size, so open/diagonal pairs (S-Y, Y-R, R-A, A-Z) carry the same colour as the
straight-sided ones (Z-E, E-N).

Ownable detail (separate element, id "wm-accent"): THE LINE. The vermilion line that turns the shop
into a website in the film becomes the signature under the name: a bar running the full ink width
whose two ends are cut at exactly the angle of the Z's diagonal. It leans forward ("/"), it is
derived from the letterforms rather than pasted under them, and it is the one thing in the mark
that is vermilion. The letters themselves stay untouched for legibility.

Output is deterministic: fixed font, fixed numbers, coordinates rounded to 0.1 font unit.
Coordinates are font units (1000/em), y flipped: y=0 is the cap line, y=CAP is the baseline.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = ROOT / "assets/fonts/Archivo-Variable.ttf"
OUT_SVG = ROOT / "assets/svg/wordmark.svg"
LOCKUP_HTML = ROOT / "compositions/lockup.html"

TEXT = "SYRAZEN"
VARIATIONS = {"wdth": 125, "wght": 800}
INK = "#14161F"
VERMILION = "#FF4A1C"

TRACKING = 20  # font units = +0.02em (frame.md wordmark tracking)
# Optical pair corrections on top of tracking + the font's own kerning (font units).
# Effective white per pair at +20 tracking = facing sidebearings + kerning + the white inside each
# facing profile, depth-limited to ~120u (0.6 stem); measured: SY 207, YR 231, RA 226, AZ 243,
# ZE 224, EN 217. Pairs were pulled toward ~205-210, then compared by eye at 852 px against the
# untouched setting: Y-R and A-Z read loose (open triangles), Z-E / E-N slightly airy (open
# right sides of Z and E). Closest contacts after correction stay >= 39u (~5 px at 852 px).
PAIR_ADJUST = {
    "SY": -4,
    "YR": -26,
    "RA": -18,
    "AZ": -26,
    "ZE": -8,
    "EN": -4,
}

# The line (wm-accent): geometry in font units.
ACCENT_GAP = 156  # baseline -> top edge of the line
ACCENT_THICKNESS = 92  # ~0.6x the E arm (150u): reads as a line, not a letter part

# Display size used for the inline copy in the lockup and the SVG width/height attributes.
DISPLAY_WIDTH_PX = 852

ROUND = 1  # decimals in path data


def fmt(v: float) -> str:
    s = f"{v:.{ROUND}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def shape():
    blob = hb.Blob.from_file_path(str(FONT))
    face = hb.Face(blob)
    font = hb.Font(face)
    font.set_variations(VARIATIONS)
    buf = hb.Buffer()
    buf.add_str(TEXT)
    buf.guess_segment_properties()
    hb.shape(font, buf, {"kern": True, "liga": False})
    return font, buf.glyph_infos, buf.glyph_positions


def glyph_bounds(font, gid):
    bp = BoundsPen(None)
    font.draw_glyph_with_pen(gid, bp)
    return bp.bounds  # (xMin, yMin, xMax, yMax) in font units, y up


def cap_height(font) -> float:
    # Height of the flat top of "H" at this instance (wdth/wght shift it slightly from OS/2).
    b = hb.Buffer()
    b.add_str("H")
    b.guess_segment_properties()
    hb.shape(font, b)
    return glyph_bounds(font, b.glyph_infos[0].codepoint)[3]


def z_diagonal_slope(font) -> float:
    """dy/dx of the Z's diagonal stroke, read from its outline (longest non-axis-aligned segment)."""
    b = hb.Buffer()
    b.add_str("Z")
    b.guess_segment_properties()
    hb.shape(font, b)

    class Seg:
        def __init__(self):
            self.pts = []

        def moveTo(self, p):
            self.pts.append(p)

        def lineTo(self, p):
            self.pts.append(p)

        def curveTo(self, *ps):
            self.pts.append(ps[-1])

        def qCurveTo(self, *ps):
            self.pts.append(ps[-1])

        def closePath(self):
            pass

        endPath = closePath

    s = Seg()
    font.draw_glyph_with_pen(b.glyph_infos[0].codepoint, s)
    best = None
    pts = s.pts + s.pts[:1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        if abs(dx) < 1 or abs(dy) < 1:
            continue
        length = (dx * dx + dy * dy) ** 0.5
        if best is None or length > best[0]:
            best = (length, abs(dy / dx))
    return best[1]


def build():
    font, infos, positions = shape()
    cap = cap_height(font)
    slope = z_diagonal_slope(font)

    # Pen positions: hb advance (incl. kerning) + tracking + optical pair correction.
    pen_x = []
    advances = []
    x = 0.0
    for i, (info, pos) in enumerate(zip(infos, positions)):
        pen_x.append(x)
        adv = pos.x_advance
        if i < len(TEXT) - 1:
            adv += TRACKING + PAIR_ADJUST.get(TEXT[i] + TEXT[i + 1], 0)
        advances.append(adv)
        x += adv

    # Ink extents of the set word.
    boxes = [glyph_bounds(font, info.codepoint) for info in infos]
    ink_left = pen_x[0] + boxes[0][0]
    ink_right = pen_x[-1] + boxes[-1][2]
    ink_top = max(b[3] for b in boxes)  # y-up: highest point (overshoot)
    ink_bottom = min(b[1] for b in boxes)  # y-up: lowest point (overshoot, negative)
    width = ink_right - ink_left

    # Letter paths, translated so x=0 is the left ink edge and y=0 the cap line (y down).
    letters = []
    for ch, info, px in zip(TEXT, infos, pen_x):
        sp = SVGPathPen(None, ntos=fmt)
        tp = TransformPen(sp, (1, 0, 0, -1, px - ink_left, cap))
        font.draw_glyph_with_pen(info.codepoint, tp)
        letters.append((ch, sp.getCommands()))

    # The line: full ink width, both ends cut parallel to the Z diagonal ("/").
    top = cap + ACCENT_GAP
    bot = top + ACCENT_THICKNESS
    slant = ACCENT_THICKNESS / slope
    accent_d = (
        f"M{fmt(0)} {fmt(bot)}L{fmt(slant)} {fmt(top)}L{fmt(width)} {fmt(top)}"
        f"L{fmt(width - slant)} {fmt(bot)}Z"
    )

    vb_y = cap - ink_top  # negative: top overshoot above the cap line
    vb_h = bot - vb_y
    return {
        "cap": cap,
        "slope": slope,
        "slant": slant,
        "width": width,
        "vb": (0.0, vb_y, width, vb_h),
        "letters": letters,
        "accent_d": accent_d,
        "accent_box": (0.0, top, width, ACCENT_THICKNESS),
        "advances": list(zip(TEXT, [p.x_advance for p in positions], advances)),
        "overshoot_bottom": -ink_bottom,
    }


def svg_standalone(w) -> str:
    vx, vy, vw, vh = w["vb"]
    scale = DISPLAY_WIDTH_PX / vw
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{fmt(vx)} {fmt(vy)} {fmt(vw)} {fmt(vh)}" '
        f'width="{DISPLAY_WIDTH_PX}" height="{fmt(vh * scale)}" role="img" aria-label="SYRAZEN">',
        "  <title>SYRAZEN</title>",
        "  <!-- Proposed wordmark: Archivo (OFL) wdth 125 / wght 800, outlined; tools/build_wordmark.py -->",
        f'  <g id="wm-letters" fill="{INK}">',
    ]
    for ch, d in w["letters"]:
        lines.append(f'    <path id="wm-{ch}" d="{d}"/>')
    lines.append("  </g>")
    lines.append(f'  <path id="wm-accent" fill="{VERMILION}" d="{w["accent_d"]}"/>')
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def svg_inline(w, indent: str) -> str:
    """Copy for compositions/lockup.html: lk- prefixed ids, masks for the reveal animation."""
    vx, vy, vw, vh = w["vb"]
    cap = w["cap"]
    ax, ay, aw, ah = w["accent_box"]
    pad = 60
    # Letters mask: everything above baseline + bottom overshoot. Letters rise from below it.
    clip_bottom = cap + w["overshoot_bottom"] + 0.5
    out = [
        f'<svg id="lk-wm" viewBox="{fmt(vx)} {fmt(vy)} {fmt(vw)} {fmt(vh)}" role="img" aria-label="SYRAZEN">',
        "  <defs>",
        '    <clipPath id="lk-wm-clip" clipPathUnits="userSpaceOnUse">',
        f'      <rect x="{fmt(-pad)}" y="{fmt(vy - pad)}" width="{fmt(vw + 2 * pad)}" height="{fmt(clip_bottom - vy + pad)}"/>',
        "    </clipPath>",
        '    <clipPath id="lk-wm-accent-clip" clipPathUnits="userSpaceOnUse">',
        f'      <rect x="{fmt(ax)}" y="{fmt(ay - 1)}" width="{fmt(aw)}" height="{fmt(ah + 2)}"/>',
        "    </clipPath>",
        "  </defs>",
        f'  <g id="lk-wm-letters" clip-path="url(#lk-wm-clip)" fill="{INK}">',
    ]
    for ch, d in w["letters"]:
        out.append(f'    <path id="lk-wm-{ch}" d="{d}"/>')
    out += [
        "  </g>",
        '  <g clip-path="url(#lk-wm-accent-clip)">',
        f'    <path id="lk-wm-accent" fill="{VERMILION}" d="{w["accent_d"]}"/>',
        "  </g>",
        "</svg>",
    ]
    return "\n".join(indent + line for line in out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inline", action="store_true", help="refresh the inline copy in compositions/lockup.html")
    args = ap.parse_args()

    w = build()
    OUT_SVG.parent.mkdir(parents=True, exist_ok=True)
    OUT_SVG.write_text(svg_standalone(w), encoding="utf-8")

    vx, vy, vw, vh = w["vb"]
    scale = DISPLAY_WIDTH_PX / vw
    print(f"wrote {OUT_SVG.relative_to(ROOT)}")
    print(f"viewBox {fmt(vx)} {fmt(vy)} {fmt(vw)} {fmt(vh)} (font units, 1000/em)")
    print(f"SVG size at display width: {DISPLAY_WIDTH_PX} x {vh * scale:.1f} px (scale {scale:.5f} px/unit)")
    print(
        f"cap height {w['cap']:.1f}u = {w['cap'] * scale:.1f}px; baseline at viewBox y {w['cap']:.1f} "
        f"= {(w['cap'] - vy) * scale:.1f}px from SVG top"
    )
    ax, ay, aw, ah = w["accent_box"]
    print(
        f"accent: top {(ay - vy) * scale:.1f}px, thickness {ah * scale:.1f}px from SVG top; "
        f"ends cut at Z diagonal slope {w['slope']:.4f} (slant {w['slant']:.1f}u = {w['slant'] * scale:.1f}px)"
    )
    print("per-letter advances (font units): glyph hb_advance(kerned) -> set advance (incl. tracking + pair)")
    for ch, hb_adv, adv in w["advances"]:
        print(f"  {ch}: {hb_adv:4d} -> {adv:4d}  ({adv * scale:.1f}px)")

    if args.inline:
        html = LOCKUP_HTML.read_text(encoding="utf-8")
        pat = re.compile(r"(?P<indent>[ \t]*)<!-- @wordmark:begin[^>]*-->.*?<!-- @wordmark:end -->", re.S)
        m = pat.search(html)
        if not m:
            sys.exit("lockup.html: @wordmark:begin/@wordmark:end markers not found")
        ind = m.group("indent")
        block = (
            f"{ind}<!-- @wordmark:begin (generated by tools/build_wordmark.py --inline; do not hand-edit) -->\n"
            f"{svg_inline(w, ind)}\n{ind}<!-- @wordmark:end -->"
        )
        html = html[: m.start()] + block + html[m.end() :]
        LOCKUP_HTML.write_text(html, encoding="utf-8")
        print(f"refreshed inline wordmark in {LOCKUP_HTML.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
