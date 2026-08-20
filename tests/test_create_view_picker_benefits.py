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

def test_animate_server_files_under_video_group():
    assert cv._CAPABILITY_TO_MODEL_DOOR_GROUP["animate"] == "Video"
    assert "Animate" not in cv._MODEL_DOOR_GROUP_ORDER

def test_collapsed_dropdown_labels_use_friendly_names_not_raw_server_labels():
    """Finding 2 regression test: the resting/selected DropDown button
    renders straight off the `Gtk.StringList` built in
    `_populate_model_dropdown` (the two-line factory is list-only, per that
    method's own comment) — so a raw `ServerDef.label` implementation string
    ("Wan2.2-T2V-A14B  (P300X2)") must never leak into `labels`, only into
    `_model_dropdown_entries`' 3rd element (selection bookkeeping, never
    rendered directly)."""
    view = cv.CreateView.__new__(cv.CreateView)
    view._active_medium = Medium(id="video", label="Video", icon="🎥",
                                 kind="video", source="native", generator=None)
    view._status_service = None
    view._model_health = {}
    view._model_dropdown = Gtk.DropDown()
    view._populate_model_dropdown(view._active_medium)

    model = view._model_dropdown.get_model()
    labels = [model.get_string(i) for i in range(model.get_n_items())]

    assert any(label.endswith("Wan 2.2") for label in labels)
    assert not any("Wan2.2-T2V-A14B" in label for label in labels)

    # entries' own label_text (selection bookkeeping only) is untouched —
    # still the raw ServerDef.label, per the fix's "do not change
    # `_model_dropdown_entries`" constraint.
    entry_labels = {k: label for k, _c, label in view._model_dropdown_entries}
    assert entry_labels["wan2.2"] == "Wan2.2-T2V-A14B  (P300X2)"


def test_row_meta_carries_friendly_name_and_benefit():
    view = cv.CreateView.__new__(cv.CreateView)
    view._active_medium = Medium(id="video", label="Video", icon="🎥",
                                 kind="video", source="native", generator=None)
    view._status_service = None
    view._model_health = {}
    # build a minimal dropdown widget the populate needs
    view._model_dropdown = Gtk.DropDown()
    view._populate_model_dropdown(view._active_medium)
    meta = view._model_row_meta
    names = [m[0] for m in meta]
    assert "AnimateDiff" in names and "Wan 2.2" in names and "Animate" in names
    # animatediff row carries its benefit
    ad = next(m for m in meta if m[0] == "AnimateDiff")
    assert "local" in ad[1].lower()
