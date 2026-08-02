# SP-1: Four-Verb Loop Nav (spine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reframe the top navigation into the legible creative loop **✨ Create → 🔭 Discover → 📺 Watch → 🔀 Remix ↺**, with 🧩 Pipelines set apart as the advanced tool and Servers ▾ pinned right.

**Architecture:** `_build_loop_nav()` (app/main_window.py) builds the whole loop — the three radio verbs PLUS the Watch action button that used to be appended later in `_build_ui`, interleaved with arrow-glyph labels and a trailing ↺. `_build_ui` drops its now-duplicate Watch block and inserts a visual divider before Pipelines. CSS renders the verbs as separate pills (not a joined segmented control) and styles the arrows/loop/divider.

**Tech Stack:** Python 3 (system `/usr/bin/python3`), GTK4 / PyGObject, pytest under `xvfb-run`.

## Global Constraints

- **Behavior-preserving:** Create/Discover still switch `_gallery_stack`; Watch still calls `_on_open_attractor`; Remix still routes to the Muse; Pipelines still toggles the studio. No handler logic changes.
- `_loop_nav` keeps exactly the keys `{"create","discover","remix"}` and `_loop_nav_create_btn` keeps pointing at the Create button (other code + `__init__`'s default-active line depend on these).
- `self._attractor_btn` remains the same attribute name, same `_on_open_attractor` click handler, same initial `set_sensitive(False)` (later flipped by `_update_attractor_btn`).
- `_CSS` is a `b"""..."""` byte literal — **ASCII only**; all emoji/glyphs live in Python `str` labels, never in CSS.
- Palette tokens only (`@tt_accent`, `@tt_bg_dark`, `@tt_text_muted`, `@tt_border`, …) — no hardcoded hex.
- System `/usr/bin/python3`; run tests with `xvfb-run --auto-servernum /usr/bin/python3 -m pytest`. Local commits only; do not push. Bump `VERSION` + prepend `debian/changelog`.

---

### Task 1: Rebuild the loop nav into the four-verb cycle

**Files:**
- Modify: `app/main_window.py` — `_build_loop_nav()` (currently ~6671-6727), the Watch/Pipelines block in `_build_ui()` (currently ~5462-5498), and the `.loop-nav-*` CSS (currently ~359-403).
- Modify (test harness + assertions): `tests/test_main_window_loop_nav.py` (~43-119).

**Interfaces:**
- Consumes: existing `self._on_open_attractor`, `self._on_loop_nav_create/_discover/_remix`, `self._servers_control.servers_button`, `self._pipelines_btn` wiring, `self._update_attractor_btn` (reads `self._attractor_btn`).
- Produces: unchanged public surface — `self._loop_nav` (dict, keys create/discover/remix), `self._loop_nav_create_btn`, `self._attractor_btn`, `self._pipelines_btn`. New row child order: `[Create, →, Discover, →, Watch, →, Remix, ↺, divider, Pipelines, spacer, Servers]`.

- [ ] **Step 1: Update the isolated-test harness so `_build_loop_nav` can build**

The harness builds a bare MainWindow and binds `_build_loop_nav` directly. The new `_build_loop_nav` references `self._on_open_attractor` (to connect the Watch button), so the harness must provide it. In `tests/test_main_window_loop_nav.py`, inside `_make_mw` (after line 104 `obj._rebuild_context_menu = MagicMock()`), add:

```python
    obj._on_open_attractor = MagicMock()
```

- [ ] **Step 2: Write the failing structure test**

Replace `test_build_loop_nav_exposes_keyed_buttons` (lines 110-119) with a version that also asserts the new loop members and ordering:

```python
def test_build_loop_nav_exposes_keyed_buttons(tmp_path, monkeypatch):
    """_build_loop_nav returns the loop row: the three keyed verbs plus the
    Watch action button, in create -> discover -> watch -> remix order."""
    obj = _make_mw(tmp_path, monkeypatch)

    row = obj._build_loop_nav()

    assert isinstance(row, Gtk.Widget)
    assert set(obj._loop_nav.keys()) == {"create", "discover", "remix"}
    for btn in obj._loop_nav.values():
        assert isinstance(btn, Gtk.ToggleButton)
    # Watch is a plain action button built here now (not in _build_ui).
    assert isinstance(obj._attractor_btn, Gtk.Button)
    assert not obj._attractor_btn.get_sensitive()  # starts disabled

    # The four verbs appear in loop order among the row's children.
    labels = []
    child = row.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            labels.append(child.get_label())
        child = child.get_next_sibling()
    assert labels == ["✨ Create", "🔭 Discover", "📺 Watch", "🔀 Remix"]
```

- [ ] **Step 3: Run it — verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_main_window_loop_nav.py::test_build_loop_nav_exposes_keyed_buttons -v`
Expected: FAIL — old `_build_loop_nav` neither builds `self._attractor_btn` nor puts a "📺 Watch" button in the row (label is "📺 Watch TT-TV" and only built in `_build_ui`).

- [ ] **Step 4: Rewrite `_build_loop_nav()`**

Replace the body of `_build_loop_nav` (lines 6683-6727, i.e. everything after the docstring) with:

```python
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.add_css_class("loop-nav-row")

        def _sep(glyph: str = "→", *, loop: bool = False) -> Gtk.Label:
            # → = "->", ↺ = the "go again" loop glyph. Kept as escapes
            # so this stays copy-safe; they are Python str labels, never CSS.
            lbl = Gtk.Label(label=glyph)
            lbl.add_css_class("loop-nav-loop" if loop else "loop-nav-arrow")
            return lbl

        create_btn = Gtk.ToggleButton(label="✨ Create")
        create_btn.add_css_class("loop-nav-btn")
        create_btn.set_tooltip_text(
            "Create — start something new: pick a medium or type an idea."
        )

        # Discover absorbs Curate: browsing and collecting are one act — you
        # star, build playlists, and thread things together AS you find them.
        discover_btn = Gtk.ToggleButton(label="\U0001f52d Discover")
        discover_btn.add_css_class("loop-nav-btn")
        discover_btn.set_tooltip_text(
            "Discover & curate — browse what you've made: star it, add it to "
            "playlists, thread it together (individual artifacts or whole projects)."
        )

        # Watch is an ACTION (opens the TT-TV kiosk window), not a surface radio,
        # but it sits in the loop between Discover and Remix so the cycle reads
        # left to right. Built HERE now (moved out of _build_ui) so it can be
        # interleaved in loop order. Same attribute/handler/initial-state as before.
        self._attractor_btn = Gtk.Button(label="\U0001f4fa Watch")
        self._attractor_btn.add_css_class("loop-nav-btn")
        self._attractor_btn.add_css_class("loop-nav-action")
        self._attractor_btn.set_tooltip_text(
            "Watch TT-TV — a living kiosk stream of your media that also keeps\n"
            "generating new content; remix anything you see."
        )
        self._attractor_btn.set_sensitive(False)
        self._attractor_btn.connect("clicked", self._on_open_attractor)

        remix_btn = Gtk.ToggleButton(label="\U0001f500 Remix")
        remix_btn.add_css_class("loop-nav-btn")
        remix_btn.set_tooltip_text(
            "Remix — the Muse: turn anything into a new pipeline, and go again."
        )

        discover_btn.set_group(create_btn)
        remix_btn.set_group(create_btn)

        create_btn.connect("toggled", lambda b: b.get_active() and self._on_loop_nav_create())
        discover_btn.connect("toggled", lambda b: b.get_active() and self._on_loop_nav_discover())
        remix_btn.connect("toggled", lambda b: b.get_active() and self._on_loop_nav_remix())

        row.append(create_btn)
        row.append(_sep())
        row.append(discover_btn)
        row.append(_sep())
        row.append(self._attractor_btn)
        row.append(_sep())
        row.append(remix_btn)
        row.append(_sep("↺", loop=True))

        # Keyed lookup for tests and for __init__'s default-active line.
        self._loop_nav = {
            "create": create_btn,
            "discover": discover_btn,
            "remix": remix_btn,
        }
        self._loop_nav_create_btn = create_btn
        return row
```

- [ ] **Step 5: Remove the duplicate Watch block from `_build_ui` and add the divider**

In `_build_ui`, delete the old Watch construction (current lines 5462-5470, from `self._attractor_btn = Gtk.Button(label="📺 Watch TT-TV")` through `loop_nav_row.append(self._attractor_btn)`). In its place — right before the `# ── Pipelines nav entry ──` comment — insert a divider so Pipelines reads as set-apart:

```python
        # Build and register menu actions before creating the bar
        self._build_menu_actions()

        # ── Pipelines: set apart from the four-verb loop as the advanced tool ──
        _loop_div = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        _loop_div.add_css_class("loop-nav-divider")
        loop_nav_row.append(_loop_div)
```

Leave the existing Pipelines block (`self._pipelines_btn = ...` through `loop_nav_row.append(self._pipelines_btn)`) and the spacer + `servers_button` appends exactly as they are — they already come after this point.

- [ ] **Step 6: Update the CSS — separate pills + arrow/loop/divider styling**

In the `_CSS` byte literal: change `.loop-nav-btn` `border-radius` from `0` to `6px`; delete the now-unused positional blocks `.loop-nav-btn-left` / `.loop-nav-btn-mid` / `.loop-nav-btn-right` (current lines 382-392); and append the new classes. Resulting region (replace lines 364-392):

```
.loop-nav-btn {
    background-color: @tt_bg_dark;
    color: @tt_text_muted;
    border: 1px solid @tt_border;
    border-radius: 6px;
    padding: 6px 18px;
    font-size: 13px;
    font-weight: bold;
    min-height: 0;
}
.loop-nav-btn label {
    padding: 0;
    margin: 0;
}
.loop-nav-btn:hover {
    background-color: @tt_border;
    color: @tt_text;
}
.loop-nav-arrow {
    color: @tt_text_muted;
    padding: 0 5px;
    font-size: 13px;
}
.loop-nav-loop {
    color: @tt_accent;
    padding: 0 10px 0 5px;
    font-size: 15px;
    font-weight: bold;
}
.loop-nav-divider {
    margin: 4px 12px;
}
```

Keep the existing `.loop-nav-btn-active, .loop-nav-btn:checked { ... }` and its `:hover` block (current lines 393-403) unchanged — the active/checked styling still applies to the radio verbs.

- [ ] **Step 7: Run the structure test — verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_main_window_loop_nav.py::test_build_loop_nav_exposes_keyed_buttons -v`
Expected: PASS.

- [ ] **Step 8: Run the whole loop-nav suite — verify no regression**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_main_window_loop_nav.py -v`
Expected: PASS (radio mutual-exclusion, default-active, and route tests still green — handlers were not touched). If any test asserted an old positional CSS class or the old "Watch TT-TV" label, update the assertion to the new structure (loop order / "📺 Watch"); do NOT change handler behavior to satisfy a test.

- [ ] **Step 9: Full-window construction smoke — verify `_build_ui` still assembles**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_main_window_shell_layout.py tests/test_main_window_create_view_mount.py tests/test_right_stack_dual_renderer.py -v`
Expected: PASS — confirms moving `_attractor_btn` construction into `_build_loop_nav` (earlier in `_build_ui`) and adding the divider didn't break window assembly or the attractor-enable path (`_update_attractor_btn` still finds `self._attractor_btn`).

- [ ] **Step 10: Bump version + changelog**

Set `VERSION` to `0.52.0` (minor — user-visible nav change). Prepend a `debian/changelog` stanza:

```
tt-local-generator (0.52.0) noble; urgency=medium

  * feat(nav): reframe the top nav into the four-verb creative loop
    (Create -> Discover -> Watch -> Remix, go again) with the Watch action
    folded into loop order, Pipelines set apart as the advanced tool, and
    the verbs rendered as separate pills. Behavior unchanged. (SP-1 of the
    Unified Stage redesign, docs/superpowers/specs/2026-08-02-unified-stage-design.md)

 -- Taylor Singletary <tsingletary@tenstorrent.com>  <DATE>
```

Use the real RFC-2822 date (`date -R`).

- [ ] **Step 11: Commit**

```bash
git add app/main_window.py tests/test_main_window_loop_nav.py VERSION debian/changelog \
        docs/superpowers/specs/2026-08-02-unified-stage-design.md \
        docs/superpowers/plans/2026-08-02-sp1-loop-nav-spine.md
git commit -m "feat(nav): four-verb creative loop nav (SP-1 of Unified Stage)"
```

### Finalize

Run the fast slice of the suite once more; deselect the two known flakes:
`xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module --deselect tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`

## Verification

- Loop-nav suite + shell-layout/create-mount/right-stack suites green under xvfb.
- Live (`./tt-gen`): the top bar reads **✨ Create → 🔭 Discover → 📺 Watch → 🔀 Remix ↺**, then a divider, then **🧩 Pipelines**, with **Servers ▾** at the far right. Clicking each still does exactly what it did before (Create/Discover switch surfaces, Watch opens the kiosk once media exists, Remix opens the Muse, Pipelines toggles the studio).
