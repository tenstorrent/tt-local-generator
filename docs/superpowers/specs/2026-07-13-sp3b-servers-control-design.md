# SP-3b — Standalone Servers control + status bar + server-log (on the service)

**Date:** 2026-07-13
**Branch:** `feat/pipeline-editor` (local; not merged)
**Status:** design self-approved (user directive "continue with sp-1 to 2 and 3")
**Program:** coherent shell, SP-3 (retire vestiges), stage **b of a-d**.

## Problem

Server management (start/stop/restart every service), the status bar (server
dot + queue + disk + chip), and the server-log stream all live INSIDE
`ControlPanel` (its `_toolbar_box` holds the `Servers ▾` button + popover; its
`_footer_box` holds the server-status box + `_srv_log_*` revealers). ControlPanel
is deleted in SP-3d — so these must first move to a **standalone control owned by
MainWindow, wired to `ModelStatusService`**, or they die with ControlPanel.

## Goal

Extract the server/status/log stack out of ControlPanel into a standalone,
MainWindow-owned widget (`ServersControl`) that:
- Renders the `Servers ▾` popover (start/stop/restart per service, grouped by
  capability) with **3-state dots from `ModelStatusService`** (not the old
  `_refresh_servers_popover` boolean poll).
- Renders the status bar (server dot + queue/disk/chip segments) from the
  service.
- Owns the server-log revealer (streams start-script output; collapses when the
  service reports the server ready).
Mounted by MainWindow in the top bar + footer, independent of ControlPanel, so
SP-3d can delete ControlPanel without losing server management.

## Non-goals

- No deletion of ControlPanel yet (SP-3d). During SP-3b the ControlPanel copy may
  transiently coexist; SP-3d removes it. (Prefer: MainWindow mounts the NEW
  `ServersControl` and stops mounting ControlPanel's `toolbar_box`/`footer_box`
  server bits — see "Coexistence" below.)
- No migration of generate/queue/attractor buttons (SP-3c/3d).
- Behavior-preserving: server start/stop/restart, log streaming, and status-bar
  segments work exactly as today.

## Global constraints

- **Behavior parity:** starting/stopping/restarting any service, the log stream,
  and the status-bar segments behave identically to today.
- **One source of truth:** the popover dots + status-bar server dot read
  `ModelStatusService` (retiring `_refresh_servers_popover`'s own poll — one of
  the "3 legacy pollers"). Start/stop still call `note_starting`/`note_stopping`.
- **GTK threading:** service callbacks → `GLib.idle_add`. Log streaming stays on
  its existing thread→idle_add path.
- Palette tt-vscode-toolkit; `_CSS` ASCII-only; dot glyphs in Python strings.
- System python; version bump + changelog on landing; local only. Deselect the
  two known flakes in full-suite runs.

## Architecture

### `app/servers_control.py` — `ServersControl` (new)

A `Gtk.Box`-based widget (or a small controller owning a `Gtk.MenuButton` +
`Gtk.Popover` + a status-bar `Gtk.Box` + a log `Gtk.Revealer`). Constructor takes
the collaborators it needs (no ControlPanel dependency):
`ServersControl(status_service, on_start=None, on_stop=None, on_restart=None)`
where the callbacks route to `server_manager.start/stop/restart` + the service's
`note_starting`/`note_stopping` (MainWindow supplies them, reusing its existing
`_on_start_server`/`_on_stop_server`/servers-action logic).

Moved out of ControlPanel (behavior-preserving; adapt, don't rewrite):
- `_build_servers_popover` + `_on_servers_action` + `_refresh_servers_popover`
  → the popover, but dots come from `status_service.snapshot()` (3-state) and it
  **subscribes** to the service instead of polling.
- the server-status box / status-bar segments (`_server_status_box`,
  `set_server_state`, the `tt-statusbar-*` bits) → driven by the service.
- the server-log revealer (`_srv_log_*`, `append_server_log`,
  `set_server_launching`) → owned here; MainWindow forwards log lines to it.

### MainWindow wiring

- Construct `self._servers_control = ServersControl(self._status_service, on_start=..., on_stop=..., on_restart=...)` and mount its `Servers ▾` button in the top bar (next to the loop nav) and its status-bar/log in the footer — **instead of** ControlPanel's toolbar/footer server bits.
- Route existing server-log output (currently `self._controls.append_server_log`)
  to `self._servers_control.append_server_log`; route `set_server_launching`
  likewise.
- Retire `_refresh_servers_popover`'s standalone polling (the popover now reads the
  service). `MainWindow._health_loop` and the artgen-panel poller are still
  removed later (3d), but this task removes the popover poll specifically.

### Coexistence (avoid a duplicate Servers ▾)

To prevent two `Servers ▾` controls transiently, MainWindow stops mounting
ControlPanel's server bits once `ServersControl` exists: keep ControlPanel
constructed (generate/queue/source-toggle still needed until 3c/3d) but do NOT
add its `toolbar_box` servers button / footer server-status+log to the window;
mount `ServersControl`'s instead. If ControlPanel's toolbar/footer can't be
cleanly split, hide the specific server widgets within them and mount the new
control alongside — whichever is lower-risk; the invariant is exactly one visible
Servers control and one status bar.

## Data flow

`ModelStatusService` snapshot → `ServersControl` (subscribe → idle_add → refresh
popover dots + status-bar dot). User clicks Start/Stop/Restart → callbacks →
`server_manager.*` + `note_starting/stopping` → next tick updates dots. Start
script output → MainWindow → `ServersControl.append_server_log` → log revealer;
service reports READY → collapse the log.

## Error handling

- Service snapshot for an unknown key → dot ◌; a start/stop callback failure →
  surfaced in the status bar / log, never crashes.
- Log-stream threading unchanged (idle_add).

## Testing

- `ServersControl` constructs standalone (fake `status_service` + callbacks), no
  ControlPanel dependency.
- Popover lists services grouped by capability with 3-state dots from a pushed
  snapshot; a Start click invokes `on_start(key)`; Stop → `on_stop(key)`.
- Status-bar server dot reflects the service snapshot (ready/starting/off).
- `append_server_log` appends + reveals; a READY snapshot collapses the log.
- MainWindow mounts exactly ONE Servers control + one status bar (no duplicate
  with ControlPanel); server-log output routes to `ServersControl`.
- Behavior parity: start/stop/restart still call `server_manager.*` for the right
  key (reuse the SP-2 hook tests).

## File summary

| File | Change |
|---|---|
| `app/servers_control.py` | NEW — `ServersControl` (popover + status bar + log, service-driven), extracted from ControlPanel behavior |
| `app/main_window.py` | construct + mount `ServersControl`; route server-log/launching to it; stop mounting ControlPanel's server bits; retire `_refresh_servers_popover` polling |
| `tests/…` | ServersControl standalone + popover dots/actions + status bar + log; MainWindow single-control mount + log routing + start/stop parity |
