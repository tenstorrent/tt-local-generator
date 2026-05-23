"""
ANSI art generator — 3-pass pipeline using xterm-256 foreground colors.

Pass 1 — ASCII structure:
  The LLM draws the subject using plain ASCII characters.  Spatial composition
  is the model's strongest suit; no color decisions are needed here.

Pass 2 — Block refinement:
  The ASCII sketch is redrawn using Unicode block characters (█▀▄▌▐░▒▓) for
  richer geometry.  Layout is fixed; only visual quality improves.

Pass 3 — Colorization:
  Given the exact character map, the LLM assigns one foreground color (xterm-256)
  per cell using \033[38;5;Nm<char>.  Deciding a single color for an already-
  placed character is a much simpler task than planning position + color at once.

This "structure → refinement → color" pattern is the first instance of the
multi-pass remix pipeline that will be generalised in remix-mode.
"""

from __future__ import annotations

import re

from artgen import ArtGenerator, register

_COLOR_MODES = {"256": "xterm 256-color", "16": "ANSI 16-color"}

_SUBJECT_EXAMPLES = (
    "a mountain at sunset, a lighthouse in a storm, a dragon skull, "
    "a coffee cup steaming, a retro computer, a black hole, a cat"
)

_STYLE_HINTS = {
    "landscape": "Wide panoramic.  Sky gradient top half, terrain / water bottom half.",
    "portrait":  "Centred subject with strong silhouette.  Symmetric or near-symmetric.",
    "logo":      "Bold shape or icon.  Simple high-contrast geometric treatment.",
    "scene":     "Foreground / midground / background layers.  Suggest depth and lighting.",
    "bbs":       "BBS splash screen.  Dark void background, neon-on-black central icon, 40×20.",
}

# ── Color guidance paragraphs (pass 3) ───────────────────────────────────────

_COLOR_GUIDE_SCENE = """\
COLOR GUIDE:
  Sky / night background : 16-21 (dark blues) or 232-235 (near-black)
  Foliage / greenery     : 22-46
  Earth / rock / bark    : 52-88 (dark reds, browns, ochre)
  Fire / warmth / sunset : 94-130 (oranges, ambers)
  Highlights / glows     : 220-255 (yellows, whites)
  Mist / clouds / pale   : 189-231 (cool pastels)
  Use gradients: nearby color indices make smooth transitions within a region."""

_COLOR_GUIDE_BBS = """\
COLOR GUIDE — neon-on-void:
  Void / background / space characters : 232-234 (near-black)
  Electric cyan                         : 51, 87
  Toxic green                           : 46, 82
  Hot magenta / neon pink               : 201, 199
  Gold / warning yellow                 : 226, 220
  Pure white (maximum intensity, rare)  : 255, 231
  Blood red / alarm                     : 196, 160
  Teal halo (glow bleeding outward)     : 23-26
  Violet halo                           : 55-57
  Magenta bloom                         : 88-90
  Structural gray (outlines / edges)    : 238-242

ZONE RULES (strictly by row):
  • Rows 1-2   : all 232 (void), max 1-2 isolated bright pixels (stars)
  • Rows 3-17  : neon subject in center; 232 void at the edges; halos in between
  • Rows 18-20 : all 232-234 (shadow/scanlines), no bright pixels"""

# ── Concrete 4×4 example used in pass-3 colorization prompt ──────────────────

_COLOR_EXAMPLE = """\
\033[38;5;24m█\033[38;5;25m█\033[38;5;33m█\033[38;5;39m█\033[0m
\033[38;5;22m█\033[38;5;28m█\033[38;5;34m█\033[38;5;76m█\033[0m
\033[38;5;58m█\033[38;5;94m█\033[38;5;130m█\033[38;5;172m█\033[0m
\033[38;5;0m█\033[38;5;236m█\033[38;5;240m█\033[38;5;244m█\033[0m"""


# ── Pass helpers ──────────────────────────────────────────────────────────────


def _normalize_grid(raw: str, width: int, height: int) -> str:
    """
    Strip think-blocks and markdown fences from a plain-text LLM response, then
    normalise to exactly width×height characters.

    Used after passes 1 and 2 to give the next pass a clean, correctly-sized
    input regardless of any leading explanation or off-by-one row counts.
    """
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    text = re.sub(r"```\w*\s*|```", "", text).strip()

    lines = text.split("\n")

    # Skip leading empty lines, collect non-empty art lines
    art: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        if not art and not stripped:
            continue
        art.append(stripped)

    # Drop trailing blanks only when we have surplus rows — blank rows within
    # the target height are intentional void zones (e.g. BBS top/bottom strips).
    while len(art) > height and art and not art[-1]:
        art.pop()

    # Trim to height
    art = art[:height]

    # Normalise each row to exactly width characters
    result: list[str] = []
    for row in art:
        if len(row) < width:
            row = row + " " * (width - len(row))
        elif len(row) > width:
            row = row[:width]
        result.append(row)

    # Pad missing rows with spaces
    while len(result) < height:
        result.append(" " * width)

    return "\n".join(result)


def _build_ascii_prompt(subject: str, width: int, height: int, style: str) -> str:
    """Pass 1 — plain ASCII composition; no color, no ANSI escapes."""
    hint = _STYLE_HINTS.get(style, _STYLE_HINTS["scene"])

    if style == "bbs":
        spatial = (
            "Rows 1-2:   All space characters (void).\n"
            "Rows 3-17:  Main subject — large, centred icon or sigil.\n"
            "Rows 18-20: All space characters (void footer)."
        )
    elif style == "landscape":
        spatial = (
            "Top half:    sky, clouds, stars — use . , ' ` ^ for texture.\n"
            "Bottom half: terrain, water, or ground — use _ = ~ # for mass."
        )
    else:
        spatial = (
            "Foreground:  closest elements, densest characters.\n"
            "Midground:   the main subject.\n"
            "Background:  distant/faint, use . , : ; characters."
        )

    return f"""\
Draw "{subject}" as ASCII art.
Canvas: {width} columns × {height} rows.
Style: {hint}

SPATIAL LAYOUT:
{spatial}

CHARACTERS TO USE:
  Space     → empty / void / background
  . , : ;   → faint, distant, or fine detail
  - = + ~   → horizontal edges and surfaces
  | / \\ ^   → vertical, diagonal, upward strokes
  o O 0 ( ) → rounded shapes
  # @ X %   → dense, solid, or filled areas
  * ' ` .   → stars, highlights, sparkle

Output exactly {height} rows, each exactly {width} characters wide.
No color, no ANSI codes, no markdown fences, no explanation — only the ASCII art rows.
"""


def _build_refine_prompt(ascii_art: str, subject: str, width: int, height: int) -> str:
    """Pass 2 — enrich ASCII with Unicode block characters for richer geometry."""
    return f"""\
Refine this ASCII sketch of "{subject}" by replacing characters with Unicode block \
characters where they improve visual quality.

ASCII SKETCH ({width}×{height}) — preserve the exact layout:
{ascii_art}

REPLACEMENTS (use judgment — not every character needs to change):
  Dense / solid areas  (#, @, X, O)  →  █  (U+2588 FULL BLOCK)
  Upper boundary / cap               →  ▀  (upper half block)
  Lower boundary / base              →  ▄  (lower half block)
  Left edge                          →  ▌  (left half block)
  Right edge                         →  ▐  (right half block)
  Dense fill                         →  ▓  medium →  ▒  light / shadow →  ░
  Fine lines  ( | - / \\ )           →  keep as-is (they read well)
  Background / void  (space)         →  keep as space

Rules:
  - Preserve exactly the same spatial layout and subject position
  - Do NOT add or remove any character — only substitute
  - Output exactly {height} rows × {width} characters
  - No color, no ANSI codes, no markdown fences, no explanation
"""


def _build_colorize_prompt(
    block_art: str,
    subject: str,
    style: str,
    width: int,
    height: int,
    board_name: str,
    tagline: str,
) -> str:
    """Pass 3 — wrap every character with an ANSI 256-color foreground code."""
    color_guide = _COLOR_GUIDE_BBS if style == "bbs" else _COLOR_GUIDE_SCENE

    board_ctx = ""
    if style == "bbs" and board_name:
        board_ctx = (
            f"\nBBS IDENTITY: Board name: {board_name}"
            + (f"  |  Tagline: {tagline}" if tagline else "")
            + "\nLet the name drive the color theme — neon identity.\n"
        )

    return f"""\
Add color to this block-character drawing of "{subject}".
{board_ctx}
CHARACTER MAP ({width}×{height}) — do not change any characters, only add color:
{block_art}

FORMAT — wrap every character:
  \\033[38;5;Nm<char>
  where N is an xterm-256 color index (0–255)
  End every row with \\033[0m then a newline.
  Space characters (void/background) → use \\033[38;5;232m\\033[0m (they remain invisible).

{color_guide}

EXAMPLE (4×4 ocean-to-earth gradient showing the format):
{_COLOR_EXAMPLE}

RULES:
  - Every single character must be wrapped — no bare characters, no skipped cells
  - Each row: exactly {width} wrapped characters, then \\033[0m\\n
  - Output exactly {height} rows — complete the entire canvas
  - No markdown fences, no explanation — only the {height} ANSI escape rows
"""


# ── Generator ─────────────────────────────────────────────────────────────────


@register
class AnsiGenerator(ArtGenerator):
    name = "ansi"
    description = "ANSI block-character art using escape codes — renders in any color terminal"
    output_ext = ".ans"

    def add_args(self, parser) -> None:
        parser.add_argument(
            "--subject", default="a mountain at sunset",
            help=f"What to draw.  Examples: {_SUBJECT_EXAMPLES}",
        )
        parser.add_argument(
            "--width", type=int, default=None, metavar="COLS",
            help="Width in columns (default: 40)",
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
            help="BBS board tagline (bbs style only)",
        )

    def build_prompt(self, args) -> str:
        """Return the pass-1 ASCII structure prompt (used by --simulate)."""
        style = getattr(args, "ansi_style", "scene")
        width = getattr(args, "width", None) or 40
        height = 20 if style == "bbs" else max(12, width // 2)
        return _build_ascii_prompt(
            subject=getattr(args, "subject", "a mountain at sunset"),
            width=width,
            height=height,
            style=style,
        )

    def generate_artifact(self, args, call_fn) -> str:
        """3-pass pipeline: ASCII structure → block refinement → colorization."""
        import sys

        style      = getattr(args, "ansi_style", "scene")
        subject    = getattr(args, "subject", "a mountain at sunset")
        board_name = getattr(args, "board_name", "")
        tagline    = getattr(args, "tagline", "")
        width      = getattr(args, "width", None) or 40
        height     = 20 if style == "bbs" else max(12, width // 2)

        # Pass 1 — ASCII composition
        print("[pass 1/3: ASCII structure …]", flush=True)
        raw1 = call_fn(_build_ascii_prompt(subject, width, height, style),
                       max_tokens=1024)
        ascii_art = _normalize_grid(raw1, width, height)

        # Pass 2 — block character refinement
        print("[pass 2/3: block refinement …]", flush=True)
        raw2 = call_fn(_build_refine_prompt(ascii_art, subject, width, height),
                       max_tokens=1024)
        block_art = _normalize_grid(raw2, width, height)

        # Pass 3 — colorization (larger token budget for full ANSI output)
        print("[pass 3/3: colorization …]", flush=True)
        raw3 = call_fn(
            _build_colorize_prompt(
                block_art, subject, style, width, height, board_name, tagline,
            ),
            max_tokens=8192,
        )
        return self.parse_output(raw3, args)

    def parse_output(self, raw: str, args) -> str:
        """Strip think-blocks, fences, and normalise escape notations."""
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        cleaned = re.sub(r"```\w*\s*|```", "", cleaned).strip()
        cleaned = cleaned.replace("\\033", "\033")
        cleaned = cleaned.replace("\\x1b", "\033")
        cleaned = cleaned.replace("\\e",   "\033")
        cleaned = cleaned.replace("^[",    "\033")
        # Llama-3.3 emits bare octal 033[ (no backslash) — treat as ESC[
        cleaned = re.sub(r"(?<![\\x\d])033\[", "\033[", cleaned)
        return cleaned
