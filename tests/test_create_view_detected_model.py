# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
GTK widget tests for Task 3 of the "running chat model identity" program
(.superpowers/sdd/task-3-brief.md): a running chat-LLM model that matches no
registered `server_manager.SERVERS` key must surface as its own SELECTABLE
"detected" entry in Create's Text/artgen scoped dropdown AND the Model door's
"Text" group.

This builds directly on Tasks 1 & 2 (already merged): `match_model_id`
(app/model_status.py) resolves a detected `/v1/models` id to the ONE
registered server key it belongs to (or `None`), and
`ModelStatusService.running_artgen_model()` exposes that as an
`ArtgenModelInfo(model_id, url, matched_key)` (or `None` when nothing is
running). This file never touches the real service — it injects a tiny fake
exposing exactly the surface CreateView consumes, mirroring
`_FakeStatusService` in test_create_view.py (kept as a separate class here,
per that file's own "duplicated ... to match this file's existing convention"
precedent for `_make_view`/`make_create_view`, since this fake needs one
extra method — `running_artgen_model()` — the shared fake doesn't have).

Creating GTK widgets needs a display; the full suite runs under xvfb. When no
display is available this module skips itself (matches every other
test_create_view*.py file's headless fallback).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

# Skip the whole module if a GTK display/widget cannot be created (headless).
try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

from create_mediums import Medium
from model_status import ArtgenModelInfo, Status


def _fake_mediums():
    """Same shape as test_create_view.py's `_fake_mediums()` — one native
    medium per kind plus exactly one artgen medium ("verse", kind "text").
    The detected-model entry only applies to artgen-source mediums, so
    "verse" is the medium every test below switches to."""
    return [
        Medium(id="image", label="Image", icon="\U0001f5bc️", kind="image",
               source="native", generator=None),
        Medium(id="video", label="Video", icon="\U0001f3a5", kind="video",
               source="native", generator=None),
        Medium(id="animate", label="Animate", icon="\U0001f483", kind="gif",
               source="native", generator=None),
        Medium(id="verse", label="Verse", icon="✍", kind="text",
               source="artgen", generator="verse"),
    ]


def _fake_health():
    return {"wan2.2": True, "flux": False}


class _ImmediateThread:
    """threading.Thread stand-in that runs its target synchronously on
    start() — mirrors test_create_view.py's own helper so the health-refresh
    background thread never races a real daemon thread in these tests."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _sync_create_view_threading(monkeypatch):
    import create_view
    monkeypatch.setattr(create_view.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(create_view.GLib, "idle_add", lambda fn, *a: fn(*a))


def _make_view(monkeypatch, **kwargs):
    _sync_create_view_threading(monkeypatch)
    from create_view import CreateView
    kwargs.setdefault("mediums_fn", _fake_mediums)
    kwargs.setdefault("health_fn", _fake_health)
    return CreateView(**kwargs)


class _FakeStatusServiceWithArtgenModel:
    """Fake `ModelStatusService` exposing `snapshot()`/`subscribe()`/
    `running_or_starting()` (the pre-Task-3 surface, see
    test_create_view.py's `_FakeStatusService`) PLUS `running_artgen_model()`
    — the Task 2 accessor this task's CreateView code newly consumes.

    `artgen_model`: an `ArtgenModelInfo` or `None`, returned verbatim by
    `running_artgen_model()`.
    `running`: capability -> server_key map backing `running_or_starting`
    (defaults to `{}` — "nothing running/starting" for every capability).
    `snapshot`: the per-key `Status` map `_model_dot_glyph` reads (defaults to
    `{}` — every key reads OFF/"◌" unless a test seeds it, matching the real
    service's "never polled yet" default).
    """

    def __init__(self, artgen_model=None, running=None, snapshot=None):
        self._artgen_model = artgen_model
        self._running = dict(running or {})
        self._snap = dict(snapshot or {})
        self.subscribers: list = []

    def running_artgen_model(self):
        return self._artgen_model

    def running_or_starting(self, capability: str):
        return self._running.get(capability)

    def snapshot(self) -> dict:
        return dict(self._snap)

    def subscribe(self, cb):
        self.subscribers.append(cb)

        def _unsub() -> None:
            if cb in self.subscribers:
                self.subscribers.remove(cb)

        return _unsub


def _switch_to_verse(view) -> None:
    """Activate the one artgen medium in `_fake_mediums()` — this is a FRESH
    populate of the scoped dropdown (medium swap away from the default-active
    "image"), which is the branch auto-select applies on."""
    view._chip_buttons["verse"].set_active(True)


def _dropdown_labels(view) -> "list[str]":
    model = view._model_dropdown.get_model()
    return [model.get_string(i) for i in range(model.get_n_items())]


# ── Unknown detected model: exactly one synthetic, selectable entry ──────────

def test_unknown_detected_model_appears_once_in_scoped_dropdown(monkeypatch):
    info = ArtgenModelInfo("qwen3.6-27b", "http://localhost:9009/v1", None)
    fake_service = _FakeStatusServiceWithArtgenModel(artgen_model=info)
    view = _make_view(monkeypatch, status_service=fake_service)
    _switch_to_verse(view)

    labels = _dropdown_labels(view)
    detected = [l for l in labels if "qwen3.6-27b" in l]
    assert len(detected) == 1
    assert "(detected)" in detected[0]
    assert detected[0].startswith("●")  # ● READY dot


def test_unknown_detected_model_is_selectable_in_scoped_dropdown(monkeypatch):
    info = ArtgenModelInfo("qwen3.6-27b", "http://localhost:9009/v1", None)
    fake_service = _FakeStatusServiceWithArtgenModel(artgen_model=info)
    view = _make_view(monkeypatch, status_service=fake_service)
    _switch_to_verse(view)

    labels = _dropdown_labels(view)
    idx = next(i for i, l in enumerate(labels) if "qwen3.6-27b" in l)
    view._model_dropdown.set_selected(idx)

    assert view._selected_model_key() == "__detected__:qwen3.6-27b"


def test_unknown_detected_model_appears_in_model_door_text_group(monkeypatch):
    info = ArtgenModelInfo("qwen3.6-27b", "http://localhost:9009/v1", None)
    fake_service = _FakeStatusServiceWithArtgenModel(artgen_model=info)
    view = _make_view(monkeypatch, status_service=fake_service)

    groups = view._model_door_groups()
    detected_keys = [k for k in groups["Text"] if "qwen3.6-27b" in k]
    assert len(detected_keys) == 1
    assert detected_keys[0] == "__detected__:qwen3.6-27b"

    # Also exercise the real widget-building path (no crash rendering the
    # synthetic card) and confirm its label carries "(detected)".
    door = view._build_model_door()

    def _all_labels(widget) -> "list[str]":
        out = []
        if isinstance(widget, Gtk.Label):
            out.append(widget.get_label())
        child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
        while child is not None:
            out.extend(_all_labels(child))
            child = child.get_next_sibling()
        return out

    all_labels = _all_labels(door)
    assert any("qwen3.6-27b" in l and "(detected)" in l for l in all_labels)


def test_autoselect_selects_the_detected_entry_on_fresh_populate(monkeypatch):
    """SP-2's `_autoselect_running_model_index` fresh-populate branch must
    default to the synthetic detected entry when the running model is
    unmatched — the brief's "auto-select" requirement."""
    info = ArtgenModelInfo("qwen3.6-27b", "http://localhost:9009/v1", None)
    fake_service = _FakeStatusServiceWithArtgenModel(artgen_model=info)
    view = _make_view(monkeypatch, status_service=fake_service)

    _switch_to_verse(view)  # fresh populate for the "verse" medium

    assert view._selected_model_key() == "__detected__:qwen3.6-27b"


# ── Known detected model: no synthetic entry, correct per-key dots ──────────

def test_known_running_model_adds_no_synthetic_entry(monkeypatch):
    info = ArtgenModelInfo("Qwen/Qwen3-8B", "http://localhost:8002/v1", "artgen-qwen3-8b")
    fake_service = _FakeStatusServiceWithArtgenModel(
        artgen_model=info,
        running={"artgen": "artgen-qwen3-8b"},
        snapshot={"artgen-qwen3-8b": Status.READY, "artgen-llama-3.3-70b": Status.OFF},
    )
    view = _make_view(monkeypatch, status_service=fake_service)
    _switch_to_verse(view)

    labels = _dropdown_labels(view)
    assert not any("(detected)" in l for l in labels)

    groups = view._model_door_groups()
    assert not any("__detected__" in k for k in groups["Text"])


def test_known_running_model_dots_are_model_specific(monkeypatch):
    info = ArtgenModelInfo("Qwen/Qwen3-8B", "http://localhost:8002/v1", "artgen-qwen3-8b")
    fake_service = _FakeStatusServiceWithArtgenModel(
        artgen_model=info,
        running={"artgen": "artgen-qwen3-8b"},
        snapshot={"artgen-qwen3-8b": Status.READY, "artgen-llama-3.3-70b": Status.OFF},
    )
    view = _make_view(monkeypatch, status_service=fake_service)

    assert view._model_dot_glyph("artgen-qwen3-8b") == "●"   # ●
    assert view._model_dot_glyph("artgen-llama-3.3-70b") == "◌"  # ◌


def test_autoselect_selects_the_matched_key_when_running_model_is_known(monkeypatch):
    info = ArtgenModelInfo("Qwen/Qwen3-8B", "http://localhost:8002/v1", "artgen-qwen3-8b")
    fake_service = _FakeStatusServiceWithArtgenModel(
        artgen_model=info,
        running={"artgen": "artgen-qwen3-8b"},
        snapshot={"artgen-qwen3-8b": Status.READY},
    )
    view = _make_view(monkeypatch, status_service=fake_service)

    _switch_to_verse(view)  # fresh populate for the "verse" medium

    assert view._selected_model_key() == "artgen-qwen3-8b"


# ── No running model: no synthetic entry anywhere ───────────────────────────

def test_no_running_model_adds_no_synthetic_entry(monkeypatch):
    fake_service = _FakeStatusServiceWithArtgenModel(artgen_model=None)
    view = _make_view(monkeypatch, status_service=fake_service)
    _switch_to_verse(view)

    labels = _dropdown_labels(view)
    assert not any("(detected)" in l for l in labels)

    groups = view._model_door_groups()
    assert not any("__detected__" in k for k in groups["Text"])


# ── collect() invariant: byte-for-byte unaffected by the detected entry ─────

def test_artgen_collect_is_unchanged_with_and_without_detected_entry(monkeypatch):
    """CRITICAL invariant (task-3-brief.md): the artgen medium's collected
    params dict must be identical whether or not a detected model is present
    — the synthetic entry is display/selection only, never a "model" field.
    """
    view_plain = _make_view(monkeypatch)  # status_service=None
    _switch_to_verse(view_plain)
    collected_plain = view_plain._collect_params()

    info = ArtgenModelInfo("qwen3.6-27b", "http://localhost:9009/v1", None)
    fake_service = _FakeStatusServiceWithArtgenModel(artgen_model=info)
    view_detected = _make_view(monkeypatch, status_service=fake_service)
    _switch_to_verse(view_detected)
    # Select the synthetic entry itself, the strictest case for "inert".
    labels = _dropdown_labels(view_detected)
    idx = next(i for i, l in enumerate(labels) if "qwen3.6-27b" in l)
    view_detected._model_dropdown.set_selected(idx)
    collected_detected = view_detected._collect_params()

    assert "model" not in collected_plain
    assert "model" not in collected_detected
    assert collected_plain == collected_detected


# ── Native mediums are unaffected (guard against over-broad injection) ──────

def test_native_image_medium_dropdown_never_gets_a_detected_entry(monkeypatch):
    """The synthetic entry is gated to `medium.source == "artgen"` — a native
    medium (which DOES collect a real "model" field) must never see it, even
    when an unmatched chat model happens to be running."""
    info = ArtgenModelInfo("qwen3.6-27b", "http://localhost:9009/v1", None)
    fake_service = _FakeStatusServiceWithArtgenModel(artgen_model=info)
    view = _make_view(monkeypatch, status_service=fake_service)
    # "image" is the default-active medium — no chip switch needed.

    labels = _dropdown_labels(view)
    assert not any("(detected)" in l for l in labels)
    assert "model" in view._collect_params()
