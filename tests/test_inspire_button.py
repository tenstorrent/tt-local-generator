# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for `create_param_panels.attach_inspire_button` — the shared "✨
Inspire" helper any prompt `Gtk.Entry` can attach (regression fix 1/2's
step (b): ONE reusable implementation, so Task 2's pipeline-editor field
entries and Create's own idea-door entry both drive the exact same two-mode
click contract instead of forking it).

Two-mode contract (mirrors `prompt_client.generate_prompt`'s own doc):
  - entry is EMPTY at click time  -> seed_text=""       (fresh generation)
  - entry has TEXT at click time  -> seed_text=<that text> (remix/polish)

`inspire_fn(prompt_type, seed_text, on_result, on_error)` is injected so
these tests never touch a real subprocess, network call, or thread — a fake
that invokes `on_result`/`on_error` synchronously is enough to prove the
button's loading-state toggle and entry-fill wiring.
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

import create_param_panels as cpp


@pytest.fixture(autouse=True)
def _synchronous_idle_add(monkeypatch):
    """`attach_inspire_button`'s result/error callbacks post through
    `GLib.idle_add` (GTK threading rule, CLAUDE.md) -- there's no running
    main loop in these tests to drain that queue, so make it run inline, the
    same pattern `tests/test_create_view.py`'s `_make_view` helper uses."""
    monkeypatch.setattr(cpp.GLib, "idle_add", lambda fn, *a: fn(*a))


class _FakeInspire:
    """Records `(prompt_type, seed_text, on_result, on_error)` calls without
    firing either callback by default -- lets a test drive the loading state
    and then manually invoke whichever callback it wants."""

    def __init__(self):
        self.calls = []

    def __call__(self, prompt_type, seed_text, on_result, on_error):
        self.calls.append((prompt_type, seed_text, on_result, on_error))


def _make_entry_and_button(inspire_fn, prompt_type="video"):
    entry = Gtk.Entry()
    btn = cpp.attach_inspire_button(entry, lambda: prompt_type, inspire_fn)
    return entry, btn


def test_returns_a_gtk_button():
    entry, btn = _make_entry_and_button(_FakeInspire())
    assert isinstance(btn, Gtk.Button)


def test_click_with_empty_entry_passes_empty_seed_text():
    fake = _FakeInspire()
    entry, btn = _make_entry_and_button(fake)

    btn.emit("clicked")

    assert len(fake.calls) == 1
    prompt_type, seed_text, _on_result, _on_error = fake.calls[0]
    assert prompt_type == "video"
    assert seed_text == ""


def test_click_with_existing_text_passes_it_as_seed():
    fake = _FakeInspire()
    entry, btn = _make_entry_and_button(fake)
    entry.set_text("a lighthouse at dawn")

    btn.emit("clicked")

    assert fake.calls[0][1] == "a lighthouse at dawn"


def test_click_strips_whitespace_from_seed_text():
    fake = _FakeInspire()
    entry, btn = _make_entry_and_button(fake)
    entry.set_text("   a lighthouse at dawn   ")

    btn.emit("clicked")

    assert fake.calls[0][1] == "a lighthouse at dawn"


def test_button_disables_while_generating_and_reenables_on_result():
    fake = _FakeInspire()
    entry, btn = _make_entry_and_button(fake)

    btn.emit("clicked")
    assert btn.get_sensitive() is False  # loading state

    _prompt_type, _seed_text, on_result, _on_error = fake.calls[0]
    on_result("a golden fox in a neon forest")

    assert entry.get_text() == "a golden fox in a neon forest"
    assert btn.get_sensitive() is True


def test_on_error_reenables_button_and_leaves_entry_untouched():
    fake = _FakeInspire()
    entry, btn = _make_entry_and_button(fake)
    entry.set_text("a castle")

    btn.emit("clicked")
    _prompt_type, _seed_text, _on_result, on_error = fake.calls[0]
    on_error("prompt server is down")  # must not raise

    assert btn.get_sensitive() is True
    assert entry.get_text() == "a castle"  # untouched on error


def test_inspire_fn_raising_synchronously_is_fail_soft():
    def _boom(prompt_type, seed_text, on_result, on_error):
        raise RuntimeError("boom")

    entry, btn = _make_entry_and_button(_boom)
    btn.emit("clicked")  # must not raise

    assert btn.get_sensitive() is True


def test_second_click_while_generating_is_ignored():
    """No re-entrant call while a generation is already in flight -- matches
    CreateView's own `_inspire_generating` guard."""
    fake = _FakeInspire()
    entry, btn = _make_entry_and_button(fake)

    btn.emit("clicked")
    btn.emit("clicked")  # button is insensitive, but guard defensively anyway

    assert len(fake.calls) == 1


def test_prompt_type_getter_is_read_fresh_on_each_click():
    """The prompt type is resolved via the getter AT CLICK TIME, not baked in
    at attach time -- so a caller whose active medium changes between clicks
    (e.g. CreateView switching chips) gets the current value."""
    fake = _FakeInspire()
    entry = Gtk.Entry()
    state = {"kind": "image"}
    btn = cpp.attach_inspire_button(entry, lambda: state["kind"], fake)

    btn.emit("clicked")
    fake.calls[0][2]("result one")  # resolve so the button re-enables

    state["kind"] = "video"
    btn.emit("clicked")

    assert fake.calls[0][0] == "image"
    assert fake.calls[1][0] == "video"


def test_tooltip_mentions_both_modes():
    entry, btn = _make_entry_and_button(_FakeInspire())
    tooltip = (btn.get_tooltip_text() or "").lower()
    assert "fresh" in tooltip
    assert "typed" in tooltip or "reimagine" in tooltip
