"""
Freeform generator — pass any prompt directly to the LLM.

The user supplies the full intent as --freeform "...". Output format is
inferred from --output extension (.svg, .ans, .json, .txt).
"""

from __future__ import annotations

import re
from pathlib import Path

from artgen import ArtGenerator, register

_SVG_HINT = (
    "\n\nOutput ONLY the complete SVG starting with <svg and ending with </svg>. "
    "No explanation, no markdown, no comments."
)
_JSON_HINT = (
    "\n\nOutput ONLY the JSON object or array. "
    "No markdown fences, no explanation."
)
_ANSI_HINT = (
    "\n\nOutput ONLY the ANSI art lines using block characters and escape sequences. "
    "No markdown, no explanation."
)


def _infer_ext(output_path: str | None) -> str:
    if not output_path:
        return ".txt"
    return Path(output_path).suffix.lower() or ".txt"


def _build_prompt(freeform: str, output_path: str | None) -> str:
    ext = _infer_ext(output_path)
    hint = {".svg": _SVG_HINT, ".json": _JSON_HINT, ".ans": _ANSI_HINT}.get(ext, "")
    return freeform.strip() + hint


def _parse_by_ext(raw: str, ext: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    cleaned = re.sub(r"```\w*\s*|```", "", cleaned).strip()
    if ext == ".svg":
        m = re.search(r"(<svg\b[^>]*?>.*?</svg>)", cleaned, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1)
    if ext == ".ans":
        cleaned = cleaned.replace("\\033", "\033").replace("\\e", "\033")
    return cleaned


@register
class FreeformGenerator(ArtGenerator):
    name = "freeform"
    description = "Pass any prompt directly to the LLM — output format inferred from --output extension"
    output_ext = ".txt"

    def add_args(self, parser) -> None:
        parser.add_argument(
            "--freeform", default=None, metavar="PROMPT",
            help="Freeform prompt — describe anything you want generated",
        )

    def build_prompt(self, args) -> str:
        freeform = getattr(args, "freeform", None) or ""
        if not freeform.strip():
            raise ValueError("--freeform requires a prompt string")
        return _build_prompt(freeform, getattr(args, "output", None))

    def parse_output(self, raw: str, args) -> str:
        ext = _infer_ext(getattr(args, "output", None))
        return _parse_by_ext(raw, ext)

    def default_output(self) -> Path:
        return Path("freeform.txt")
