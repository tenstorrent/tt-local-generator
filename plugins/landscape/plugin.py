"""
Landscape generator — layered SVG landscape with sky, mountains, atmosphere.

Ported from tt-agents/06_landscape_svg.py with all palette, prompt-building,
SVG parsing, and glitch post-processing logic preserved.
"""

from __future__ import annotations

import random as _random
import re
from pathlib import Path

from artgen import ArtGenerator

# ── Canvas ────────────────────────────────────────────────────────────────────

SVG_W = 800
SVG_H = 450

# ── Palettes ──────────────────────────────────────────────────────────────────

PALETTES: dict[str, dict[str, str]] = {
    "sunset": {
        "name": "Sunset", "adjective": "warm, dramatic, cinematic",
        "sky_top": "#1A0A2E", "sky_mid": "#8B1A4A", "sky_bottom": "#FF6B35",
        "sun": "#FFD700", "cloud": "#FF9966", "atmosphere": "#FF4500",
        "mountain_far": "#4A2040", "mountain_mid": "#2A1020",
        "mountain_near": "#1A0A15", "ground": "#0F0805",
    },
    "blue": {
        "name": "Midnight Blue", "adjective": "cool, serene, nocturnal",
        "sky_top": "#000B1E", "sky_mid": "#0A2A5E", "sky_bottom": "#1A6090",
        "sun": "#E8F4FF", "cloud": "#2A4A7A", "atmosphere": "#4682B4",
        "mountain_far": "#0A2A4A", "mountain_mid": "#061828",
        "mountain_near": "#040F1A", "ground": "#030A10",
    },
    "purple": {
        "name": "Violet Dusk", "adjective": "mystical, dreamy, otherworldly",
        "sky_top": "#120020", "sky_mid": "#3B1060", "sky_bottom": "#7B2D8B",
        "sun": "#F0C0FF", "cloud": "#9A4DBB", "atmosphere": "#CC66FF",
        "mountain_far": "#5A2080", "mountain_mid": "#3A1050",
        "mountain_near": "#1A0528", "ground": "#0A0215",
    },
    "red": {
        "name": "Ember", "adjective": "intense, volcanic, apocalyptic",
        "sky_top": "#1A0000", "sky_mid": "#6B0000", "sky_bottom": "#CC2200",
        "sun": "#FF8800", "cloud": "#8B2000", "atmosphere": "#FF4400",
        "mountain_far": "#4A0A00", "mountain_mid": "#2A0500",
        "mountain_near": "#150200", "ground": "#0A0100",
    },
    "orange": {
        "name": "Golden Hour", "adjective": "warm, golden, autumnal",
        "sky_top": "#1A0F00", "sky_mid": "#8B4500", "sky_bottom": "#FFB300",
        "sun": "#FFEE00", "cloud": "#CC7722", "atmosphere": "#FF8C00",
        "mountain_far": "#4A2800", "mountain_mid": "#2A1500",
        "mountain_near": "#150A00", "ground": "#0A0500",
    },
}

# ── Prompt builder ────────────────────────────────────────────────────────────


def _build_prompt(
    palette: dict, has_mountains: bool, has_clouds: bool, has_stars: bool
) -> str:
    p = palette
    w, h = SVG_W, SVG_H
    horizon_y = int(h * 0.50)
    ground_y = int(h * 0.76)

    features: list[str] = [
        f"Sky: full-background <rect> using a 3-stop linearGradient id='sky' "
        f"({p['sky_top']} → {p['sky_mid']} → {p['sky_bottom']}, vertical)",
    ]
    if has_stars:
        features.append(
            f"Stars: 35-50 <circle> elements, r=0.5-2, fill={p['sun']}, "
            f"opacity 0.5-0.9, scattered above y={int(h*0.48)}"
        )
    features.append(
        f"Atmospheric glow: one wide <ellipse> centered near y={horizon_y}, "
        f"fill={p['atmosphere']}, opacity 0.15-0.30, no stroke"
    )
    # Sun before mountains so peaks occlude it
    features.append(
        f"Sun or moon: <circle> r=28-44, center near x={int(w*0.25)}-{int(w*0.75)}, "
        f"y={int(h*0.12)}-{int(h*0.42)}, fill={p['sun']} "
        f"[MUST appear before mountain layers so peaks occlude it]"
    )
    if has_clouds:
        features.append(
            f"Clouds: 4-6 groups, each group = 3-5 overlapping <ellipse> in {p['cloud']}, "
            f"placed at varied x, y={int(h*0.10)}-{int(h*0.40)}"
        )
    if has_mountains:
        features += [
            f"Far mountains: <polygon> peaks at y={int(h*0.28)}-{int(h*0.48)}, "
            f"fill={p['mountain_far']} — must start 0,{h} and end {w},{h}",
            f"Mid mountains: <polygon> peaks at y={int(h*0.40)}-{int(h*0.58)}, "
            f"fill={p['mountain_mid']} — must start 0,{h} and end {w},{h}",
            f"Near mountains: <polygon> peaks at y={int(h*0.52)}-{int(h*0.68)}, "
            f"fill={p['mountain_near']} — must start 0,{h} and end {w},{h}",
        ]
    features.append(
        f"Ground: <rect x='0' y='{ground_y}' width='{w}' height='{h - ground_y}' "
        f"fill={p['ground']}>"
    )

    feature_block = "\n".join(f"  {i+1}. {f}" for i, f in enumerate(features))

    mountain_rule = (
        "\nMOUNTAIN POLYGON RULE: Each polygon must span the full canvas width. "
        f"The points string must begin with '0,{h}' and end with '{w},{h}' "
        "so the shape closes along the bottom edge. "
        "Add 8-12 irregular peaks between those anchors for natural ridgelines.\n"
    ) if has_mountains else ""

    defs_hint = (
        "Required in <defs>:\n"
        "  - linearGradient id='sky' (gradientUnits='objectBoundingBox', "
        "x1='0' y1='0' x2='0' y2='1') with 3 stops\n"
        + ("  - radialGradient id='glow' (cx=0.5 cy=0.5 r=0.5) for atmospheric glow\n"
           if has_mountains else "")
    )

    return (
        f"Generate a layered landscape SVG ({w}×{h}px).\n"
        f"Mood: {p['adjective']}\n\n"
        f"PALETTE (use ONLY these colors):\n"
        f"  sky_top={p['sky_top']}  sky_mid={p['sky_mid']}  sky_bottom={p['sky_bottom']}\n"
        f"  sun/moon={p['sun']}  atmosphere={p['atmosphere']}  ground={p['ground']}\n"
        + (f"  clouds={p['cloud']}\n" if has_clouds else "")
        + (f"  mountains: far={p['mountain_far']}  mid={p['mountain_mid']}  near={p['mountain_near']}\n"
           if has_mountains else "")
        + f"\nLAYERS (back to front):\n{feature_block}\n"
        + mountain_rule
        + f"\n{defs_hint}\n"
        f"RULES:\n"
        f"  - Use ONLY colors from the PALETTE above — no invented colors\n"
        f"  - No <text>, no <image>, no <use>, no external hrefs\n"
        f"  - Sun/moon MUST be drawn before mountain polygons in the SVG so it\n"
        f"    appears behind the mountains — peaks occlude it, it does NOT float on top\n"
        f"  - Mountain polygons must close at the canvas bottom (start/end at y={h})\n"
        f"    so there are no floating ridges or gaps above the ground rect\n"
        f"  - SVG root: <svg xmlns=\"http://www.w3.org/2000/svg\" "
        f"width=\"{w}\" height=\"{h}\">\n\n"
        f"Output ONLY the complete SVG, starting with <svg and ending with </svg>. "
        f"No explanation, no markdown, no comments."
    )


# ── Small-tier helpers (split by layer, per the design doc's Section 6) ──────
#
# EXPERIMENTAL / not yet validated to actually help — the large single-shot
# prompt above was live-tested clean (no repetition, valid XML) with
# --mountains --clouds --stars against the same constrained model this is
# meant to defend against. This split exists for a case not exercised live;
# if a before/after comparison doesn't show an improvement, it should not
# be relied on — see the design doc's Section 6 discussion.


def _build_background_prompt(p: dict, has_stars: bool) -> str:
    """Small tier, pass 1 — sky gradient, atmosphere glow, sun/moon, and
    stars if enabled. Bare fragment elements only."""
    w, h = SVG_W, SVG_H
    horizon_y = int(h * 0.50)
    star_hint = (
        f"  - Stars: 35-50 <circle> elements, r=0.5-2, fill={p['sun']}, "
        f"opacity 0.5-0.9, scattered above y={int(h*0.48)}\n"
        if has_stars else ""
    )
    return (
        f"Generate ONLY the sky background of a layered landscape SVG "
        f"({w}x{h}px) as bare SVG elements — no <svg> root tag, no clouds, "
        f"no mountains, no ground.\n\n"
        f"PALETTE (use ONLY these colors):\n"
        f"  sky_top={p['sky_top']}  sky_mid={p['sky_mid']}  sky_bottom={p['sky_bottom']}\n"
        f"  sun/moon={p['sun']}  atmosphere={p['atmosphere']}\n\n"
        f"ELEMENTS (in this order):\n"
        f"  - <defs>: linearGradient id='sky' (gradientUnits='objectBoundingBox', "
        f"x1='0' y1='0' x2='0' y2='1') with 3 stops {p['sky_top']} -> {p['sky_mid']} "
        f"-> {p['sky_bottom']}\n"
        f"  - Full-canvas <rect> using the sky gradient\n"
        + star_hint
        + f"  - Atmospheric glow: one wide <ellipse> centered near y={horizon_y}, "
        f"fill={p['atmosphere']}, opacity 0.15-0.30, no stroke\n"
        f"  - Sun or moon: <circle> r=28-44, center near x={int(w*0.25)}-{int(w*0.75)}, "
        f"y={int(h*0.12)}-{int(h*0.42)}, fill={p['sun']}\n\n"
        f"RULES:\n"
        f"  - Use ONLY the palette colors above\n"
        f"  - Output ONLY these elements in the order listed — no <svg> tag, "
        f"no clouds, no mountains, no ground, no explanation, no markdown."
    )


def _build_clouds_prompt(p: dict) -> str:
    """Small tier — clouds only, one call, bare fragment elements."""
    w, h = SVG_W, SVG_H
    return (
        f"Generate ONLY cloud formations for a layered landscape SVG "
        f"({w}x{h}px) as bare SVG elements — no <svg> root tag, no sky, "
        f"no mountains, no ground.\n\n"
        f"Clouds: 4-6 groups, each group = 3-5 overlapping <ellipse> in "
        f"{p['cloud']}, placed at varied x, y={int(h*0.10)}-{int(h*0.40)}\n\n"
        f"RULES:\n"
        f"  - Use ONLY {p['cloud']} for fill\n"
        f"  - Output ONLY the cloud <ellipse> elements — no <svg> tag, no "
        f"sky, no mountains, no ground, no explanation, no markdown."
    )


def _build_mountains_prompt(p: dict) -> str:
    """Small tier — the three mountain ridge layers, one call, bare
    fragment elements. Kept as a single call (unlike skyline's per-layer
    split) — three polygons is not a large-count risk; the risk here is
    the exact-coordinate closure rule, which doesn't benefit from
    splitting across calls."""
    w, h = SVG_W, SVG_H
    return (
        f"Generate ONLY three layers of mountain ridges for a layered "
        f"landscape SVG ({w}x{h}px) as bare SVG <polygon> elements — no "
        f"<svg> root tag, no sky, no clouds, no ground.\n\n"
        f"Far mountains: <polygon> peaks at y={int(h*0.28)}-{int(h*0.48)}, "
        f"fill={p['mountain_far']} — must start 0,{h} and end {w},{h}\n"
        f"Mid mountains: <polygon> peaks at y={int(h*0.40)}-{int(h*0.58)}, "
        f"fill={p['mountain_mid']} — must start 0,{h} and end {w},{h}\n"
        f"Near mountains: <polygon> peaks at y={int(h*0.52)}-{int(h*0.68)}, "
        f"fill={p['mountain_near']} — must start 0,{h} and end {w},{h}\n\n"
        f"MOUNTAIN POLYGON RULE: Each polygon must span the full canvas "
        f"width. The points string must begin with '0,{h}' and end with "
        f"'{w},{h}' so the shape closes along the bottom edge. Add 8-12 "
        f"irregular peaks between those anchors for natural ridgelines.\n\n"
        f"RULES:\n"
        f"  - Output ONLY the three <polygon> elements, far to near — no "
        f"<svg> tag, no sky, no clouds, no ground, no explanation, no markdown."
    )


def _local_ground_svg(p: dict, w: int, h: int) -> str:
    """The ground rect is fully determined by the palette and fixed
    geometry — no creative content, so no LLM call for it at all."""
    ground_y = int(h * 0.76)
    return (
        f'<rect x="0" y="{ground_y}" width="{w}" height="{h - ground_y}" '
        f'fill="{p["ground"]}"/>'
    )


def _clean_fragment(raw: str) -> str:
    """Strip think-blocks/fences from a fragment response and, if the model
    wrapped it in a full <svg>...</svg> despite being asked not to, unwrap
    it to just the inner elements."""
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    cleaned = re.sub(r"```(?:svg|xml)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"```\s*", "", cleaned.strip())
    m = re.search(r"<svg\b[^>]*>(.*)</svg>", cleaned, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return cleaned.strip()


# ── SVG parser ────────────────────────────────────────────────────────────────


def _parse_svg(raw: str) -> str | None:
    import artgen as _artgen
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    cleaned = re.sub(r"```(?:svg|xml)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"```\s*", "", cleaned.strip())
    # Attempt truncation recovery before regex search (handles missing </svg>)
    cleaned = _artgen.repair_svg(cleaned)
    m = re.search(r"(<svg\b[^>]*?>.*?</svg>)", cleaned, re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    svg = m.group(1)
    try:
        import xml.etree.ElementTree as ET
        ET.fromstring(svg)
        return svg
    except Exception:
        if any(tag in svg for tag in ("<rect", "<path", "<polygon", "<circle", "<ellipse")):
            return svg
        return None


# ── Glitch post-processor ─────────────────────────────────────────────────────

_GLITCH_WRONG_COLORS = [
    "#FF00FF", "#00FFFF", "#FFFF00", "#FF0044", "#00FF88", "#FF6600",
]
_GLITCH_TEXT_CHARS = "▓░█▒╫╬╪┼┴┬├│╣╠═╡╚╔╗╝▄▀■□▪▫◆◇"


def _glitch_corrupt_colors(svg: str, rng: _random.Random) -> tuple[str, str]:
    hex_pat = re.compile(r"#[0-9A-Fa-f]{6}")
    unique = list(dict.fromkeys(hex_pat.findall(svg)))
    if len(unique) < 2:
        return svg, "corrupt: too few colors to swap"
    n = max(1, len(unique) // 2)
    for color in rng.sample(unique, n):
        svg = svg.replace(color, rng.choice(_GLITCH_WRONG_COLORS))
    return svg, f"corrupt: replaced {n}/{len(unique)} colors with wrong palette"


def _glitch_sun_bleed(svg: str) -> tuple[str, str]:
    pat = re.compile(r'<circle\b[^>]*r="(\d+)"[^>]*/>', re.DOTALL)
    best_match, best_r = None, 0
    for m in pat.finditer(svg):
        r = int(m.group(1))
        if r > best_r:
            best_r, best_match = r, m
    if best_match is None or best_r < 20:
        return svg, "sun-bleed: no sun/moon found"
    el = best_match.group(0)
    svg = svg[: best_match.start()] + svg[best_match.end():]
    svg = svg.replace("</svg>", f"  {el}\n</svg>")
    return svg, f"sun-bleed: sun (r={best_r}) moved above all mountains"


def _glitch_flip(svg: str) -> tuple[str, str]:
    m = re.match(r"(<svg\b[^>]*>)(.*)(</svg>\s*$)", svg, re.DOTALL)
    if not m:
        return svg, "flip: parse failed"
    opening, body, closing = m.groups()
    flipped = (
        f"{opening}\n"
        f'<g transform="scale(1,-1) translate(0,-{SVG_H})">{body}</g>\n'
        f"{closing}"
    )
    return flipped, "flip: scene vertically inverted"


def _glitch_ghost_text(svg: str, rng: _random.Random) -> tuple[str, str]:
    frags = []
    for _ in range(rng.randint(6, 11)):
        x = rng.randint(10, SVG_W - 90)
        y = rng.randint(20, SVG_H - 20)
        text = "".join(rng.choices(_GLITCH_TEXT_CHARS, k=rng.randint(4, 14)))
        fill = rng.choice(_GLITCH_WRONG_COLORS)
        op = round(rng.uniform(0.25, 0.75), 2)
        size = rng.choice([7, 9, 11, 13, 16])
        frags.append(
            f'  <text x="{x}" y="{y}" font-family="monospace" font-size="{size}" '
            f'fill="{fill}" opacity="{op}">{text}</text>'
        )
    svg = svg.replace("</svg>", "\n".join(frags) + "\n</svg>")
    return svg, f"ghost: injected {len(frags)} garbled text elements"


def _apply_glitch(svg: str, seed: int | None = None) -> tuple[str, list[str]]:
    rng = _random.Random(seed)
    log: list[str] = []
    for fn in (
        lambda s: _glitch_corrupt_colors(s, rng),
        _glitch_sun_bleed,
        _glitch_flip,
        lambda s: _glitch_ghost_text(s, rng),
    ):
        svg, msg = fn(svg)
        log.append(msg)
    return svg, log


# ── Generator class ───────────────────────────────────────────────────────────


class LandscapeGenerator(ArtGenerator):
    name = "landscape"
    description = "Layered SVG landscape: sky gradients, mountains, atmosphere, sun/moon"
    output_ext = ".svg"

    def add_args(self, parser) -> None:
        parser.add_argument(
            "--palette", default="random",
            help=f"Color palette: {', '.join(PALETTES)}, random (default: random)",
        )
        parser.add_argument(
            "--mountains", dest="mountains", action="store_true", default=True,
            help="Include layered mountain ridges (default: on)",
        )
        parser.add_argument(
            "--no-mountains", dest="mountains", action="store_false",
            help="Flat terrain without mountains",
        )
        parser.add_argument(
            "--clouds", dest="clouds", action="store_true", default=False,
            help="Add cloud formations",
        )
        parser.add_argument("--no-clouds", dest="clouds", action="store_false")
        parser.add_argument(
            "--stars", dest="stars", action="store_true", default=False,
            help="Scatter stars in the sky",
        )
        parser.add_argument("--no-stars", dest="stars", action="store_false")
        parser.add_argument(
            "--glitch", action="store_true",
            help="Post-process output with glitch effects (saves <name>_glitch.svg)",
        )
        parser.add_argument(
            "--glitch-seed", type=int, default=None,
            help="Seed for reproducible glitch (default: random)",
        )

    def build_prompt(self, args) -> str:
        key = getattr(args, "palette", "random")
        if key == "random" or key not in PALETTES:
            key = _random.choice(list(PALETTES))
        return _build_prompt(
            PALETTES[key],
            getattr(args, "mountains", True),
            getattr(args, "clouds", False),
            getattr(args, "stars", False),
        )

    def generate_artifact(self, args, call_fn) -> str:
        """large/medium tier: unchanged single-call whole-image prompt (no
        evidence of failure at those tiers, live-tested clean with
        --mountains --clouds --stars). small tier: EXPERIMENTAL split by
        layer — see the module docstring above _build_background_prompt."""
        tier = getattr(call_fn, "model_tier", "large")
        if tier == "small":
            key = getattr(args, "palette", "random")
            if key == "random" or key not in PALETTES:
                key = _random.choice(list(PALETTES))
            artifact = self._generate_small(
                call_fn, PALETTES[key],
                getattr(args, "mountains", True),
                getattr(args, "clouds", False),
                getattr(args, "stars", False),
            )
            return self.post_process(artifact, args)
        raw = call_fn(self.build_prompt(args))
        return self.post_process(self.parse_output(raw, args), args)

    def _generate_small(self, call_fn, p: dict, has_mountains: bool,
                         has_clouds: bool, has_stars: bool) -> str:
        w, h = SVG_W, SVG_H

        # Generous budgets throughout: a too-small budget previously
        # truncated every fragment mid-element (found via live before/after
        # testing) — up to 50 stars alone can run past 1200 tokens once
        # gradient defs, atmosphere, and the sun/moon are included too.
        print("[small-tier: sky background …]", flush=True)
        frags = [_clean_fragment(
            call_fn(_build_background_prompt(p, has_stars), max_tokens=2500)
        )]

        if has_clouds:
            print("[small-tier: clouds …]", flush=True)
            frags.append(_clean_fragment(
                call_fn(_build_clouds_prompt(p), max_tokens=1500)
            ))

        if has_mountains:
            print("[small-tier: mountains …]", flush=True)
            frags.append(_clean_fragment(
                call_fn(_build_mountains_prompt(p), max_tokens=2500)
            ))

        frags.append(_local_ground_svg(p, w, h))

        body = "\n".join(frags)
        assembled = f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">\n{body}\n</svg>'
        return self.parse_output(assembled, None)

    def parse_output(self, raw: str, args) -> str:
        svg = _parse_svg(raw)
        if svg is None:
            raise ValueError("LLM response did not contain valid SVG markup")
        return svg

    def post_process(self, artifact: str, args) -> str:
        if not getattr(args, "glitch", False):
            return artifact
        glitch_svg, log = _apply_glitch(artifact, seed=getattr(args, "glitch_seed", None))
        for msg in log:
            print(f"  {msg}")
        # Save glitch variant alongside the clean file
        out = Path(getattr(args, "output", None) or self.default_output())
        glitch_path = out.with_stem(out.stem + "_glitch")
        glitch_path.write_text(glitch_svg)
        print(f"[glitch SVG saved → {glitch_path.name}]")
        return artifact  # clean version goes to --output path as normal
