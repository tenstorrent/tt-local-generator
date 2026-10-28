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
.servers-section-header { color: #4FD1C5; font-weight: bold; font-size: 0.9em;
                          margin-top: 8px; margin-bottom: 2px; }
.servers-row-name { color: #E6F1F0; }
.servers-row-detail { color: #9FB8B6; font-size: 0.85em; }
.servers-row-tag { color: #0F2A35; background-color: #4FD1C5; border-radius: 3px;
                   padding: 0px 4px; font-size: 0.75em; font-weight: bold; }
.servers-row-tag-external { background-color: #F6BC42; }
.servers-row-tag-remote { background-color: #9FB8B6; }
.servers-tt-note { color: #F6BC42; font-size: 0.85em; }
.servers-held-cmd { font-family: monospace; color: #E6F1F0; font-size: 0.85em; }
.servers-log-cancel { margin: 2px 4px; }
"""

_extra_css_applied = False


# ---------------------------------------------------------------------------
# Task 8 helpers: how a merged-view row and a registry entry are shown
# ---------------------------------------------------------------------------

# Health words, same vocabulary as server_control: healthy answers requests;
# a booting word or an empty/docker-status health (a `tt model ps` row that
# says "running" with no health check) is shown as booting.
_HEALTHY_WORDS = frozenset({"healthy", "ready", "ok"})
_BOOTING_WORDS = frozenset({"starting", "booting", "loading", "created", "restarting",
                            "", "unknown", "running", "none"})


def _is_remote_row(s) -> bool:
    """Saved (Connect...) servers and anything on another host: use-only."""
    return s.source == "saved" or not s.host_is_local


def _health_status(s):
    """(Status, word) for a RunningServer's dot and detail line."""
    if s.source == "saved":
        return Status.OFF, "not checked"
    h = (s.health or "").strip().lower()
    if h in _HEALTHY_WORDS:
        return Status.READY, "healthy"
    if h in _BOOTING_WORDS:
        return Status.STARTING, "booting"
    return Status.ERROR, h


def _fmt_uptime(sec) -> str:
    if not isinstance(sec, int) or isinstance(sec, bool) or sec < 0:
        return ""
    if sec < 60:
        return f"{sec}s"
    m = sec // 60
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}h {m}m"
    return f"{h // 24}d {h % 24}h"


def _safe_url(url: str) -> str:
    """A URL for display: user/password parts dropped (a token never shows)."""
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        host = u.hostname or ""
        if u.port:
            host = f"{host}:{u.port}"
        return f"{u.scheme}://{host}{u.path}" if u.scheme else str(url)
    except Exception:
        return ""


def _entry_title(e) -> str:
    """A registry entry's display name: the boutique model's own name
    (``server_manager.display_name_for``) when it has one."""
    if e is None:
        return ""
    names = getattr(_sm, "MODEL_DISPLAY_NAMES", {}) or {}
    if e.sm_key and e.sm_key in names:
        return names[e.sm_key]
    return e.name           # a boutique entry's name is its ServerDef label


def _entry_benefit(e) -> str:
    if e.benefit:
        return e.benefit
    if e.sm_key:
        try:
            return _sm.benefit_for(e.sm_key) or ""
        except Exception:
            return ""
    return ""


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

    def __init__(self, status_service, *, on_start, on_stop, on_restart,
                 on_use=None, on_connect=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._status_service = status_service
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_restart = on_restart
        # Task 8 (server control through tt): optional. ``on_use(server)`` is
        # the "Use" button of a Running/Remote row; ``on_connect()`` opens the
        # Connect dialog. Both None keeps the old popover exactly as it was.
        self._on_use = on_use
        self._on_connect = on_connect
        # set_view() state. None until the first set_view(): until then the
        # popover shows the SERVERS-based rows built below, unchanged.
        self._view_sig = None
        # Test/inspection handle: section name -> list of row records
        # ({"title", "text", "buttons": {label: Gtk.Button}, "server"/"key"}).
        self._view_rows: "dict[str, list[dict]]" = {}
        self._tt_note_text = ""

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
        self._popover = popover
        self._legacy_box = outer

        # Label is just "Servers" — a Gtk.MenuButton renders its OWN dropdown
        # arrow, so a "▾" in the string would double it up.
        btn = Gtk.MenuButton(label="Servers")
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

        # Task 8: a Cancel affordance for a start in progress (a chip queue
        # wait or a long boot). Hidden until set_cancel_action() shows it.
        self._cancel_btn = Gtk.Button(label="Cancel start")
        self._cancel_btn.add_css_class("servers-popover-btn")
        self._cancel_btn.add_css_class("servers-log-cancel")
        self._cancel_btn.set_halign(Gtk.Align.START)
        self._cancel_btn.set_visible(False)
        self._cancel_cb = None
        self._cancel_btn.connect("clicked", self._on_cancel_clicked)

        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        log_box.append(self._cancel_btn)
        log_box.append(scroll)
        revealer.set_child(log_box)
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
    # Task 8: sections built from the merged server view
    # ------------------------------------------------------------------
    def set_view(self, running, registry, held=None, tt_reason: str = "") -> bool:
        """Rebuild the popover as sections from a merged-view snapshot.

        ``running``: ``server_view.RunningServer`` rows (every source).
        ``registry``: ``{key: server_registry.ModelEntry}``.
        ``held``: ownership records that hold leases nobody runs under
        (``<key>#unreapable-<n>`` and ``<key>#kept-<n>``), as dicts.
        ``tt_reason``: why ``tt`` data is missing ("" when it is not).

        Main thread only (the caller posts it with ``GLib.idle_add``).
        A snapshot equal to the last one is skipped, so a popover the user
        has open is not rebuilt under the pointer every refresh.
        Returns ``GLib.SOURCE_REMOVE``.
        """
        running = list(running or [])
        registry = dict(registry or {})
        held = [dict(h) for h in (held or []) if isinstance(h, dict)]
        sig = (tuple(running), tuple(sorted(registry.items())),
               tuple(tuple(sorted((k, str(v)) for k, v in h.items())) for h in held),
               tt_reason or "")
        if sig == self._view_sig:
            return GLib.SOURCE_REMOVE
        self._view_sig = sig
        # The SERVERS-based rows are no longer shown: drop their refs so the
        # status-snapshot repaint does not touch detached widgets.
        self._row_dots.clear()
        self._row_start_btns.clear()
        self._row_stop_btns.clear()
        self._row_restart_btns.clear()
        self._view_rows = {"running": [], "ready": [], "available": [],
                           "remote": [], "held": []}
        self._tt_note_text = ""

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        outer.set_margin_start(10)
        outer.set_margin_end(10)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hdr = Gtk.Label(label="Model servers")
        hdr.add_css_class("servers-popover-key")
        hdr.set_hexpand(True)
        hdr.set_xalign(0)
        top.append(hdr)
        if self._on_connect is not None:
            conn = Gtk.Button(label="Connect...")
            conn.add_css_class("servers-popover-btn")
            conn.set_tooltip_text("Add a model server on another machine by its URL")
            conn.connect("clicked", lambda _b: self._on_connect())
            top.append(conn)
            self._connect_btn = conn
        outer.append(top)

        if tt_reason:
            self._tt_note_text = (f"Servers started with tt are not listed: {tt_reason}. "
                                  "Script servers are still shown.")
            note = Gtk.Label(label=self._tt_note_text)
            note.add_css_class("servers-tt-note")
            note.set_xalign(0)
            note.set_wrap(True)
            note.set_max_width_chars(60)
            outer.append(note)

        remote_rows = [s for s in running if _is_remote_row(s)]
        local_rows = [s for s in running if not _is_remote_row(s)]
        running_keys = {s.key for s in running}

        # ── Running ──
        outer.append(self._section_header(f"Running ({len(local_rows)})"))
        if not local_rows:
            outer.append(self._empty_line("No model server is running on this machine."))
        for s in local_rows:
            outer.append(self._running_row(s, registry, "running"))

        # ── Ready to start ── (installed entries that are not running)
        ready = [e for e in registry.values()
                 if e.installed and e.key not in running_keys]
        ready.sort(key=lambda e: (e.backend != "boutique", _entry_title(e).lower()))
        outer.append(self._section_header(f"Ready to start ({len(ready)})"))
        if not ready:
            outer.append(self._empty_line("Every installed model is running."))
        for e in ready:
            outer.append(self._entry_row(e, "ready", "Start"))

        # ── Available ── (catalog models not installed), collapsed
        avail = [e for e in registry.values()
                 if not e.installed and e.key not in running_keys]
        avail.sort(key=lambda e: _entry_title(e).lower())
        if avail:
            exp_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for e in avail:
                exp_box.append(self._entry_row(e, "available", "Get and start"))
            exp = Gtk.Expander()
            # The header style goes on the label only: a class on the
            # Expander itself is inherited by every row inside it.
            exp.set_label_widget(self._section_header(
                f"Available to download ({len(avail)})"))
            exp.set_child(exp_box)
            self._available_expander = exp
            outer.append(exp)

        # ── Remote ──
        if remote_rows:
            outer.append(self._section_header(f"Remote ({len(remote_rows)})"))
            for s in remote_rows:
                outer.append(self._running_row(s, registry, "remote"))

        # ── Held leases ── (only when there are any)
        if held:
            outer.append(self._section_header(f"Held leases ({len(held)})"))
            for h in held:
                outer.append(self._held_row(h))

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_propagate_natural_width(True)
        scroll.set_max_content_height(680)
        scroll.set_min_content_width(480)
        scroll.set_child(outer)
        self._popover.set_child(scroll)
        self._sections_box = outer
        return GLib.SOURCE_REMOVE

    # -- row builders --------------------------------------------------
    @staticmethod
    def _section_header(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.add_css_class("servers-section-header")
        lbl.set_xalign(0)
        return lbl

    @staticmethod
    def _empty_line(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.add_css_class("servers-row-detail")
        lbl.set_xalign(0)
        return lbl

    def _make_row(self, section: str, *, status, title: str, tags, detail: str,
                  buttons, record: dict) -> Gtk.Box:
        """One row: dot | title + tags / detail line | buttons.

        ``tags``: list of (text, css_class_or_None). ``buttons``: list of
        (label, tooltip, callback, css_class_or_None).
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.add_css_class("servers-popover-row")
        texts = [title]

        if status is not None:
            dot = Gtk.Label(label=_GLYPH[status])
            dot.add_css_class("servers-popover-dot")
            dot.add_css_class(_ROW_DOT_CLASS[status])
            dot.set_valign(Gtk.Align.START)
            row.append(dot)
            record["glyph"] = _GLYPH[status]

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        col.set_hexpand(True)
        line1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        name = Gtk.Label(label=title)
        name.add_css_class("servers-row-name")
        name.set_xalign(0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(34)
        name.set_tooltip_text(title)
        line1.append(name)
        for text, cls in tags:
            tag = Gtk.Label(label=text)
            tag.add_css_class("servers-row-tag")
            if cls:
                tag.add_css_class(cls)
            tag.set_valign(Gtk.Align.CENTER)
            line1.append(tag)
            texts.append(text)
        col.append(line1)
        if detail:
            det = Gtk.Label(label=detail)
            det.add_css_class("servers-row-detail")
            det.set_xalign(0)
            det.set_wrap(True)
            det.set_max_width_chars(48)
            col.append(det)
            texts.append(detail)
        row.append(col)

        record["buttons"] = {}
        for label, tooltip, cb, cls in buttons:
            btn = Gtk.Button(label=label)
            btn.add_css_class("servers-popover-btn")
            if cls:
                btn.add_css_class(cls)
            btn.set_valign(Gtk.Align.CENTER)
            if tooltip:
                btn.set_tooltip_text(tooltip)
            btn.connect("clicked", lambda _b, f=cb: f())
            row.append(btn)
            record["buttons"][label] = btn
            texts.append(label)

        record["title"] = title
        record["text"] = " | ".join(texts)
        record["widget"] = row
        self._view_rows[section].append(record)
        return row

    def _running_row(self, s, registry: dict, section: str) -> Gtk.Box:
        status, health_word = _health_status(s)
        entry = registry.get(s.key)
        title = _entry_title(entry) if entry is not None and s.source != "saved" else s.name
        tags = [(s.source, None)]
        if section == "remote":
            tags.append(("remote", "servers-row-tag-remote"))
        elif s.owner != "app":
            tags.append(("not started here", "servers-row-tag-external"))
        bits = []
        if s.port is not None:
            bits.append(f"port {s.port}")
        bits.append(health_word)
        up = _fmt_uptime(s.uptime_s)
        if up:
            bits.append(f"up {up}")
        if section == "remote" and s.url:
            bits.append(_safe_url(s.url))
        buttons = []
        if self._on_use is not None:
            buttons.append(("Use", f"Use {title}", lambda srv=s: self._on_use(srv), None))
        # Stop: never for a remote row (spec 4.5); a local server the app did
        # not start says "Stop..." because ServerControl asks first.
        if section == "running" and s.host_is_local:
            if s.owner == "app":
                buttons.append(("Stop", f"Stop {title}",
                                lambda srv=s: self._on_stop(srv), "servers-popover-btn-stop"))
            else:
                buttons.append(("Stop...", f"{title} was not started by tt-local-generator; "
                                "you will be asked before it is stopped",
                                lambda srv=s: self._on_stop(srv), "servers-popover-btn-stop"))
        return self._make_row(section, status=status, title=title, tags=tags,
                              detail=" · ".join(bits), buttons=buttons,
                              record={"server": s, "key": s.key})

    def _entry_row(self, e, section: str, verb: str) -> Gtk.Box:
        title = _entry_title(e)
        tags = [("script" if e.backend == "boutique" else "tt", None)]
        detail = _entry_benefit(e)
        buttons = [(verb, f"{verb} {title}", lambda k=e.key: self._on_start(k), None)]
        # No status dot: a model that is not running has no state to show
        # (the OFF glyph is near-invisible on the popover and says nothing).
        return self._make_row(section, status=None, title=title, tags=tags,
                              detail=detail, buttons=buttons, record={"key": e.key})

    def _held_row(self, h: dict) -> Gtk.Box:
        name = str(h.get("name") or "?")
        ids = [x.strip() for x in str(h.get("lease_id") or "").split(",") if x.strip()]
        cmd = "gozer release " + " ".join(ids) if ids else ""
        if h.get("unreapable") or "#unreapable-" in name:
            why = ("Kept after a start that could not be cleaned up. Release it by hand "
                   "once nothing of that start runs:")
        else:
            why = ("Kept after a stop that failed. It is released at a later refresh once "
                   "its server is gone, or by hand:")
        model = name.split("#", 1)[0]
        buttons = []
        if cmd:
            buttons.append(("Copy", "Copy the release command",
                            lambda c=cmd: self._copy_text(c), None))
        row = self._make_row("held", status=None, title=f"{model}: lease {', '.join(ids) or '?'}",
                             tags=[], detail=why, buttons=buttons,
                             record={"key": name, "command": cmd})
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        wrap.append(row)
        if cmd:
            c = Gtk.Label(label=cmd)
            c.add_css_class("servers-held-cmd")
            c.set_xalign(0)
            c.set_selectable(True)
            c.set_margin_start(4)
            wrap.append(c)
            self._view_rows["held"][-1]["text"] += " | " + cmd
        return wrap

    @staticmethod
    def _copy_text(text: str) -> None:
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(text)

    # -- cancel affordance ---------------------------------------------
    def set_cancel_action(self, label: str, callback) -> None:
        """Show a Cancel button above the server log that calls ``callback``
        once. Main thread only."""
        self._cancel_cb = callback
        self._cancel_btn.set_label(label)
        self._cancel_btn.set_sensitive(True)
        self._cancel_btn.set_visible(True)
        self._log_revealer.set_reveal_child(True)

    def clear_cancel_action(self) -> None:
        """Hide the Cancel button. Main thread only."""
        self._cancel_cb = None
        self._cancel_btn.set_visible(False)

    def _on_cancel_clicked(self, _btn) -> None:
        cb, self._cancel_cb = self._cancel_cb, None
        self._cancel_btn.set_sensitive(False)
        if cb is not None:
            cb()

    # ------------------------------------------------------------------
    # Test-helper accessors (per task-1-brief.md)
    # ------------------------------------------------------------------
    def _section_rows(self, section: str) -> "list[dict]":
        """Row records of one set_view() section (running/ready/available/
        remote/held)."""
        return list(self._view_rows.get(section, []))

    def _log_text(self) -> str:
        return self._log_buf.get_text(self._log_buf.get_start_iter(),
                                      self._log_buf.get_end_iter(), False)

    def _server_row_glyphs(self) -> "dict[str, str]":
        """Return {server_manager key: currently-displayed glyph}."""
        return {key: dot.get_label() for key, dot in self._row_dots.items()}

    def _activate_start(self, key: str) -> None:
        """Invoke on_start(key) as if the row's Start button were clicked."""
        self._handle_action(key, "start")

    def _log_revealed(self) -> bool:
        """Return whether the server-log revealer is currently expanded."""
        return self._log_revealer.get_reveal_child()
