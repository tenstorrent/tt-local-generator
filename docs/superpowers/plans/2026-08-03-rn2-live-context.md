# RN-2: Live-Context Layer (breadcrumb + resumable tray) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Non-destructive, stateful navigation — a "you are here" breadcrumb + a tray of resumable live contexts (a running pipeline, a remix draft, a watch session) that persist across navigation, so you can leave any activity and return to it. No dead-ends.

**Architecture:** A GUI-free `app/nav_state.py` (`NavState`) is the single source of truth for the current breadcrumb trail + the ordered set of live contexts, with change-only subscriptions (mirrors `ModelStatusService`/`ready_to_run`). GUI widgets render it; `MainWindow` sets crumbs on navigation and opens/closes contexts at the pipeline-run / remix / watch sites. Routing surface selection through this one model is also what dissolves the Pipelines-toggle-vs-loop-radio conflict behind the double-click.

**Tech Stack:** Python 3 (`/usr/bin/python3`), GTK4/PyGObject, pytest under `xvfb-run`.

## Global Constraints

- **`nav_state.py` is GUI-free** (no `gi` import) and **injectable** (`notify` callback in the ctor; no threads/timers of its own) so it unit-tests with fakes.
- **Change-only notification:** subscribers fire only when state actually changes (crumbs list or contexts list differ), never on a no-op set — mirrors `ModelStatusService`. A raising subscriber must never break the notifier.
- **Additive, non-destructive:** RN-2 adds a state model + two widgets + wiring; it does not delete the existing surface-switch handlers in this task. (A later task may route them through `NavState`; Task 1 ships the model standalone.)
- System `/usr/bin/python3`; tests `xvfb-run --auto-servernum` (Task 1's are pure — plain `/usr/bin/python3` is fine). Bump `VERSION` + changelog per shippable task. Local commits only; do not push.

## Sub-project shape (tasks)

- **Task 1 (this plan, detailed): `nav_state.py`** — the pure state model + tests. Standalone, shippable.
- **Task 2 (outlined; detailed after Task 1): breadcrumb + context-tray widgets** rendering `NavState`, mounted in the top bar, `MainWindow` constructs a `NavState` and wires `set_crumbs` on each navigation.
- **Task 3 (outlined): register real contexts** — open a `pipeline` context when a pipeline runs (resume → show pipeline studio), a `remix` context when a remix starts (resume → Create pre-seeded), a `watch` context when the attractor opens (resume → present it); clicking a tray chip resumes; closing dismisses. Route the Pipelines show/hide through `NavState` so the double-click is gone.

---

### Task 1: `nav_state.py` — the navigation-state model

**Files:**
- Create: `app/nav_state.py`
- Test: `tests/test_nav_state.py`

**Interfaces:**
- Produces: `Crumb(label: str, target: Optional[str] = None)` (frozen); `Context(id: str, label: str, kind: str = "", running: bool = False)` (frozen); `class NavState`.
- `NavState` API (consumed by Task 2/3):
  - `set_crumbs(crumbs: list[Crumb]) -> None` — replace the trail; notify iff changed.
  - `crumbs() -> list[Crumb]` — copy of the current trail.
  - `open_context(ctx: Context) -> None` — add, or replace-in-place by `id` (order preserved on replace); notify iff changed.
  - `update_context(id: str, **fields) -> None` — change `label`/`running`/`kind` of an existing context; no-op if absent; notify iff changed.
  - `close_context(id: str) -> None` — remove by id; notify iff changed.
  - `contexts() -> list[Context]` — copy, in insertion order.
  - `has_context(id: str) -> bool`.
  - `subscribe(cb: Callable[[NavState], None]) -> Callable[[], None]` — returns an unsubscribe; cb called on every change; a raising cb never breaks the loop.

- [ ] **Step 1: Write the failing tests** (`tests/test_nav_state.py`) — pure, no GTK:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from nav_state import NavState, Crumb, Context


def test_crumbs_set_get_and_change_only_notify():
    fired = []
    ns = NavState(notify=lambda s: fired.append(len(s.crumbs())))
    ns.set_crumbs([Crumb("Library", "library"), Crumb("Lighthouse")])
    assert [c.label for c in ns.crumbs()] == ["Library", "Lighthouse"]
    assert fired == [2]
    # setting the identical trail again does NOT notify
    ns.set_crumbs([Crumb("Library", "library"), Crumb("Lighthouse")])
    assert fired == [2]
    ns.set_crumbs([Crumb("Create")])
    assert fired == [2, 1]


def test_open_update_close_context():
    fired = []
    ns = NavState(notify=lambda s: fired.append([c.id for c in s.contexts()]))
    ns.open_context(Context("pipeline", "Pipeline 2/5", kind="pipeline", running=True))
    ns.open_context(Context("remix", "Remix: lighthouse", kind="remix"))
    assert [c.id for c in ns.contexts()] == ["pipeline", "remix"]
    assert ns.has_context("pipeline") and ns.has_context("remix")
    # open with an existing id REPLACES in place (order preserved), notifies on change
    ns.open_context(Context("pipeline", "Pipeline 3/5", kind="pipeline", running=True))
    ctxs = ns.contexts()
    assert [c.id for c in ctxs] == ["pipeline", "remix"]
    assert ctxs[0].label == "Pipeline 3/5"
    # update mutates named fields
    ns.update_context("pipeline", running=False, label="Pipeline done")
    assert ns.contexts()[0].running is False and ns.contexts()[0].label == "Pipeline done"
    # update of an absent id is a no-op (no notify)
    before = len(fired)
    ns.update_context("nope", running=True)
    assert len(fired) == before
    # close removes
    ns.close_context("pipeline")
    assert [c.id for c in ns.contexts()] == ["remix"]
    # closing an absent id is a no-op (no notify)
    before = len(fired)
    ns.close_context("nope")
    assert len(fired) == before


def test_open_identical_context_does_not_notify():
    fired = []
    ns = NavState(notify=lambda s: fired.append(1))
    c = Context("watch", "Watch", kind="watch", running=True)
    ns.open_context(c)
    ns.open_context(Context("watch", "Watch", kind="watch", running=True))  # identical
    assert fired == [1]


def test_subscribe_unsubscribe_and_raising_subscriber_is_isolated():
    seen_a, seen_b = [], []
    ns = NavState()
    def bad(_s):
        raise RuntimeError("boom")
    un_bad = ns.subscribe(bad)
    ns.subscribe(lambda s: seen_a.append(1))
    ns.set_crumbs([Crumb("X")])          # bad raises but a still fires
    assert seen_a == [1]
    un_bad()
    ns.subscribe(lambda s: seen_b.append(1))
    ns.set_crumbs([Crumb("Y")])
    assert seen_b == [1]


def test_ctor_notify_and_subscribers_both_fire():
    ctor_hits, sub_hits = [], []
    ns = NavState(notify=lambda s: ctor_hits.append(1))
    ns.subscribe(lambda s: sub_hits.append(1))
    ns.open_context(Context("p", "P"))
    assert ctor_hits == [1] and sub_hits == [1]
```

- [ ] **Step 2: Run — verify fail** (`ModuleNotFoundError: nav_state`).
Run: `/usr/bin/python3 -m pytest tests/test_nav_state.py -v`

- [ ] **Step 3: Implement `app/nav_state.py`:**

```python
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
```

- [ ] **Step 4: Run — verify pass.** `/usr/bin/python3 -m pytest tests/test_nav_state.py -v` → all pass.

- [ ] **Step 5: Commit.**
```bash
git add app/nav_state.py tests/test_nav_state.py
git commit -m "feat(nav): NavState — pure live-context/breadcrumb model (RN-2 t1)"
```

### Finalize (Task 1)

No VERSION bump yet — `NavState` is unwired until Task 2 mounts it (bump lands with the first user-visible slice). Leave `docs/superpowers/plans/2026-08-03-rn2-live-context.md` in the tree.

## Verification (Task 1)

`tests/test_nav_state.py` green (pure, no display). The model is standalone and imported by nothing yet — Task 2 mounts the widgets and wires `MainWindow`.

---

### Task 2: breadcrumb + context-tray widgets + navigation wiring

**Files:**
- Create: `app/nav_widgets.py`
- Test: `tests/test_nav_widgets.py`
- Modify: `app/main_window.py` — construct `NavState`, mount both widgets, set crumbs on each navigation, route `on_navigate`.

**Interfaces:**
- Consumes Task 1's `NavState`/`Crumb`/`Context`.
- Produces: `class Breadcrumb(Gtk.Box)` ctor `(nav_state, on_navigate)`; `class ContextTray(Gtk.Box)` ctor `(nav_state, on_resume, on_dismiss)`. Both subscribe to `nav_state` and re-render (via `GLib.idle_add`, so an off-thread context mutation in Task 3 is safe). `MainWindow` gains `self._nav_state`, `self._breadcrumb`, `self._context_tray`, and a `_set_crumbs(crumbs)` helper that no-ops when `_nav_state` is absent (so existing test harnesses are unaffected).

- [ ] **Step 1: Write failing widget tests** (`tests/test_nav_widgets.py`) — GTK-probe/skip header like `tests/test_nav_state.py`+display probe:

```python
import sys
from pathlib import Path
sys.path.insert(0, "/usr/lib/python3/dist-packages")
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pytest
try:
    import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display available", allow_module_level=True)

from nav_state import NavState, Crumb, Context
from nav_widgets import Breadcrumb, ContextTray

def _labels(box):
    out, ch = [], box.get_first_child()
    while ch is not None:
        # descend one level to catch Button/Label labels
        if isinstance(ch, Gtk.Button): out.append(("btn", ch.get_label()))
        elif isinstance(ch, Gtk.Label): out.append(("lbl", ch.get_label()))
        else: out.append(("box", None))
        ch = ch.get_next_sibling()
    return out

def test_breadcrumb_renders_links_and_leaf(monkeypatch):
    # render synchronously: make GLib.idle_add call immediately
    import nav_widgets
    monkeypatch.setattr(nav_widgets.GLib, "idle_add", lambda fn, *a: (fn(*a), False)[1])
    ns = NavState()
    nav = []
    bc = Breadcrumb(ns, on_navigate=lambda t: nav.append(t))
    ns.set_crumbs([Crumb("Library", "library"), Crumb("Lighthouse")])
    kinds = _labels(bc)
    assert ("btn", "Library") in kinds       # linked crumb -> button
    assert ("lbl", "Lighthouse") in kinds     # leaf crumb -> plain label
    # clicking the link routes on_navigate with the target
    ch = bc.get_first_child()
    while ch is not None and not (isinstance(ch, Gtk.Button) and ch.get_label() == "Library"):
        ch = ch.get_next_sibling()
    ch.emit("clicked")
    assert nav == ["library"]

def test_context_tray_chip_resume_and_dismiss(monkeypatch):
    import nav_widgets
    monkeypatch.setattr(nav_widgets.GLib, "idle_add", lambda fn, *a: (fn(*a), False)[1])
    ns = NavState()
    resumed, dismissed = [], []
    tray = ContextTray(ns, on_resume=lambda i: resumed.append(i), on_dismiss=lambda i: dismissed.append(i))
    assert tray.get_visible() is False           # empty -> hidden
    ns.open_context(Context("pipeline", "Pipeline 2/5", kind="pipeline", running=True))
    assert tray.get_visible() is True
    # find the resume + close buttons in the single chip
    chip = tray.get_first_child()
    btns = []
    c = chip.get_first_child()
    while c is not None:
        if isinstance(c, Gtk.Button): btns.append(c)
        c = c.get_next_sibling()
    # first button = open/resume (label carries the context label), last = ✕
    btns[0].emit("clicked"); assert resumed == ["pipeline"]
    btns[-1].emit("clicked"); assert dismissed == ["pipeline"]
```

- [ ] **Step 2: Run — verify fail** (`ModuleNotFoundError: nav_widgets`).
Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_nav_widgets.py -v`

- [ ] **Step 3: Implement `app/nav_widgets.py`:**

```python
"""Breadcrumb + ContextTray — GTK views that render a NavState.

Dumb views: they subscribe to a NavState and rebuild on change (deferred via
GLib.idle_add, so an off-thread context mutation is safe). Every action goes
out through an injected callback; the widgets never mutate NavState or touch
generation. Glyphs live in Python str labels (never CSS).
"""
from __future__ import annotations

from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib  # noqa: E402


def _clear(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


class Breadcrumb(Gtk.Box):
    def __init__(self, nav_state, on_navigate: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("nav-breadcrumb")
        self._ns = nav_state
        self._on_navigate = on_navigate
        nav_state.subscribe(lambda _s: GLib.idle_add(self._render))
        self._render()

    def _render(self) -> bool:
        _clear(self)
        crumbs = self._ns.crumbs()
        for i, cr in enumerate(crumbs):
            if i:
                sep = Gtk.Label(label="›")
                sep.add_css_class("nav-crumb-sep")
                self.append(sep)
            if cr.target:
                b = Gtk.Button(label=cr.label)
                b.add_css_class("nav-crumb-link")
                b.connect("clicked", lambda _b, t=cr.target: self._on_navigate(t))
                self.append(b)
            else:
                l = Gtk.Label(label=cr.label)
                l.add_css_class("nav-crumb-here")
                self.append(l)
        self.set_visible(len(crumbs) > 0)
        return False  # GLib.idle_add: run once


class ContextTray(Gtk.Box):
    def __init__(self, nav_state, on_resume: Callable[[str], None],
                 on_dismiss: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("nav-context-tray")
        self._ns = nav_state
        self._on_resume = on_resume
        self._on_dismiss = on_dismiss
        nav_state.subscribe(lambda _s: GLib.idle_add(self._render))
        self._render()

    def _render(self) -> bool:
        _clear(self)
        ctxs = self._ns.contexts()
        for ctx in ctxs:
            chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            chip.add_css_class("nav-ctx-chip")
            if ctx.running:
                dot = Gtk.Label(label="●")
                dot.add_css_class("nav-ctx-dot")
                chip.append(dot)
            open_btn = Gtk.Button(label=ctx.label)
            open_btn.add_css_class("nav-ctx-open")
            open_btn.connect("clicked", lambda _b, i=ctx.id: self._on_resume(i))
            chip.append(open_btn)
            close_btn = Gtk.Button(label="✕")
            close_btn.add_css_class("nav-ctx-close")
            close_btn.connect("clicked", lambda _b, i=ctx.id: self._on_dismiss(i))
            chip.append(close_btn)
            self.append(chip)
        self.set_visible(len(ctxs) > 0)
        return False
```

- [ ] **Step 4: Run — verify pass.** `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_nav_widgets.py -v`

- [ ] **Step 5: Wire into `main_window.py`.**
  - Import near the ModelStatusService import: `from nav_state import NavState, Crumb`; `from nav_widgets import Breadcrumb, ContextTray`.
  - Construct BEFORE `_build_ui()` (near `self._status_service = ModelStatusService()`): `self._nav_state = NavState()`.
  - Add the guarded helper (method on MainWindow):
    ```python
    def _set_crumbs(self, crumbs) -> None:
        ns = getattr(self, "_nav_state", None)
        if ns is not None:
            ns.set_crumbs(crumbs)
    ```
  - In `_build_ui`, build the tray and mount it in `loop_nav_row` BETWEEN the spacer and Servers (so it sits at the right, before Servers). Replace the two lines
    `loop_nav_row.append(_nav_spacer)` / `loop_nav_row.append(self._servers_control.servers_button)` with:
    ```python
    loop_nav_row.append(_nav_spacer)
    self._context_tray = ContextTray(
        self._nav_state, on_resume=self._on_context_resume, on_dismiss=self._on_context_dismiss
    )
    loop_nav_row.append(self._context_tray)
    loop_nav_row.append(self._servers_control.servers_button)
    ```
  - Build the breadcrumb and mount it as a slim row between the menu bar and `inner_paned` — i.e. right after `root_box.append(self._menu_bar)`:
    ```python
    self._breadcrumb = Breadcrumb(self._nav_state, on_navigate=self._on_crumb_navigate)
    self._breadcrumb_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    self._breadcrumb_bar.add_css_class("nav-breadcrumb-bar")
    self._breadcrumb_bar.append(self._breadcrumb)
    root_box.append(self._breadcrumb_bar)
    ```
  - Add the routing handlers (Task 2 stubs the context ones — Task 3 fills them):
    ```python
    def _on_crumb_navigate(self, target: str) -> None:
        btn = self._loop_nav.get(target) if hasattr(self, "_loop_nav") else None
        if target == "library" and self._loop_nav.get("discover"):
            self._loop_nav["discover"].set_active(True)
        elif target == "create" and self._loop_nav.get("create"):
            self._loop_nav["create"].set_active(True)
        elif target == "pipeline":
            self._show_pipelines()

    def _on_context_resume(self, ctx_id: str) -> None:
        pass  # RN-2 Task 3 wires resume per kind

    def _on_context_dismiss(self, ctx_id: str) -> None:
        self._nav_state.close_context(ctx_id)  # RN-2 Task 3 also stops the activity per kind
    ```
  - Set crumbs on each navigation (guarded via `_set_crumbs`):
    - `_on_loop_nav_create` → `self._set_crumbs([Crumb("✨ Create")])`
    - `_on_loop_nav_discover` → `self._set_crumbs([Crumb("🗂 Library")])`
    - `_show_pipelines` → `self._set_crumbs([Crumb("🧩 Pipelines")])`
    - `_on_open_attractor` (once it actually opens) → `self._set_crumbs([Crumb("🗂 Library", "library"), Crumb("📺 Watch")])`
    - `_on_card_selected(record)` / `_on_artgen_card_selected(media_id)` → `self._set_crumbs([Crumb("🗂 Library", "library"), Crumb(<short title>)])` (use `record.prompt`/derive a short title; keep to ~40 chars).

- [ ] **Step 6: CSS** (ASCII-only, in `main_window` `_CSS`): `.nav-breadcrumb-bar { padding: 4px 12px; }`, `.nav-crumb-link` (button, flat, accent text), `.nav-crumb-here` (muted/bold), `.nav-crumb-sep` (muted), `.nav-context-tray`, `.nav-ctx-chip` (rounded, bordered), `.nav-ctx-dot` (accent, small), `.nav-ctx-open`/`.nav-ctx-close` (flat). Palette tokens only.

- [ ] **Step 7: Regression + build smoke.**
Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_nav_widgets.py tests/test_nav_state.py tests/test_main_window_loop_nav.py tests/test_main_window_shell_layout.py tests/test_main_window_create_view_mount.py -q`
Expected: PASS (the `_set_crumbs` guard means the loop-nav harness — which has no `_nav_state` — is unaffected; if `_on_loop_nav_*` now references `Crumb`, ensure the import is module-level so the harness sees it).

- [ ] **Step 8: Version + changelog + commit.** `VERSION` → `0.59.0` (first visible RN-2 slice). Changelog: "feat(nav): 'you are here' breadcrumb + live-context tray (RN-2)". Commit code + tests + VERSION + changelog + plan.

### Verification (Task 2)
- Widget/state/loop-nav/shell suites green.
- Live (`./tt-gen` + `shot.sh`): a breadcrumb bar under the menu shows the current place (Create / Library / Pipelines / Library › Watch / Library › <item>), and clicking an upstream crumb navigates back. The context tray is empty (hidden) until Task 3 registers pipeline/remix/watch contexts.

### Task 3: register pipeline + watch contexts (leave-and-return)

Scope: register the two concrete resumable contexts (a **pipeline** session, a
**watch** session) so the tray becomes usable — leave a pipeline for the
Library and click the chip to return; open Watch, leave, return. Wire the Task-2
resume/dismiss stubs. (The Pipelines double-click fix is a SEPARATE follow-up —
`_show_pipelines` is idempotent, so resume-via-chip works regardless; the radio
change that fixes the toggle double-click is deferred to keep this task low-risk
and test-verifiable, since it can't be visually confirmed here.)

**Files:**
- Modify: `app/main_window.py`
- Test: `tests/test_nav_contexts.py`

**Interfaces:** consumes Task 1 `Context` (add to the existing `from nav_state import NavState, Crumb` → `, Context`). Produces guarded `_nav_open_context(ctx)` / `_nav_close_context(id)` helpers; fleshed-out `_on_context_resume`/`_on_context_dismiss`.

- [ ] **Step 1: Failing test** (`tests/test_nav_contexts.py`) — `__new__` harness (like `test_main_window_loop_nav.py`), binds the methods under test, fakes `_show_pipelines`/`_on_open_attractor`/`_attractor_win`/`_gallery_stack`/`_loop_nav`:

```python
# header: sys.path + GTK-probe/skip like tests/test_main_window_loop_nav.py
from unittest.mock import MagicMock, patch
import main_window as mw
from nav_state import NavState, Context

def _mw():
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)
    obj._nav_state = NavState()
    for name in ("_nav_open_context", "_nav_close_context",
                 "_on_context_resume", "_on_context_dismiss"):
        setattr(obj, name, getattr(mw.MainWindow, name).__get__(obj))
    obj._show_pipelines = MagicMock()
    obj._on_open_attractor = MagicMock()
    obj._attractor_win = None
    obj._gallery_stack = MagicMock()
    obj._gallery_stack.get_visible_child_name.return_value = "pipelines"
    obj._loop_nav = {"discover": MagicMock()}
    return obj

def test_resume_pipeline_shows_pipelines():
    obj = _mw()
    obj._on_context_resume("pipeline")
    obj._show_pipelines.assert_called_once()

def test_resume_watch_opens_attractor():
    obj = _mw()
    obj._on_context_resume("watch")
    obj._on_open_attractor.assert_called_once()

def test_dismiss_pipeline_leaves_to_library_and_closes_context():
    obj = _mw()
    obj._nav_state.open_context(Context("pipeline", "Pipeline", kind="pipeline"))
    obj._on_context_dismiss("pipeline")
    obj._loop_nav["discover"].set_active.assert_called_once_with(True)   # go to Library
    assert not obj._nav_state.has_context("pipeline")

def test_dismiss_watch_closes_the_window():
    obj = _mw()
    obj._nav_state.open_context(Context("watch", "Watch", kind="watch", running=True))
    win = MagicMock(); obj._attractor_win = win
    obj._on_context_dismiss("watch")
    win.close.assert_called_once()   # closing the window triggers _on_attractor_closed -> close_context

def test_nav_helpers_noop_without_nav_state():
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)
    for n in ("_nav_open_context", "_nav_close_context"):
        setattr(obj, n, getattr(mw.MainWindow, n).__get__(obj))
    obj._nav_open_context(Context("x", "x"))   # no _nav_state -> no crash
    obj._nav_close_context("x")
```

- [ ] **Step 2: Run — verify fail.** `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_nav_contexts.py -v`

- [ ] **Step 3: Implement in `main_window.py`.**
  - Import: change `from nav_state import NavState, Crumb` → `from nav_state import NavState, Crumb, Context`.
  - Guarded helpers (near `_set_crumbs`):
    ```python
    def _nav_open_context(self, ctx) -> None:
        ns = getattr(self, "_nav_state", None)
        if ns is not None:
            ns.open_context(ctx)

    def _nav_close_context(self, ctx_id: str) -> None:
        ns = getattr(self, "_nav_state", None)
        if ns is not None:
            ns.close_context(ctx_id)
    ```
  - In `_show_pipelines` (after it switches to the pipelines page): `self._nav_open_context(Context("pipeline", "🧩 Pipeline", kind="pipeline"))`.
  - In `_on_open_attractor`, in the branch that CREATES the window (right after `self._attractor_win = win`): `self._nav_open_context(Context("watch", "📺 Watch", kind="watch", running=True))`. (The early `if self._attractor_win is not None: present(); return` branch — a resume — does NOT re-open the context; it already exists.)
  - In `_on_attractor_closed` (right after `self._attractor_win = None`): `self._nav_close_context("watch")`.
  - Replace the Task-2 stubs:
    ```python
    def _on_context_resume(self, ctx_id: str) -> None:
        if ctx_id == "pipeline":
            self._show_pipelines()
        elif ctx_id == "watch":
            self._on_open_attractor()   # presents the existing kiosk window

    def _on_context_dismiss(self, ctx_id: str) -> None:
        if ctx_id == "watch":
            win = getattr(self, "_attractor_win", None)
            if win is not None:
                win.close()  # -> _on_attractor_closed -> _nav_close_context("watch")
                return
        if ctx_id == "pipeline":
            # leaving the pipeline session entirely -> back to Library
            gs = getattr(self, "_gallery_stack", None)
            if gs is not None and gs.get_visible_child_name() == "pipelines":
                self._loop_nav["discover"].set_active(True)
        self._nav_close_context(ctx_id)
    ```

- [ ] **Step 4: Run — verify pass.** `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_nav_contexts.py -v`

- [ ] **Step 5: Regression.** `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_nav_contexts.py tests/test_nav_state.py tests/test_nav_widgets.py tests/test_main_window_loop_nav.py tests/test_main_window_attractor_model_source.py -q` → PASS. (The `_nav_open_context` guard keeps the loop-nav/attractor harnesses — which don't set `_nav_state` — working.)

- [ ] **Step 6: Version + changelog + commit.** `VERSION` → `0.60.0`. Changelog: "feat(nav): resumable pipeline + watch contexts in the tray (leave-and-return) (RN-2 t3)". Commit code + test + VERSION + changelog + plan.

### Verification (Task 3)
- Context suites green.
- Live (`./tt-gen` + `shot.sh`): open Pipelines → a "🧩 Pipeline" chip appears in the tray; go to Library → chip persists; click it → back in Pipelines. Open Watch (▶ Play) → a "📺 Watch ●" chip appears; close the kiosk → chip disappears; while open, its ✕ closes the kiosk. (The Pipelines-button double-click fix is a tracked follow-up.)
