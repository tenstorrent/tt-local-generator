# app/model_picker.py — shared per-capability model picker.
#
# Pipeline UX overhaul (SP task 3): the pipeline editor wants a per-step
# model dropdown that looks and behaves exactly like Create's scoped model
# dropdown (server_manager.servers_for_capability + display_name_for/
# benefit_for + ModelStatusService dots), WITHOUT importing from
# create_view.py or forking its logic. This module is the shared seam:
#
#   - `picker_entries(...)` is the pure data function both Create (in a
#     future refactor) and the pipeline editor can call. It has zero GTK
#     imports, so it's importable and testable on a headless CI box.
#   - `ModelPickerRow` is the GTK widget wrapper pipeline steps embed today.
#
# The GTK import is deferred to `ModelPickerRow`'s module-level try/except so
# `picker_entries` keeps working in environments with no display server at
# all (matches the "guard the GTK import" constraint in the task brief).
from __future__ import annotations

from typing import Callable, Optional

import server_manager as _sm

try:  # pragma: no cover - exercised implicitly by any GTK-backed test
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    _GTK_OK = True
except Exception:  # pragma: no cover - headless / no GTK bindings installed
    _GTK_OK = False


def _dot(has_service: bool, status) -> str:
    """Resolve one entry's status glyph.

    `has_service=False` means no `ModelStatusService` is wired up at all
    (e.g. a bare/legacy caller) -- in that case every entry reads solid "●"
    rather than falsely implying "we checked and it's off". When a service
    IS present, an unknown/missing key (never seen READY or STARTING) is
    genuinely "off/unknown" -> "◌".
    """
    if not has_service:
        return "●"
    try:
        from model_status import Status
    except Exception:
        # model_status not importable for some reason -- fail soft to the
        # same "assume fine" glyph a no-service caller gets.
        return "●"
    if status == Status.READY:
        return "●"
    if status == Status.STARTING:
        return "◐"
    return "◌"


def picker_entries(capability: str, snapshot: Optional[dict] = None,
                    has_service: bool = False) -> "list[tuple[str, str, str, str]]":
    """Pure: `(key, display_name, benefit, dot)` for every model that can
    perform `capability`.

    `snapshot` is a `{server_manager key: model_status.Status}` dict, normally
    straight from `ModelStatusService.snapshot()`. `has_service` gates
    whether an absent snapshot entry means "off" (service present, key just
    hasn't reported READY/STARTING) or "unknown, assume fine" (no service at
    all -- mirrors CreateView's boolean-health fallback).

    `capability == "animatediff"` is a special case: AnimateDiff is a
    self-contained generator with no `ServerDef`/server to start, so it gets
    a single synthetic entry (always solid "●" -- there's nothing to poll)
    instead of iterating `servers_for_capability`.
    """
    snapshot = snapshot or {}
    if capability == "animatediff":
        return [(
            "animatediff",
            _sm.display_name_for("animatediff"),
            _sm.benefit_for("animatediff"),
            "●",
        )]
    out = []
    for sdef in _sm.servers_for_capability(capability):
        out.append((
            sdef.key,
            _sm.display_name_for(sdef.key),
            _sm.benefit_for(sdef.key),
            _dot(has_service, snapshot.get(sdef.key)),
        ))
    return out


if _GTK_OK:

    class ModelPickerRow(Gtk.Box):
        """A `Gtk.DropDown` + benefit sub-label for one pipeline-step
        capability, sharing `picker_entries` with any other picker surface.

        Mirrors `CreateView`'s status-service subscribe/unrealize pattern
        (see `create_view.py`'s `_status_unsub`/`_on_unrealize`) so a
        long-lived `ModelStatusService` never calls back into a torn-down
        row, and so live status changes (a server finishing its health
        check) rebuild the dots in place instead of needing a fresh row.
        """

        def __init__(self, capability: str, status_service=None,
                     selected_key: Optional[str] = None,
                     on_change: Optional[Callable[[Optional[str]], None]] = None):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)

            self._capability = capability
            self._status_service = status_service
            self._on_change = on_change
            # Ordered in lockstep with the DropDown's Gtk.StringList so
            # `selected_key()` can map "selected index" -> "server key".
            self._entries: "list[tuple[str, str, str, str]]" = []

            self._dropdown = Gtk.DropDown()
            self._benefit_label = Gtk.Label(xalign=0)
            self._benefit_label.add_css_class("dim-label")
            self._benefit_label.add_css_class("caption")
            self._benefit_label.set_wrap(True)

            self.append(self._dropdown)
            self.append(self._benefit_label)

            snapshot = status_service.snapshot() if status_service is not None else {}
            self._rebuild(snapshot, preferred_key=selected_key)

            self._dropdown.connect("notify::selected", self._on_selected_changed)

            # Subscribe for live dot updates, mirroring CreateView: seed
            # synchronously above, then keep in sync via subscribe(); tear
            # down on unrealize so the service never calls into a dead row.
            self._status_unsub: Optional[Callable[[], None]] = None
            if self._status_service is not None:
                self._status_unsub = self._status_service.subscribe(
                    lambda snap: GLib.idle_add(self._on_status_snapshot, snap)
                )
            self.connect("unrealize", self._on_unrealize)

        # ------------------------------------------------------------
        # Build / rebuild
        # ------------------------------------------------------------
        def _rebuild(self, snapshot: dict, preferred_key: Optional[str] = None) -> None:
            """(Re)populate the dropdown's model list from `picker_entries`,
            preserving the currently-selected key across a live status
            update when possible (falls back to `preferred_key`, then index
            0 -- a single-entry capability like AnimateDiff always lands on
            its one entry)."""
            if preferred_key is None:
                preferred_key = self.selected_key()

            self._entries = picker_entries(
                self._capability, snapshot=snapshot,
                has_service=self._status_service is not None,
            )
            labels = [f"{dot} {name}" for (_key, name, _benefit, dot) in self._entries]
            self._dropdown.set_model(Gtk.StringList.new(labels))

            index = 0
            if preferred_key is not None:
                for i, (key, *_rest) in enumerate(self._entries):
                    if key == preferred_key:
                        index = i
                        break
            if self._entries:
                self._dropdown.set_selected(index)
                self._benefit_label.set_label(self._entries[index][2])
            else:
                self._benefit_label.set_label("")

        # ------------------------------------------------------------
        # Public API
        # ------------------------------------------------------------
        def selected_key(self) -> Optional[str]:
            """The `server_manager` key of the currently-selected entry, or
            `None` if the capability has no entries at all."""
            idx = self._dropdown.get_selected()
            if idx is None or idx == Gtk.INVALID_LIST_POSITION or not self._entries:
                return None
            if idx >= len(self._entries):
                return None
            return self._entries[idx][0]

        # ------------------------------------------------------------
        # Signal handlers
        # ------------------------------------------------------------
        def _on_selected_changed(self, *_args) -> None:
            idx = self._dropdown.get_selected()
            if self._entries and idx is not None and idx != Gtk.INVALID_LIST_POSITION \
                    and idx < len(self._entries):
                self._benefit_label.set_label(self._entries[idx][2])
            if self._on_change is not None:
                self._on_change(self.selected_key())

        def _on_status_snapshot(self, snap: dict) -> bool:
            """`GLib.idle_add` target for the service's `subscribe()`
            callback -- runs on the main thread. Rebuilds the dropdown
            labels/benefit text from the fresh snapshot, keeping whatever
            key was already selected. Returns `GLib.SOURCE_REMOVE` since
            this fires once per pushed snapshot, not a repeating timer."""
            self._rebuild(dict(snap or {}))
            return GLib.SOURCE_REMOVE

        def _on_unrealize(self, *_args) -> None:
            """Unsubscribe from the status service when this row is torn
            down, so a long-lived `ModelStatusService` never keeps calling
            back into a destroyed widget. Guards both 'no service was ever
            injected' and 'already unsubscribed' (GTK can fire `unrealize`
            more than once in some teardown paths)."""
            unsub = self._status_unsub
            if unsub is not None:
                self._status_unsub = None
                unsub()
