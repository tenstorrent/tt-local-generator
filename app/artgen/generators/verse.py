"""
Verse generator — structured text art forms.

Produces haiku sequences, lore fragments, epitaphs, and couplets.
Strong form constraints keep LLM output tight and memorable.
"""

from __future__ import annotations

from artgen import ArtGenerator, register

_FORMS: dict[str, dict] = {
    "haiku": {
        "description": "haiku sequence",
        "system": (
            "You write haiku sequences. Each haiku is three lines: 5-7-5 syllables. "
            "Use concrete imagery, seasonal reference (kigo), and a cutting word or pivot (kireji). "
            "No explanation, no title, no numbering. Separate haiku with a blank line. "
            "Never add gore or disturbing content."
        ),
        "user_template": (
            "Write {count} haiku on the theme: {theme}\n"
            "Each haiku: three lines, 5-7-5 syllables, concrete imagery. "
            "Separate with a blank line. Output only the haiku."
        ),
    },
    "lore": {
        "description": "world-building lore fragment",
        "system": (
            "You write short world-building lore entries — the kind found in fantasy or "
            "sci-fi game item descriptions or codex entries. Terse, evocative, specific. "
            "Under 80 words per entry. No preamble, no explanation."
        ),
        "user_template": (
            "Write {count} lore fragment(s) on the theme: {theme}\n"
            "Each fragment: a name/title on the first line, then 2-4 sentences of lore. "
            "Separate entries with a blank line."
        ),
    },
    "epitaph": {
        "description": "epitaph or memorial inscription",
        "system": (
            "You write epitaphs and memorial inscriptions — solemn, compressed, true. "
            "4-8 lines each. The subject may be real or invented. No rhyme required. "
            "Avoid cliché. Output only the inscription text."
        ),
        "user_template": (
            "Write {count} epitaph(s) on the theme: {theme}\n"
            "Each 4-8 lines. Solemn, compressed. Separate with a blank line."
        ),
    },
    "couplet": {
        "description": "rhyming couplet pair",
        "system": (
            "You write rhyming couplets — two-line units that rhyme on the second line. "
            "Strong meter, surprising rhyme, no padding. Output only the couplet lines."
        ),
        "user_template": (
            "Write {count} rhyming couplet(s) on the theme: {theme}\n"
            "Each: two lines, rhyming. Separate with a blank line."
        ),
    },
}


def _build_messages(form: str, theme: str, count: int) -> tuple[str, str]:
    """Return (system_prompt, user_message) for the given form."""
    spec = _FORMS[form]
    system = spec["system"]
    user = spec["user_template"].format(count=count, theme=theme)
    return system, user


@register
class VerseGenerator(ArtGenerator):
    name = "verse"
    description = "Structured text art: haiku sequences, lore fragments, epitaphs, couplets"
    output_ext = ".txt"

    def add_args(self, parser) -> None:
        parser.add_argument(
            "--form", choices=list(_FORMS), default="haiku",
            help="Verse form (default: haiku)",
        )
        parser.add_argument(
            "--theme", default="the passage of time",
            help='Thematic seed (default: "the passage of time")',
        )
        parser.add_argument(
            "--count", type=int, default=3, metavar="N",
            help="Number of verses to generate (default: 3)",
        )

    def build_prompt(self, args) -> str:
        form = getattr(args, "form", "haiku")
        theme = getattr(args, "theme", "the passage of time")
        count = getattr(args, "count", 3)
        # Verse uses a system prompt — stash it on args so call_llm can use it
        system, user = _build_messages(form, theme, count)
        args._verse_system = system
        return user

    def parse_output(self, raw: str, args) -> str:
        import re
        # Strip thinking blocks and markdown
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        cleaned = re.sub(r"```\w*\s*|```", "", cleaned).strip()
        return cleaned

    def default_output(self) -> "Path":
        from pathlib import Path
        return Path("verse.txt")
