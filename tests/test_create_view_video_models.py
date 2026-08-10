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
import create_param_panels as cpp
from create_mediums import Medium

VIDEO = Medium(id="video", label="Video", icon="🎥", kind="video", source="native", generator=None)

def test_video_scoped_keys_animatediff_first_and_animate_present(monkeypatch):
    view = cv.CreateView.__new__(cv.CreateView)
    view._active_medium = VIDEO
    view._status_service = None
    keys = view._scoped_model_keys(VIDEO)
    assert keys == ["animatediff", "wan2.2", "mochi", "skyreels", "wan2.2-i2v", "animate"]

def test_animate_canonical_resolves_for_video():
    assert cv._canonical_model_id_for(VIDEO, "animate") == "wan2.2-animate-14b"

def test_video_model_id_to_key_inverts_animate():
    import main_window as mw
    assert mw._VIDEO_MODEL_ID_TO_KEY["wan2.2-animate-14b"] == "animate"
    assert cpp._VIDEO_MODEL_IDS["animate"] == "wan2.2-animate-14b"

def test_video_model_id_to_key_inverts_wan_i2v():
    import main_window as mw
    assert mw._VIDEO_MODEL_ID_TO_KEY["wan2.2-i2v-a14b"] == "wan2.2-i2v"
    assert cpp._VIDEO_MODEL_IDS["wan2.2-i2v"] == "wan2.2-i2v-a14b"
