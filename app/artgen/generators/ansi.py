"""
ANSI art generator — pixel-grid paintings using xterm-256 background colors.

The LLM produces a W×H grid where each cell is a single SPACE character
preceded by \033[48;5;Nm (background color) and each row ends with \033[0m\n.
This is the most reliable format for LLMs: they only need to pick one color
index per cell, and the result renders as a pixelated image in any terminal.

The "bbs" style generates splash screens in the tradition of 80s/90s dial-up
BBS art: dark background, neon highlights, bold central iconography, 80×25
canvas matching the standard BBS terminal dimensions.
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
    "bbs":       "BBS splash screen. Dark void background, neon-on-black central icon, 80×25.",
}

# Concrete 4×4 example the LLM can mirror:
_EXAMPLE = """\
\033[48;5;24m \033[48;5;25m \033[48;5;33m \033[48;5;39m \033[0m
\033[48;5;22m \033[48;5;28m \033[48;5;34m \033[48;5;76m \033[0m
\033[48;5;58m \033[48;5;94m \033[48;5;130m \033[48;5;172m \033[0m
\033[48;5;0m \033[48;5;236m \033[48;5;240m \033[48;5;244m \033[0m"""

# BBS-specific palette and composition guidance:
_BBS_PALETTE = """\
BBS COLOR PALETTE — void darkness with neon puncture:
  BACKGROUND (fill 65%+ of canvas with these):
    232-234 : near-black (the essential darkness — use liberally)
    17-19   : deep navy void
    52-53   : deep crimson shadow

  NEON HIGHLIGHTS (the glowing subject and hot edges only):
    51, 87  : electric cyan / ice blue
    46, 82  : toxic green / hacker glow
    201,199 : hot magenta / neon pink
    226,220 : bright gold / warning yellow
    255,231 : pure white (maximum intensity, use sparingly)
    196,160 : blood red / alarm

  MIDTONE HALOS (glow bleeding outward from bright elements):
    23-26   : dim teal aura
    55-57   : dim violet
    88-90   : dark magenta bloom
    238-242 : structural gray (outlines, chassis, bones)"""

_BBS_COMPOSITION = """\
CLASSIC BBS SPLASH SCREEN LAYOUT (25 rows total):
  Rows 1-3:    Deep void. Near-black with 2-3 scattered bright pinpoints (stars/static).
  Rows 4-20:   MAIN IMAGE — the board's icon/sigil, large and centered.
               Hard neon edges. Radiance bleeding into surrounding darkness.
               Think: glowing skull, coiled dragon, cracked circuit board,
               rearing demon, lone hacker silhouette, exploding nova, eye of god.
               Be bold. This is the first thing callers see when they dial in.
  Rows 21-23:  Ground shadow. Dark reflection or base, grounding the main image.
  Rows 24-25:  Footer strip. Near-black with faint scanline texture."""


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


def _build_bbs_prompt(subject: str, board_name: str, tagline: str, width: int = 80) -> str:
    height = 25  # standard 80×25 BBS terminal

    board_ctx = ""
    if board_name:
        board_ctx = f"""\
BOARD IDENTITY:
  Name    : {board_name}
  Tagline : {tagline or "connecting minds across the void"}

  The art IS this board's identity. The central image is its sigil.
  Let the name drive the visual theme — every pixel serves the board's soul.

"""

    return f"""\
Create a BBS splash screen for: {subject}

{board_ctx}CANVAS: {width} columns × {height} rows

{_BBS_COMPOSITION}

OUTPUT FORMAT — follow exactly:
  • Each pixel = one SPACE with background color: \\033[48;5;Nm
  • Row: \\033[48;5;N1m \\033[48;5;N2m ... \\033[48;5;N{width}m \\033[0m
  • Exactly {height} rows × {width} pixels each

{_BBS_PALETTE}

RULES:
  - Darkness is the canvas. Neon is the signal. Most pixels are near-black.
  - The main subject (rows 4-20) should glow — hard bright edges on dark.
  - No gradual pastel transitions. Punch. Contrast. Neon-on-void.
  - No markdown fences, no explanation — only the {height} ANSI escape rows.
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
            help="Width in pixels/columns (default: 40; bbs style defaults to 80)",
        )
        parser.add_argument(
            "--colors", choices=["256", "16"], default="256",
            help="Color depth (currently only 256 is used; kept for CLI compat)",
        )
        parser.add_argument(
            "--ansi-style", choices=list(_STYLE_HINTS), default="scene",
            dest="ansi_style",
            help="Composition style: scene, landscape, portrait, logo, bbs (default: scene)",
        )
        parser.add_argument(
            "--board-name", default="", metavar="NAME",
            dest="board_name",
            help="BBS board name — gives the art its identity (bbs style only)",
        )
        parser.add_argument(
            "--tagline", default="", metavar="TEXT",
            help="BBS board tagline shown below the name (bbs style only)",
        )

    def build_prompt(self, args) -> str:
        style = getattr(args, "ansi_style", "scene")
        if style == "bbs":
            width = getattr(args, "width", None) or 80
            return _build_bbs_prompt(
                subject=getattr(args, "subject", "glowing skull on dark void"),
                board_name=getattr(args, "board_name", ""),
                tagline=getattr(args, "tagline", ""),
                width=width,
            )
        return _build_prompt(
            getattr(args, "subject", "a mountain at sunset"),
            getattr(args, "width", 40),
            style,
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
        # Llama-3.3 emits bare octal 033[ (no backslash) — treat as ESC[
        cleaned = re.sub(r"(?<![\\x\d])033\[", "\033[", cleaned)
        return cleaned
