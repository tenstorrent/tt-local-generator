# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Task 6 (SDD task-6-brief.md): the reveal-on-demand "Animate needs" section
in the Video form — Motion video / Character image / Mode inputs shown only
when the scoped model dropdown's current selection is the Animate model.

Two pure/widget-level units, tested standalone (no full CreateView needed):

  - `_animate_extras_visible_for(model_key)` — the pure visibility predicate.
  - `_AnimateExtras` — the GTK widget exposing `.collect()` / `.set_paths()`
    / `.set_mode()` test seams, built from the shared path-picker-row /
    mode-toggle-row helpers factored out of `AnimateParamPanel` (see
    `create_param_panels.build_path_picker_row`/`build_mode_toggle_row`).

Plus the hard invariant (Step 6): a Video job with a NON-animate model must
never gain `reference_video_path`/`reference_image_path`/`animate_mode` keys
in `_collect_params()` — the guard that keeps this reveal section from
leaking into every other model's collect() dict.
"""
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


def test_animate_extras_collect_shape():
    ex = cv._AnimateExtras()
    ex.set_paths("/m.mp4", "/c.png")   # test helper to set without a dialog
    ex.set_mode("replacement")
    got = ex.collect()
    assert got == {"reference_video_path": "/m.mp4",
                   "reference_image_path": "/c.png",
                   "animate_mode": "replacement"}


def test_reveal_visible_only_for_animate():
    # visibility helper is pure logic on the selected key
    assert cv._animate_extras_visible_for("animate") is True
    assert cv._animate_extras_visible_for("wan2.2") is False
    assert cv._animate_extras_visible_for("animatediff") is False
    assert cv._animate_extras_visible_for(None) is False


def test_animate_extras_starts_hidden():
    """The reveal section is chrome that appears only once the Animate model
    is picked — it must never be visible at construction time."""
    ex = cv._AnimateExtras()
    assert ex.get_visible() is False


def test_animate_extras_default_mode_is_animation():
    """Mirrors AnimateParamPanel's own default (`_ANIMATE_MODE_ANIMATION`) —
    an untouched mode toggle collects "animation", not an empty/None value."""
    ex = cv._AnimateExtras()
    got = ex.collect()
    assert got["animate_mode"] == "animation"
    assert got["reference_video_path"] == ""
    assert got["reference_image_path"] == ""


class _FakeDropdown:
    """Stand-in for `self._model_dropdown` — `_collect_params`'s "model"
    override block (unconditionally, when a `collect()` dict carries a
    "model" key) calls `self._model_dropdown.get_selected()`. `entries`
    defaults to `[]` via `getattr(self, "_model_dropdown_entries", [])`
    when unset, so `0 <= idx < len(entries)` is False and the override is
    skipped — this fake exists purely to avoid an AttributeError on a
    `CreateView.__new__` instance that never ran `__init__`."""
    def get_selected(self):
        return 0


def test_collect_params_unchanged_for_non_animate(monkeypatch):
    # A video job with a non-animate model must not gain animate keys.
    view = cv.CreateView.__new__(cv.CreateView)
    view._animate_extras = cv._AnimateExtras()
    view._animate_extras.set_paths("/should-not-leak.mp4", "/x.png")
    monkeypatch.setattr(view, "_selected_model_key", lambda: "wan2.2")
    view._model_dropdown = _FakeDropdown()
    # minimal params source: fake active panel returning a base dict
    class _P:
        def collect(self): return {"prompt": "p", "model": "wan2.2-t2v"}
        def applied_modifier_text(self): return ""
    view._active_panel = _P()
    view._prompt_entry = None  # exercise the no-prompt-entry branch if present
    params = view._collect_params()
    assert "reference_video_path" not in params
    assert "reference_image_path" not in params
    assert "animate_mode" not in params


def test_collect_params_includes_animate_keys_when_animate_selected(monkeypatch):
    """The mirror-image case of the guard above: when the active medium is
    "video" and the Animate model IS selected, the extras' current values DO
    fold into params. (`_active_medium` must be set to the "video" medium —
    `_collect_params`'s fold is gated on both, not the model key alone; see
    that method's docstring for why the pre-existing native "animate"
    medium test fixture needs the merge to NOT fire.)"""
    view = cv.CreateView.__new__(cv.CreateView)
    view._animate_extras = cv._AnimateExtras()
    view._animate_extras.set_paths("/motion.mp4", "/char.png")
    view._animate_extras.set_mode("replacement")
    view._active_medium = Medium(id="video", label="Video", icon="\U0001f3a5",
                                  kind="video", source="native", generator=None)
    monkeypatch.setattr(view, "_selected_model_key", lambda: "animate")
    view._model_dropdown = _FakeDropdown()

    class _P:
        def collect(self): return {"prompt": "p", "model": "wan2.2-animate-14b"}
        def applied_modifier_text(self): return ""
    view._active_panel = _P()
    view._prompt_entry = None
    params = view._collect_params()
    assert params["reference_video_path"] == "/motion.mp4"
    assert params["reference_image_path"] == "/char.png"
    assert params["animate_mode"] == "replacement"
