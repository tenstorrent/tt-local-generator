# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for `MainWindow._create_inspire_fn` — CreateView's "Inspire me" seam
(regression fix 1/2, restoring the two-mode Inspire behavior the deleted
ControlPanel/ArtgenPanel Inspire buttons had: empty seed -> fresh generation;
non-empty seed -> the backend polishes/remixes the existing words).

The regression: `_create_inspire_fn` hardcoded `""` as the seed passed to
`prompt_client.generate_prompt`, so Create's Inspire button could only ever
generate from scratch — even when the brief already had text in it. This
file pins the fix: whatever `seed_text` CreateView passes through the (now
4-arg) `inspire_fn(prompt_type, seed_text, on_result, on_error)` seam must
reach `prompt_client.generate_prompt` unchanged.

Mirrors the `__new__` + unbound-method-binding harness style established in
tests/test_main_window_create_generate.py: construct a minimal MainWindow via
`__new__` (skipping the heavy `Gtk.ApplicationWindow.__init__`), bind only
`_create_inspire_fn`, and monkeypatch `threading.Thread`/`GLib.idle_add` to
run synchronously so the background-thread seam is exercised inline without
a real thread or event loop.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class _ImmediateThread:
    """threading.Thread stand-in that runs its target synchronously on start()."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _make_mw(monkeypatch):
    """Minimal MainWindow exposing only what `_create_inspire_fn` touches."""
    import main_window as mw

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    obj._prompt_gen_system_prompt = "a test system prompt"
    obj._create_inspire_fn = mw.MainWindow._create_inspire_fn.__get__(obj)

    monkeypatch.setattr(mw.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(mw.GLib, "idle_add", lambda fn, *a: fn(*a))

    return obj


def test_empty_seed_text_reaches_generate_prompt_as_fresh_mode(monkeypatch):
    """Empty seed_text (idea-door entry starts empty) -> generate_prompt is
    called with "" -> fresh three-tier generation (algo/markov/LLM)."""
    obj = _make_mw(monkeypatch)
    calls = []

    def _fake_generate_prompt(source, seed_text, system_prompt):
        calls.append((source, seed_text, system_prompt))
        return "a fresh prompt"

    import main_window as mw
    monkeypatch.setattr(mw.prompt_client, "generate_prompt", _fake_generate_prompt)

    results = []
    obj._create_inspire_fn("video", "", results.append, MagicMock())

    assert calls == [("video", "", "a test system prompt")]
    assert results == ["a fresh prompt"]


def test_nonempty_seed_text_reaches_generate_prompt_unchanged(monkeypatch):
    """Non-empty seed_text (existing brief text) -> generate_prompt receives
    THAT text, not "" -- this is the regression fix: previously the seed was
    hardcoded to "" regardless of what the caller passed."""
    obj = _make_mw(monkeypatch)
    calls = []

    def _fake_generate_prompt(source, seed_text, system_prompt):
        calls.append((source, seed_text, system_prompt))
        return "a castle reimagined in cyberpunk neon"

    import main_window as mw
    monkeypatch.setattr(mw.prompt_client, "generate_prompt", _fake_generate_prompt)

    results = []
    obj._create_inspire_fn("image", "a castle", results.append, MagicMock())

    assert calls == [("image", "a castle", "a test system prompt")]
    assert results == ["a castle reimagined in cyberpunk neon"]


def test_generate_prompt_exception_routes_to_on_error(monkeypatch):
    obj = _make_mw(monkeypatch)

    import main_window as mw

    def _boom(source, seed_text, system_prompt):
        raise RuntimeError("prompt server down")

    monkeypatch.setattr(mw.prompt_client, "generate_prompt", _boom)

    errors = []
    obj._create_inspire_fn("video", "a castle", MagicMock(), errors.append)

    assert errors == ["prompt server down"]
