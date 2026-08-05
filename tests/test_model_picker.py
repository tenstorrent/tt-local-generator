import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import model_picker as mp
import server_manager as sm

# `picker_entries` is pure and must be importable/testable headless (no
# display at all). `ModelPickerRow` needs a real GTK display, so only ITS
# tests are gated -- probe once and skip just those via the marker below
# (mirrors test_create_view.py's module-level probe, but scoped per-test
# here since this file also carries genuinely headless tests).
try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    Gtk.Entry()  # probe: raises without a usable display
    _GTK_DISPLAY_OK = True
except Exception:  # pragma: no cover - environment-dependent
    _GTK_DISPLAY_OK = False

needs_display = pytest.mark.skipif(
    not _GTK_DISPLAY_OK, reason="no GTK display available"
)


def test_picker_entries_image_lists_image_servers():
    entries = mp.picker_entries("image", snapshot={}, has_service=False)
    keys = [e[0] for e in entries]
    assert set(keys) == {s.key for s in sm.servers_for_capability("image")}
    assert all(len(e) == 4 for e in entries)  # (key, name, benefit, dot)


def test_picker_entries_animatediff_single_synthetic():
    entries = mp.picker_entries("animatediff", snapshot={}, has_service=True)
    assert len(entries) == 1
    key, name, benefit, dot = entries[0]
    assert key == "animatediff" and name == "AnimateDiff" and dot == "●" and benefit


def test_picker_entries_status_glyphs(monkeypatch):
    from model_status import Status
    snap = {s.key: Status.READY for s in sm.servers_for_capability("image")}
    first = mp.picker_entries("image", snapshot=snap, has_service=True)[0]
    assert first[3] == "●"
    snap2 = {k: Status.STARTING for k in snap}
    assert mp.picker_entries("image", snapshot=snap2, has_service=True)[0][3] == "◐"
    assert mp.picker_entries("image", snapshot={}, has_service=True)[0][3] == "◌"  # off/unknown


def test_dot_no_service_is_solid():
    assert mp.picker_entries("image", snapshot={}, has_service=False)[0][3] == "●"


# ── ModelPickerRow (widget, needs a real display) ───────────────────────────


class _FakeStatusService:
    """Minimal stand-in for `model_status.ModelStatusService`, exposing only
    `snapshot()`/`subscribe(cb)` -- the same fake-service discipline used by
    `tests/test_create_view.py`. `push()` mimics a poll tick landing (stores
    the new snapshot, fans it out to subscribers) without touching real
    health checks, sockets, or subprocesses."""

    def __init__(self, initial=None):
        self._snapshot = dict(initial or {})
        self.subscribers = []
        self.unsubscribed = []

    def snapshot(self):
        return dict(self._snapshot)

    def subscribe(self, cb):
        self.subscribers.append(cb)

        def _unsub():
            self.unsubscribed.append(cb)
            if cb in self.subscribers:
                self.subscribers.remove(cb)

        return _unsub

    def push(self, snap):
        self._snapshot = dict(snap)
        for cb in list(self.subscribers):
            cb(self.snapshot())


@needs_display
def test_row_lists_entries_and_defaults_to_index_0():
    row = mp.ModelPickerRow("image")
    expected = [s.key for s in sm.servers_for_capability("image")]
    assert row.selected_key() == expected[0]


@needs_display
def test_row_animatediff_single_entry_autoselects():
    row = mp.ModelPickerRow("animatediff")
    assert row.selected_key() == "animatediff"
    model = row._dropdown.get_model()
    assert model.get_n_items() == 1
    assert model.get_string(0) == "● AnimateDiff"


@needs_display
def test_row_respects_selected_key():
    keys = [s.key for s in sm.servers_for_capability("image")]
    assert len(keys) >= 1
    row = mp.ModelPickerRow("image", selected_key=keys[-1])
    assert row.selected_key() == keys[-1]


@needs_display
def test_row_subscribes_and_rebuilds_dots_on_push(monkeypatch):
    from model_status import Status

    # `ModelPickerRow` posts snapshot rebuilds via `GLib.idle_add` (main-
    # thread marshalling for a real background-thread subscriber). No main
    # loop is pumped in this test, so make it run inline -- same fix
    # `test_create_view.py` uses (`_sync_create_view_threading`) for the
    # identical issue.
    monkeypatch.setattr(mp.GLib, "idle_add", lambda fn, *a: fn(*a))

    keys = [s.key for s in sm.servers_for_capability("image")]
    fake_service = _FakeStatusService({keys[0]: Status.STARTING})
    row = mp.ModelPickerRow("image", status_service=fake_service, selected_key=keys[0])
    assert len(fake_service.subscribers) == 1

    model = row._dropdown.get_model()
    labels = [model.get_string(i) for i in range(model.get_n_items())]
    assert any(label.startswith("◐ ") for label in labels)

    fake_service.push({keys[0]: Status.READY})

    model2 = row._dropdown.get_model()
    labels2 = [model2.get_string(i) for i in range(model2.get_n_items())]
    assert any(label.startswith("● ") for label in labels2)
    # Selection is preserved across the rebuild triggered by the push.
    assert row.selected_key() == keys[0]


@needs_display
def test_row_construction_never_fires_on_change():
    """Building a row (even with a non-default `selected_key=`) is a
    programmatic selection, not a user action -- `on_change` must never fire
    just from constructing the widget."""
    keys = [s.key for s in sm.servers_for_capability("image")]
    if len(keys) < 2:
        pytest.skip("need >=2 image servers to exercise a non-first selection")
    seen = []
    row = mp.ModelPickerRow("image", selected_key=keys[1], on_change=seen.append)
    assert seen == []
    assert row.selected_key() == keys[1]


@needs_display
def test_row_status_push_does_not_spuriously_fire_on_change_for_non_first_selection(monkeypatch):
    """Regression: `Gtk.DropDown.set_model()` resets selection to index 0 and
    fires `notify::selected` immediately, before `set_selected(index)` fires
    it again with the real key. Unguarded, a live status-service push (the
    ~5s poll) on a row whose selection ISN'T index 0 would call `on_change`
    TWICE -- once with the wrong (index-0) key, once with the right one --
    a phantom "model switched then reverted" signal on every poll tick.

    Selects `keys[1]` (a non-first entry) so the bug (which the original
    push test masked by always selecting `keys[0]`) is actually exercised."""
    from model_status import Status

    monkeypatch.setattr(mp.GLib, "idle_add", lambda fn, *a: fn(*a))

    keys = [s.key for s in sm.servers_for_capability("image")]
    if len(keys) < 2:
        pytest.skip("need >=2 image servers to exercise a non-first selection")

    fake_service = _FakeStatusService({keys[1]: Status.STARTING})
    seen = []
    row = mp.ModelPickerRow(
        "image", status_service=fake_service, selected_key=keys[1], on_change=seen.append
    )
    assert row.selected_key() == keys[1]
    assert seen == []  # construction itself must not fire on_change

    # Simulate a poll tick landing -- dots change, selection does not.
    fake_service.push({keys[1]: Status.READY})

    assert row.selected_key() == keys[1]  # still the originally-selected key
    assert seen == []  # a dot-only rebuild must never fire on_change at all,
    # and in particular must never fire with the wrong (index-0) key


@needs_display
def test_row_user_selection_still_fires_on_change_once():
    """A genuine user pick (driving the dropdown directly, as a click would)
    must still reach `on_change` exactly once, with the newly-selected key --
    the suppression guard must not swallow real user actions."""
    keys = [s.key for s in sm.servers_for_capability("image")]
    if len(keys) < 2:
        pytest.skip("need >=2 image servers to exercise a selection change")
    seen = []
    row = mp.ModelPickerRow("image", selected_key=keys[0], on_change=seen.append)
    assert seen == []

    row._dropdown.set_selected(1)

    assert seen == [row.selected_key()]
    assert row.selected_key() == keys[1]


@needs_display
def test_row_unrealize_unsubscribes():
    fake_service = _FakeStatusService()
    row = mp.ModelPickerRow("image", status_service=fake_service)
    assert len(fake_service.subscribers) == 1
    row._on_unrealize()
    assert fake_service.subscribers == []
    # Idempotent -- a second unrealize (GTK can fire it more than once)
    # must not raise.
    row._on_unrealize()


@needs_display
def test_row_on_change_fires_with_selected_key():
    keys = [s.key for s in sm.servers_for_capability("image")]
    if len(keys) < 2:
        pytest.skip("need >=2 image servers to exercise a selection change")
    seen = []
    row = mp.ModelPickerRow("image", selected_key=keys[0], on_change=seen.append)
    row._dropdown.set_selected(1)
    assert seen and seen[-1] == row.selected_key()
