import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pytest
try:
    import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display", allow_module_level=True)

import create_view as cv
from create_mediums import Medium

VIDEO = Medium(id="video", label="Video", icon="🎥", kind="video", source="native", generator=None)
ENTRIES = [("animatediff", "animatediff-blackhole", "AnimateDiff"),
           ("wan2.2", "wan2.2-t2v", "Wan 2.2"),
           ("mochi", "mochi-1-preview", "Mochi"),
           ("skyreels", "skyreels-v2-i2v-14b-540p", "SkyReels"),
           ("animate", "wan2.2-animate-14b", "Animate")]

class _Status:
    def __init__(self, mapping): self.mapping = mapping  # cap -> key or None
    def running_or_starting(self, cap): return self.mapping.get(cap)

def _view(status):
    v = cv.CreateView.__new__(cv.CreateView)
    v._status_service = status
    return v

def test_nothing_running_defaults_to_animatediff():
    v = _view(_Status({}))
    assert ENTRIES[v._autoselect_running_model_index(VIDEO, ENTRIES)][0] == "animatediff"

def test_running_video_server_preferred():
    v = _view(_Status({"video": "mochi"}))
    assert ENTRIES[v._autoselect_running_model_index(VIDEO, ENTRIES)][0] == "mochi"

def test_running_animate_server_preferred():
    v = _view(_Status({"animate": "animate"}))
    assert ENTRIES[v._autoselect_running_model_index(VIDEO, ENTRIES)][0] == "animate"

def test_no_status_service_returns_zero():
    v = _view(None)
    assert v._autoselect_running_model_index(VIDEO, ENTRIES) == 0  # index 0 == animatediff
