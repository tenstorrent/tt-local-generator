"""
Tests for Task 7: routing the Video medium's Animate model to
AnimateGenerationWorker via _native_generate_args.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

import main_window as mw
from create_mediums import Medium


def _mw():
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        return mw.MainWindow.__new__(mw.MainWindow)


VIDEO = Medium(
    id="video", label="Video", icon="🎥", kind="video", source="native", generator=None)


def test_video_animate_model_routes_to_animate_source():
    obj = _mw()
    params = {"prompt": "hi", "model": "wan2.2-animate-14b",
              "reference_video_path": "/m.mp4", "reference_image_path": "/c.png",
              "animate_mode": "replacement"}
    _args, kwargs = obj._native_generate_args(VIDEO, params)
    assert kwargs["model_source"] == "animate"
    assert kwargs["ref_video_path"] == "/m.mp4"
    assert kwargs["ref_char_path"] == "/c.png"
    assert kwargs["animate_mode"] == "replacement"


def test_video_animatediff_model_unchanged():
    obj = _mw()
    _a, kw = obj._native_generate_args(VIDEO, {"prompt": "x", "model": "animatediff-blackhole"})
    assert kw["model_source"] == "video"
    assert kw["video_model_key"] == "animatediff"
    assert kw["animatediff_args"] is not None


def test_video_plain_model_unchanged():
    obj = _mw()
    _a, kw = obj._native_generate_args(VIDEO, {"prompt": "x", "model": "wan2.2-t2v"})
    assert kw["model_source"] == "video"
    assert kw["video_model_key"] == "wan2"
    assert kw["animatediff_args"] is None
