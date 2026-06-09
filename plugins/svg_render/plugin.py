# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
SVG render utility plugin.

Converts a vector SVG logo to a raster PNG, ready for RMBG background
removal and FLUX decoration pipelines.  Tries cairosvg first (pip install
cairosvg), falls back to inkscape CLI.

In-process usage:
    from plugins.svg_render.plugin import svg_to_png, is_available
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_available: bool | None = None


def is_available() -> bool:
    global _available
    if _available is not None:
        return _available
    try:
        import cairosvg  # noqa: F401
        _available = True
        return _available
    except ImportError:
        pass
    if shutil.which("inkscape"):
        _available = True
        return _available
    _available = False
    return _available


def svg_to_png(svg_path: str, output_path: str, size: int = 1024) -> None:
    """Render *svg_path* to a raster PNG at *size*×*size* pixels.

    The SVG is scaled to fit within *size*×*size* with white padding to fill
    the square canvas.  Output is always a square PNG.

    Args:
        svg_path:    Path to the source SVG file.
        output_path: Destination PNG path.
        size:        Canvas size in pixels (default 1024).
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Try cairosvg first
    try:
        import cairosvg
        from PIL import Image
        import io

        png_bytes = cairosvg.svg2png(
            url=str(Path(svg_path).resolve()),
            output_width=size,
            output_height=size,
        )
        # Paste onto white square canvas to normalise any transparency
        src = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
        # Centre the rendered image
        ox = (size - src.width) // 2
        oy = (size - src.height) // 2
        canvas.paste(src, (ox, oy), src)
        canvas.convert("RGB").save(output_path)
        return
    except ImportError:
        pass
    except Exception as e:
        print(f"[svg_render] cairosvg failed: {e}, trying inkscape")

    # Fall back to inkscape
    if shutil.which("inkscape"):
        subprocess.run(
            [
                "inkscape",
                f"--export-filename={output_path}",
                f"--export-width={size}",
                f"--export-height={size}",
                str(svg_path),
            ],
            check=True,
            capture_output=True,
        )
        return

    raise RuntimeError(
        "svg_render: neither cairosvg nor inkscape is available. "
        "Install one: pip install cairosvg  OR  apt install inkscape"
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: plugin.py <input.svg> <output.png> [size]")
        sys.exit(1)
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 1024
    svg_to_png(sys.argv[1], sys.argv[2], size)
    print(f"Saved: {sys.argv[2]}")
