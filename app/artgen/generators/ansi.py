"""
ANSI art generator — pixel-grid paintings using xterm-256 background colors.

The LLM produces a W×H grid where each cell is a single SPACE character
preceded by \033[48;5;Nm (background color) and each row ends with \033[0m\n.
This is the most reliable format for LLMs: they only need to pick one color
index per cell, and the result renders as a pixelated image in any terminal.
"""

from __future__ import annotations

import re

from artgen import ArtGenerator, register

_SUBJECT_EXAMPLES = (
    "a mountain at sunset, a lighthouse in a storm, a dragon skull, "
    "a coffee cup steaming, a retro computer, a black hole, a cat"
)

_COLOR_MODES = {"256": "xterm 256-color", "16": "ANSI 16-color"}

_STYLE_HINTS = {
    "landscape": "Wide panoramic. Sky gradient top half, terrain / water bottom half.",
    "portrait":  "Centred subject with strong silhouette. Symmetric or near-symmetric.",
    "logo":      "Bold shape or icon. Simple high-contrast geometric treatment.",
    "scene":     "Foreground / midground / background layers. Suggest depth and lighting.",
}

# Concrete 4×4 example the LLM can mirror:
_EXAMPLE = """\
\033[48;5;24m \033[48;5;25m \033[48;5;33m \033[48;5;39m \033[0m
\033[48;5;22m \033[48;5;28m \033[48;5;34m \033[48;5;76m \033[0m
\033[48;5;58m \033[48;5;94m \033[48;5;130m \033[48;5;172m \033[0m
\033[48;5;0m \033[48;5;236m \033[48;5;240m \033[48;5;244m \033[0m"""


def _build_prompt(subject: str, width: int, style: str) -> str:
    # Keep aspect ratio ~2:1 (terminal cells are taller than wide)
    height = max(12, width // 2)
    style_hint = _STYLE_HINTS.get(style, _STYLE_HINTS["scene"])

    return f"""\
Generate pixel art of: {subject}

CANVAS: {width} columns × {height} rows

COMPOSITION: {style_hint}

OUTPUT FORMAT — follow this exactly:
  • Each pixel is one SPACE with a background color:  \\033[48;5;N m
    where N is an xterm-256 color index (0–255)
  • A row of {width} pixels looks like:
      \\033[48;5;N1m \\033[48;5;N2m ... \\033[48;5;N{width}m \\033[0m
  • End every row with \\033[0m (reset) then a newline
  • Output exactly {height} rows, each with exactly {width} pixels

COLOR PALETTE GUIDE:
  16-21   : deep blues (night sky, ocean depth)
  22-46   : greens (forest, grass, foliage)
  52-88   : dark reds, browns (earth, rock, shadow)
  94-130  : oranges, ambers (sunset, fire, sand)
  148-190 : yellows, lime (highlights, sun, bright foliage)
  232-255 : grayscale 232=black … 255=white

EXAMPLE (4×4 ocean-to-earth gradient):
{_EXAMPLE}

RULES:
  - Change color every cell to create the image — do NOT use one color for everything
  - Use gradients within regions (sky darkens at top, brightens at horizon, etc.)
  - Every row must end with \\033[0m and a newline character
  - No markdown fences, no explanation, no preamble — only the ANSI pixel rows
"""


@register
class AnsiGenerator(ArtGenerator):
    name = "ansi"
    description = "ANSI block-character art using escape codes — renders in any color terminal"
    output_ext = ".ans"

    def add_args(self, parser) -> None:
        parser.add_argument(
            "--subject", default="a mountain at sunset",
            help=f"What to draw. Examples: {_SUBJECT_EXAMPLES}",
        )
        parser.add_argument(
            "--width", type=int, default=40, metavar="COLS",
            help="Width in pixels/columns (default: 40)",
        )
        parser.add_argument(
            "--colors", choices=["256", "16"], default="256",
            help="Color depth (currently only 256 is used; kept for CLI compat)",
        )
        parser.add_argument(
            "--ansi-style", choices=list(_STYLE_HINTS), default="scene",
            dest="ansi_style",
            help="Composition style (default: scene)",
        )

    def build_prompt(self, args) -> str:
        return _build_prompt(
            getattr(args, "subject", "a mountain at sunset"),
            getattr(args, "width", 40),
            getattr(args, "ansi_style", "scene"),
        )

    def parse_output(self, raw: str, args) -> str:
        # Strip thinking blocks
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        # Strip markdown fences
        cleaned = re.sub(r"```\w*\s*|```", "", cleaned).strip()
        # Normalise escape notation: \033, \x1b, \e, ^[ → actual ESC byte
        cleaned = cleaned.replace("\\033", "\033")
        cleaned = cleaned.replace("\\x1b", "\033")
        cleaned = cleaned.replace("\\e", "\033")
        # Some LLMs emit ^[ for ESC
        cleaned = cleaned.replace("^[", "\033")
        return cleaned
