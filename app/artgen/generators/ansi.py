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

import json
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
    "bbs":       "BBS splash screen.  Dark void background, neon-on-black central icon, 80×20.",
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


# ── Small/medium-tier helpers (band-segmented structure, legend-based color) ──
#
# A whole-canvas free-form prompt ("compose this scene, decide the colors")
# reliably collapses into repeating token loops or rambling explanation on a
# heavily quantized / speculative-decoding model — validated live against
# Qwen/Qwen3.8-27B (q4kv + dflash2) on 2026-09-21; see the design doc. The
# fix that worked: small, explicitly-anchored, mechanical sub-tasks instead
# of one big creative one. These helpers implement that for the "small" tier
# (band-segmented structure, per-row colorize) and are reused by the
# "medium" tier's colorize pass (legend generation, boundary-independent
# parsing).

# Per-style band roles for pass-1 structure, mirroring the same 3-way split
# _build_ascii_prompt already uses (bbs / landscape / everything else).
# Each style maps to exactly 3 bands: (band_label, characters_to_use,
# row_role_phrases). row_role_phrases is a short list describing what a row
# at that RELATIVE position within the band should contain — proportionally
# indexed to whatever the band's actual row count turns out to be, so the
# same table works for any canvas height.
_BAND_ROLE_PHRASES: dict[str, list[tuple[str, str, list[str]]]] = {
    "bbs": [
        ("void top", "space",
         ["all void, empty space"]),
        ("neon subject", "| / \\ o O 0 ( ) - = ^ * # @ X %",
         ["top of the neon icon/sigil, its highest point",
          "upper body of the icon",
          "widest/boldest part of the icon",
          "lower body of the icon, tapering toward its base"]),
        ("void bottom", "space",
         ["all void, empty space"]),
    ],
    "landscape": [
        ("sky", "space . , ' ` ^ - ~",
         ["highest sky, sparse stars or clouds",
          "sky with more texture, drifting clouds",
          "lower sky nearing the horizon, denser cloud texture",
          "horizon line texture, where sky meets terrain"]),
        ("horizon", "| / \\ o O 0 ( ) - = ^",
         ["silhouette breaking the horizon line",
          "main shapes along the horizon",
          "foreground shapes, larger and closer",
          "base of the foreground shapes"]),
        ("terrain", "space _ = ~ # . , :",
         ["terrain or water just past the horizon",
          "midground terrain/water texture",
          "busier foreground terrain/water texture",
          "nearest, densest terrain/water texture"]),
    ],
    "_default": [
        ("background", "space . , : ; - = + ~ / \\",
         ["sparse faint texture, mostly empty space",
          "light texture, a few scattered marks",
          "denser texture, more marks mixed together",
          "densest texture in this band, closest to the subject"]),
        ("subject", "| / \\ o O 0 ( ) - = ^ * # @ X %",
         ["top of the main subject, its highest point",
          "upper body of the subject",
          "middle body of the subject, its widest or most detailed part",
          "lower body of the subject, tapering toward its base"]),
        ("foreground", "space ~ = # X . , :",
         ["structure or ground directly beneath the subject",
          "first layer of ground/base texture",
          "a busier, more turbulent layer of ground/base texture",
          "calmest, plainest layer, farthest from the subject"]),
    ],
}


def _default_width(style: str) -> int:
    """The canvas width used when --width is omitted: wider for bbs's
    80x20 splash-screen convention, narrower for everything else. Single
    source of truth for this — both build_prompt/generate_artifact and
    the GUI's dynamic_default() hook (AnsiGenerator.dynamic_default) call
    this exact function, so the number the Create panel shows can never
    drift from the number a generation actually uses."""
    return 80 if style == "bbs" else 40


def _split_bands(height: int, n_bands: int = 3) -> list[int]:
    """Split height rows into n_bands as evenly as possible, giving any
    remainder to the earliest bands. _split_bands(12, 3) == [4, 4, 4];
    _split_bands(20, 3) == [7, 7, 6]."""
    base = height // n_bands
    rem = height % n_bands
    return [base + (1 if i < rem else 0) for i in range(n_bands)]


def _build_band_prompt(subject: str, style: str, band_label: str, chars: str,
                        row_phrases: list[str], width: int, n_rows: int) -> str:
    """Pass 1 (small tier) — one band of the canvas, with an explicit
    content instruction per row (proportionally indexed into row_phrases).
    A generic 'vary each row' instruction was validated to still collapse
    into repetition; naming what a specific row contains did not."""
    hint = _STYLE_HINTS.get(style, _STYLE_HINTS["scene"])
    lines = []
    for i in range(n_rows):
        idx = min(len(row_phrases) - 1, (i * len(row_phrases)) // n_rows)
        lines.append(f"Row {i + 1}: {row_phrases[idx]}")
    row_block = "\n".join(lines)
    return f"""\
Draw the "{band_label}" band of an ASCII art scene: "{subject}".
Style: {hint}
Respond with ONLY a JSON array of exactly {n_rows} strings, each exactly
{width} characters long, using only these characters: {chars}

Row-by-row content:
{row_block}

Output only the JSON array, nothing else.
"""


def _parse_row_json(raw: str, n_rows: int, width: int) -> list[str]:
    """Parse a JSON array of row-strings; fails soft to blank-padded rows
    on any parse error (malformed JSON, no array found, wrong element
    types) rather than raising."""
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    text = re.sub(r"```\w*\s*|```", "", text).strip()
    rows: list[str] = []
    try:
        start = text.index("[")
        end = text.rindex("]")
        arr = json.loads(text[start:end + 1])
        # A JSON array element need not be a string (could be a number,
        # object, null, ...) — str()-ing a dict/list would leak its Python
        # repr straight into the canvas as if it were row content. Keep
        # real strings; replace anything else with a blank placeholder so
        # row POSITIONS stay aligned (a filter would shift every row after
        # a bad one up by one).
        rows = [r if isinstance(r, str) else "" for r in arr]
    except Exception:
        # Fail soft to blank rows — do NOT fall back to the raw text split
        # into lines: unparseable garbage (a stray sentence, an error
        # message) is not row content and must not leak into the canvas.
        rows = []
    out: list[str] = []
    for row in rows[:n_rows]:
        row = row[:width].ljust(width)
        out.append(row)
    while len(out) < n_rows:
        out.append(" " * width)
    return out


def _build_legend_prompt(distinct_chars: str, subject: str, style: str,
                          board_name: str, tagline: str) -> str:
    """One small call that decides colors ONCE, as a lookup table, rather
    than asking the model to decide colors for every cell of a whole canvas
    in one shot (the freeform version of pass 3 that was validated to make
    the model ramble through visible reasoning text instead of ever
    emitting the requested output)."""
    color_guide = _COLOR_GUIDE_BBS if style == "bbs" else _COLOR_GUIDE_SCENE
    board_ctx = ""
    if style == "bbs" and board_name:
        board_ctx = (
            f"\nBBS IDENTITY: Board name: {board_name}"
            + (f"  |  Tagline: {tagline}" if tagline else "")
            + "\nLet the name drive the color theme — neon identity.\n"
        )
    return f"""\
Decide ONE xterm-256 color index (0-255) for each distinct character below,
for a colorized drawing of "{subject}".
{board_ctx}
CHARACTERS PRESENT: {distinct_chars}

{color_guide}

Respond with ONLY a JSON object mapping each character to its color index,
e.g. {{"#": 236, " ": 232}}. Space must always be included. No explanation,
no markdown fences — only the JSON object.
"""


def _parse_legend_json(raw: str) -> dict[str, int]:
    """Parse the legend JSON object; fails soft to an empty dict on any
    parse error (the caller fills in fallback colors for every character
    the model omitted or that failed to parse)."""
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    text = re.sub(r"```\w*\s*|```", "", text).strip()
    try:
        start = text.index("{")
        end = text.rindex("}")
        obj = json.loads(text[start:end + 1])
    except Exception:
        return {}
    # Build entry-by-entry: one bad value (e.g. a color given as a color
    # name instead of an int) must only drop THAT entry, never discard the
    # whole legend — losing the whole legend turns the entire picture
    # monochrome gray via the caller's fallback, which is worse than a
    # legend that's merely missing one character's color.
    legend: dict[str, int] = {}
    for k, v in obj.items():
        key = str(k)[:1]
        if not key:
            continue
        try:
            legend[key] = int(v)
        except (TypeError, ValueError):
            continue
    return legend


def _generate_legend(call_fn, block_art: str, subject: str, style: str,
                      board_name: str, tagline: str) -> dict[str, int]:
    """Build the character→color legend for block_art. Never raises: any
    character missing from the model's response (or the whole response
    being unparseable) gets a neutral gray fallback (244), and space
    always resolves to the void color (232)."""
    distinct = "".join(sorted(set(block_art) - {"\n"}))
    raw = call_fn(
        _build_legend_prompt(distinct, subject, style, board_name, tagline),
        max_tokens=400,
    )
    legend = _parse_legend_json(raw)
    for ch in distinct:
        legend.setdefault(ch, 244)
    legend.setdefault(" ", 232)
    return legend


def _clamp_color_index(color) -> str:
    """Clamp a color index to the valid xterm-256 range [0, 255] before it
    lands in an escape code — a legend can hand back an out-of-range value
    (e.g. a typo'd "9999"), and an unclamped index would emit an escape
    code no terminal defines a color for."""
    try:
        n = int(color)
    except (TypeError, ValueError):
        n = 244
    n = max(0, min(255, n))
    return str(n)


def _colorize_row(row: str, legend: dict[str, int]) -> str:
    """Mechanically apply an already-decided legend to one row: wrap every
    character of ROW (never a model's own output — there is no model call
    on this path) in its legend color, falling back to a neutral gray for
    any character the legend doesn't cover.

    No LLM call here. Both a per-row 'apply this mapping' call (small tier)
    and a whole-canvas version of the same call (medium tier) were
    validated to return output byte-identical to this pure function, at a
    real cost (up to 20+ sequential round-trips on the small tier's
    original per-row design) for zero information gain — the model's only
    real contribution is `_generate_legend`'s color choices, not the
    mechanical act of applying them. See the design doc's "Validated
    technique" section for the empirical finding."""
    body = "".join(
        f"\033[38;5;{_clamp_color_index(legend.get(ch, 244))}m{ch}"
        for ch in row
    )
    return body + "\033[0m"


def _colorize_grid(block_art: str, legend: dict[str, int]) -> str:
    """`_colorize_row` applied to every row of block_art — the medium
    tier's whole-canvas equivalent of the small tier's per-row pass. Since
    neither tier calls the model for this step, "whole canvas" vs. "one
    row" no longer has any cost difference; this just joins the per-row
    results."""
    return "\n".join(_colorize_row(row, legend) for row in block_art.split("\n"))


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
            help="Width in columns (default: 80 for bbs style, 40 for all others)",
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
        width = getattr(args, "width", None) or _default_width(style)
        height = 20 if style == "bbs" else max(12, width // 2)
        return _build_ascii_prompt(
            subject=getattr(args, "subject", "a mountain at sunset"),
            width=width,
            height=height,
            style=style,
        )

    def dynamic_default(self, dest: str, values: dict) -> "int | None":
        """Optional hook `create_param_panels.ArtgenParamPanel` calls so the
        Create surface's width field can SHOW and forward the real
        effective default (80 for bbs, 40 otherwise) instead of a bare
        0-means-auto sentinel the user has to trust a tooltip to interpret.
        `values` is a snapshot of every field's current collected value —
        keyed the same as `args` would be. Calls the exact same
        `_default_width` that `build_prompt`/`generate_artifact` use, so
        the number shown in the GUI can never drift from the number an
        actual generation uses. Returns None for any other dest (no
        dynamic default there) — never raises."""
        if dest != "width":
            return None
        return _default_width(values.get("ansi_style") or "scene")

    def generate_artifact(self, args, call_fn) -> str:
        """Dispatch to a tier-specific pipeline based on call_fn.model_tier
        (stamped by _make_call_fn in cli.py/mcp_server.py — see
        model_capability.py). Any call_fn without the attribute (e.g. a
        hand-rolled test double, or any caller predating this change)
        defaults to "large", reproducing this generator's original
        whole-canvas 3-pass behavior byte-for-byte."""
        tier = getattr(call_fn, "model_tier", "large")

        style      = getattr(args, "ansi_style", "scene")
        subject    = getattr(args, "subject", "a mountain at sunset")
        board_name = getattr(args, "board_name", "")
        tagline    = getattr(args, "tagline", "")
        width      = getattr(args, "width", None) or _default_width(style)
        height     = 20 if style == "bbs" else max(12, width // 2)

        if tier == "small":
            return self._generate_small(
                call_fn, subject, style, width, height, board_name, tagline,
            )
        if tier == "medium":
            return self._generate_medium(
                call_fn, subject, style, width, height, board_name, tagline,
            )
        return self._generate_large(
            args, call_fn, subject, style, width, height, board_name, tagline,
        )

    def _generate_large(self, args, call_fn, subject, style, width, height,
                         board_name, tagline) -> str:
        """3-pass pipeline: ASCII structure → block refinement → colorization.
        This is the original ansi generator behavior, unchanged — the
        regression-safety default for every model this generator has ever
        worked on."""
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

    def _generate_small(self, call_fn, subject, style, width, height,
                         board_name, tagline) -> str:
        """Band-segmented structure (pass 1) + unchanged whole-canvas block
        refinement (pass 2) + legend-then-local-mechanical-apply color
        (pass 3, no LLM call — see `_colorize_row`'s docstring for why).
        The band-segmented structure technique was validated live against
        Qwen/Qwen3.8-27B (q4kv + dflash2) on 2026-09-21. This assembled,
        generalized pipeline has not itself been run end-to-end against
        real hardware yet — see the plan's "Post-plan validation" section.

        Intentionally bypasses parse_output — this pipeline already builds
        clean, normalized ANSI output itself (no think-blocks/fences/escape
        notations to strip), so a future override of parse_output for this
        generator would only affect the large tier, not this one."""
        print("[small-tier: band-segmented ASCII structure …]", flush=True)
        specs = _BAND_ROLE_PHRASES.get(style, _BAND_ROLE_PHRASES["_default"])
        if style == "bbs":
            band_heights = [2, max(1, height - 4), 2]
        else:
            band_heights = _split_bands(height, 3)

        rows: list[str] = []
        for (band_label, chars, phrases), n_rows in zip(specs, band_heights):
            if n_rows <= 0:
                continue
            # Each response needs to carry n_rows * width characters plus
            # JSON syntax overhead (quotes, commas) per row — a budget that
            # only scales with n_rows (not width) undersizes wide canvases:
            # bbs's default width=80 needs roughly double what width=40
            # needs for the same row count.
            raw = call_fn(
                _build_band_prompt(subject, style, band_label, chars,
                                    phrases, width, n_rows),
                max_tokens=max(200, n_rows * (width + 20)),
            )
            rows.extend(_parse_row_json(raw, n_rows, width))
        ascii_art = _normalize_grid("\n".join(rows), width, height)

        print("[small-tier: block refinement …]", flush=True)
        raw2 = call_fn(_build_refine_prompt(ascii_art, subject, width, height),
                       max_tokens=1024)
        block_art = _normalize_grid(raw2, width, height)

        print("[small-tier: legend + colorization …]", flush=True)
        legend = _generate_legend(call_fn, block_art, subject, style,
                                   board_name, tagline)
        return _colorize_grid(block_art, legend)

    def _generate_medium(self, call_fn, subject, style, width, height,
                          board_name, tagline) -> str:
        """Unchanged whole-canvas structure/refine (passes 1-2, same
        prompts as the large tier — no evidence they fail at this tier)
        plus a legend-then-local-mechanical-apply color pass (no LLM call
        — see `_colorize_row`'s docstring for why).

        Intentionally bypasses parse_output — this pipeline already builds
        clean, normalized ANSI output itself (no think-blocks/fences/escape
        notations to strip), so a future override of parse_output for this
        generator would only affect the large tier, not this one."""
        print("[medium-tier: ASCII structure …]", flush=True)
        raw1 = call_fn(_build_ascii_prompt(subject, width, height, style),
                       max_tokens=1024)
        ascii_art = _normalize_grid(raw1, width, height)

        print("[medium-tier: block refinement …]", flush=True)
        raw2 = call_fn(_build_refine_prompt(ascii_art, subject, width, height),
                       max_tokens=1024)
        block_art = _normalize_grid(raw2, width, height)

        print("[medium-tier: legend + colorization …]", flush=True)
        legend = _generate_legend(call_fn, block_art, subject, style,
                                   board_name, tagline)
        return _colorize_grid(block_art, legend)

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
