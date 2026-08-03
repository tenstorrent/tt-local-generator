"""NavState — the single source of truth for "you are here" navigation.

GUI-free. Holds two things and notifies on change:
  * the current breadcrumb trail (`list[Crumb]`) — where you are, with the
    upstream steps clickable to go back;
  * the ordered set of live, resumable CONTEXTS (`list[Context]`) — in-flight
    work (a running pipeline, a remix draft, a watch session) you can leave and
    return to. This is the tray that makes navigation non-destructive.

Routing surface selection + in-flight activities through this ONE model is what
dissolves the scattered toggle/radio state that produced the pipeline
double-click and the dead-ends.

Notification is CHANGE-ONLY (a no-op set never fires) and isolated (a raising
subscriber never breaks the loop) — same discipline as ModelStatusService. The
model touches no GTK and owns no threads/timers; a `notify` callback and
`subscribe()` callbacks are the only outputs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, List, Optional


@dataclass(frozen=True)
class Crumb:
    """One step in the breadcrumb trail. `target` is an opaque id the caller
    maps back to a navigation action; None marks the current (leaf) step."""
    label: str
    target: Optional[str] = None


@dataclass(frozen=True)
class Context:
    """A resumable in-flight activity shown as a tray chip. `id` is stable
    (used to update/close/resume); `running` shows a live dot."""
    id: str
    label: str
    kind: str = ""
    running: bool = False


class NavState:
    def __init__(self, notify: "Optional[Callable[[NavState], None]]" = None) -> None:
        self._crumbs: List[Crumb] = []
        self._contexts: List[Context] = []
        self._subs: List[Callable[[NavState], None]] = []
        if notify is not None:
            self._subs.append(notify)

    # ── breadcrumb trail ────────────────────────────────────────────────
    def set_crumbs(self, crumbs: "List[Crumb]") -> None:
        new = list(crumbs)
        if new == self._crumbs:
            return
        self._crumbs = new
        self._emit()

    def crumbs(self) -> "List[Crumb]":
        return list(self._crumbs)

    # ── live contexts ───────────────────────────────────────────────────
    def open_context(self, ctx: Context) -> None:
        for i, existing in enumerate(self._contexts):
            if existing.id == ctx.id:
                if existing == ctx:
                    return  # identical — no change
                self._contexts[i] = ctx  # replace in place, preserve order
                self._emit()
                return
        self._contexts.append(ctx)
        self._emit()

    def update_context(self, ctx_id: str, **fields) -> None:
        for i, existing in enumerate(self._contexts):
            if existing.id == ctx_id:
                updated = replace(existing, **fields)
                if updated == existing:
                    return
                self._contexts[i] = updated
                self._emit()
                return
        # absent id -> no-op

    def close_context(self, ctx_id: str) -> None:
        kept = [c for c in self._contexts if c.id != ctx_id]
        if len(kept) == len(self._contexts):
            return  # absent -> no-op
        self._contexts = kept
        self._emit()

    def contexts(self) -> "List[Context]":
        return list(self._contexts)

    def has_context(self, ctx_id: str) -> bool:
        return any(c.id == ctx_id for c in self._contexts)

    # ── subscriptions ───────────────────────────────────────────────────
    def subscribe(self, cb: "Callable[[NavState], None]") -> "Callable[[], None]":
        self._subs.append(cb)
        def _unsub() -> None:
            try:
                self._subs.remove(cb)
            except ValueError:
                pass
        return _unsub

    def _emit(self) -> None:
        for cb in list(self._subs):
            try:
                cb(self)
            except Exception:
                pass  # an isolated subscriber failure never breaks navigation
