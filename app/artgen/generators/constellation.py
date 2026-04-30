"""
Constellation generator — invented star charts with named stars, connecting
lines, and optional mythological lore fragments.
"""

from __future__ import annotations

from artgen import ArtGenerator, register

SVG_W = 800
SVG_H = 500

_CULTURE_PROMPTS = {
    "invented": (
        "Invent names that sound like no real language — short, consonant-heavy, "
        "memorable. Examples: Veyra, Caelun, Thresh, Mira-Oss, Dunket."
    ),
    "norse": (
        "Use Old Norse naming conventions — compound words, references to nature, "
        "battle, or fate. Examples: Skáldr, Fenrir's Eye, Njord's Lamp, Bifröst Tail."
    ),
    "greek": (
        "Use Hellenized names with mythological resonance — heroes, titans, animals. "
        "Examples: Arktouros Minor, Selene's Veil, Kairos, Eridanos."
    ),
    "random": (
        "Mix naming styles freely — some invented, some with Latin roots, some that "
        "feel ancient but unnamed. Variety is key."
    ),
}


def _build_prompt(culture: str, star_count: int, include_lore: bool) -> str:
    culture_hint = _CULTURE_PROMPTS.get(culture, _CULTURE_PROMPTS["random"])
    w, h = SVG_W, SVG_H
    bg_stars = star_count * 3

    lore_instruction = ""
    if include_lore:
        lore_instruction = (
            "\nLORE: After </svg>, write 2-4 sentences of constellation mythology as an "
            "XML comment: <!-- LORE: ... -->  Describe what the constellation represents "
            "in the culture's cosmology. Do not add lore inside the SVG itself.\n"
        )

    return (
        f"Generate an invented star chart SVG ({w}×{h}px).\n\n"
        f"BACKGROUND:\n"
        f"  <rect width='{w}' height='{h}' fill='#000B1E'/>\n"
        f"  Scatter {bg_stars}-{bg_stars+20} small background stars:\n"
        f"    <circle r='0.5'-'1.2', fill='#FFFFFF', opacity='0.2'-'0.5'\n\n"
        f"CONSTELLATION ({star_count} named stars):\n"
        f"  - Place {star_count} principal stars as <circle> elements:\n"
        f"      bright anchor stars: r=3-5, fill='#E8F0F2' or '#81E6D9'\n"
        f"      secondary stars: r=1.5-3, fill='#4FD1C5' or '#B0C4DE'\n"
        f"  - Connect stars with <line> elements, stroke='#4FD1C5', stroke-width='0.8', opacity='0.4'\n"
        f"    Draw the connecting pattern to suggest a recognisable shape (animal, figure, object)\n"
        f"  - Label each named star with <text> near the circle:\n"
        f"      font-family='monospace' font-size='8' fill='#4FD1C5' opacity='0.85'\n\n"
        f"NAMING CONVENTION:\n  {culture_hint}\n\n"
        f"CONSTELLATION NAME:\n"
        f"  Place the constellation's full name near the bottom:\n"
        f"    <text> font-family='monospace' font-size='11' fill='#607D8B' text-anchor='middle'\n"
        f"    Format: ✦ Name ✦\n\n"
        f"PALETTE: #000B1E (bg) · #E8F0F2 (bright star) · #81E6D9 · #4FD1C5 (teal) · "
        f"#B0C4DE · #607D8B (dim text) — no other colors\n\n"
        f"RULES:\n"
        f"  - SVG root: <svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{w}\" height=\"{h}\">\n"
        f"  - No <image>, no <use>, no external hrefs\n"
        f"  - Output the complete SVG first, then lore comment if requested.\n"
        f"  - No markdown, no explanation outside the SVG/comment."
        + lore_instruction
    )


@register
class ConstellationGenerator(ArtGenerator):
    name = "constellation"
    description = "Invented star chart SVG: named stars, connecting lines, optional lore"
    output_ext = ".svg"

    def add_args(self, parser) -> None:
        parser.add_argument(
            "--culture", choices=list(_CULTURE_PROMPTS), default="invented",
            help="Star-naming culture/style (default: invented)",
        )
        parser.add_argument(
            "--stars", type=int, default=8, metavar="N",
            help="Number of named constellation stars (default: 8)",
        )
        parser.add_argument(
            "--lore", action="store_true", default=False,
            help="Append mythological lore as an XML comment after </svg>",
        )

    def build_prompt(self, args) -> str:
        return _build_prompt(
            getattr(args, "culture", "invented"),
            getattr(args, "stars", 8),
            getattr(args, "lore", False),
        )

    def parse_output(self, raw: str, args) -> str:
        import re
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        cleaned = re.sub(r"```\w*\s*|```", "", cleaned).strip()
        m = re.search(r"(<svg\b.*?</svg>.*?)$", cleaned, re.DOTALL | re.IGNORECASE)
        if not m:
            raise ValueError("LLM response did not contain valid SVG markup")
        return m.group(1).strip()
