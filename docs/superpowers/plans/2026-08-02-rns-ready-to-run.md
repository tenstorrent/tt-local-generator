# RN-S: Ready-to-Run — server readiness tied to Create intent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** When you hit Create for a medium/model whose server isn't running, confirm-and-start it (stopping + `tt-smi -r` resetting any conflicting server on the shared chips first), then run the job — reusing the switch machinery pipelines already use. Confirm-before-switch, never silent.

**Architecture:** A new GUI-free `app/ready_to_run.py` makes the pure decision (which server is required, which running server conflicts, whether a reset is needed). `MainWindow` gates `_on_create_generate` through it: ready → launch now; not ready → a confirm dialog that stops/reset/starts on a background thread (reusing `server_manager.start/stop`, `pipeline_engine._tt_smi_reset`, `ServersControl`'s log, and `ModelStatusService.note_*`) and launches the deferred job once healthy.

**Tech Stack:** Python 3 (`/usr/bin/python3`), GTK4/PyGObject, pytest under `xvfb-run`.

## Global Constraints

- **⚠️ Hardware safety (from memory `reference_qb2_card924055_fragility`):** backend-switch churn has hard-locked this box (QB2 chip 3 ARC-NOC failure). The switch MUST be **user-confirmed before execution, never auto-run**; minimize churn; reuse the proven `pipeline_engine._tt_smi_reset()` reset path. `ready_to_run.py` only DECIDES — it never touches hardware.
- **Generation unchanged when a server is already ready:** if the required server is READY (or there's nothing to start — a synthetic `"animatediff"`/detected key, or no `status_service`), Create launches exactly as it does today (`_launch_create_job` = the current dispatch, moved verbatim). The gate is a pre-flight, additive.
- **Soft-fail:** a Create click must never crash the app (existing invariant). The gate and its callbacks catch exceptions → status message; the deferred-launch path keeps the current `_on_create_generate` try/except semantics.
- `ready_to_run.py` is **GUI-free** (imports only `server_manager` + stdlib) and **injectable** (`status_of` is a callable) so it unit-tests with fakes, no display/threads/sockets.
- GTK threading: the switch runs on a `threading.Thread`; all widget/log updates via `GLib.idle_add`. System `/usr/bin/python3`; tests `xvfb-run --auto-servernum`. Bump `VERSION` + changelog. Local commits only; do not push.

## Reference facts (from the code map)

- `server_manager.SERVERS` keys + capabilities: media/port-8000 group (mutually exclusive) = caps `video` (`wan2.2`,`mochi`,`skyreels`) / `image` (`flux`,`sdxl`,`z-image-turbo`,`motif`) / `animate` (`animate`); artgen/port-8002 group = cap `artgen` (`artgen-qwen3-8b`, …). `ServerDef.capabilities` is a tuple; `.label` is human. `start(key, gui=True)` non-blocking; `stop(key)`; `is_healthy(key, timeout=2.0) -> bool`.
- `pipeline_engine._tt_smi_reset()` (no args) runs `tt-smi -r` + sleeps. `pipeline_engine._docker_stop_all()` exists but we prefer `server_manager.stop(key)` for a targeted stop.
- `ModelStatusService`: `Status` enum (`OFF/STARTING/READY/ERROR`, str subclass), `status(key)`, `note_starting(key)`, `note_stopping(key)`. MainWindow holds it as `self._status_service` (may be None in tests). Import: `from model_status import Status`.
- `CreateView._selected_model_key() -> Optional[str]` returns the selected scoped key — a real `server_manager.SERVERS` key for native mediums, else `None`/synthetic `"animatediff"`/detected sentinel.
- `_on_create_generate(self, medium, params)` — `app/main_window.py:8646`. Re-entrancy guard 8701-8712 (keep as-is). Dispatch try/except 8713-~8735 (this is what moves into `_launch_create_job`).
- `ServersControl` (via `self._servers_control`): `set_server_launching(key, bool)`, `append_server_log(str)` (reveals/streams the log panel).
- Confirm-dialog sibling in this file: `_on_playlist_delete` (~6186) uses `Gtk.MessageDialog(modal=True, buttons=NONE)` + `add_button` + `connect("response", ...)` + `present()`.

---

### Task 1: `ready_to_run.py` — the pure decision

**Files:**
- Create: `app/ready_to_run.py`
- Test: `tests/test_ready_to_run.py`

**Interfaces:**
- Produces: `required_server(selected_key) -> Optional[str]`; `conflicting_server(target_key, status_of) -> Optional[str]`; `SwitchPlan(target, conflict, needs_reset)` (frozen dataclass); `plan_switch(selected_key, status_of) -> SwitchPlan`. `status_of(key)` returns a status string ("ready"/"starting"/"off"/…).

- [ ] **Step 1: Write the failing tests** (`tests/test_ready_to_run.py`):

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import ready_to_run as rtr


def _status(map_):
    # map_: {key: "ready"|"starting"|"off"}; missing -> "off"
    return lambda k: map_.get(k, "off")


def test_required_server_only_for_real_keys():
    assert rtr.required_server("flux") == "flux"
    assert rtr.required_server("wan2.2") == "wan2.2"
    assert rtr.required_server("animatediff") is None   # synthetic, no server
    assert rtr.required_server("__detected__:foo") is None
    assert rtr.required_server(None) is None
    assert rtr.required_server("") is None


def test_conflict_is_a_running_server_sharing_hardware():
    # flux (image, port 8000) conflicts with wan2.2 (video, port 8000)
    assert rtr.conflicting_server("flux", _status({"wan2.2": "ready"})) == "wan2.2"
    assert rtr.conflicting_server("flux", _status({"wan2.2": "starting"})) == "wan2.2"
    # nothing running -> no conflict
    assert rtr.conflicting_server("flux", _status({})) is None
    # an artgen server does NOT conflict with a media server (different hardware group)
    assert rtr.conflicting_server("flux", _status({"artgen-qwen3-8b": "ready"})) is None
    # artgen conflicts with another artgen
    assert rtr.conflicting_server("artgen-qwen3-8b", _status({"artgen-qwen3-32b": "ready"})) == "artgen-qwen3-32b"
    # a media server does NOT conflict with an artgen target
    assert rtr.conflicting_server("artgen-qwen3-8b", _status({"flux": "ready"})) is None


def test_plan_switch():
    p = rtr.plan_switch("flux", _status({}))
    assert (p.target, p.conflict, p.needs_reset) == ("flux", None, False)
    p = rtr.plan_switch("flux", _status({"wan2.2": "ready"}))
    assert (p.target, p.conflict, p.needs_reset) == ("flux", "wan2.2", True)
    p = rtr.plan_switch("animatediff", _status({"wan2.2": "ready"}))
    assert (p.target, p.conflict, p.needs_reset) == (None, None, False)
    p = rtr.plan_switch(None, _status({}))
    assert (p.target, p.conflict, p.needs_reset) == (None, None, False)
```

- [ ] **Step 2: Run — verify fail** (`ModuleNotFoundError: ready_to_run`).
Run: `/usr/bin/python3 -m pytest tests/test_ready_to_run.py -v`

- [ ] **Step 3: Implement `app/ready_to_run.py`:**

```python
"""Ready-to-Run: pure decisions tying Create/pipeline intent to server state.

GUI-free. Given the selected model key and a way to read current server status,
decide which server must be running for a job and which currently-running server
(sharing the same Blackhole chips) would have to be stopped + reset first.

HARDWARE NOTE: backend-switch churn is risky on this hardware (a QB2 card has a
recurring ARC-NOC failure that has hard-locked the box on churn), so any switch a
plan describes MUST be user-confirmed before execution — never auto-run. This
module only DECIDES; it never touches hardware.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import server_manager

# Mutually-exclusive hardware groups today: the diffusion/media server on
# port 8000 (video | image | animate) and the artgen LLM on port 8002 (artgen).
# Switching WITHIN a group means stopping the incumbent + resetting the boards.
_MEDIA_CAPS = frozenset({"video", "image", "animate"})
_ARTGEN_CAPS = frozenset({"artgen"})


def _group_of(key: str) -> Optional[str]:
    sdef = server_manager.SERVERS.get(key)
    if sdef is None:
        return None
    caps = frozenset(sdef.capabilities)
    if caps & _MEDIA_CAPS:
        return "media"
    if caps & _ARTGEN_CAPS:
        return "artgen"
    return None


def required_server(selected_key: Optional[str]) -> Optional[str]:
    """The server that must run for `selected_key`, or None when nothing needs
    starting: a None/empty selection, or a synthetic/detected key with no
    `server_manager.SERVERS` entry (e.g. "animatediff", detected sentinels)."""
    if not selected_key:
        return None
    return selected_key if selected_key in server_manager.SERVERS else None


def conflicting_server(target_key: str, status_of: Callable[[str], str]) -> Optional[str]:
    """A READY/STARTING server sharing `target_key`'s hardware group (so it must
    be stopped + the boards reset before target starts), or None."""
    group = _group_of(target_key)
    if group is None:
        return None
    for key in server_manager.SERVERS:
        if key == target_key or _group_of(key) != group:
            continue
        if str(status_of(key)).lower() in ("ready", "starting"):
            return key
    return None


@dataclass(frozen=True)
class SwitchPlan:
    target: Optional[str]     # server to start (None -> nothing to do)
    conflict: Optional[str]   # running server to stop first (None -> none)
    needs_reset: bool         # tt-smi -r required (True iff a conflict is stopped)


def plan_switch(selected_key: Optional[str], status_of: Callable[[str], str]) -> SwitchPlan:
    target = required_server(selected_key)
    if target is None:
        return SwitchPlan(None, None, False)
    conflict = conflicting_server(target, status_of)
    return SwitchPlan(target, conflict, conflict is not None)
```

- [ ] **Step 4: Run — verify pass.** `/usr/bin/python3 -m pytest tests/test_ready_to_run.py -v` → all pass. (No GTK needed — pure module.)

- [ ] **Step 5: Commit.**
```bash
git add app/ready_to_run.py tests/test_ready_to_run.py
git commit -m "feat(create): ready_to_run — pure server-switch decision (RN-S t1)"
```

---

### Task 2: the confirm-and-start gate in `_on_create_generate`

**Files:**
- Modify: `app/main_window.py` — extract `_launch_create_job`, add the gate + dialog + switch-thread methods.
- Test: `tests/test_ready_to_run_gate.py`

**Interfaces:**
- Consumes Task 1's `ready_to_run.plan_switch`. New methods: `_launch_create_job(medium, params)` (the moved dispatch), `_ensure_server_ready_then(medium, params)`, `_confirm_start_server(plan, medium, proceed)`, `_perform_switch_then(plan, proceed)`.

- [ ] **Step 1: Write the failing gate test** (`tests/test_ready_to_run_gate.py`). Uses the `__new__`-harness style of `tests/test_main_window_loop_nav.py`; binds only `_ensure_server_ready_then`; fakes the collaborators:

```python
import sys
from pathlib import Path
from types import SimpleNamespace
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
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display available", allow_module_level=True)


def _mw(selected_key, status_map):
    import main_window as mw
    from model_status import Status
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._ensure_server_ready_then = mw.MainWindow._ensure_server_ready_then.__get__(obj)
    obj._create_view = SimpleNamespace(_selected_model_key=lambda: selected_key)
    def _status(k):
        return Status(status_map.get(k, "off"))
    obj._status_service = SimpleNamespace(status=_status)
    obj._launch_create_job = MagicMock()
    obj._confirm_start_server = MagicMock()
    return obj


def test_ready_server_launches_immediately():
    obj = _mw("flux", {"flux": "ready"})
    obj._ensure_server_ready_then(SimpleNamespace(label="Image"), {"p": 1})
    obj._launch_create_job.assert_called_once()
    obj._confirm_start_server.assert_not_called()


def test_synthetic_key_launches_immediately():
    obj = _mw("animatediff", {"wan2.2": "ready"})
    obj._ensure_server_ready_then(SimpleNamespace(label="AnimateDiff"), {})
    obj._launch_create_job.assert_called_once()
    obj._confirm_start_server.assert_not_called()


def test_offline_server_shows_confirm_dialog_and_defers():
    obj = _mw("flux", {"wan2.2": "ready"})   # flux off, wan2.2 running (conflict)
    obj._ensure_server_ready_then(SimpleNamespace(label="Image"), {})
    obj._launch_create_job.assert_not_called()
    obj._confirm_start_server.assert_called_once()
    plan = obj._confirm_start_server.call_args.args[0]
    assert plan.target == "flux" and plan.conflict == "wan2.2" and plan.needs_reset is True
```

- [ ] **Step 2: Run — verify fail** (`_ensure_server_ready_then` doesn't exist).
Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_ready_to_run_gate.py -v`

- [ ] **Step 3: Extract `_launch_create_job`.** In `app/main_window.py`, MOVE the dispatch `try/except` block currently inside `_on_create_generate` (the `try:` at ~8713 through its `except` clause end, i.e. the native/artgen/else dispatch + soft-fail) VERBATIM into a new method, unchanged:

```python
    def _launch_create_job(self, medium, params: dict) -> None:
        """The begin+dispatch half of a Create job (extracted from
        `_on_create_generate` for RN-S so the server-readiness gate can defer
        it). Body is the former dispatch verbatim — same soft-fail semantics."""
        <PASTE the moved try/except dispatch block here, unchanged>
```

- [ ] **Step 4: Replace the dispatch site in `_on_create_generate` with the gate.** After the re-entrancy guard (`if self._create_job_active: ... return`, unchanged), the remaining body becomes:

```python
        # RN-S: make sure the server this job needs is ready first. If it is
        # (or there's nothing to start), launch now; otherwise a confirm dialog
        # starts it (stopping + resetting a conflicting server) and launches
        # when ready. Confirm-before-switch — churn is risky on this hardware.
        try:
            self._ensure_server_ready_then(medium, params)
        except Exception as exc:
            self._set_status(f"Couldn't start generation: {exc}")
```

- [ ] **Step 5: Add the gate + dialog + switch methods:**

```python
    def _ensure_server_ready_then(self, medium, params: dict) -> None:
        """RN-S gate: launch now if the required server is ready / nothing to
        start; else show the confirm-and-start dialog and defer the launch."""
        import ready_to_run
        from model_status import Status
        svc = getattr(self, "_status_service", None)
        if svc is None:
            self._launch_create_job(medium, params)
            return
        try:
            selected = self._create_view._selected_model_key()
        except Exception:
            selected = None
        plan = ready_to_run.plan_switch(selected, lambda k: svc.status(k))
        if plan.target is None or svc.status(plan.target) == Status.READY:
            self._launch_create_job(medium, params)
            return
        self._confirm_start_server(
            plan, medium, lambda: self._launch_create_job(medium, params)
        )

    def _confirm_start_server(self, plan, medium, proceed) -> None:
        """Confirm dialog for starting `plan.target` (stopping + tt-smi -r a
        conflicting server first). On accept -> `_perform_switch_then`; on
        cancel -> nothing (the job never began — no pending state to clear)."""
        target_label = server_manager.SERVERS[plan.target].label
        text = f"Start the {target_label} server for {medium.label}?"
        if plan.conflict:
            conflict_label = server_manager.SERVERS[plan.conflict].label
            secondary = (
                f"{conflict_label} is running on the shared Blackhole chips. It "
                f"will be stopped and the boards reset (tt-smi -r) before "
                f"{target_label} starts — this can take a few minutes."
            )
        else:
            secondary = (
                f"{target_label} isn't running yet. Start it? "
                f"This can take a few minutes to warm up."
            )
        dialog = Gtk.MessageDialog(
            modal=True, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE, text=text, secondary_text=secondary,
        )
        dialog.set_transient_for(self)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        start_btn = dialog.add_button(f"Start {target_label}", Gtk.ResponseType.ACCEPT)
        start_btn.add_css_class("suggested-action")

        def _on_response(dlg, resp):
            dlg.destroy()
            if resp == Gtk.ResponseType.ACCEPT:
                self._perform_switch_then(plan, proceed)

        dialog.connect("response", _on_response)
        dialog.present()

    def _perform_switch_then(self, plan, proceed) -> None:
        """Stop any conflicting server (+ tt-smi -r), start `plan.target`, and
        run `proceed` once it's healthy. Reuses server_manager + pipeline_engine's
        reset + ServersControl's log. All widget updates via GLib.idle_add."""
        import time
        import pipeline_engine
        target = plan.target
        self._servers_control.set_server_launching(target, True)
        self._servers_control.append_server_log(
            f"Preparing {target}…"
            + (f" (stopping {plan.conflict} + reset)" if plan.conflict else "")
        )

        def run() -> None:
            try:
                if plan.conflict:
                    server_manager.stop(plan.conflict)
                    try:
                        self._status_service.note_stopping(plan.conflict)
                    except Exception:
                        pass
                    if plan.needs_reset:
                        GLib.idle_add(self._servers_control.append_server_log,
                                      "Resetting boards (tt-smi -r)…")
                        pipeline_engine._tt_smi_reset()
                GLib.idle_add(self._servers_control.append_server_log, f"Starting {target}…")
                server_manager.start(target, gui=True)
                try:
                    self._status_service.note_starting(target)
                except Exception:
                    pass
                waited, deadline = 0, 1800   # first-run compiles can be long
                while waited < deadline:
                    if server_manager.is_healthy(target):
                        GLib.idle_add(self._servers_control.append_server_log, f"{target} ready.")
                        GLib.idle_add(self._servers_control.set_server_launching, target, False)
                        GLib.idle_add(proceed)
                        return
                    time.sleep(15)
                    waited += 15
                GLib.idle_add(self._servers_control.append_server_log,
                              f"{target} didn't become ready in time — try again from Create.")
                GLib.idle_add(self._servers_control.set_server_launching, target, False)
            except Exception as e:
                GLib.idle_add(self._servers_control.append_server_log, f"Error starting {target}: {e}")
                GLib.idle_add(self._servers_control.set_server_launching, target, False)

        threading.Thread(target=run, daemon=True).start()
```

- [ ] **Step 6: Run the gate test — verify pass.**
Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_ready_to_run_gate.py -v` → all pass.

- [ ] **Step 7: Create-path regression.** Confirm the extraction didn't change the ready-path behavior:
Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_main_window_create_generate.py tests/test_main_window_create_view_mount.py -q` → PASS. (If a test asserted `_on_create_generate` dispatches inline, update it to the new seam — the gate calls `_launch_create_job` synchronously when ready, so the observable end state is unchanged for a ready server. Do NOT weaken assertions.)

- [ ] **Step 8: Bump + changelog + commit.** `VERSION` → `0.55.0`. Prepend a `debian/changelog` stanza (feat: Create checks server readiness; offers to start/switch — stop + tt-smi -r + start — with confirmation, reusing the pipeline switch path; safe-by-default per hardware fragility). Commit:
```bash
git add app/main_window.py tests/test_ready_to_run_gate.py VERSION debian/changelog \
        docs/superpowers/plans/2026-08-02-rns-ready-to-run.md
git commit -m "feat(create): confirm-and-start server gate on Create (RN-S t2)"
```

### Finalize

Full suite green (deselect the two known flakes). Update CLAUDE.md with a short "Ready-to-Run" note: `ready_to_run.py` decides the switch; `_ensure_server_ready_then` gates `_on_create_generate`; the switch reuses `server_manager` + `pipeline_engine._tt_smi_reset` + `ServersControl`; confirm-before-switch is a hard safety rule (QB2 fragility). Note the follow-ons: pipeline studio already switches between steps (make its UX consistent later); a readiness-clarity pass on the Create option labels is optional (the status dots already show it).

## Verification

- `tests/test_ready_to_run.py` + `tests/test_ready_to_run_gate.py` green; create-path regression green.
- **Live (hardware, careful — this switches backends):** in Create, pick a medium whose server is OFF while another shares the chips → hit Create → the confirm dialog names the stop/reset/start → accept → the servers log streams stop → `tt-smi -r` → start → the job runs when ready. Picking a medium whose server is already READY → Create launches with NO dialog (unchanged). Cancel → nothing happens. Do this sparingly (churn is risky); verify the box stays healthy (`tt-smi -s`) after.
