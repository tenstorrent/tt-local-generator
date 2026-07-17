# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""
servers_control.py — standalone `ServersControl` widget (SP-3b Task 1).

Extracts three ControlPanel-owned pieces of UI into one dependency-free
widget:
  - the "Servers ▾" `Gtk.MenuButton` + popover (start/stop/restart per managed
    service, grouped by capability) — lifted from
    `ControlPanel._build_servers_popover` / `_on_servers_action` /
    `_refresh_servers_popover`.
  - the status bar (server dot + queue/disk/chip segments) — lifted from the
    `_StatusBar` class's dot + segment plumbing.
  - the collapsible server-log revealer — lifted from
    `ControlPanel._srv_log_revealer` / `append_server_log` /
    `set_server_launching`.

The key behavioral change from the code it was lifted from: this widget owns
NO polling of its own. Every dot/glyph it renders comes from
`model_status.ModelStatusService.snapshot()`, refreshed only via
`subscribe()`. That keeps exactly one place answering "is a model on" (see
model_status.py's module docstring, and CLAUDE.md's "artgen LLM endpoint
discovery" section for the history of why two disagreeing answers to that
question was a real, previously-shipped bug).

3-state glyphs (see `model_status.Status`):
    ◌  OFF        — nothing detected
    ◐  STARTING   — `note_starting()` was called, or a port is open but not
                     yet answering health checks
    ●  READY      — health check passing

`Status.ERROR` reuses the OFF glyph (the brief's vocabulary is strictly
3-state) but gets its own CSS class so it can still render in a distinct
(red) color, matching the existing `tt-statusbar-dot-error` convention.

Start/Stop/Restart buttons never call `server_manager` directly — they call
the injected `on_start`/`on_stop`/`on_restart` callables. The caller (planned
for Task 2: `MainWindow`) wires those to the actual
`server_manager.start/stop/restart` + `status_service.note_starting/
note_stopping` calls, exactly as `ControlPanel._on_servers_action` does
today. This module reads `server_manager.SERVERS`/`CAPABILITY_LABELS` only
for grouping and display — never for side effects.

Queue/disk/chip segments are likewise NOT polled here: `set_status_segments()`
is the only way they change. The caller owns whatever polling/timers feed it
(mirroring how `_StatusBar.update_queue()`/`_refresh_disk()` were pure setters
from their caller's point of view — just consolidated behind one method here).

`servers_button` / `status_bar` / `log_widget` are three INDEPENDENT widgets,
not one bundled unit — none of them are pre-parented into `self` (this
`Gtk.Box` subclass exists only to keep the constructor/property/test-helper
surface stable; it is not itself mounted anywhere in the current wiring). A
caller is free to mount any subset in whatever locations make sense. Task 2
(`MainWindow`) mounts `servers_button` + `log_widget` only — deliberately
NOT `status_bar` — because the window already has its own aggregate server
dot (`_StatusBar`/`_hw_statusbar`, fed by the older per-tab health loop);
mounting this widget's `status_bar` too would put two disagreeing "is a
server on" dots on screen at once, the exact bug this module's single-
source-of-truth design exists to prevent. See task-2-report.md's "Issue 2"
for the review finding that caught this, and CLAUDE.md's "artgen LLM
endpoint discovery" section for the historical precedent of that bug class.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango

import server_manager as _sm
from model_status import Status

# ---------------------------------------------------------------------------
# Glyphs + CSS class maps
# ---------------------------------------------------------------------------

# 3-state glyph per Status. OFF and ERROR intentionally share a glyph — see
# the module docstring — and are told apart only by CSS class/color.
_GLYPH: "dict[Status, str]" = {
    Status.OFF: "◌",
    Status.STARTING: "◐",
    Status.READY: "●",
    Status.ERROR: "◌",
}

# Popover per-server-row dot CSS classes. "-off"/"-on" already exist in
# main_window.py's global stylesheet (the popover was previously 2-state);
# "-starting"/"-error" are new and styled by this module's own supplemental
# CSS provider (see _ensure_extra_css below).
_ROW_DOT_CLASS: "dict[Status, str]" = {
    Status.OFF: "servers-popover-dot-off",
    Status.STARTING: "servers-popover-dot-starting",
    Status.READY: "servers-popover-dot-on",
    Status.ERROR: "servers-popover-dot-error",
}

# Status-bar dot CSS classes. All four already exist in main_window.py's
# global stylesheet (the old _StatusBar dot was already 3-state-ish, driven
# by update_server()/update_starting()/update_error()), so no supplemental
# CSS is needed for these.
_BAR_DOT_CLASS: "dict[Status, str]" = {
    Status.OFF: "tt-statusbar-dot-offline",
    Status.STARTING: "tt-statusbar-dot-starting",
    Status.READY: "tt-statusbar-dot-ready",
    Status.ERROR: "tt-statusbar-dot-error",
}

_BAR_DOT_TEXT: "dict[Status, str]" = {
    Status.OFF: "offline",
    Status.STARTING: "starting…",
    Status.READY: "ready",
    Status.ERROR: "error",
}

# Supplemental CSS — ASCII only, per project convention (glyphs are Python
# strings, never baked into a b"""..." CSS literal). Colors are hardcoded
# (not referenced via main_window.py's `@define-color tt_accent`/`tt_error`)
# because this module must be standalone-loadable — including under test —
# without ever assuming main_window's provider has been applied. Values
# match tt_accent (#4FD1C5) / tt_error (#FF6B6B) so the two stylesheets read
# as one system whenever both happen to be loaded together.
_EXTRA_CSS = b"""
.servers-popover-dot-starting { color: #4FD1C5; }
.servers-popover-dot-error    { color: #FF6B6B; }
"""

_extra_css_applied = False


def _ensure_extra_css() -> None:
    """Apply the supplemental CSS provider once per process.

    Guarded at module level so constructing many `ServersControl` instances
    (every test in this file does) never stacks duplicate providers onto the
    display.
    """
    global _extra_css_applied
    if _extra_css_applied:
        return
    display = Gdk.Display.get_default()
    if display is None:
        # No display (fully headless, no Xvfb) — nothing to style. Glyphs,
        # callbacks, and every other behavior are unaffected by CSS.
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_EXTRA_CSS)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _extra_css_applied = True


class ServersControl(Gtk.Box):
    """Standalone servers-popover + status-bar + log widget, service-driven.

    Parameters
    ----------
    status_service : object exposing `snapshot() -> dict[str, Status]` and
        `subscribe(cb) -> unsubscribe_callable`. In production this is a
        `model_status.ModelStatusService`; tests pass a fake with the same
        two methods (see tests/test_servers_control.py).
    on_start, on_stop, on_restart : Callable[[str], None]
        Invoked with a `server_manager.SERVERS` key when the matching popover
        row button is clicked. This widget never calls `server_manager`
        itself — the caller decides what "start" actually does.
    """

    def __init__(self, status_service, *, on_start, on_stop, on_restart) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._status_service = status_service
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_restart = on_restart

        # Keys currently marked "launching" via set_server_launching(); the
        # log revealer auto-collapses once every launching key resolves to
        # READY in a snapshot (or is explicitly cleared).
        self._launching: "set[str]" = set()

        _ensure_extra_css()

        # Popover row widget refs, keyed by server_manager key. Populated by
        # _build_servers_button()/_build_row(); read by _on_snapshot() and
        # the _server_row_glyphs() test helper.
        self._row_dots: "dict[str, Gtk.Label]" = {}
        self._row_start_btns: "dict[str, Gtk.Button]" = {}
        self._row_stop_btns: "dict[str, Gtk.Button]" = {}
        self._row_restart_btns: "dict[str, Gtk.Button]" = {}

        self._snapshot: "dict[str, Status]" = dict(status_service.snapshot())

        self._servers_button = self._build_servers_button()
        self._status_bar_widget = self._build_status_bar()
        self._log_revealer = self._build_log_revealer()

        # Deliberately NOT appended to `self` (SP-3b Task 2 fix, post-review):
        # `servers_button`, `status_bar`, and `log_widget` are three
        # independently-mountable widgets, not one bundled unit. MainWindow
        # mounts `servers_button` in its top toolbar and `log_widget` in a
        # persistent footer, but does NOT mount `status_bar` at all --
        # the window already has its own aggregate server dot
        # (`_StatusBar`/`_hw_statusbar`), and showing this widget's dot too
        # would be exactly the two-disagreeing-sources-of-truth bug this
        # program exists to eliminate (see CLAUDE.md's "artgen LLM endpoint
        # discovery" section for the historical precedent). `status_bar`
        # stays a real, working widget (queue/disk/chip segments still
        # settable via `set_status_segments`) for whichever future caller
        # wants it -- it's just unparented until someone does.
        # `self` (this Gtk.Box) is therefore never itself added to a window
        # in the current wiring; it remains a Gtk.Box subclass only so its
        # constructor signature/properties/test helpers stay unchanged.

        # Subscribe LAST — once every widget _on_snapshot() touches exists,
        # so a status_service implementation that notifies synchronously
        # from within subscribe() can never hit a half-built widget.
        self._unsubscribe = status_service.subscribe(
            lambda snap: GLib.idle_add(self._on_snapshot, snap)
        )
        # Paint the already-known snapshot immediately. subscribe() only
        # pushes on the *next* change, so without this the widget would
        # render nothing until the service's poll thread ticks (or, for
        # the fake service used in tests, never).
        self._on_snapshot(self._snapshot)

        # Cleanup: unsubscribe when `self` leaves the widget tree. This is a
        # defensive fallback only -- since `self` is no longer guaranteed to
        # ever be mounted (see above), the primary cleanup path is now the
        # owner calling `close()` explicitly (MainWindow does this from
        # `do_close_request`, alongside `self._status_service.stop()`).
        self.connect("unrealize", lambda *_a: self.close())

    # ------------------------------------------------------------------
    # Public properties (interface contract — see task-1-brief.md)
    # ------------------------------------------------------------------
    @property
    def servers_button(self) -> Gtk.MenuButton:
        """The "Servers ▾" top-bar `Gtk.MenuButton` (owns its own popover)."""
        return self._servers_button

    @property
    def status_bar(self) -> Gtk.Widget:
        """Server dot + queue/disk/chip segments.

        Not mounted anywhere by MainWindow today (see the __init__ comment
        above) -- kept as a real, independent widget in case a future caller
        wants an aggregate dot + queue/disk/chip segments without the
        window's existing `_hw_statusbar`/`_StatusBar` dashboard.
        """
        return self._status_bar_widget

    @property
    def log_widget(self) -> Gtk.Widget:
        """The collapsible server-log revealer, independent of `status_bar`.

        Mount this (not `status_bar`) wherever the app wants start/stop/
        restart log output to stream -- e.g. MainWindow mounts it in a
        persistent, always-present location (not inside anything that gets
        hidden per-mode or deleted alongside ControlPanel)."""
        return self._log_revealer

    # ------------------------------------------------------------------
    # Popover construction (adapted from ControlPanel._build_servers_popover)
    # ------------------------------------------------------------------
    def _build_servers_button(self) -> Gtk.MenuButton:
        popover = Gtk.Popover()
        popover.set_has_arrow(False)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        outer.set_margin_start(10)
        outer.set_margin_end(10)

        hdr_lbl = Gtk.Label(label="Managed Services")
        hdr_lbl.add_css_class("servers-popover-key")
        hdr_lbl.set_hexpand(True)
        hdr_lbl.set_xalign(0)
        outer.append(hdr_lbl)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(4)
        sep.set_margin_bottom(4)
        outer.append(sep)

        # Group servers by capability; preserve CAPABILITY_LABELS order (same
        # grouping ControlPanel._build_servers_popover used).
        by_cap: "dict[str, list]" = {cap: [] for cap in _sm.CAPABILITY_LABELS}
        for key, sdef in _sm.SERVERS.items():
            for cap in (sdef.capabilities or ()):
                if cap in by_cap:
                    by_cap[cap].append((key, sdef))

        for cap, cap_label in _sm.CAPABILITY_LABELS.items():
            servers_in_cap = by_cap.get(cap, [])
            if not servers_in_cap:
                continue  # e.g. "animatediff" — hardware-only, no server entry
            cap_hdr = Gtk.Label(label=cap_label)
            cap_hdr.add_css_class("servers-cap-header")
            cap_hdr.set_xalign(0)
            outer.append(cap_hdr)
            for key, sdef in servers_in_cap:
                outer.append(self._build_row(key, sdef))

        popover.set_child(outer)

        btn = Gtk.MenuButton(label="Servers ▾")
        btn.add_css_class("servers-menu-btn")
        btn.set_tooltip_text("Start, stop, or restart any managed service")
        btn.set_popover(popover)
        return btn

    def _build_row(self, key: str, sdef) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.add_css_class("servers-popover-row")

        dot = Gtk.Label(label=_GLYPH[Status.OFF])
        dot.add_css_class("servers-popover-dot")
        dot.add_css_class(_ROW_DOT_CLASS[Status.OFF])
        self._row_dots[key] = dot
        row.append(dot)

        text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text_col.set_hexpand(True)
        name_lbl = Gtk.Label(label=sdef.label)
        name_lbl.add_css_class("servers-popover-key")
        name_lbl.set_xalign(0)
        name_lbl.set_max_width_chars(1)
        name_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        text_col.append(name_lbl)
        row.append(text_col)

        start_btn = Gtk.Button(label="▶ Start")
        start_btn.add_css_class("servers-popover-btn")
        start_btn.set_tooltip_text(f"Start {sdef.label}")
        start_btn.connect("clicked", lambda _b, k=key: self._handle_action(k, "start"))
        self._row_start_btns[key] = start_btn
        row.append(start_btn)

        stop_btn = Gtk.Button(label="■ Stop")
        stop_btn.add_css_class("servers-popover-btn")
        stop_btn.add_css_class("servers-popover-btn-stop")
        stop_btn.set_tooltip_text(f"Stop {sdef.label}")
        stop_btn.connect("clicked", lambda _b, k=key: self._handle_action(k, "stop"))
        self._row_stop_btns[key] = stop_btn
        row.append(stop_btn)

        restart_btn = Gtk.Button(label="↺")
        restart_btn.add_css_class("servers-popover-btn")
        restart_btn.set_tooltip_text(f"Restart {sdef.label}")
        restart_btn.connect("clicked", lambda _b, k=key: self._handle_action(k, "restart"))
        self._row_restart_btns[key] = restart_btn
        row.append(restart_btn)

        return row

    # ------------------------------------------------------------------
    # Status bar construction (adapted from _StatusBar)
    # ------------------------------------------------------------------
    def _build_status_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bar.add_css_class("tt-statusbar")

        def _sep() -> Gtk.Label:
            lbl = Gtk.Label(label=" │ ")
            lbl.add_css_class("tt-statusbar-sep")
            return lbl

        self._bar_dot = Gtk.Label(label=_GLYPH[Status.OFF])
        self._bar_dot.add_css_class("tt-statusbar-dot")
        self._bar_dot.add_css_class(_BAR_DOT_CLASS[Status.OFF])

        self._bar_lbl = Gtk.Label(label=_BAR_DOT_TEXT[Status.OFF])
        self._bar_lbl.add_css_class("tt-statusbar-seg")
        self._bar_lbl.set_max_width_chars(1)
        self._bar_lbl.set_ellipsize(Pango.EllipsizeMode.END)

        dot_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        dot_box.append(self._bar_dot)
        dot_box.append(self._bar_lbl)
        bar.append(dot_box)

        # ── Queue depth (hidden when unset/zero) ──────────────────────────
        self._queue_sep = _sep()
        self._queue_sep.set_visible(False)
        bar.append(self._queue_sep)
        self._queue_lbl = Gtk.Label(label="")
        self._queue_lbl.add_css_class("tt-statusbar-seg")
        self._queue_lbl.set_visible(False)
        bar.append(self._queue_lbl)

        # ── Disk free (hidden until the caller sets it) ───────────────────
        self._disk_sep = _sep()
        self._disk_sep.set_visible(False)
        bar.append(self._disk_sep)
        self._disk_lbl = Gtk.Label(label="")
        self._disk_lbl.add_css_class("tt-statusbar-seg")
        self._disk_lbl.set_visible(False)
        bar.append(self._disk_lbl)

        # ── Chip telemetry (hidden until the caller sets it) ──────────────
        self._chip_sep = _sep()
        self._chip_sep.set_visible(False)
        bar.append(self._chip_sep)
        self._chip_lbl = Gtk.Label(label="")
        self._chip_lbl.add_css_class("tt-statusbar-seg")
        self._chip_lbl.set_visible(False)
        bar.append(self._chip_lbl)

        return bar

    # ------------------------------------------------------------------
    # Log revealer construction (adapted from ControlPanel's _srv_log_*)
    # ------------------------------------------------------------------
    def _build_log_revealer(self) -> Gtk.Revealer:
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        revealer.set_transition_duration(150)
        revealer.set_reveal_child(False)

        self._log_buf = Gtk.TextBuffer()
        log_view = Gtk.TextView.new_with_buffer(self._log_buf)
        log_view.set_editable(False)
        log_view.set_cursor_visible(False)
        log_view.set_wrap_mode(Gtk.WrapMode.CHAR)
        log_view.set_hexpand(False)
        log_view.add_css_class("server-log")

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(-1, 80)
        # Prevent long log lines from propagating their natural width upward.
        scroll.set_propagate_natural_width(False)
        scroll.set_child(log_view)
        self._log_scroll = scroll

        revealer.set_child(scroll)
        return revealer

    # ------------------------------------------------------------------
    # Snapshot-driven rendering (no polling — subscribe() drives this)
    # ------------------------------------------------------------------
    def _on_snapshot(self, snap: "dict[str, Status]") -> bool:
        """Re-render every 3-state dot from a fresh status snapshot.

        Registered (wrapped in `GLib.idle_add`) as the `subscribe()` callback
        so it always runs on the main/GTK thread, and called directly once
        from `__init__` to paint the already-known snapshot immediately.
        Returns `GLib.SOURCE_REMOVE` so it behaves correctly if GTK ever
        treats the idle_add-wrapped call as a repeating source.
        """
        self._snapshot = dict(snap)

        for key, dot in self._row_dots.items():
            st = self._snapshot.get(key, Status.OFF)
            self._apply_dot(dot, _ROW_DOT_CLASS, st)

            start_btn = self._row_start_btns.get(key)
            if start_btn is not None:
                start_btn.set_sensitive(st not in (Status.READY, Status.STARTING))
            stop_btn = self._row_stop_btns.get(key)
            if stop_btn is not None:
                stop_btn.set_sensitive(st != Status.OFF)
            restart_btn = self._row_restart_btns.get(key)
            if restart_btn is not None:
                restart_btn.set_sensitive(st != Status.OFF)

        self._refresh_bar_dot()
        self._refresh_launching()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _apply_dot(label: Gtk.Label, class_map: "dict[Status, str]", status: Status) -> None:
        for cls in class_map.values():
            label.remove_css_class(cls)
        label.add_css_class(class_map[status])
        label.set_label(_GLYPH[status])

    def _refresh_bar_dot(self) -> None:
        """Aggregate every known key into one overall status-bar dot.

        Preference order READY > STARTING > ERROR > OFF — this widget has no
        notion of "the current medium"/"the current tab" the way the old
        per-tab `_StatusBar`/`ControlPanel` did, so the best available signal
        across every managed service is the most useful single dot to show.

        Unregistered running chat model (bugfix): `self._snapshot`'s per-key
        statuses alone under-report this when the running chat-LLM backend
        doesn't match any registered `server_manager.SERVERS` entry
        (`ModelStatusService.running_artgen_model().matched_key is None`) --
        every artgen/prompt key legitimately resolves OFF in that case (see
        `model_status.py`'s `_tick()` docstring), even though a chat endpoint
        genuinely answers requests. `running_artgen_model() is not None` is
        the single source of truth for "a chat model is running, matched or
        not", so it overrides the snapshot-only aggregate the same way
        `main_window._render_status_snapshot`'s mirrored aggregate does.
        """
        values = self._snapshot.values()
        if Status.READY in values:
            agg = Status.READY
        elif Status.STARTING in values:
            agg = Status.STARTING
        elif Status.ERROR in values:
            agg = Status.ERROR
        else:
            agg = Status.OFF
        if agg != Status.READY and self._status_service.running_artgen_model() is not None:
            agg = Status.READY
        self._apply_dot(self._bar_dot, _BAR_DOT_CLASS, agg)
        self._bar_lbl.set_label(_BAR_DOT_TEXT[agg])

    def _refresh_launching(self) -> None:
        """Auto-collapse the log revealer once every launching key is READY."""
        if not self._launching:
            return
        done = {k for k in self._launching if self._snapshot.get(k) == Status.READY}
        if done:
            self._launching -= done
        if not self._launching:
            self._log_revealer.set_reveal_child(False)

    # ------------------------------------------------------------------
    # Button action plumbing
    # ------------------------------------------------------------------
    def _handle_action(self, key: str, action: str) -> None:
        """Dispatch a popover row button click to the injected callable.

        Deliberately does nothing else (no busy-locking, no direct
        server_manager call) — the caller's on_start/on_stop/on_restart is
        responsible for the actual server_manager call *and* for calling
        status_service.note_starting()/note_stopping() so the next snapshot
        reflects the action. Button sensitivity then updates itself on the
        next _on_snapshot() call, with no separate bookkeeping needed here.
        """
        if action == "start":
            self._on_start(key)
        elif action == "stop":
            self._on_stop(key)
        elif action == "restart":
            self._on_restart(key)
        else:
            raise ValueError(f"Unknown server action: {action!r}")

    # ------------------------------------------------------------------
    # Public methods (interface contract)
    # ------------------------------------------------------------------
    def append_server_log(self, line: str) -> None:
        """Append one line to the server log and reveal the log panel.

        Must be called on the main thread (same discipline as the code this
        was lifted from — see CLAUDE.md's GTK threading section).
        """
        end = self._log_buf.get_end_iter()
        self._log_buf.insert(end, line + "\n")
        adj = self._log_scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        self._log_revealer.set_reveal_child(True)

    def set_server_launching(self, key: str, launching: bool) -> None:
        """Mark `key` as launching (reveals the log) or settled.

        The revealer collapses once every currently-launching key has either
        been explicitly cleared here or resolved to READY in a status
        snapshot (`_refresh_launching`, called from `_on_snapshot`).
        """
        if launching:
            self._launching.add(key)
            self._log_revealer.set_reveal_child(True)
        else:
            self._launching.discard(key)
            if not self._launching:
                self._log_revealer.set_reveal_child(False)

    def set_status_segments(self, *, queue=None, disk=None, chip=None) -> None:
        """Set the queue/disk/chip status-bar segments.

        Each argument independently defaults to `None`, meaning "leave this
        segment unchanged" — callers that only track e.g. queue depth can
        call this without needing to know the current disk/chip text.
        `queue` may be an int (formatted as "queue: N", hidden at 0) or a
        preformatted string; `disk`/`chip` are preformatted strings, hidden
        when falsy (empty string).
        """
        if queue is not None:
            if isinstance(queue, int):
                visible = queue > 0
                text = f"queue: {queue}" if visible else ""
            else:
                text = str(queue)
                visible = bool(text)
            self._queue_lbl.set_label(text)
            self._queue_lbl.set_visible(visible)
            self._queue_sep.set_visible(visible)

        if disk is not None:
            visible = bool(disk)
            self._disk_lbl.set_label(disk if visible else "")
            self._disk_lbl.set_visible(visible)
            self._disk_sep.set_visible(visible)

        if chip is not None:
            visible = bool(chip)
            self._chip_lbl.set_label(chip if visible else "")
            self._chip_lbl.set_visible(visible)
            self._chip_sep.set_visible(visible)

    def close(self) -> None:
        """Unsubscribe from the status service.

        Idempotent — the closure returned by `subscribe()` is itself
        idempotent (see model_status.py), and `_unsubscribe` is set to
        `None` after the first call so a second `close()` (e.g. from both an
        explicit caller and the "unrealize" handler) is a clean no-op.
        """
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    # ------------------------------------------------------------------
    # Test-helper accessors (per task-1-brief.md)
    # ------------------------------------------------------------------
    def _server_row_glyphs(self) -> "dict[str, str]":
        """Return {server_manager key: currently-displayed glyph}."""
        return {key: dot.get_label() for key, dot in self._row_dots.items()}

    def _activate_start(self, key: str) -> None:
        """Invoke on_start(key) as if the row's Start button were clicked."""
        self._handle_action(key, "start")

    def _log_revealed(self) -> bool:
        """Return whether the server-log revealer is currently expanded."""
        return self._log_revealer.get_reveal_child()
