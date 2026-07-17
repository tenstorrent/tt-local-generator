# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""
Tests for app/servers_control.py — SP-3b Task 1: standalone `ServersControl`.

`ServersControl` must be constructible with only a `status_service` (any
object exposing `snapshot()`/`subscribe()` — a real `model_status.
ModelStatusService` or, as here, a minimal fake) and three plain callables
(`on_start`/`on_stop`/`on_restart`). No ControlPanel or MainWindow dependency.

Per the task brief (.superpowers/sdd/task-1-brief.md), these tests exercise:
  - bare construction (servers_button / status_bar both present)
  - popover row dots render the 3-state glyph (◌/◐/●) from the service's
    snapshot() at construction time
  - a row's Start button invokes the injected on_start callable with the
    server_manager key, and nothing else
  - append_server_log() reveals the log panel

Run: xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_servers_control.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import servers_control as sc
import model_status as ms


def _svc(snap, artgen_model=None):
    """Minimal fake status_service: snapshot() + subscribe() + (Task: the
    "aggregate surfaces reflect an unregistered running chat model" fix)
    running_artgen_model(), returning `artgen_model` verbatim (an
    `model_status.ArtgenModelInfo` or None) -- mirrors the real
    `ModelStatusService`'s method of the same name, the single source of
    truth for "is a chat model running right now, matched or not"."""

    class F:
        def __init__(s):
            s._cb = None

        def snapshot(s):
            return dict(snap)

        def subscribe(s, cb):
            s._cb = cb
            return lambda: None

        def running_artgen_model(s):
            return artgen_model

    return F()


def test_constructs_standalone():
    calls = []
    c = sc.ServersControl(
        _svc({}),
        on_start=lambda k: calls.append(("start", k)),
        on_stop=lambda k: calls.append(("stop", k)),
        on_restart=lambda k: calls.append(("restart", k)),
    )
    assert c.servers_button is not None and c.status_bar is not None


def test_log_widget_property_exists_independent_of_status_bar():
    """Post-Task-2-review addition: `log_widget` is a third, independent
    mountable widget (distinct from `status_bar`), added so a caller can
    mount the log without also pulling in the aggregate status-bar dot --
    see task-2-report.md's "Issue 2" for why MainWindow needs this split."""
    c = sc.ServersControl(
        _svc({}), on_start=lambda k: None, on_stop=lambda k: None, on_restart=lambda k: None
    )
    assert c.log_widget is not None
    assert c.log_widget is not c.status_bar


def test_public_widgets_unparented_at_construction():
    """None of servers_button / status_bar / log_widget are pre-parented
    into `self` (the ServersControl Gtk.Box) or into each other -- each is
    independently placeable by the caller, which is what makes it possible
    for MainWindow to mount `servers_button` + `log_widget` while leaving
    `status_bar` unmounted entirely (avoiding a second aggregate server
    dot -- see task-2-report.md's "Issue 1"/"Issue 2")."""
    c = sc.ServersControl(
        _svc({}), on_start=lambda k: None, on_stop=lambda k: None, on_restart=lambda k: None
    )
    assert c.servers_button.get_parent() is None
    assert c.status_bar.get_parent() is None
    assert c.log_widget.get_parent() is None


def test_popover_dots_from_snapshot():
    c = sc.ServersControl(
        _svc({"flux": ms.Status.READY, "wan2.2": ms.Status.STARTING}),
        on_start=lambda k: None,
        on_stop=lambda k: None,
        on_restart=lambda k: None,
    )
    glyphs = c._server_row_glyphs()  # test helper: {key: glyph}
    assert glyphs["flux"] == "●" and glyphs["wan2.2"] == "◐"


def test_off_key_renders_off_glyph():
    """A key the fake service never mentions still defaults to OFF (◌)."""
    c = sc.ServersControl(
        _svc({"flux": ms.Status.READY}),
        on_start=lambda k: None,
        on_stop=lambda k: None,
        on_restart=lambda k: None,
    )
    glyphs = c._server_row_glyphs()
    assert glyphs.get("mochi") == "◌"


def test_start_button_invokes_callback():
    calls = []
    c = sc.ServersControl(
        _svc({"flux": ms.Status.OFF}),
        on_start=lambda k: calls.append(k),
        on_stop=lambda k: None,
        on_restart=lambda k: None,
    )
    c._activate_start("flux")
    assert calls == ["flux"]


def test_stop_and_restart_invoke_their_own_callback_only():
    stop_calls, restart_calls, start_calls = [], [], []
    c = sc.ServersControl(
        _svc({"flux": ms.Status.READY}),
        on_start=lambda k: start_calls.append(k),
        on_stop=lambda k: stop_calls.append(k),
        on_restart=lambda k: restart_calls.append(k),
    )
    c._handle_action("flux", "stop")
    c._handle_action("flux", "restart")
    assert stop_calls == ["flux"]
    assert restart_calls == ["flux"]
    assert start_calls == []


def test_append_log_reveals():
    c = sc.ServersControl(
        _svc({}), on_start=lambda k: None, on_stop=lambda k: None, on_restart=lambda k: None
    )
    assert c._log_revealed() is False
    c.append_server_log("Application startup...")
    assert c._log_revealed() is True


def test_set_server_launching_reveals_and_settles():
    c = sc.ServersControl(
        _svc({}), on_start=lambda k: None, on_stop=lambda k: None, on_restart=lambda k: None
    )
    c.set_server_launching("flux", True)
    assert c._log_revealed() is True
    c.set_server_launching("flux", False)
    assert c._log_revealed() is False


def test_launching_collapses_on_ready_snapshot():
    """The log auto-collapses once a launching key resolves to READY."""
    c = sc.ServersControl(
        _svc({"flux": ms.Status.OFF}),
        on_start=lambda k: None,
        on_stop=lambda k: None,
        on_restart=lambda k: None,
    )
    c.set_server_launching("flux", True)
    assert c._log_revealed() is True
    c._on_snapshot({"flux": ms.Status.READY})
    assert c._log_revealed() is False


def test_set_status_segments_none_leaves_unchanged():
    """Passing no kwargs must be a safe no-op — every segment stays hidden."""
    c = sc.ServersControl(
        _svc({}), on_start=lambda k: None, on_stop=lambda k: None, on_restart=lambda k: None
    )
    c.set_status_segments()  # should not raise
    c.set_status_segments(queue=3, disk="120 GB free", chip="65C")
    assert c._queue_lbl.get_label() == "queue: 3"
    assert c._disk_lbl.get_label() == "120 GB free"
    assert c._chip_lbl.get_label() == "65C"
    c.set_status_segments(queue=0)
    assert c._queue_lbl.get_visible() is False


# ── Aggregate bar dot reflects an unregistered running chat model ──────────
#
# Root cause: `_refresh_bar_dot` aggregates ONLY the per-key `snapshot()`
# statuses (READY > STARTING > ERROR > OFF across every key). When the
# running chat-LLM model doesn't match any registered `server_manager.
# SERVERS` entry (`ArtgenModelInfo.matched_key is None`), every artgen/prompt
# key legitimately resolves OFF -- so with no OTHER server up, the aggregate
# read "offline" even though a chat endpoint genuinely answers requests (the
# same one CreateView already surfaces as a selectable "(detected)" entry).
# `running_artgen_model() is not None` is the single source of truth that
# must override this, mirroring the identical fix in
# `main_window._render_status_snapshot` (its own docstring says the two are
# meant to mirror each other's aggregation policy).

def test_unregistered_running_model_marks_bar_dot_ready():
    info = ms.ArtgenModelInfo("qwen3.6-27b", "http://localhost:9001", None)
    c = sc.ServersControl(
        _svc({}, artgen_model=info),  # every SERVERS key OFF/absent
        on_start=lambda k: None,
        on_stop=lambda k: None,
        on_restart=lambda k: None,
    )
    assert c._bar_dot.get_label() == "●"
    assert c._bar_lbl.get_label() == "ready"


def test_no_running_model_bar_dot_stays_offline():
    """Regression control: with no chat endpoint up at all, the bar dot
    must stay offline -- the fix must not force "ready" unconditionally."""
    c = sc.ServersControl(
        _svc({}, artgen_model=None),
        on_start=lambda k: None,
        on_stop=lambda k: None,
        on_restart=lambda k: None,
    )
    assert c._bar_dot.get_label() == "◌"
    assert c._bar_lbl.get_label() == "offline"


def test_known_matched_running_model_bar_dot_still_ready_no_regression():
    """A running model that DOES match a registered SERVERS key already
    resolves READY through the ordinary per-key path -- the new override
    must be a no-op here, not a source of double-counting or divergence."""
    info = ms.ArtgenModelInfo("Qwen/Qwen3-8B", "http://localhost:8002", "artgen-qwen3-8b")
    c = sc.ServersControl(
        _svc({"artgen-qwen3-8b": ms.Status.READY}, artgen_model=info),
        on_start=lambda k: None,
        on_stop=lambda k: None,
        on_restart=lambda k: None,
    )
    assert c._bar_dot.get_label() == "●"
    assert c._bar_lbl.get_label() == "ready"
