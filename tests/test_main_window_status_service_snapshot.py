# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Guard tests for SP-3d-6 (.superpowers/sdd/task-6-brief.md; audit §4 in
.superpowers/sdd/sp3d-audit.md): retiring the three legacy health pollers
(`_health_loop`/`_artgen_health_loop`/`_prompt_gen_health_loop`, each with its
own background thread pinging a different port) and re-pointing
`_hw_statusbar`'s dot at the single `ModelStatusService` instance MainWindow
already constructs (SP-2 Task 1) and `ServersControl` already reads (SP-3b).

Two-pronged pattern, same as tests/test_main_window_status_service.py and
tests/test_main_window_servers_control.py:

  - Source-level assertions that the three pollers, their result handlers,
    their `_start_*_worker` launchers, and their stop-event/thread bookkeeping
    are genuinely deleted (not just unreachable) -- a plain substring search
    would false-positive on the explanatory prose left behind (this file's own
    comments legitimately mention "_health_loop" by name as history), so these
    check for `def <name>(` and `self._<attr>` patterns specifically.
  - A behavioral test that binds the REAL (unbound)
    `_render_status_snapshot`/`_on_status_snapshot`/`_check_animatediff_hardware`
    methods onto a minimal stand-in via `.__get__` (mirrors
    `_make_control_panel_double`/`_make_main_window_double` in the two files
    above) and feeds it real `ModelStatusService` snapshot dicts (keyed by
    real `server_manager.SERVERS` keys) to prove the dot/capability-row/
    recover-jobs-action rendering actually happens, not just that the right
    substrings appear somewhere in the file.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

_SRC = (Path(__file__).parent.parent / "app" / "main_window.py").read_text()


# ── Source-level guards: the three pollers are actually gone ────────────────

def test_legacy_poller_methods_are_gone():
    for name in (
        "_start_health_worker",
        "_health_loop",
        "_on_health_result",
        "_start_prompt_gen_health_worker",
        "_prompt_gen_health_loop",
        "_on_prompt_gen_health",
        "_start_artgen_health_worker",
        "_artgen_health_loop",
        "_on_artgen_health_result",
    ):
        assert f"def {name}(" not in _SRC, f"{name} still defined"


def test_no_health_stop_events_or_threads_remain():
    for attr in (
        "self._health_stop",
        "self._artgen_health_stop",
        "self._pg_stop",
        "self._health_thread",
        "self._auto_tab_switched",
    ):
        assert attr not in _SRC, f"{attr} still referenced"


def test_start_health_worker_calls_removed_from_build():
    # The three `self._start_*_health_worker()` call sites that used to fire
    # from __init__ right after _build_ui() are gone.
    for call in (
        "self._start_health_worker()",
        "self._start_prompt_gen_health_worker()",
        "self._start_artgen_health_worker()",
    ):
        assert call not in _SRC


def test_new_snapshot_methods_present():
    assert "def _on_status_snapshot(self, snap:" in _SRC
    assert "def _render_status_snapshot(self, snap:" in _SRC
    assert "def _check_animatediff_hardware(self) -> None:" in _SRC


def test_subscribes_hw_statusbar_to_status_service():
    """The subscribe() call feeding `_on_status_snapshot` must exist inside
    `__init__`, textually AFTER `self._build_ui()` -- `_hw_statusbar` itself is
    constructed inside `_build_ui` (a separate method, defined later in the
    file and invoked by name, so its *body* isn't where call-order is proven;
    the call site `self._build_ui()` inside `__init__` is), so subscribing
    only after that call returns guarantees the widget already exists."""
    assert "self._status_service.subscribe(" in _SRC
    assert "GLib.idle_add(self._on_status_snapshot, snap)" in _SRC
    init_idx = _SRC.index("    def __init__(self, app: Gtk.Application,")
    next_def_idx = _SRC.index("\n    def _build_ui(self) -> None:")
    init_body = _SRC[init_idx:next_def_idx]
    build_ui_call_idx = init_body.index("self._build_ui()")
    sub_idx = init_body.index("self._status_unsubscribe = self._status_service.subscribe(")
    assert build_ui_call_idx < sub_idx


def test_close_unsubscribes_and_no_longer_sets_health_stop():
    start = _SRC.index("def do_close_request(self) -> bool:")
    body = _SRC[start:]
    assert "self._status_service.stop()" in body
    assert "self._status_unsubscribe()" in body
    assert "self._health_stop.set()" not in body
    assert "self._pg_stop" not in body


def test_todo_sp3d_marker_resolved():
    """The `TODO(SP-3d)` marker this task exists to resolve is gone (or at
    least no longer describes this as unfinished) -- `_hw_statusbar`'s own
    construction site should read as done, not as an open TODO."""
    assert "TODO(SP-3d): once `_health_loop`" not in _SRC


# ── Behavioral: _render_status_snapshot actually renders the dot ────────────

class _FakeAction:
    def __init__(self):
        self.enabled_calls: list = []

    def set_enabled(self, val: bool) -> None:
        self.enabled_calls.append(val)


class _FakeAlwaysRunning:
    """Fake GenerationWorker stand-in whose _running() reports True, used to
    verify the "Server ready" status message is suppressed while a job runs."""

    def _running(self) -> bool:
        return True


class _FakeStatusBar:
    """Records every call `_render_status_snapshot` makes, instead of
    touching a real `_StatusBar` Gtk.Box (heavy/GTK-tree dependent)."""

    def __init__(self):
        self.capability_calls: list = []   # (cap, ready, detail)
        self.server_calls: list = []       # (ready, model)
        self.starting_calls: int = 0
        self.error_calls: int = 0

    def update_capability(self, cap, ready, detail=""):
        self.capability_calls.append((cap, ready, detail))

    def update_server(self, ready, model):
        self.server_calls.append((ready, model))

    def update_starting(self):
        self.starting_calls += 1

    def update_error(self, msg="failed — click for details"):
        self.error_calls += 1


class _FakeEvent:
    def __init__(self):
        self.was_set = False

    def set(self):
        self.was_set = True


class _FakeArtgenStatusService:
    """Stand-in for `ModelStatusService` exposing ONLY `running_artgen_model()`
    -- the rest of `_render_status_snapshot`'s inputs (the `snap` dict) are
    fed directly to the method under test, so this fake never needs
    `snapshot()`/`subscribe()`/etc. Mirrors the fake in
    tests/test_create_view_detected_model.py's docstring, which notes the
    real service's `running_artgen_model()` is the single source of truth
    consulted by every surface that needs "is a chat model running right
    now, matched or not"."""

    def __init__(self, artgen_model=None):
        self._artgen_model = artgen_model

    def running_artgen_model(self):
        return self._artgen_model


def _make_mw(recover_action=None, artgen_model=None):
    """Bind the real (unbound) snapshot-rendering methods from MainWindow
    onto a bare stand-in, per the established `.__get__` pattern.

    `artgen_model` (an `model_status.ArtgenModelInfo` or None) feeds the
    fake `_status_service.running_artgen_model()` -- see the "aggregate
    surfaces reflect an unregistered running chat model" fix this arg
    exists to test.
    """
    import main_window as mw

    class _FakeMainWindow:
        pass

    obj = _FakeMainWindow()
    obj._alive = True
    obj._hw_statusbar = _FakeStatusBar()
    obj._log_tail_stop = None
    obj._worker_gen = None
    obj._status_lbl_calls = []
    obj._set_status = lambda text: obj._status_lbl_calls.append(text)
    obj._status_agg_prev = mw.Status.OFF
    obj._recover_action = recover_action if recover_action is not None else _FakeAction()
    obj.lookup_action = lambda name: obj._recover_action if name == "recover-jobs" else None
    obj._status_service = _FakeArtgenStatusService(artgen_model)

    obj._render_status_snapshot = mw.MainWindow._render_status_snapshot.__get__(obj)
    obj._on_status_snapshot = mw.MainWindow._on_status_snapshot.__get__(obj)
    return obj, mw


def test_all_ready_sets_ready_dot_and_capability_and_enables_recover():
    obj, mw = _make_mw()
    Status = mw.Status
    snap = {"wan2.2": Status.READY}
    obj._render_status_snapshot(snap)

    assert obj._hw_statusbar.server_calls == [(True, "ready")]
    assert ("video", True, "Wan2.2-T2V-A14B  (P300X2)") in obj._hw_statusbar.capability_calls
    assert obj._recover_action.enabled_calls == [True]
    assert obj._status_agg_prev == Status.READY


def test_all_off_sets_offline_dot_and_disables_recover():
    obj, mw = _make_mw()
    Status = mw.Status
    obj._render_status_snapshot({})
    assert obj._hw_statusbar.server_calls == [(False, "offline")]
    assert obj._recover_action.enabled_calls == [False]
    assert obj._status_agg_prev == Status.OFF


def test_starting_calls_update_starting_once_then_freezes_timer():
    """update_starting() resets the elapsed timer on every call -- verify the
    snapshot handler only calls it on the OFF/READY -> STARTING transition,
    not again while the aggregate stays STARTING across repeated pushes."""
    obj, mw = _make_mw()
    Status = mw.Status
    obj._render_status_snapshot({"wan2.2": Status.STARTING})
    assert obj._hw_statusbar.starting_calls == 1
    assert obj._recover_action.enabled_calls == [False]

    # Same aggregate state pushed again (e.g. an unrelated key changed
    # elsewhere) -- must NOT reset the timer a second time.
    obj._render_status_snapshot({"wan2.2": Status.STARTING, "flux": Status.OFF})
    assert obj._hw_statusbar.starting_calls == 1

    # Now it resolves to READY -- exits the STARTING state cleanly.
    obj._render_status_snapshot({"wan2.2": Status.READY})
    assert obj._hw_statusbar.server_calls == [(True, "ready")]


def test_error_status_calls_update_error():
    obj, mw = _make_mw()
    Status = mw.Status
    obj._render_status_snapshot({"wan2.2": Status.ERROR})
    assert obj._hw_statusbar.error_calls == 1
    assert obj._recover_action.enabled_calls == [False]


def test_recover_jobs_scoped_to_media_capabilities_not_artgen():
    """Recover Jobs resumes video/image generation jobs -- an artgen-only
    READY snapshot (the chat-LLM backend on port 8002) must NOT enable it,
    even though the "artgen" capability row itself does go ready."""
    obj, mw = _make_mw()
    Status = mw.Status
    obj._render_status_snapshot({"artgen-qwen3-8b": Status.READY})
    assert ("artgen", True, "Qwen3-8B") in obj._hw_statusbar.capability_calls
    assert obj._recover_action.enabled_calls == [False]
    # Aggregate dot still reflects READY (mirrors ServersControl's own
    # "any key READY" aggregation -- it has no notion of "which capability
    # the user currently cares about" either).
    assert obj._hw_statusbar.server_calls == [(True, "ready")]


def test_unregistered_running_model_marks_artgen_capability_ready():
    """Fix under test: `ModelStatusService` now tracks the running chat-LLM
    backend's *matched_key* separately (Task 2 of the running-model
    program) -- when it's `None` (a model started outside this app, or a
    new weights repo with no `ServerDef` yet), every artgen `SERVERS` key
    in the raw `snap` legitimately resolves OFF (see model_status.py's
    `_tick()` docstring). Before this fix, that made the "artgen" capability
    row -- and the overall aggregate dot -- read offline even though a chat
    endpoint IS up and CreateView already surfaces it as a selectable
    "(detected)" entry. `running_artgen_model() is not None` is the one
    signal that must override both."""
    from model_status import ArtgenModelInfo

    info = ArtgenModelInfo("qwen3.6-27b", "http://localhost:9001", None)
    obj, mw = _make_mw(artgen_model=info)
    Status = mw.Status
    # Every server_manager key is OFF/absent -- the exact "unregistered
    # model running" scenario (match_model_id found no SERVERS key for it).
    obj._render_status_snapshot({})

    assert ("artgen", True, "qwen3.6-27b (detected)") in obj._hw_statusbar.capability_calls
    # The overall aggregate dot mirrors ServersControl's own policy (per
    # this method's own docstring) -- it must also read "ready", not
    # "offline", so the two rows painted from the same snapshot never
    # disagree with each other.
    assert obj._hw_statusbar.server_calls == [(True, "ready")]


def test_no_running_model_artgen_capability_stays_offline():
    """Regression control: with NO chat endpoint up at all
    (`running_artgen_model()` returns None), the artgen capability row and
    the aggregate dot must stay offline -- the fix must not make everything
    read "ready" unconditionally."""
    obj, mw = _make_mw(artgen_model=None)
    obj._render_status_snapshot({})

    assert ("artgen", False, "") in obj._hw_statusbar.capability_calls
    assert obj._hw_statusbar.server_calls == [(False, "offline")]


def test_known_matched_model_artgen_capability_ready_no_regression():
    """A running model that DOES match a registered SERVERS key (the
    pre-existing, already-working case) must keep showing that server's own
    label -- the new unregistered-model branch must never run when
    `ready_sdef` already resolved, and must not change existing behavior for
    the ordinary path."""
    from model_status import ArtgenModelInfo

    info = ArtgenModelInfo("Qwen/Qwen3-8B", "http://localhost:8002", "artgen-qwen3-8b")
    obj, mw = _make_mw(artgen_model=info)
    Status = mw.Status
    obj._render_status_snapshot({"artgen-qwen3-8b": Status.READY})

    assert ("artgen", True, "Qwen3-8B") in obj._hw_statusbar.capability_calls
    assert obj._hw_statusbar.server_calls == [(True, "ready")]


def test_ready_stops_log_tail_and_sets_status_when_idle():
    obj, mw = _make_mw()
    Status = mw.Status
    fake_event = _FakeEvent()
    obj._log_tail_stop = fake_event
    obj._render_status_snapshot({"wan2.2": Status.READY})
    assert fake_event.was_set is True
    assert obj._log_tail_stop is None
    assert obj._status_lbl_calls == ["Server ready — enter a prompt and click Generate"]


def test_ready_does_not_overwrite_status_while_worker_running():
    obj, mw = _make_mw()
    Status = mw.Status
    obj._worker_gen = _FakeAlwaysRunning()
    obj._render_status_snapshot({"wan2.2": Status.READY})
    assert obj._status_lbl_calls == []


def test_animatediff_capability_skipped_by_snapshot_renderer():
    """"animatediff" has no server_manager.SERVERS entry -- the snapshot
    renderer must not try (and fail) to look it up; it's left to
    `_check_animatediff_hardware`'s standalone one-shot probe."""
    obj, mw = _make_mw()
    obj._render_status_snapshot({})
    caps_touched = {c for c, _r, _d in obj._hw_statusbar.capability_calls}
    assert "animatediff" not in caps_touched


def test_on_status_snapshot_noop_when_window_not_alive():
    """`_on_status_snapshot` (the GLib.idle_add-wrapped subscribe() callback)
    must bail out before touching any widget once the window is closing --
    same `self._alive` guard the retired `_on_health_result` used."""
    obj, mw = _make_mw()
    obj._alive = False
    result = obj._on_status_snapshot({"wan2.2": mw.Status.READY})
    assert result is False
    assert obj._hw_statusbar.server_calls == []


def test_check_animatediff_hardware_runs_in_background_thread(monkeypatch):
    """The one-shot AnimateDiff hardware probe (moved out of the retired
    `_start_artgen_health_worker`'s nested closure) still runs off the main
    thread and posts its result via GLib.idle_add."""
    import main_window as mw

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    idle_calls = []
    monkeypatch.setattr(mw.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(mw.GLib, "idle_add", lambda fn, *a: idle_calls.append((fn, a)))

    class _FakeMainWindow:
        pass

    obj = _FakeMainWindow()
    obj._hw_statusbar = _FakeStatusBar()
    obj._check_animatediff_hardware = mw.MainWindow._check_animatediff_hardware.__get__(obj)

    obj._check_animatediff_hardware()

    assert len(idle_calls) == 1
    fn, args = idle_calls[0]
    assert fn == obj._hw_statusbar.update_capability
    assert args[0] == "animatediff"
