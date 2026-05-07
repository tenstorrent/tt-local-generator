"""
Skyline generator — procedural city silhouette SVG.

LLM generates varied building heights, lit windows, antenna clusters, and a
night/dusk/day sky to create a unique urban panorama each time.
"""

from __future__ import annotations

from artgen import ArtGenerator, register

SVG_W = 800
SVG_H = 450

_ERA_PALETTES = {
    "modern": {
        "sky_top": "#000B1E", "sky_mid": "#0A2040", "sky_bottom": "#1A3A60",
        "building_far": "#061828", "building_mid": "#0A2438", "building_near": "#0F2E44",
        "window": "#F4C471", "window_dim": "#3A2A0A", "antenna": "#1A3C54",
        "ground": "#030A10", "moon": "#D4E8FF",
        "adjective": "sleek, metropolitan, late-night",
    },
    "retro": {
        "sky_top": "#0A0018", "sky_mid": "#1A0040", "sky_bottom": "#3A0060",
        "building_far": "#0A0025", "building_mid": "#150035", "building_near": "#200045",
        "window": "#FF9900", "window_dim": "#3A1F00", "antenna": "#300055",
        "ground": "#050010", "moon": "#FFE0A0",
        "adjective": "neon-lit, 1970s, retrofuturistic",
    },
    "futuristic": {
        "sky_top": "#000A08", "sky_mid": "#001A15", "sky_bottom": "#003025",
        "building_far": "#001810", "building_mid": "#002818", "building_near": "#003820",
        "window": "#4FD1C5", "window_dim": "#0A2A25", "antenna": "#004530",
        "ground": "#000808", "moon": "#80FFF0",
        "adjective": "biopunk, overgrown towers, teal-lit",
    },
}

_DENSITY_DESC = {
    "low": "8-12 buildings with wide gaps between them, open skyline",
    "medium": "16-22 buildings, varied spacing, some clusters",
    "high": "28-38 buildings tightly packed, dense urban canyon",
}

_SKY_DESC = {
    "day": "bright gradient from pale blue (#87CEEB) at top to white (#FFFFFF) near horizon, no moon",
    "dusk": "warm gradient from deep orange (#CC4400) through purple (#6B0080) to dark navy (#0A0030)",
    "night": "deep navy gradient with moon disc and scattered stars",
}


def _build_prompt(era: str, density: str, sky: str) -> str:
    pal = _ERA_PALETTES[era]
    w, h = SVG_W, SVG_H
    ground_y = int(h * 0.88)

    return (
        f"Generate a city skyline SVG ({w}×{h}px).\n"
        f"Era/mood: {pal['adjective']}\n\n"
        f"SKY: {_SKY_DESC[sky]}\n"
        f"  Use a linearGradient id='sky' (vertical, 2-3 stops) for the sky background rect.\n\n"
        f"BUILDINGS ({_DENSITY_DESC[density]}):\n"
        f"  - Draw buildings as <rect> elements spanning from their top down to y={ground_y}\n"
        f"  - Vary heights so tallest reaches y=20-60, shortest y={int(h*0.55)}-{int(h*0.70)}\n"
        f"  - Use 3 depth layers:\n"
        f"      far (y_top > {int(h*0.40)}): fill={pal['building_far']}, opacity 0.6\n"
        f"      mid: fill={pal['building_mid']}, opacity 0.8\n"
        f"      near: fill={pal['building_near']}, full opacity\n"
        f"  - Add lit windows: small <rect> elements (3-5px wide, 2-4px tall) fill={pal['window']}\n"
        f"    Scatter 2-8 lit windows per building, a few dark ones fill={pal['window_dim']}\n"
        f"  - Tall buildings get antenna/spire: thin <line> or <rect> at top, fill={pal['antenna']}\n\n"
        f"GROUND: <rect x='0' y='{ground_y}' width='{w}' height='{h-ground_y}' fill={pal['ground']}>\n\n"
        f"PALETTE — only these colors:\n"
        f"  sky fills from gradient · buildings: {pal['building_far']} {pal['building_mid']} {pal['building_near']}\n"
        f"  windows: {pal['window']} (lit) {pal['window_dim']} (dim) · ground: {pal['ground']}\n"
        f"  {'moon: ' + pal['moon'] if sky == 'night' else ''}\n\n"
        f"RULES:\n"
        f"  - No <text>, no <image>, no <use>, no external hrefs\n"
        f"  - SVG root: <svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{w}\" height=\"{h}\">\n"
        f"  - Output ONLY the complete SVG, no explanation, no markdown."
    )


@register
class SkylineGenerator(ArtGenerator):
    name = "skyline"
    description = "Procedural city skyline SVG: buildings, lit windows, antennas, night sky"
    output_ext = ".svg"

    def add_args(self, parser) -> None:
        parser.add_argument(
            "--era", choices=list(_ERA_PALETTES), default="modern",
            help="City era/aesthetic (default: modern)",
        )
        parser.add_argument(
            "--density", choices=list(_DENSITY_DESC), default="medium",
            help="Building density (default: medium)",
        )
        parser.add_argument(
            "--sky", choices=list(_SKY_DESC), default="night",
            help="Sky condition (default: night)",
        )

    def build_prompt(self, args) -> str:
        return _build_prompt(
            getattr(args, "era", "modern"),
            getattr(args, "density", "medium"),
            getattr(args, "sky", "night"),
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
        svg = m.group(1)
        if not any(t in svg for t in ("<rect", "<polygon", "<path")):
            raise ValueError("SVG appears to have no visible elements")
        return svg
