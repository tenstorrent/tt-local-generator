"""
Constellation generator — invented star charts with named stars, connecting
lines, and optional mythological lore fragments.
"""

from __future__ import annotations

import random as _random
import re

from artgen import ArtGenerator

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


# ── Small-tier helpers (per the design doc's Section 6) ──────────────────────
#
# VALIDATED — unlike skyline/landscape's split (which is preventive, no
# observed failure), this one fixes a REAL failure reproduced live:
# `tt-ctl artgen constellation --culture greek --stars 20 --lore` against
# this same constrained model returned a response truncated mid-element
# (no </svg> at all — cut off inside a <text> tag's opacity attribute) at
# the CLI's flat max_tokens=4096 default. Root cause: up to 60 background
# filler stars (star_count*3 to +20) were eating the token budget the 20
# actual named stars needed. Those filler stars have zero creative
# content — they're uniformly random dim dots — so building them locally
# instead of asking the model to draw them frees the entire budget for
# the one part that's actually creative.


def _local_background_rect_svg() -> str:
    return f'<rect width="{SVG_W}" height="{SVG_H}" fill="#000B1E"/>'


def _local_background_stars_svg(star_count: int, seed: int | None = None) -> str:
    """Uniformly random dim background stars — no creative content, no
    LLM call needed. star_count*3 to +20, matching the original prompt's
    own count range."""
    rng = _random.Random(seed)
    bg_stars = star_count * 3
    count = rng.randint(bg_stars, bg_stars + 20)
    circles = []
    for _ in range(count):
        cx = rng.randint(5, SVG_W - 5)
        cy = rng.randint(5, SVG_H - 5)
        r = round(rng.uniform(0.5, 1.2), 1)
        opacity = round(rng.uniform(0.2, 0.5), 2)
        circles.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#FFFFFF" opacity="{opacity}"/>'
        )
    return "\n".join(circles)


def _build_constellation_prompt(culture: str, star_count: int, include_lore: bool) -> str:
    """Small tier — the actual constellation only (principal stars,
    connecting lines, labels, naming, lore): the one genuinely creative
    part. Bare fragment elements — no background rect, no filler stars."""
    culture_hint = _CULTURE_PROMPTS.get(culture, _CULTURE_PROMPTS["random"])
    lore_instruction = ""
    if include_lore:
        lore_instruction = (
            "\nLORE: After the elements, write 2-4 sentences of constellation "
            "mythology as an XML comment: <!-- LORE: ... -->  Describe what the "
            "constellation represents in the culture's cosmology.\n"
        )
    return (
        f"Generate ONLY the named constellation for a star chart SVG as bare "
        f"SVG elements — no <svg> root tag, no background rect, no background "
        f"filler stars.\n\n"
        f"CONSTELLATION ({star_count} named stars):\n"
        f"  - Place {star_count} principal stars as <circle> elements:\n"
        f"      bright anchor stars: r=3-5, fill='#E8F0F2' or '#81E6D9'\n"
        f"      secondary stars: r=1.5-3, fill='#4FD1C5' or '#B0C4DE'\n"
        f"  - Connect stars with <line> elements, stroke='#4FD1C5', "
        f"stroke-width='0.8', opacity='0.4'\n"
        f"    Draw the connecting pattern to suggest a recognisable shape "
        f"(animal, figure, object)\n"
        f"  - Label each named star with <text> near the circle:\n"
        f"      font-family='monospace' font-size='8' fill='#4FD1C5' opacity='0.85'\n\n"
        f"NAMING CONVENTION:\n  {culture_hint}\n\n"
        f"CONSTELLATION NAME:\n"
        f"  Place the constellation's full name near the bottom:\n"
        f"    <text> font-family='monospace' font-size='11' fill='#607D8B' "
        f"text-anchor='middle'\n"
        f"    Format: ✦ Name ✦\n\n"
        f"PALETTE: #E8F0F2 (bright star) · #81E6D9 · #4FD1C5 (teal) · "
        f"#B0C4DE · #607D8B (dim text) — no other colors\n\n"
        f"RULES:\n"
        f"  - No <image>, no <use>, no external hrefs\n"
        f"  - Output ONLY the constellation elements (stars, lines, labels, "
        f"name) — no <svg> tag, no background, no explanation, no markdown."
        + lore_instruction
    )


def _clean_fragment(raw: str) -> str:
    """Strip think-blocks/fences and, if the model wrapped its fragment in
    a full <svg>...</svg> despite being asked not to, unwrap it to just the
    inner elements — preserving anything after </svg> (e.g. a lore comment
    the model still placed in its usual spot) so _split_lore can still
    find it."""
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    cleaned = re.sub(r"```\w*\s*|```", "", cleaned).strip()
    m = re.search(r"<svg\b[^>]*>(.*)</svg>(.*)", cleaned, re.DOTALL | re.IGNORECASE)
    if m:
        inner, trailing = m.group(1).strip(), m.group(2).strip()
        return f"{inner}\n{trailing}".strip() if trailing else inner
    return cleaned


def _split_lore(fragment: str) -> tuple[str, str]:
    """Split a trailing <!-- LORE: ... --> comment off the constellation
    fragment, so it can be re-appended after the assembled </svg> — the
    same position the original single-call prompt used."""
    m = re.search(r"(<!--\s*LORE:.*?-->)", fragment, re.DOTALL | re.IGNORECASE)
    if not m:
        return fragment.strip(), ""
    return fragment[:m.start()].strip(), m.group(1)


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

    def generate_artifact(self, args, call_fn) -> str:
        """large/medium tier: unchanged single-call prompt. small tier:
        background rect + filler stars built locally (see module docstring
        above _local_background_rect_svg — this fixes a REAL truncation
        failure reproduced live at --stars 20, not a preventive guess)."""
        tier = getattr(call_fn, "model_tier", "large")
        if tier == "small":
            return self._generate_small(
                call_fn,
                getattr(args, "culture", "invented"),
                getattr(args, "stars", 8),
                getattr(args, "lore", False),
            )
        raw = call_fn(self.build_prompt(args))
        return self.post_process(self.parse_output(raw, args), args)

    def _generate_small(self, call_fn, culture: str, star_count: int,
                         include_lore: bool) -> str:
        print("[small-tier: constellation …]", flush=True)
        raw = call_fn(
            _build_constellation_prompt(culture, star_count, include_lore),
            # Generous budget: a too-small one previously truncated the
            # response mid-element even after removing the background-star
            # flood (found via live before/after testing) — each named star
            # needs a circle + connecting line(s) + label text, plus the
            # constellation name and an optional lore comment.
            max_tokens=max(4096, 250 * star_count),
        )
        body, lore = _split_lore(_clean_fragment(raw))

        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_W}" height="{SVG_H}">\n'
            f"{_local_background_rect_svg()}\n\n"
            f"<!-- Background stars -->\n{_local_background_stars_svg(star_count)}\n\n"
            f"<!-- Constellation -->\n{body}\n</svg>"
        )
        if lore:
            svg = f"{svg}\n{lore}"
        return self.parse_output(svg, None)

    def parse_output(self, raw: str, args) -> str:
        import re
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        cleaned = re.sub(r"```\w*\s*|```", "", cleaned).strip()
        m = re.search(r"(<svg\b.*?</svg>.*?)$", cleaned, re.DOTALL | re.IGNORECASE)
        if not m:
            raise ValueError("LLM response did not contain valid SVG markup")
        return m.group(1).strip()
