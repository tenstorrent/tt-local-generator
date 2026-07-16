# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for `SeedImageWell` (SP-3c-1: `.superpowers/sdd/task-1-brief.md`,
section 3c-1 of `docs/superpowers/specs/2026-07-13-sp3c-migrate-into-
create-design.md`) — the reusable seed-image / i2i conditioning-image well
shared by `ImageParamPanel` and `VideoParamPanel` (`app/create_param_panels.py`).

Adapted from ControlPanel's `_seed_thumb_box` (`main_window.py`) but with no
app-state dependency: a plain `Gtk.FileDialog` (async, try/except on
`open_finish()` per CLAUDE.md) instead of ControlPanel's full
`PickerPopover`/gallery integration, plus a `Gtk.DropTarget` for a single
dropped file.

Public API under test: `path()`, `set_path(path)`, `clear()`. The click/
right-click/drop wiring is exercised via the same "call the handler directly
with a fake dlg/gfile" pattern `test_create_view.py` already uses for
`AnimateParamPanel`'s file pickers — no real dialog or compositor drag is
ever needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

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

from create_param_panels import SeedImageWell


def _make_png(tmp_path: Path, name: str = "seed.png") -> Path:
    """A minimal real file on disk — SeedImageWell.set_path() guards on
    `Path.is_file()`, so tests need an actual file, not just a path string."""
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG header is enough; never decoded by these tests
    return p


# ── construction / basic contract ───────────────────────────────────────────


def test_seed_image_well_is_a_gtk_box():
    well = SeedImageWell()
    assert isinstance(well, Gtk.Box)


def test_path_defaults_to_empty_string():
    well = SeedImageWell()
    assert well.path() == ""


def test_well_starts_without_has_seed_css_class():
    well = SeedImageWell()
    assert not well.has_css_class("has-seed")


# ── set_path / clear ─────────────────────────────────────────────────────────


def test_set_path_to_a_real_file_updates_path(tmp_path):
    well = SeedImageWell()
    png = _make_png(tmp_path)

    well.set_path(str(png))

    assert well.path() == str(png)
    assert well.has_css_class("has-seed")


def test_set_path_rejects_a_directory(tmp_path):
    """Mirrors ControlPanel's `_set_seed_image` guard: a directory passes
    `Path.exists()` but not `Path.is_file()` — accepting it would crash a
    worker's `read_bytes()` call at generation time instead of failing here."""
    well = SeedImageWell()

    well.set_path(str(tmp_path))  # tmp_path is a real directory

    assert well.path() == ""
    assert not well.has_css_class("has-seed")


def test_set_path_rejects_a_nonexistent_file():
    well = SeedImageWell()

    well.set_path("/no/such/file/anywhere.png")

    assert well.path() == ""
    assert not well.has_css_class("has-seed")


def test_set_path_empty_string_is_a_noop_clear():
    well = SeedImageWell()
    well.set_path("")
    assert well.path() == ""


def test_clear_resets_a_set_path(tmp_path):
    well = SeedImageWell()
    png = _make_png(tmp_path)
    well.set_path(str(png))
    assert well.path() == str(png)

    well.clear()

    assert well.path() == ""
    assert not well.has_css_class("has-seed")


def test_set_path_replacing_one_file_with_another(tmp_path):
    well = SeedImageWell()
    first = _make_png(tmp_path, "first.png")
    second = _make_png(tmp_path, "second.png")

    well.set_path(str(first))
    assert well.path() == str(first)

    well.set_path(str(second))
    assert well.path() == str(second)


# ── click -> Gtk.FileDialog (async, per CLAUDE.md) ──────────────────────────


def test_left_click_opens_a_file_dialog(monkeypatch):
    """The well's click handler opens a plain `Gtk.FileDialog` — no
    PickerPopover, no `_store`/history dependency (unlike ControlPanel's
    well). Captures the dialog instance `open()` is called on, proving the
    click path reaches a real `Gtk.FileDialog.open()` call."""
    captured = []

    def _fake_open(self, parent, cancellable, callback):
        captured.append((self, parent, callback))

    monkeypatch.setattr(Gtk.FileDialog, "open", _fake_open)

    well = SeedImageWell()
    well._open_file_dialog()

    assert len(captured) == 1
    dlg, _parent, callback = captured[0]
    assert isinstance(dlg, Gtk.FileDialog)
    assert callback == well._on_file_picked


def test_file_picked_callback_sets_path_from_gfile(tmp_path):
    """Simulates a completed `Gtk.FileDialog.open()` round-trip without
    opening a real dialog — same pattern `test_create_view.py` uses for
    `AnimateParamPanel`'s file pickers."""
    well = SeedImageWell()
    png = _make_png(tmp_path)

    class _FakeGFile:
        def get_path(self):
            return str(png)

    class _FakeDlg:
        def open_finish(self, _result):
            return _FakeGFile()

    well._on_file_picked(_FakeDlg(), None)

    assert well.path() == str(png)


def test_file_picked_cancel_does_not_raise_or_change_path(tmp_path):
    """`open_finish()` raises when the user cancels — the well must swallow
    that (CLAUDE.md's FileDialog try/except pattern) and leave the existing
    seed image untouched."""
    well = SeedImageWell()
    png = _make_png(tmp_path)
    well.set_path(str(png))

    class _FakeDlg:
        def open_finish(self, _result):
            raise Exception("cancelled")

    well._on_file_picked(_FakeDlg(), None)  # must not raise

    assert well.path() == str(png)


# ── right-click -> clear ─────────────────────────────────────────────────────


def test_clear_via_public_api_matches_right_click_handler(tmp_path):
    """The right-click gesture is wired straight to `clear()` — exercised
    here via the public method directly (GestureClick "released" signals
    aren't practical to synthesize headlessly), proving the same effect."""
    well = SeedImageWell()
    well.set_path(str(_make_png(tmp_path)))

    well.clear()

    assert well.path() == ""


# ── drop target ──────────────────────────────────────────────────────────────


def test_on_drop_sets_path_from_dropped_file(tmp_path):
    well = SeedImageWell()
    png = _make_png(tmp_path)

    class _FakeGioFile:
        def get_path(self):
            return str(png)

    accepted = well._on_drop(None, _FakeGioFile(), 0, 0)

    assert accepted is True
    assert well.path() == str(png)


def test_on_drop_with_unresolvable_path_rejects_without_raising():
    well = SeedImageWell()

    class _NoPathFile:
        def get_path(self):
            return None

    accepted = well._on_drop(None, _NoPathFile(), 0, 0)

    assert accepted is False
    assert well.path() == ""


def test_on_drop_none_value_does_not_raise():
    well = SeedImageWell()
    assert well._on_drop(None, None, 0, 0) is False
