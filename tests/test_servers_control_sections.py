# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""Task 8: the Servers menu sections, its controller and the confirm bridge.

Real GTK widgets (run under xvfb), fake ServerControl / fake views. Where a
test needs the real ServerControl it uses the in-memory fakes from
tests/test_server_control.py (``Env``): no test runs tt, gozer or docker.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).parent))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    Gtk.Label()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)

from gi.repository import Gdk

# The confirm-bridge tests present real dialogs. On a Wayland session GTK
# would put them on the user's desktop, so they run only on X11 (xvfb-run;
# tests/conftest.py pins GDK_BACKEND=x11 inside xvfb-run).
_ON_WAYLAND = "Wayland" in type(Gdk.Display.get_default()).__name__
presents_windows = pytest.mark.skipif(
    _ON_WAYLAND, reason="presents dialogs; run under xvfb-run with GDK_BACKEND=x11")

import connect_server_dialog as csd
import model_status as ms
import server_manager as _sm
import server_menu as smenu
import servers_control as sc_mod
from server_control import StartOutcome, StopOutcome
from server_registry import ModelEntry
from server_view import RunningServer, merge

APP = Path(__file__).parent.parent / "app"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

class _FakeService:
    def snapshot(self):
        return {}

    def subscribe(self, cb):
        return lambda: None

    def running_artgen_model(self):
        return None


def entry(key, *, backend="tt", sm_key=None, name=None, installed=True, benefit="",
          tt_name=None, group="artgen", chips=2, port=None):
    return ModelEntry(key=key, name=name or key, capabilities=(), backend=backend,
                      tt_name=tt_name if tt_name is not None else (key if backend == "tt" else None),
                      sm_key=sm_key, hardware_group=group, chips=chips, port=port,
                      served_id=None, runner_key=None, installed=installed, benefit=benefit)


def rs(key, *, name=None, source="tt", url=None, local=True, port=20000,
       health="healthy", uptime_s=3720, container="c1", container_id="60e0b238c988",
       owner="external"):
    return RunningServer(key=key, name=name or key, source=source,
                         url=url or f"http://127.0.0.1:{port}", host_is_local=local,
                         port=port, health=health, uptime_s=uptime_s, container=container,
                         container_id=container_id, served_id=None, owner=owner)


def widget(**kw):
    calls = {"start": [], "stop": [], "restart": [], "use": [], "connect": []}
    w = sc_mod.ServersControl(
        _FakeService(),
        on_start=kw.get("on_start", lambda k: calls["start"].append(k)),
        on_stop=kw.get("on_stop", lambda s: calls["stop"].append(s)),
        on_restart=kw.get("on_restart", lambda s: calls["restart"].append(s)),
        on_use=kw.get("on_use", lambda s: calls["use"].append(s)),
        on_connect=kw.get("on_connect", lambda: calls["connect"].append(1)),
    )
    return w, calls


def pump(until=lambda: False, timeout=5.0) -> bool:
    """Run the real default GLib main context until ``until()`` or timeout."""
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        if until():
            return True
        time.sleep(0.01)
    while ctx.pending():
        ctx.iteration(False)
    return bool(until())


def row_by_key(w, section, key):
    rows = [r for r in w._section_rows(section) if r.get("key") == key]
    assert rows, f"no {section} row for {key}: {w._section_rows(section)}"
    return rows[0]


REG = {
    "flux": entry("flux", backend="boutique", sm_key="flux", name=_sm.SERVERS["flux"].label,
                  benefit="Fast images", port=8000, group="media"),
    "Llama-3.1-8B-Instruct": entry("Llama-3.1-8B-Instruct"),
    "Qwen3-32B": entry("Qwen3-32B", installed=False),
}


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------

def test_running_section_lists_a_terminal_started_model_with_source_tag():
    w, _ = widget()
    w.set_view([rs("Llama-3.1-8B-Instruct", port=20000)], REG)
    row = row_by_key(w, "running", "Llama-3.1-8B-Instruct")
    assert "Llama-3.1-8B-Instruct" in row["text"]
    assert " tt " in f" {row['text'].replace('|', ' ')} "          # the source tag
    assert "port 20000" in row["text"]
    assert "up 1h 2m" in row["text"]
    assert row["glyph"] == "●"
    assert "Use" in row["buttons"]
    # A terminal-started server is not the app's: Stop asks first.
    assert "Stop..." in row["buttons"] and "Stop" not in row["buttons"]


def test_stop_button_absent_for_a_remote_row():
    w, _ = widget()
    saved = rs("lab", name="lab box", source="saved", url="http://10.0.0.5:8000",
               local=False, port=8000, health="unknown", container_id=None, owner="unknown")
    other_host = rs("Qwen3-8B", source="discovered", url="http://10.0.0.6:8002",
                    local=False, port=8002, container_id=None, owner="unknown")
    local_saved = rs("mine", source="saved", url="http://127.0.0.1:9000", local=True,
                     port=9000, health="unknown", container_id=None, owner="external")
    w.set_view([saved, other_host, local_saved], REG)
    remote = w._section_rows("remote")
    assert len(remote) == 3
    assert w._section_rows("running") == []
    for r in remote:
        assert "Use" in r["buttons"]
        assert not any(lbl.startswith("Stop") for lbl in r["buttons"]), r["text"]
        assert "Start" not in r["buttons"]


def test_app_owned_row_has_plain_stop():
    w, calls = widget()
    s = rs("Llama-3.1-8B-Instruct", owner="app")
    w.set_view([s], REG)
    row = row_by_key(w, "running", "Llama-3.1-8B-Instruct")
    assert "Stop" in row["buttons"] and "Stop..." not in row["buttons"]
    row["buttons"]["Stop"].emit("clicked")
    assert calls["stop"] == [s]


def test_tt_running_row_with_empty_health_is_shown_as_booting():
    w, _ = widget()
    w.set_view([rs("Llama-3.1-8B-Instruct", health="running")], REG)
    row = row_by_key(w, "running", "Llama-3.1-8B-Instruct")
    assert row["glyph"] == "◐"
    assert "booting" in row["text"]


def test_start_on_a_running_model_calls_on_use_not_on_start():
    w, calls = widget()
    running = rs("flux", source="script", port=8000, container_id=None, uptime_s=None)
    w.set_view([running], REG)
    assert [r["key"] for r in w._section_rows("ready")] == ["Llama-3.1-8B-Instruct"]
    row = row_by_key(w, "running", "flux")
    assert not any(lbl in ("Start", "Get and start") for lbl in row["buttons"])
    row["buttons"]["Use"].emit("clicked")
    assert calls["use"] == [running]
    assert calls["start"] == []


def test_ready_to_start_shows_boutique_name_and_benefit():
    w, calls = widget()
    w.set_view([], REG)
    row = row_by_key(w, "ready", "flux")
    assert row["title"] == _sm.SERVERS["flux"].label
    assert "Fast images" in row["text"]
    assert "script" in row["text"]
    row["buttons"]["Start"].emit("clicked")
    assert calls["start"] == ["flux"]
    # Boutique entries come first.
    assert w._section_rows("ready")[0]["key"] == "flux"


def test_ready_row_falls_back_to_the_server_manager_benefit():
    w, _ = widget()
    reg = {"mochi": entry("mochi", backend="boutique", sm_key="mochi",
                          name=_sm.SERVERS["mochi"].label, benefit="")}
    w.set_view([], reg)
    want = _sm.benefit_for("mochi")
    assert want and want in row_by_key(w, "ready", "mochi")["text"]


def test_available_section_offers_get_and_start():
    w, calls = widget()
    w.set_view([], REG)
    row = row_by_key(w, "available", "Qwen3-32B")
    assert "Get and start" in row["buttons"]
    assert "Qwen3-32B" not in [r["key"] for r in w._section_rows("ready")]
    row["buttons"]["Get and start"].emit("clicked")
    assert calls["start"] == ["Qwen3-32B"]


def test_tt_missing_shows_the_reason_and_keeps_script_rows():
    w, _ = widget()
    script = rs("flux", source="script", port=8000, container_id=None, uptime_s=None)
    w.set_view([script], REG, [], tt_reason="tt is not installed")
    assert "tt is not installed" in w._tt_note_text
    assert row_by_key(w, "running", "flux")
    # Without a reason there is no note.
    w.set_view([script], REG, [], tt_reason="")
    assert w._tt_note_text == ""


def test_existing_server_rows_unchanged_when_set_view_never_called():
    w, calls = widget()
    assert set(w._server_row_glyphs()) == set(_sm.SERVERS)
    assert w._popover.get_child() is w._legacy_box
    w._activate_start("flux")
    assert calls["start"] == ["flux"]
    w._row_stop_btns["flux"].emit("clicked")
    assert calls["stop"] == ["flux"]
    assert w._section_rows("running") == []


def test_same_view_twice_is_not_rebuilt():
    w, _ = widget()
    w.set_view([rs("Llama-3.1-8B-Instruct")], REG)
    child = w._popover.get_child()
    w.set_view([rs("Llama-3.1-8B-Instruct")], REG)
    assert w._popover.get_child() is child


def test_held_leases_block_lists_unreapable_records_with_the_release_hint():
    w, _ = widget()
    held = [{"name": "Qwen3-8B#unreapable-1", "port": None, "container_id": None,
             "lease_id": "L1,L2", "started_at": 1.0, "unreapable": True}]
    w.set_view([], REG, held)
    rows = w._section_rows("held")
    assert len(rows) == 1
    assert "gozer release L1 L2" in rows[0]["text"]
    assert rows[0]["command"] == "gozer release L1 L2"
    assert "Copy" in rows[0]["buttons"]
    # No block when nothing is held.
    w.set_view([], REG, [])
    assert w._section_rows("held") == []
    # held_records picks the unreapable and kept records only.
    recs = [{"name": "a", "lease_id": "L9"},
            {"name": "b#unreapable-1", "lease_id": "L1", "unreapable": True},
            {"name": "c#kept-1", "lease_id": "L3"},
            {"name": "d#unreapable-2", "lease_id": ""}]
    assert [r["name"] for r in smenu.held_records(recs)] == ["b#unreapable-1", "c#kept-1"]


# --------------------------------------------------------------------------
# confirm bridge
# --------------------------------------------------------------------------

def _ask_in_worker(bridge, kind="stop_external", text="Stop it?"):
    box = {}

    def work():
        box["answer"] = bridge(kind, text)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t, box


@presents_windows
def test_confirm_bridge_blocks_worker_until_main_thread_answers():
    bridge = smenu.ConfirmBridge(None)
    t, box = _ask_in_worker(bridge)
    assert pump(lambda: bridge._open_dialog() is not None)
    # The main loop keeps running while the worker waits.
    ticks = []
    GLib.idle_add(lambda: ticks.append(1) or False)
    pump(lambda: bool(ticks), timeout=1.0)
    assert ticks == [1]
    assert t.is_alive() and "answer" not in box
    # A second question while one is open is queued, not stacked.
    t2, box2 = _ask_in_worker(bridge, "switch", "Switch?")
    assert pump(lambda: bridge._queued() == 1, timeout=2.0)
    first = bridge._open_dialog()
    first.response(Gtk.ResponseType.ACCEPT)
    t.join(2.0)
    assert box["answer"] is True
    assert pump(lambda: bridge._open_dialog() not in (None, first))
    bridge._open_dialog().response(Gtk.ResponseType.CANCEL)
    t2.join(2.0)
    assert box2["answer"] is False


@presents_windows
def test_confirm_bridge_returns_literal_true_only_for_the_affirmative_button():
    bridge = smenu.ConfirmBridge(None)
    for resp, want in ((Gtk.ResponseType.ACCEPT, True), (Gtk.ResponseType.CANCEL, False),
                       (Gtk.ResponseType.YES, False), (Gtk.ResponseType.OK, False)):
        t, box = _ask_in_worker(bridge)
        assert pump(lambda: bridge._open_dialog() is not None)
        bridge._open_dialog().response(resp)
        t.join(2.0)
        assert box["answer"] is want, resp
        pump(lambda: bridge._open_dialog() is None)
    # Called on the main thread it would deadlock: it answers False at once.
    assert bridge("stop_external", "x") is False


@presents_windows
def test_confirm_bridge_close_and_escape_answer_false():
    bridge = smenu.ConfirmBridge(None)
    # Escape (Gtk.Dialog's "close" keybinding signal)
    t, box = _ask_in_worker(bridge)
    assert pump(lambda: bridge._open_dialog() is not None)
    bridge._open_dialog().emit("close")
    t.join(2.0)
    assert box["answer"] is False
    pump(lambda: bridge._open_dialog() is None)
    # The dialog's own close button / window manager close
    t, box = _ask_in_worker(bridge)
    assert pump(lambda: bridge._open_dialog() is not None)
    bridge._open_dialog().close()
    t.join(2.0)
    assert box["answer"] is False
    pump(lambda: bridge._open_dialog() is None)
    # The main window closes with one question open and one queued.
    t1, b1 = _ask_in_worker(bridge)
    assert pump(lambda: bridge._open_dialog() is not None)
    t2, b2 = _ask_in_worker(bridge)
    assert pump(lambda: bridge._queued() == 1, timeout=2.0)
    bridge.close()
    t1.join(2.0)
    t2.join(2.0)
    assert b1["answer"] is False and b2["answer"] is False
    # After close, a new question is answered no without a dialog.
    t3, b3 = _ask_in_worker(bridge)
    t3.join(2.0)
    assert b3["answer"] is False


@presents_windows
def test_confirm_bridge_times_out_to_false():
    bridge = smenu.ConfirmBridge(None, timeout=0.2)
    t, box = _ask_in_worker(bridge)
    assert pump(lambda: "answer" in box, timeout=3.0)
    assert box["answer"] is False
    assert pump(lambda: bridge._open_dialog() is None, timeout=2.0)


# --------------------------------------------------------------------------
# controller wired to the real ServerControl (in-memory fakes)
# --------------------------------------------------------------------------

def _menu_with_env(env, **kw):
    holder = {}
    w = sc_mod.ServersControl(
        _FakeService(),
        on_start=lambda k: holder["m"].start(k),
        on_stop=lambda s: holder["m"].stop(s),
        on_restart=lambda s: holder["m"].restart(s),
        on_use=lambda s: holder["m"].use(s))
    bridge = smenu.ConfirmBridge(None)
    asked = []

    def confirm(kind, text):
        asked.append(kind)
        return bridge(kind, text)

    env.confirm_fn = confirm
    menu = smenu.ServerMenu(w, confirm=bridge,
                            control_factory=lambda _c: env.control(), **kw)
    holder["m"] = menu
    return w, menu, bridge, asked


@presents_windows
def test_stop_on_external_row_goes_through_confirm(tmp_path):
    from test_server_control import Env
    env = Env(tmp_path)
    ext = rs("Cat-7B", name="Cat-7B", container_id="abcdef123456", port=20001)
    env.view = [ext]
    w, menu, bridge, asked = _menu_with_env(env)
    w.set_view([ext], REG)
    row = row_by_key(w, "running", "Cat-7B")
    row["buttons"]["Stop..."].emit("clicked")
    assert pump(lambda: bridge._open_dialog() is not None)
    assert asked == ["stop_external"]
    bridge._open_dialog().response(Gtk.ResponseType.CANCEL)
    assert pump(lambda: "Declined" in w._log_text())
    assert not any(c[0] in ("tt.stop", "docker.stop", "boutique.stop") for c in env.calls)
    assert env.view == [ext]
    # A yes stops it.
    row["buttons"]["Stop..."].emit("clicked")
    assert pump(lambda: bridge._open_dialog() is not None)
    bridge._open_dialog().response(Gtk.ResponseType.ACCEPT)
    assert pump(lambda: "Stopped" in w._log_text())
    assert any(c[0] in ("tt.stop", "docker.stop") for c in env.calls)


def test_progress_line_reaches_the_server_log(tmp_path):
    """The pipe lesson: a line from tt_backend.serve, through ServerControl's
    on_progress, a real worker thread and GLib.idle_add, into the log."""
    from test_server_control import Env
    env = Env(tmp_path)
    env.gozer_available = False
    env.serve_result = env.serve_result.__class__(True, "", ("loading weights 3/7",))
    env.serve_adds = rs("Cat-7B", name="Cat-7B", container_id="abcdef123456", port=20001,
                        uptime_s=1)
    w, menu, bridge, asked = _menu_with_env(env)
    main = threading.current_thread()
    seen_threads = []
    real_append = w.append_server_log

    def spy(line):
        seen_threads.append(threading.current_thread())
        real_append(line)

    w.append_server_log = spy
    t = menu.start("Cat-7B")
    assert t is not threading.current_thread()
    assert pump(lambda: "Started" in w._log_text(), timeout=10.0), w._log_text()
    assert "loading weights 3/7" in w._log_text()
    assert ("tt.serve", "Cat-7B", ()) in env.calls
    assert all(th is main for th in seen_threads)
    assert asked == []
    assert pump(lambda: not w._cancel_btn.get_visible())


class _FakeControl:
    def __init__(self, start_fn=None):
        self.start_fn = start_fn or (lambda key, cancel: StartOutcome("started", "u", "", None))
        self.reaps = []
        self.stops = []

    def start(self, key, *, on_progress=None, cancel=None, port=None):
        return self.start_fn(key, cancel)

    def stop(self, server):
        self.stops.append(server)
        return StopOutcome("stopped", f"{server.name} was stopped.")

    def restart(self, s, **kw):
        return StartOutcome("started", "u", "", None)

    def reap_stale_leases(self, ids, eps):
        self.reaps.append((ids, eps))
        return []


def _menu(control, **kw):
    w, calls = widget()
    menu = smenu.ServerMenu(w, control_factory=lambda _c: control,
                            confirm=smenu.ConfirmBridge(None), **kw)
    return w, menu


def test_cancelled_outcome_is_logged_as_cancelled_not_declined():
    def start(key, cancel):
        while not cancel():
            time.sleep(0.01)
        return StartOutcome("cancelled", None, "The wait for chips was cancelled.", None)

    w, menu = _menu(_FakeControl(start))
    menu.start("Llama-3.1-8B-Instruct")
    assert w._cancel_btn.get_visible()
    w._cancel_btn.emit("clicked")
    assert pump(lambda: "Cancelled" in w._log_text())
    assert "eclined" not in w._log_text()
    assert pump(lambda: not w._cancel_btn.get_visible())


def test_failed_start_shows_the_server_control_message():
    msg = "Llama failed to boot: tt exited 3: out of DRAM."
    w, menu = _menu(_FakeControl(lambda k, c: StartOutcome("failed", None, msg, None)))
    menu.start("Llama-3.1-8B-Instruct")
    assert pump(lambda: msg in w._log_text())
    assert "Start failed" in w._log_text()


def test_every_start_outcome_has_its_own_plain_line():
    lines = {k: smenu.start_outcome_line("M", StartOutcome(k, "http://h:1", "msg.", None))
             for k in ("started", "already_running", "starting_elsewhere", "declined",
                       "cancelled", "waiting_for_chips", "chips_held", "failed")}
    assert len(set(lines.values())) == len(lines)
    assert lines["already_running"].startswith("M: Running (started elsewhere)")
    assert "Cancelled" in lines["cancelled"] and "Declined" not in lines["cancelled"]
    assert "waiting for chips" in lines["waiting_for_chips"].lower()


def test_already_running_race_only_logs():
    ctl = _FakeControl(lambda k, c: StartOutcome("already_running", "http://127.0.0.1:20000",
                                                 "", None))
    w, menu = _menu(ctl)
    menu.start("Llama-3.1-8B-Instruct")
    assert pump(lambda: "Running (started elsewhere)" in w._log_text())
    assert ctl.stops == []


# --------------------------------------------------------------------------
# refresh
# --------------------------------------------------------------------------

def test_refresh_thread_stops_on_close_and_never_touches_widgets_off_thread():
    main = threading.current_thread()
    hits = []

    class _W:
        def set_view(self, *a):
            hits.append(threading.current_thread())
            return False

        def append_server_log(self, line):
            hits.append(threading.current_thread())

    cycles = []

    def snap(saved, sh):
        cycles.append(threading.current_thread())
        return REG, [rs("Llama-3.1-8B-Instruct")], True, ""

    menu = smenu.ServerMenu(_W(), control_factory=lambda _c: _FakeControl(),
                            snapshot_fn=snap, interval=0.05,
                            confirm=smenu.ConfirmBridge(None))
    menu.start_refresh()
    assert pump(lambda: len(hits) >= 2, timeout=5.0)
    assert all(th is not main for th in cycles)
    assert all(th is main for th in hits)
    menu.close()
    assert menu._join(5.0)


def test_closed_window_gets_no_set_view():
    alive = {"v": True}
    hits = []

    class _W:
        def set_view(self, *a):
            hits.append(1)

    menu = smenu.ServerMenu(_W(), control_factory=lambda _c: _FakeControl(),
                            snapshot_fn=lambda s, h: (REG, [], True, ""),
                            alive_fn=lambda: alive["v"], confirm=smenu.ConfirmBridge(None))
    menu.refresh_once()
    alive["v"] = False
    pump(timeout=0.3)
    assert hits == []


def test_reap_called_with_none_when_ps_failed():
    ctl = _FakeControl()
    w, menu = _menu(ctl, snapshot_fn=lambda s, h: (REG, [rs("flux", source="script",
                                                             container_id=None, port=8000)],
                                                   False, "tt model ps failed: timeout"),
                    script_health_fn=lambda: {"flux": True})
    menu.refresh_once()
    assert ctl.reaps[-1][0] is None
    assert ("flux", 8000) in ctl.reaps[-1][1]
    pump(lambda: bool(w._tt_note_text))
    assert "tt model ps failed" in w._tt_note_text
    # ps ok: 12-char short ids.
    ctl2 = _FakeControl()
    long_id = "60e0b238c988" + "f" * 52
    _, menu2 = _menu(ctl2, snapshot_fn=lambda s, h: (REG, [rs("Llama-3.1-8B-Instruct",
                                                             container_id=long_id)], True, ""),
                     script_health_fn=lambda: {})
    menu2.refresh_once()
    assert ctl2.reaps[-1][0] == {"60e0b238c988"}
    # Script health failed: endpoints unknown (None), so no script record is reaped.
    ctl3 = _FakeControl()

    def boom():
        raise RuntimeError("down")

    _, menu3 = _menu(ctl3, snapshot_fn=lambda s, h: (REG, [], True, ""), script_health_fn=boom)
    menu3.refresh_once()
    assert ctl3.reaps[-1] == (set(), None)


def test_extra_ready_fn_makes_a_terminal_started_server_ready_in_the_status_service():
    reg = dict(REG)
    reg["flux"] = entry("flux", backend="boutique", sm_key="flux", tt_name="FLUX.1-schnell",
                        port=8000, group="media")
    terminal = rs("flux", name="FLUX.1-schnell", source="tt", port=8000)
    booting = rs("Llama-3.1-8B-Instruct", health="starting")
    _, menu = _menu(_FakeControl(), snapshot_fn=lambda s, h: (reg, [terminal, booting], True, ""),
                    script_health_fn=lambda: {})
    svc = ms.ModelStatusService(health_fn=lambda: {}, detect_fn=lambda: (None, None),
                                port_probe=lambda k: False, clock=lambda: 1.0,
                                extra_ready_fn=menu.ready_keys)
    svc._tick()
    assert svc.status("flux") == ms.Status.OFF      # nothing refreshed yet
    menu.refresh_once()
    svc._tick()
    assert svc.status("flux") == ms.Status.READY
    assert menu.ready_keys() == {"flux"}


def test_docker_status_row_counts_as_healthy_once_its_endpoint_answers():
    row = rs("flux", source="tt", port=8000, health="running")
    _, menu = _menu(_FakeControl(), snapshot_fn=lambda s, h: (REG, [row], True, ""),
                    script_health_fn=lambda: {}, probe_fn=lambda url: True)
    menu.refresh_once()
    assert menu.ready_keys() == {"flux"}
    _, menu2 = _menu(_FakeControl(), snapshot_fn=lambda s, h: (REG, [row], True, ""),
                     script_health_fn=lambda: {}, probe_fn=lambda url: False)
    menu2.refresh_once()
    assert menu2.ready_keys() == set()


def test_no_legacy_methods_are_called_by_the_gui():
    pat = re.compile(r"\.(start|stop)_legacy\s*\(")
    hits = []
    for p in sorted(APP.rglob("*.py")):
        if p.name == "server_control.py":
            continue
        for i, ln in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if pat.search(ln):
                hits.append(f"{p.name}:{i}")
    assert hits == []
    # tt-ctl is the one caller.
    assert pat.search((APP.parent / "tt-ctl").read_text())


# --------------------------------------------------------------------------
# Connect dialog
# --------------------------------------------------------------------------

class _Settings:
    def __init__(self):
        self.data = {"remote_servers": []}

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v):
        self.data[k] = v


TOKEN = "hf_SEKRIT_token_1234"


def test_connect_dialog_token_never_shown_in_row_text_or_logs():
    st = _Settings()
    csd.save_remote_server(st, "10.0.0.5:8000", "lab box", TOKEN)
    assert st.data["remote_servers"][0]["token"] == TOKEN   # stored (plain text)
    rows = csd.saved_rows(st)
    assert all(TOKEN not in str(r) for r in rows)
    servers = merge([], {}, [], rows, [], {})
    w, _ = widget()
    w.set_view(servers, {})
    texts = [r["text"] for r in w._section_rows("remote")]
    assert texts and all(TOKEN not in t for t in texts)
    # Use logs the URL, never the token.
    menu = smenu.ServerMenu(w, control_factory=lambda _c: _FakeControl(),
                            confirm=smenu.ConfirmBridge(None))
    menu.use(servers[0])
    assert "10.0.0.5:8000" in w._log_text() and TOKEN not in w._log_text()

    # A probe error that echoes the token is scrubbed.
    import urllib.error

    def opener(req, timeout):
        assert req.get_header("Authorization") == f"Bearer {TOKEN}"
        raise urllib.error.URLError(f"refused for {TOKEN}")

    ok, msg = csd.probe_server("http://10.0.0.5:8000", TOKEN, opener=opener)
    assert not ok and TOKEN not in msg

    # The dialog: Test runs on a background thread and its answer is scrubbed.
    probe_threads = []

    def probe(url, token):
        probe_threads.append(threading.current_thread())
        return False, f"401 for token {token}"

    dlg = csd.ConnectServerDialog(None, settings=st, probe=probe)
    dlg.url_entry.set_text("http://10.0.0.5:8000")
    dlg.token_entry.set_text(TOKEN)
    dlg.test()
    assert pump(lambda: "Not reachable" in dlg.result_label.get_label())
    assert TOKEN not in dlg.result_label.get_label()
    assert TOKEN not in (dlg.result_label.get_tooltip_text() or "")
    assert probe_threads and probe_threads[0] is not threading.main_thread()
    dlg.close()


def test_connect_dialog_saves_normalised_remote_server_without_duplicates():
    st = _Settings()
    csd.save_remote_server(st, " HTTP://LabBox:8000/v1/ ", "lab", "")
    csd.save_remote_server(st, "labbox:8000", "lab again", "t")
    assert st.data["remote_servers"] == [{"url": "http://labbox:8000", "label": "lab again",
                                          "token": "t"}]
    with pytest.raises(ValueError):
        csd.save_remote_server(st, "ftp://x", "", "")
    assert csd.normalise_url("http://user:pw@h:1/x/") == "http://h:1/x"
    assert csd.plain_http_warning("http://10.0.0.5:8000")
    assert csd.plain_http_warning("http://127.0.0.1:8000") == ""
    assert csd.plain_http_warning("https://10.0.0.5") == ""

    saved = []
    dlg = csd.ConnectServerDialog(None, settings=st, on_saved=saved.append)
    dlg.url_entry.set_text("10.0.0.9:9000")
    assert dlg.http_warning.get_visible()
    dlg.label_entry.set_text("other")
    assert dlg.save() is True
    assert [e["url"] for e in st.data["remote_servers"]] == ["http://labbox:8000",
                                                             "http://10.0.0.9:9000"]
    assert saved and saved[0] == st.data["remote_servers"]
    # The app setting exists with an empty default.
    import app_settings
    assert app_settings.DEFAULTS["remote_servers"] == []
