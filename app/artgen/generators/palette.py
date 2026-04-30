"""
Palette generator — evocative named color palettes with prose lore.

LLM invents a name, 5-8 harmonious hex colors, and a prose description of
what the palette *feels like*. Output is JSON; optionally also writes CSS
custom properties.
"""

from __future__ import annotations

import json
from pathlib import Path

from artgen import ArtGenerator, register

_MOOD_EXAMPLES = (
    "volcanic, brackish, iron winter, drowned empire, overripe summer, "
    "fever dream, cathedral dust, ocean trench, harvest moon, static electricity"
)


def _build_prompt(mood: str, count: int) -> str:
    return (
        f"Invent a named color palette.\n\n"
        f"MOOD / THEME: {mood}\n\n"
        f"OUTPUT FORMAT (strict JSON, nothing else):\n"
        f"{{\n"
        f'  "name": "Evocative palette name (2-4 words)",\n'
        f'  "colors": [\n'
        f'    {{"hex": "#RRGGBB", "role": "one-word role like background/shadow/accent/highlight"}},\n'
        f'    ... ({count} colors total)\n'
        f'  ],\n'
        f'  "lore": "2-3 sentences describing what this palette feels like — '
        f'sensory, specific, no cliché. Like describing a place or material, not an abstraction."\n'
        f"}}\n\n"
        f"RULES:\n"
        f"  - Exactly {count} colors\n"
        f"  - Colors must be harmonious but not monotonous — include darks, mids, and lights\n"
        f"  - Hex codes must be valid 6-digit: #RRGGBB\n"
        f"  - Name should be evocative, not generic ('Ocean Blues' is bad; 'Drowned Ironwork' is good)\n"
        f"  - Lore must be concrete and sensory, not abstract ('a sense of melancholy' is bad)\n"
        f"  - Output ONLY the JSON object — no markdown fences, no preamble, no explanation."
    )


def _to_css(palette_data: dict) -> str:
    name_slug = palette_data.get("name", "palette").lower().replace(" ", "-")
    lines = [f"/* {palette_data.get('name', 'Palette')} */", f":root {{"]
    for i, color in enumerate(palette_data.get("colors", []), 1):
        role = color.get("role", f"color-{i}").lower().replace(" ", "-")
        lines.append(f"  --{name_slug}-{role}: {color['hex']};")
    lines.append("}")
    return "\n".join(lines) + "\n"


@register
class PaletteGenerator(ArtGenerator):
    name = "palette"
    description = "Named color palette with evocative prose lore — outputs JSON (+ optional CSS)"
    output_ext = ".json"

    def add_args(self, parser) -> None:
        parser.add_argument(
            "--mood", default="volcanic",
            help=f'Mood/theme seed. Examples: {_MOOD_EXAMPLES} (default: volcanic)',
        )
        parser.add_argument(
            "--count", type=int, default=6, metavar="N",
            help="Number of colors in the palette (default: 6)",
        )
        parser.add_argument(
            "--export-css", action="store_true",
            help="Also write a .css file with CSS custom properties",
        )

    def build_prompt(self, args) -> str:
        return _build_prompt(
            getattr(args, "mood", "volcanic"),
            getattr(args, "count", 6),
        )

    def parse_output(self, raw: str, args) -> str:
        import re
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        cleaned = re.sub(r"```\w*\s*|```", "", cleaned).strip()
        # Find the JSON object
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            raise ValueError("LLM response did not contain a JSON object")
        raw_json = m.group(0)
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in LLM response: {e}")
        if "colors" not in data or "name" not in data:
            raise ValueError("JSON missing required fields: name, colors")
        # Normalise and pretty-print
        return json.dumps(data, indent=2, ensure_ascii=False)

    def post_process(self, artifact: str, args) -> str:
        if not getattr(args, "export_css", False):
            return artifact
        try:
            data = json.loads(artifact)
            css = _to_css(data)
            out_path = Path(getattr(args, "output", None) or self.default_output())
            css_path = out_path.with_suffix(".css")
            css_path.write_text(css)
            print(f"[CSS saved → {css_path.name}]")
        except Exception as e:
            print(f"[WARNING: could not write CSS: {e}]")
        return artifact
