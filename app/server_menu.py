# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""The Servers menu's controller: ServerControl calls, confirm dialogs, refresh.

Task 8 of the server-control plan (spec
docs/superpowers/specs/2026-10-28-tt-cli-server-control-design.md, 4.3-4.6).
``MainWindow`` owns one ``ServerMenu``; ``ServersControl`` (the widget) owns no
logic and calls back into it.

Threading (CLAUDE.md, "GTK threading discipline"):

* Every ``ServerControl`` call, every ``tt``/gozer/docker call and every
  snapshot runs on a worker thread. Results reach widgets only through
  ``GLib.idle_add``, and every main-thread callback first checks
  ``alive_fn()`` so nothing touches a closed window.
* ``ConfirmBridge`` is the ``confirm_fn`` ServerControl calls from that worker
  thread. It posts the dialog to the main thread and blocks the WORKER on a
  ``threading.Event`` until the user answers. The GTK main loop is never
  blocked. It returns the literal ``True`` only for the affirmative button;
  Cancel/No, closing the dialog, Escape, the window closing and a 10-minute
  timeout are all ``False``. A second question while one is open waits in a
  queue and is shown after the first one is answered.

Refresh: one daemon thread (``start()``/``close()``) runs ``refresh_once`` every
10 s and right after each action. A cycle reads the merged view
(``server_runtime.snapshot_detail``), reaps stale leases once
(``ServerControl.reap_stale_leases``, with None when ``tt model ps`` failed),
and hands the result to ``ServersControl.set_view`` via ``GLib.idle_add``. The
same snapshot feeds ``ready_keys()``, which ``ModelStatusService`` calls as its
``extra_ready_fn`` so a server started from a terminal makes its SERVERS key
READY for the status bar and Create's dots.

The GUI never calls ``ServerControl.start_legacy``/``stop_legacy``: those are
for ``tt-ctl``'s old keys only.
"""
from __future__ import annotations

import collections
import dataclasses
import logging
import socket
import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

log = logging.getLogger(__name__)

REFRESH_INTERVAL_S = 10.0
CONFIRM_TIMEOUT_S = 600.0

_HEALTHY = frozenset({"healthy", "ready", "ok"})
# A `tt model ps` row with a docker status and no health check.
_MAYBE = frozenset({"", "unknown", "running", "none"})

# Per-kind dialog heading and affirmative button label. The safe button is
# always "No" and is the default.
_CONFIRM_KINDS = {
    "switch": ("Switch models?", "Switch"),
    "stop_external": ("Stop a server tt-local-generator did not start?", "Stop it"),
    "unknown_hardware": ("Start a model with unknown hardware needs?", "Start anyway"),
    "unknown_chips": ("Start without knowing the chip count?", "Start anyway"),
    "adopt_unknown": ("Treat this server as started here?", "Use anyway"),
}

# Plain-English first words of each outcome's log line.
_START_WORDS = {
    "started": "Started",
    "already_running": "Running (started elsewhere)",
    "starting_elsewhere": "Being started elsewhere",
    "declined": "Declined",
    "cancelled": "Cancelled",
    "waiting_for_chips": "Still waiting for chips",
    "chips_held": "Chips are held by another lease",
    "failed": "Start failed",
}
_STOP_WORDS = {
    "stopped": "Stopped",
    "declined": "Declined",
    "not_allowed": "Not stopped",
    "failed": "Stop failed",
}


def start_outcome_line(name: str, outcome) -> str:
    """One log line for a StartOutcome. ``cancelled`` says "Cancelled" (the
    user cancelled the wait), distinct from ``declined`` (the user said no)."""
    kind = getattr(outcome, "kind", "failed")
    word = _START_WORDS.get(kind, kind)
    msg = (getattr(outcome, "message", "") or "").strip()
    url = getattr(outcome, "url", None)
    if kind == "already_running":
        return f"{name}: {word}" + (f" at {url}." if url else ".")
    if kind == "started" and not msg and url:
        msg = f"{name} is ready at {url}."
    return f"{name}: {word}. {msg}".rstrip()


def stop_outcome_line(name: str, outcome) -> str:
    kind = getattr(outcome, "kind", "failed")
    word = _STOP_WORDS.get(kind, kind)
    msg = (getattr(outcome, "message", "") or "").strip()
    return f"{name}: {word}. {msg}".rstrip()


def ready_keys(servers, registry) -> set[str]:
    """SERVERS keys whose merged-view row is healthy and on this machine.

    A row's key is a registry key; a boutique entry's ``sm_key`` is the
    SERVERS key (a tt-only model has none and is skipped: the status service
    only knows SERVERS keys)."""
    import server_manager
    out: set[str] = set()
    for s in servers or []:
        if not s.host_is_local or s.source == "saved":
            continue
        if (s.health or "").lower() not in _HEALTHY:
            continue
        e = (registry or {}).get(s.key)
        sm_key = e.sm_key if e is not None else None
        if sm_key is None and s.key in server_manager.SERVERS:
            sm_key = s.key
        if sm_key:
            out.add(sm_key)
    return out


def held_records(records) -> list[dict]:
    """Records holding a lease that no current server owns: the
    ``<key>#unreapable-<n>`` and ``<key>#kept-<n>`` names ServerControl writes,
    plus any record marked ``unreapable``."""
    out = []
    for r in records or []:
        if not isinstance(r, dict) or not str(r.get("lease_id") or "").strip():
            continue
        name = str(r.get("name") or "")
        if r.get("unreapable") or "#unreapable-" in name or "#kept-" in name:
            out.append(dict(r))
    return out


def _port_open(port, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    if not isinstance(port, int) or isinstance(port, bool):
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# ConfirmBridge
# ---------------------------------------------------------------------------

class _Question:
    __slots__ = ("kind", "text", "event", "answer", "done")

    def __init__(self, kind: str, text: str) -> None:
        self.kind, self.text = kind, text
        self.event = threading.Event()
        self.answer = False
        self.done = False


class ConfirmBridge:
    """``confirm_fn(kind, text)`` for ServerControl, callable from a worker.

    ``parent``: the window the dialog is transient for (may be None).
    ``alive_fn``: False once the window is closing; questions then answer False.
    """

    def __init__(self, parent=None, *, timeout: float = CONFIRM_TIMEOUT_S,
                 alive_fn: Callable[[], bool] = lambda: True) -> None:
        self._parent = parent
        self._timeout = timeout
        self._alive_fn = alive_fn
        self._lock = threading.Lock()
        self._queue: "collections.deque[_Question]" = collections.deque()
        self._current: "tuple[_Question, Gtk.Widget] | None" = None
        self._closed = False

    # -- worker side ---------------------------------------------------------
    def __call__(self, kind: str, text: str) -> bool:
        if threading.current_thread() is threading.main_thread():
            # Blocking here would freeze the GTK main loop and the dialog could
            # never be answered. ServerControl must run on a worker.
            log.error("ConfirmBridge called on the main thread (%s); answering no", kind)
            return False
        q = _Question(str(kind), str(text))
        with self._lock:
            if self._closed:
                return False
            self._queue.append(q)
        GLib.idle_add(self._pump)
        if not q.event.wait(self._timeout):
            with self._lock:
                q.answer, q.done = False, True
                try:
                    self._queue.remove(q)
                except ValueError:
                    pass
            GLib.idle_add(self._expire, q)
            return False
        return True if q.answer is True else False

    # -- main-thread side ------------------------------------------------
    def _answer(self, q: _Question, yes: bool) -> None:
        with self._lock:
            if q.done:
                return
            q.answer = True if yes is True else False
            q.done = True
        q.event.set()

    def _pump(self) -> bool:
        """Show the next queued question unless one is open. Main thread."""
        with self._lock:
            if self._current is not None:
                return False
            if self._closed or not self._alive_fn():
                pending, self._queue = list(self._queue), collections.deque()
                q = None
            else:
                pending = []
                q = self._queue.popleft() if self._queue else None
        for p in pending:
            self._answer(p, False)
        if q is None or q.done:
            if q is not None:
                GLib.idle_add(self._pump)
            return False
        dialog = self._build_dialog(q)
        with self._lock:
            self._current = (q, dialog)
        dialog.present()
        return False

    def _build_dialog(self, q: _Question):
        heading, yes_label = _CONFIRM_KINDS.get(q.kind, ("Are you sure?", "Yes"))
        dialog = Gtk.MessageDialog(modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE, text=heading,
                                   secondary_text=q.text)
        if self._parent is not None:
            dialog.set_transient_for(self._parent)
        no_btn = dialog.add_button("No", Gtk.ResponseType.CANCEL)
        yes_btn = dialog.add_button(yes_label, Gtk.ResponseType.ACCEPT)
        if q.kind in ("switch", "stop_external"):
            yes_btn.add_css_class("destructive-action")
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        no_btn.grab_focus()

        def on_response(dlg, resp):
            # Only the affirmative button is a yes. Escape, the window's close
            # button and destroy all arrive here as other responses.
            self._finish(q, dlg, resp == Gtk.ResponseType.ACCEPT)

        dialog.connect("response", on_response)
        dialog.connect("destroy", lambda dlg: self._finish(q, dlg, False))
        return dialog

    def _finish(self, q: _Question, dialog, yes: bool) -> None:
        self._answer(q, yes)
        with self._lock:
            mine = self._current is not None and self._current[0] is q
            if mine:
                self._current = None
        if mine:
            try:
                dialog.destroy()
            except Exception:
                pass
            GLib.idle_add(self._pump)

    def _expire(self, q: _Question) -> bool:
        """A worker timed out: close its dialog if it is still open."""
        with self._lock:
            cur = self._current
        if cur is not None and cur[0] is q:
            self._finish(q, cur[1], False)
        return False

    def close(self) -> None:
        """Window closing: every open and queued question answers False."""
        with self._lock:
            self._closed = True
            pending, self._queue = list(self._queue), collections.deque()
            cur = self._current
        for p in pending:
            self._answer(p, False)
        if cur is not None:
            self._finish(cur[0], cur[1], False)

    # test helpers
    def _open_dialog(self):
        with self._lock:
            return self._current[1] if self._current is not None else None

    def _queued(self) -> int:
        with self._lock:
            return len(self._queue)


# ---------------------------------------------------------------------------
# ServerMenu
# ---------------------------------------------------------------------------

class ServerMenu:
    """Runs the Servers menu's actions and its refresh loop.

    ``servers_control``: the ``ServersControl`` widget.
    ``control_factory(confirm_fn)``: builds the ``ServerControl`` (default:
    ``server_runtime.build_server_control``); called once, on a worker.
    ``snapshot_fn(saved, script_health)``: ``(registry, servers, ps_ok,
    reason)``; default ``server_runtime.snapshot_detail`` (with the saved
    remote servers and the script health read here).
    ``saved_fn()``: saved remote servers as merge entries (no token).
    ``records_fn()``: the ownership records (for the held-leases block).
    ``probe_fn(url)``: endpoint probe for rows that show only a docker status.
    ``script_health_fn()``: ``server_manager.status_all`` (None on failure).
    ``status_service``: optional; ``note_starting``/``note_stopping`` hooks.
    ``alive_fn()``: False once the window is closing.
    """

    def __init__(self, servers_control, *, parent=None, control_factory=None,
                 snapshot_fn=None, saved_fn=None, records_fn=None, probe_fn=None,
                 script_health_fn=None, status_service=None,
                 alive_fn: Callable[[], bool] = lambda: True,
                 interval: float = REFRESH_INTERVAL_S,
                 confirm: ConfirmBridge | None = None,
                 token_urls_fn: Callable[[], set] | None = None) -> None:
        self._sc = servers_control
        self._alive_fn = alive_fn
        self.confirm = confirm or ConfirmBridge(parent, alive_fn=alive_fn)
        self._control_factory = control_factory
        self._snapshot_fn = snapshot_fn
        self._saved_fn = saved_fn or (lambda: [])
        # URLs of saved remote servers that have a bearer token (never the token).
        self._token_urls_fn = token_urls_fn or (lambda: set())
        self._records_fn = records_fn
        self._probe_fn = probe_fn
        self._script_health_fn = script_health_fn
        self._status_service = status_service
        self._interval = interval
        self._control = None
        self._control_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._refresh_lock = threading.Lock()
        # Latest snapshot, replaced whole (read by ready_keys on another thread).
        self._latest: tuple = ({}, [])

    # -- plumbing ---------------------------------------------------------
    def control(self):
        """The ServerControl, built on first use. Worker thread only."""
        with self._control_lock:
            if self._control is None:
                factory = self._control_factory
                if factory is None:
                    import server_runtime
                    factory = server_runtime.build_server_control
                self._control = factory(self.confirm)
            return self._control

    def _post(self, fn, *args) -> None:
        """Run ``fn(*args)`` on the main thread if the window is alive."""
        def call():
            if self._alive_fn():
                try:
                    fn(*args)
                except Exception:
                    log.exception("servers menu UI update failed")
            return False
        GLib.idle_add(call)

    def _log(self, line: str) -> None:
        self._post(self._sc.append_server_log, line)

    def _spawn(self, target, name: str) -> threading.Thread:
        t = threading.Thread(target=target, daemon=True, name=name)
        t.start()
        return t

    def _name_for(self, key: str) -> str:
        reg = self._latest[0] or {}
        e = reg.get(key)
        return e.name if e is not None else str(key)

    def _note(self, method: str, key: str) -> None:
        if self._status_service is None:
            return
        try:
            import server_manager
            if key in server_manager.SERVERS:
                getattr(self._status_service, method)(key)
        except Exception:
            pass

    # -- actions (main thread entry points) -------------------------------
    def start(self, key: str):
        """Start a registry key through ServerControl.start on a worker."""
        name = self._name_for(key)
        cancel_ev = threading.Event()
        self._sc.set_server_launching(key, True)
        self._sc.append_server_log(f"Starting {name}...")
        self._sc.set_cancel_action(key, f"Cancel start of {name} (stops what was launched)",
                                   cancel_ev.set)

        def run():
            self._note("note_starting", key)
            try:
                out = self.control().start(key, on_progress=self._log,
                                           cancel=cancel_ev.is_set)
            except Exception as exc:          # start() should not raise
                log.exception("start of %s raised", key)
                from server_control import StartOutcome
                out = StartOutcome("failed", None, f"{exc}.", None)
            self._finish_start(key, name, out)
            self.refresh_now()

        return self._spawn(run, f"server-start-{key}")

    def _finish_start(self, key: str, name: str, out) -> None:
        """Worker side of the end of a start/restart: status hooks, the outcome
        line, and the log kept open for anything the user must read."""
        if out.kind not in ("started", "already_running"):
            self._note("note_stopping", key)
        self._log(start_outcome_line(name, out))
        if out.kind not in ("started", "already_running"):
            self._post(self._sc.keep_log_open)
        self._post(self._sc.clear_cancel_action, key)
        self._post(self._sc.set_server_launching, key, False)

    def stop(self, server_or_key):
        """Stop a RunningServer (or, from an old SERVERS row, a key) through
        ServerControl.stop on a worker. ServerControl asks first for a server
        the app did not start."""
        key = getattr(server_or_key, "key", server_or_key)
        name = getattr(server_or_key, "name", None) or self._name_for(key)
        self._sc.set_server_launching(key, True)
        self._sc.append_server_log(f"Stopping {name}...")

        def run():
            try:
                rows = self._rows_for(server_or_key)
                if not rows:
                    self._log(f"{name}: not running on this machine; nothing to stop.")
                for srv in rows:
                    out = self.control().stop(srv)
                    if out.kind == "stopped":
                        self._note("note_stopping", key)
                    self._log(stop_outcome_line(srv.name, out))
                    if out.kind != "stopped":
                        self._post(self._sc.keep_log_open)
            except Exception as exc:
                log.exception("stop of %s raised", key)
                self._log(f"{name}: Stop failed. {exc}.")
                self._post(self._sc.keep_log_open)
            self._post(self._sc.set_server_launching, key, False)
            self.refresh_now()

        return self._spawn(run, f"server-stop-{key}")

    def restart(self, server_or_key):
        key = getattr(server_or_key, "key", server_or_key)
        name = getattr(server_or_key, "name", None) or self._name_for(key)
        cancel_ev = threading.Event()
        self._sc.set_server_launching(key, True)
        self._sc.append_server_log(f"Restarting {name}...")
        self._sc.set_cancel_action(key, f"Cancel start of {name} (stops what was launched)",
                                   cancel_ev.set)

        def run():
            self._note("note_starting", key)
            try:
                out = self.control().restart(server_or_key, on_progress=self._log,
                                             cancel=cancel_ev.is_set)
            except Exception as exc:
                from server_control import StartOutcome
                out = StartOutcome("failed", None, f"{exc}.", None)
            self._finish_start(key, name, out)
            self.refresh_now()

        return self._spawn(run, f"server-restart-{key}")

    def forget(self, key: str) -> None:
        """A server was stopped outside ServerControl (the status bar's Stop,
        the Create switch): drop the app's record of it through
        ``ServerControl.forget`` (lease released only when gozer shows its
        chips free). Worker thread only. Never raises."""
        try:
            self.control().forget(key)
        except Exception:
            log.exception("forget %s failed", key)
        self.refresh_now()

    def use(self, server) -> None:
        """"Use": the app finds local chat servers by itself and has no
        setting that picks an endpoint, so Use copies the URL and says so."""
        url = server.url or ""
        if not url:
            self._sc.append_server_log(f"{server.name}: no URL to use.")
            return
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(url)
        line = (f"{server.name}: copied {url} to the clipboard. tt-local-generator finds "
                "chat servers on this machine by itself; paste the URL where a tool asks "
                "for an endpoint.")
        if server.source == "saved" and self._url_has_token(url):
            line += " The token is not copied; set it where you use the URL."
        self._sc.append_server_log(line)

    def _url_has_token(self, url: str) -> bool:
        try:
            return url in set(self._token_urls_fn() or ())
        except Exception:
            return False

    def _rows_for(self, server_or_key) -> list:
        """The RunningServer rows a Stop acts on. A key (old SERVERS row)
        resolves to its local rows in a fresh view."""
        if not isinstance(server_or_key, str):
            return [server_or_key]
        snap = self._snapshot()
        reg, servers = snap[0], snap[1]
        e = reg.get(server_or_key)
        out = []
        for s in servers:
            if not s.host_is_local or s.source == "saved":
                continue
            if s.key == server_or_key or (e is not None and e.sm_key and s.key == e.sm_key):
                out.append(s)
        return out

    # -- refresh ------------------------------------------------------------
    def _snapshot(self):
        """``(registry, servers, ps_ok, reason, script_health)``. Worker only.
        ``script_health`` is None when the script health check failed."""
        saved = []
        try:
            saved = list(self._saved_fn() or [])
        except Exception:
            log.exception("reading saved remote servers failed")
        sh = None
        if self._script_health_fn is not None:
            try:
                sh = self._script_health_fn()
            except Exception:
                log.warning("script health check failed", exc_info=True)
                sh = None
        fn = self._snapshot_fn
        if fn is None:
            import server_runtime

            def fn(saved_rows, script_health):
                return server_runtime.snapshot_detail(script_health=script_health,
                                                      saved=saved_rows)
        try:
            registry, servers, ok, reason = fn(saved, sh)
        except Exception as exc:
            log.exception("server snapshot failed")
            return {}, [], False, f"could not read the running servers ({exc})", sh
        return registry, servers, ok, reason, sh

    def _probe_maybe(self, servers):
        """A local row with only a docker status counts as healthy when its
        endpoint answers (the same rule ServerControl uses)."""
        if self._probe_fn is None:
            return servers
        out = []
        for s in servers:
            if (s.host_is_local and s.source != "saved" and s.url
                    and (s.health or "").lower() in _MAYBE):
                try:
                    if self._probe_fn(s.url) is True:
                        s = dataclasses.replace(s, health="healthy")
                except Exception:
                    pass
            out.append(s)
        return out

    def refresh_once(self) -> None:
        """One cycle: snapshot, reap, hand the view to the widget. Worker only."""
        with self._refresh_lock:
            registry, servers, ps_ok, reason, script_health = self._snapshot()
            servers = self._probe_maybe(list(servers or []))
            registry = dict(registry or {})
            self._latest = (registry, servers)

            records = []
            if self._records_fn is not None:
                try:
                    records = list(self._records_fn() or [])
                except Exception:
                    records = []

            # Reap: None for container ids when ps failed (nothing pruned);
            # endpoints only when the script health check itself answered, and
            # a record whose port still answers counts as live.
            live_ids = None
            if ps_ok:
                live_ids = {str(s.container_id)[:12] for s in servers
                            if s.container_id and s.host_is_local}
            live_eps = None
            if script_health is not None:
                live_eps = set()
                for s in servers:
                    if s.host_is_local and s.port is not None:
                        live_eps.add((s.key, s.port))
                        live_eps.add((s.name, s.port))
                for r in records:
                    if (not r.get("container_id") and r.get("port") is not None
                            and _port_open(r.get("port"))):
                        live_eps.add((r.get("name"), r.get("port")))
            try:
                self.control().reap_stale_leases(live_ids, live_eps)
            except Exception:
                log.exception("lease reap failed")

            held = held_records(records)
            self._post(self._sc.set_view, servers, registry, held,
                       "" if ps_ok else (reason or "tt model ps failed"))

    def ready_keys(self) -> set[str]:
        """SERVERS keys healthy in the latest snapshot. Any thread."""
        reg, servers = self._latest
        try:
            return ready_keys(servers, reg)
        except Exception:
            return set()

    def refresh_now(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh_once()
            except Exception:
                log.exception("servers refresh failed")
            self._wake.wait(self._interval)
            self._wake.clear()

    def start_refresh(self) -> None:
        if self._thread is None:
            self._thread = self._spawn(self._loop, "servers-refresh")

    def close(self) -> None:
        """Window closing: stop the refresh thread, answer open questions no."""
        self._stop.set()
        self._wake.set()
        self.confirm.close()

    def _join(self, timeout: float = 5.0) -> bool:
        t = self._thread
        if t is not None:
            t.join(timeout)
            return not t.is_alive()
        return True
