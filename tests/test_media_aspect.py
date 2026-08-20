"""Regression test: _load_pixbuf must preserve aspect ratio (no stretch).

The detail pane used GdkPixbuf.scale_simple(w, h) which force-stretched images
to a fixed 16:9 box, distorting square/portrait media. It now uses
new_from_file_at_scale(..., preserve_aspect_ratio=True) so images fit (letterbox)
inside the box with their native proportions intact.
"""
import sys
from pathlib import Path

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

import main_window  # noqa: E402


def _write_png(tmp_path, name, w, h):
    """Create a solid PNG of exactly w×h and return its path."""
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    pb.fill(0x3366ffff)
    out = tmp_path / name
    pb.savev(str(out), "png", [], [])
    return str(out)


def _aspect(pb):
    return pb.get_width() / pb.get_height()


def _close(a, b):
    # Aspect is preserved up to integer-pixel rounding of the fitted dimensions.
    return abs(a - b) < 0.02


def test_square_source_stays_square_in_16x9_box(tmp_path):
    # 1024×1024 square (like FLUX) into a 400×225 (16:9) box.
    src = _write_png(tmp_path, "square.png", 1024, 1024)
    pb = main_window._load_pixbuf(src, 400, 225)
    assert pb is not None
    # Height-constrained → 225×225, aspect preserved (1.0), NOT stretched to 400×225.
    assert _close(_aspect(pb), 1.0)
    assert pb.get_width() <= 400 and pb.get_height() <= 225


def test_widescreen_source_preserves_ratio(tmp_path):
    # 2:1 source into 400×225 → width-constrained to 400×200 (still 2:1).
    src = _write_png(tmp_path, "wide.png", 1000, 500)
    pb = main_window._load_pixbuf(src, 400, 225)
    assert pb is not None
    assert _close(_aspect(pb), 2.0)
    assert pb.get_width() <= 400 and pb.get_height() <= 225


def test_portrait_source_preserves_ratio(tmp_path):
    # Portrait 1:2 into 400×225 → height-constrained, still 0.5 aspect.
    src = _write_png(tmp_path, "tall.png", 500, 1000)
    pb = main_window._load_pixbuf(src, 400, 225)
    assert pb is not None
    assert _close(_aspect(pb), 0.5)
    assert pb.get_width() <= 400 and pb.get_height() <= 225


def test_missing_file_returns_none():
    assert main_window._load_pixbuf("/no/such/image.png", 400, 225) is None
