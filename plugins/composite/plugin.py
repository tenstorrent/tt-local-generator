# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Composite utility plugin.

Layers a foreground PNG (with alpha channel, e.g. from RMBG) over a
background image, centered and scaled.  Used as the final step in logo
decoration workflows: FLUX generates the decorative background, RMBG
isolates the logo mark, this plugin combines them.

PIL runs in system python — no venv or torch required.
"""
from __future__ import annotations

from pathlib import Path

_available: bool | None = None


def is_available() -> bool:
    global _available
    if _available is not None:
        return _available
    try:
        from PIL import Image  # noqa: F401
        _available = True
    except ImportError:
        _available = False
    return _available


def composite_images(
    background_path: str,
    foreground_path: str,
    output_path: str,
    scale: float = 0.72,
) -> None:
    """Composite *foreground_path* (RGBA) centered over *background_path*.

    The foreground is scaled so its longer dimension equals *scale* × the
    background size.  Aspect ratio is preserved.  Output is saved as RGB JPEG.

    Args:
        background_path: Path to the background image (any PIL format).
        foreground_path: Path to the foreground PNG with alpha channel.
        output_path:     Destination path for the composited JPEG.
        scale:           Fraction of background size for the foreground's
                         longer dimension (default 0.72 = 72%).
    """
    from PIL import Image

    bg = Image.open(background_path).convert("RGBA")
    bg_w, bg_h = bg.size

    fg = Image.open(foreground_path).convert("RGBA")
    fg_w, fg_h = fg.size

    # Scale foreground so its longer dimension = scale * background longer dim
    bg_long = max(bg_w, bg_h)
    fg_long = max(fg_w, fg_h)
    ratio = (bg_long * scale) / fg_long
    new_fg_w = int(fg_w * ratio)
    new_fg_h = int(fg_h * ratio)
    fg = fg.resize((new_fg_w, new_fg_h), Image.LANCZOS)

    # Center the foreground on the background
    offset_x = (bg_w - new_fg_w) // 2
    offset_y = (bg_h - new_fg_h) // 2
    bg.paste(fg, (offset_x, offset_y), fg)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(output_path, quality=95)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print("Usage: plugin.py <background> <foreground> <output> [scale]")
        sys.exit(1)
    scale = float(sys.argv[4]) if len(sys.argv) > 4 else 0.72
    composite_images(sys.argv[1], sys.argv[2], sys.argv[3], scale)
    print(f"Saved: {sys.argv[3]}")
