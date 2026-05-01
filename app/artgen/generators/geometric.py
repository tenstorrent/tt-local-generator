"""
Geometric generator — abstract tiled/layered SVG geometry.

Styles range from Mondrian-style grids to recursive fractals to circuit-board
trace patterns. Palette is either a built-in named scheme or drawn from the
landscape palette system.
"""

from __future__ import annotations

from artgen import ArtGenerator, register

SVG_W = 600
SVG_H = 600

_STYLE_PROMPTS = {
    "mondrian": (
        "Divide the canvas with horizontal and vertical lines into irregular rectangles. "
        "Fill some rectangles with bold primary-adjacent colors from the palette, leave others "
        "as the background. Lines are thick (2-4px). Inspired by De Stijl / Mondrian."
    ),
    "circuit": (
        "Draw a circuit-board trace pattern: horizontal and vertical wire lines that meet at "
        "90° angles, with small filled circles (pads) at junctions. Lines are thin (1-2px). "
        "Add 3-5 larger rectangular 'chip' outlines. Feels like a PCB layout."
    ),
    "recursive": (
        "Draw a recursive geometric subdivision: start with the full canvas, split it into "
        "regions, then split those regions again 2-3 levels deep. Each region gets a slightly "
        "different opacity or shade of the palette. Triangles, rectangles, or hexagons."
    ),
    "weave": (
        "Draw an interlocking geometric weave: two sets of parallel diagonal lines crossing "
        "at ~45°, creating a lattice. Where lines cross, use intersection dots. "
        "Background shows through the gaps. Feels like a technical textile or isometric grid."
    ),
}

_COMPLEXITY_HINTS = {
    "low": "Keep it minimal — fewer elements, more whitespace, bold shapes.",
    "high": "Dense, intricate — many small elements, high detail, complex overlaps.",
}

_NAMED_PALETTES = {
    "teal": {
        "bg": "#0F2A35", "a": "#4FD1C5", "b": "#81E6D9", "c": "#EC96B8",
        "d": "#F4C471", "line": "#1A3C47",
    },
    "mono": {
        "bg": "#0A0A0A", "a": "#E8E8E8", "b": "#AAAAAA", "c": "#666666",
        "d": "#333333", "line": "#1A1A1A",
    },
    "ember": {
        "bg": "#1A0000", "a": "#FF6B35", "b": "#CC2200", "c": "#FF8800",
        "d": "#FFD700", "line": "#300000",
    },
    "forest": {
        "bg": "#0A1A0A", "a": "#27AE60", "b": "#4CAF7D", "c": "#C9B46E",
        "d": "#8B3A3A", "line": "#0F250F",
    },
}


def _build_prompt(style: str, palette_name: str, complexity: str) -> str:
    pal = _NAMED_PALETTES.get(palette_name, _NAMED_PALETTES["teal"])
    w, h = SVG_W, SVG_H
    style_desc = _STYLE_PROMPTS[style]
    complexity_hint = _COMPLEXITY_HINTS[complexity]

    return (
        f"Generate an abstract geometric SVG ({w}×{h}px).\n\n"
        f"STYLE: {style_desc}\n\n"
        f"COMPLEXITY: {complexity_hint}\n\n"
        f"PALETTE (use ONLY these):\n"
        f"  background={pal['bg']}\n"
        f"  accent-a={pal['a']}  accent-b={pal['b']}  accent-c={pal['c']}  accent-d={pal['d']}\n"
        f"  line/stroke={pal['line']}\n\n"
        f"TECHNIQUE:\n"
        f"  - Use fill opacity 0.05-0.20 on overlapping regions to show depth through layers\n"
        f"  - Accent colors may appear solid or semi-transparent\n"
        f"  - Lines/strokes use the line color at opacity 0.3-0.8\n"
        f"  - Small filled circles (r=2-4) mark junction/pivot points\n\n"
        f"RULES:\n"
        f"  - SVG root: <svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{w}\" height=\"{h}\">\n"
        f"  - No <text>, no <image>, no <use>, no external hrefs\n"
        f"  - Start with <rect width='{w}' height='{h}' fill='{pal['bg']}'/> as background\n"
        f"  - Output ONLY the complete SVG, no explanation, no markdown."
    )


@register
class GeometricGenerator(ArtGenerator):
    name = "geometric"
    description = "Abstract tiled SVG geometry: Mondrian, circuit-board, recursive, weave"
    output_ext = ".svg"

    def add_args(self, parser) -> None:
        parser.add_argument(
            "--style", choices=list(_STYLE_PROMPTS), default="mondrian",
            help="Geometric style (default: mondrian)",
        )
        parser.add_argument(
            "--geo-palette", choices=list(_NAMED_PALETTES), default="teal",
            dest="geo_palette",
            help="Color palette (default: teal)",
        )
        parser.add_argument(
            "--complexity", choices=list(_COMPLEXITY_HINTS), default="low",
            help="Detail level (default: low)",
        )

    def build_prompt(self, args) -> str:
        return _build_prompt(
            getattr(args, "style", "mondrian"),
            getattr(args, "geo_palette", "teal"),
            getattr(args, "complexity", "low"),
        )

    def parse_output(self, raw: str, args) -> str:
        import re
        import artgen as _artgen
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        cleaned = re.sub(r"```\w*\s*|```", "", cleaned).strip()
        cleaned = _artgen.repair_svg(cleaned)
        m = re.search(r"(<svg\b[^>]*?>.*?</svg>)", cleaned, re.DOTALL | re.IGNORECASE)
        if not m:
            raise ValueError("LLM response did not contain valid SVG markup")
        return m.group(1)
