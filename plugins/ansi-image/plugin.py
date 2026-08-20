# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
ansi-image -- pure-Pillow image -> ANSI-art utility plugin (Effort B Task 1).

Reimplements the technique behind closed-source GUI converters like
`rez2ans-next` (image -> CP437 ANSI art) in-process, since that tool can't be
embedded. Unlike rmbg/blip/depth (which shell out to a venv python for
torch), this plugin has exactly one dependency -- Pillow, already used
elsewhere in the app -- so conversion runs fully in-process with no
subprocess and no LLM. Output is fully deterministic for a given input.

HARD CONSTRAINT -- one color per cell: the app's ANSI renderer
(`app/artgen_render.py`'s `ansi_to_html`, via `parse_ansi_grid`) colors each
character cell using a SINGLE channel -- background if the character is a
space, foreground otherwise. It does not support two-color half-block cells
(e.g. `▀` with distinct fg+bg). So every cell here is emitted as
`\\x1b[38;5;Nm█` (set foreground to xterm-256 index N, then a full block
character) -- foreground+block, the SAME format the 3-pass `ansi` LLM
generator (`app/artgen/generators/ansi.py`) already emits and the format
`parse_ansi_grid` treats as current/canonical (the legacy `\\x1b[48;5;Nm `
background+space format is only accepted for backward compatibility with
older artifacts).

Palette: reuses `artgen_render._XTERM256_HEX` -- the exact same 256-entry
xterm color table the renderer uses to turn an escape code back into a hex
color -- so nearest-color quantization here can never drift from what the
viewer actually draws. `artgen_render` lives in `app/`, which is not always
on `sys.path` when a plugin is loaded standalone (by the MCP server or by
`_load_plugin`-style test harnesses), so we add it the same way
`plugins/animatediff/plugin.py` does.

Color-count design choice (see `_candidate_indices`):
  - `colors=256` (default): search indices 16..255 only -- the 6x6x6 color
    cube plus the 24-step grayscale ramp. These are the entries with
    precise, evenly-spaced RGB values, ideal for photographic gradients.
    Indices 0..15 (the "16 system colors") are excluded from this mode
    because mixing them into a nearest-neighbor search over smooth photo
    content tends to inject a handful of visually inconsistent, oddly
    saturated cells (their spacing is uneven relative to the cube).
  - `colors=16`: search indices 0..15 only -- the classic DOS/BBS 16-color
    palette, for an intentionally retro look when that's the point.

In-process usage:
    from plugins.ansi_image.plugin import image_to_ansi, is_available
(Python identifiers can't contain a hyphen; import via importlib for the
"ansi-image" directory name, as the MCP server and this plugin's own test
suite both do.)
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the app package is on sys.path (plugin_loader sets cwd to repo root,
# but app/ is not always on sys.path when loaded as a plugin) -- same pattern
# as plugins/animatediff/plugin.py.
_APP_DIR = Path(__file__).resolve().parent.parent.parent / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# Sane bounds so a huge/odd-aspect source image can't produce an absurdly
# large or degenerate ANSI grid.
_MAX_COLS = 120
_MAX_ROWS = 60
_DEFAULT_COLS = 80
_DEFAULT_COLORS = 256

_available: bool | None = None  # None = not yet probed

# Lazily built (r, g, b) tuples parsed from artgen_render._XTERM256_HEX.
# Deferred until first actually needed (image_to_ansi) rather than at import
# time, so is_available() -- a pure Pillow check -- never has to import
# artgen_render (which pulls in gi/Gtk).
_PALETTE_RGB: list[tuple[int, int, int]] | None = None


def is_available() -> bool:
    """Return True if Pillow (PIL) is importable.

    Pure check, no subprocess -- this plugin runs entirely in-process.
    """
    global _available
    if _available is not None:
        return _available
    try:
        import PIL  # noqa: F401
    except Exception:
        _available = False
    else:
        _available = True
    return _available


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _palette_rgb() -> list[tuple[int, int, int]]:
    """Return the 256-entry xterm palette as RGB tuples (index-aligned).

    Built once from `artgen_render._XTERM256_HEX` -- the SAME table the
    app's ANSI renderer uses -- so our nearest-color choice always matches
    what actually gets drawn on screen.
    """
    global _PALETTE_RGB
    if _PALETTE_RGB is None:
        from artgen_render import _XTERM256_HEX
        _PALETTE_RGB = [_hex_to_rgb(h) for h in _XTERM256_HEX]
    return _PALETTE_RGB


def _candidate_indices(colors: int) -> list[int]:
    """Which palette indices are eligible for nearest-color search.

    See the module docstring's "Color-count design choice" for why 256-mode
    excludes indices 0..15.
    """
    if colors == 16:
        return list(range(0, 16))
    return list(range(16, 256))


def _nearest_xterm_index(rgb: tuple[int, int, int], candidates: list[int]) -> int:
    """Nearest xterm-256 palette index to *rgb* by squared-euclidean distance."""
    palette = _palette_rgb()
    r, g, b = rgb
    best_idx = candidates[0]
    best_dist = None
    for idx in candidates:
        cr, cg, cb = palette[idx]
        dist = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_idx = idx
    return best_idx


def image_to_ansi(src: str, cols: int = _DEFAULT_COLS, colors: int = _DEFAULT_COLORS) -> str:
    """Convert the image at *src* to one-color-per-cell ANSI block art.

    Args:
        src:    Path to the input image (any PIL-readable format).
        cols:   Output width in character columns. Clamped to
                [1, `_MAX_COLS`]; default 80.
        colors: Palette size -- 256 (xterm color cube + grayscale ramp,
                photographic) or 16 (DOS/BBS system palette, retro). Any
                other value falls back to 256.

    Returns:
        A string of `\\x1b[38;5;Nm█` cells (foreground + full block), one
        row per output line, `\\n`-separated, ending with a trailing
        `\\x1b[0m` reset. This is exactly the format
        `artgen_render.parse_ansi_grid` already parses (it's the same
        fg+block format the 3-pass `ansi` LLM generator emits), so the
        result renders in the app's existing `.ans` viewer with no changes
        there.

    Raises:
        FileNotFoundError: If *src* does not exist.
    """
    src_path = Path(src)
    if not src_path.exists():
        raise FileNotFoundError(src)

    from PIL import Image

    cols = max(1, min(int(cols), _MAX_COLS))
    if colors not in (16, 256):
        colors = _DEFAULT_COLORS

    img = Image.open(src_path).convert("RGB")
    w, h = img.size

    # Terminal character cells are roughly twice as tall as they are wide,
    # so a naive cols x (cols * h/w) grid renders visibly stretched
    # vertically. Halving the height-derived row count compensates.
    rows = round(cols * (h / w) * 0.5) if w else 1
    rows = max(1, min(rows, _MAX_ROWS))

    # LANCZOS averages/interpolates rather than nearest-sampling a single
    # source pixel per cell, which matters a lot at these tiny target sizes
    # (a 200x20px photo downsampled to 80x4 cells needs real averaging, not
    # single-pixel picks, or the result is noisy rather than representative).
    resized = img.resize((cols, rows), Image.Resampling.LANCZOS)
    pixels = resized.load()

    candidates = _candidate_indices(colors)

    lines = []
    for y in range(rows):
        cells = []
        for x in range(cols):
            r, g, b = pixels[x, y]
            idx = _nearest_xterm_index((r, g, b), candidates)
            cells.append(f"\x1b[38;5;{idx}m█")
        lines.append("".join(cells))

    return "\n".join(lines) + "\x1b[0m"
