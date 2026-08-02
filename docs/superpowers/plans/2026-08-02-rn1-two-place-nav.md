# RN-1: Revised Nav — Two Places (Create / Library) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the shipped four-verb loop nav with the converged **two-place** model: **✨ Create · 🗂 Library**, a **▶ Play** companion for Watch, no Remix nav button (Remix is a per-item action), Pipelines set apart. No rigid sequence, no arrows.

**Architecture:** Rewrite `_build_loop_nav()` in `app/main_window.py`: radio group shrinks to `{create, discover}`, the Discover pill is relabeled "🗂 Library", the Watch button (`_attractor_btn`) is relabeled "▶ Play" and kept as a Library companion, the Remix pill and all arrow/↺ separators are removed. Behavior of Create/Library/Play/Pipelines is otherwise unchanged.

**Tech Stack:** Python 3 (`/usr/bin/python3`), GTK4/PyGObject, pytest under `xvfb-run`.

## Global Constraints

- **Behavior-preserving except for what's removed:** Create still switches to "create" + hides the detail pane; Library (discover) still switches galleries + shows the detail pane; ▶ Play still calls `_on_open_attractor` and is gated by `_update_attractor_btn`; Pipelines still toggles the studio.
- **Keep the `_on_loop_nav_remix` METHOD** — it is still called by `CreateView`'s "Start with inspiration" door via the `on_inspiration=self._on_loop_nav_remix` seam (constructed in `_build_ui`). Only the nav *button* and its `"toggled"` connection are removed. A blank Muse also stays reachable via the 🧩 Pipelines toggle.
- **Preserve attribute contracts:** `self._loop_nav` (dict) now has keys exactly `{"create","discover"}`; `self._loop_nav_create_btn` still points at Create; `self._attractor_btn` keeps its name, its `_on_open_attractor` click handler, and its initial `set_sensitive(False)` (flipped later by `_update_attractor_btn`, which reads `self._attractor_btn`). `__init__`'s `self._loop_nav_create_btn.set_active(True)` line is unchanged.
- `_CSS` is a `b"""..."""` BYTES literal — ASCII only; glyphs live in Python `str` labels (use `\Uxxxxxxxx`/`\uxxxx` escapes for non-ASCII, matching the existing SP-1 style).
- Palette tokens only in CSS. System `/usr/bin/python3`; tests `xvfb-run --auto-servernum /usr/bin/python3 -m pytest`. Bump `VERSION` + changelog. Local commits only; do not push.

## Reference — current shipped `_build_loop_nav` (post-SP-1, ~line 6697)

Row currently appends, in order: `create_btn`, `_sep()`, `discover_btn`("🔭 Discover"), `_sep()`, `self._attractor_btn`("📺 Watch", classes `loop-nav-btn`+`loop-nav-action`, `set_sensitive(False)`, `connect("clicked", self._on_open_attractor)`), `_sep()`, `remix_btn`("🔀 Remix"), `_sep("↺", loop=True)`. Radio group `{create, discover, remix}`; `self._loop_nav` has those three keys. The `_sep(glyph, loop=)` helper builds arrow/↺ `Gtk.Label`s. CSS classes `.loop-nav-arrow` / `.loop-nav-loop` (main_window `_CSS` ~line 382-392) style those separators; `.loop-nav-action` (~line after `.loop-nav-divider`) tints the Watch button.

---

### Task 1: Rebuild `_build_loop_nav` as two places + ▶ Play

**Files:**
- Modify: `app/main_window.py` — `_build_loop_nav()` body (~6709-6770); remove unused `.loop-nav-arrow`/`.loop-nav-loop` CSS rules (~382-392).
- Modify (tests): `tests/test_main_window_loop_nav.py`; and update any stale label/structure assertions in `tests/test_main_window_shell_layout.py` and `tests/test_main_window_attractor_model_source.py` (SP-1 set these to the old "📺 Watch"/`row.append(self._attractor_btn)` shape).

**Interfaces:**
- Produces: `self._loop_nav` keys `{"create","discover"}`; `self._loop_nav_create_btn`; `self._attractor_btn` (label "▶ Play"). Row child order (buttons only): `["✨ Create", "🗂 Library", "▶ Play"]`. No arrow/↺ labels. `_on_loop_nav_remix` method retained, no nav button.

- [ ] **Step 1: Write the failing structure test.** Replace `test_build_loop_nav_exposes_keyed_buttons` in `tests/test_main_window_loop_nav.py` with:

```python
def test_build_loop_nav_two_places_plus_play(tmp_path, monkeypatch):
    """The nav is two places (Create, Library) plus a ▶ Play companion —
    no Remix pill, no arrow/loop separators."""
    obj = _make_mw(tmp_path, monkeypatch)

    row = obj._build_loop_nav()

    assert isinstance(row, Gtk.Widget)
    assert set(obj._loop_nav.keys()) == {"create", "discover"}
    for btn in obj._loop_nav.values():
        assert isinstance(btn, Gtk.ToggleButton)
    assert isinstance(obj._attractor_btn, Gtk.Button)
    assert not obj._attractor_btn.get_sensitive()

    labels = []
    child = row.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            labels.append(child.get_label())
        child = child.get_next_sibling()
    assert labels == ["✨ Create", "🗂 Library", "▶ Play"]
    # no arrow/loop separator labels remain in the row
    texts = []
    child = row.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            texts.append(child.get_text())
        child = child.get_next_sibling()
    assert "→" not in texts and "↺" not in texts
```

- [ ] **Step 2: Run — verify fail.**
Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_main_window_loop_nav.py::test_build_loop_nav_two_places_plus_play -v`
Expected: FAIL (current nav has 4 buttons incl. "📺 Watch"/"🔀 Remix" and `_loop_nav` has a "remix" key).

- [ ] **Step 3: Rewrite `_build_loop_nav`.** Replace the body after the docstring (currently ~6709-6770) with:

```python
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.add_css_class("loop-nav-row")

        create_btn = Gtk.ToggleButton(label="✨ Create")
        create_btn.add_css_class("loop-nav-btn")
        create_btn.set_tooltip_text(
            "Create — start something new: pick a medium or type an idea."
        )

        # Library absorbs Discover + Curate: browse and collect everything
        # you've made — star it, build playlists, thread it together.
        discover_btn = Gtk.ToggleButton(label="\U0001f5c2 Library")
        discover_btn.add_css_class("loop-nav-btn")
        discover_btn.set_tooltip_text(
            "Library — browse and collect everything you've made: star it, "
            "add it to playlists, thread it together."
        )

        # Watch is a ▶ Play LENS on the Library (opens the TT-TV kiosk for now;
        # an in-app embedded stream comes later). Not a top-level place, not in
        # the radio group — a plain action button sitting beside Library. Same
        # attribute/handler/initial-state as before (relabelled only).
        self._attractor_btn = Gtk.Button(label="▶ Play")
        self._attractor_btn.add_css_class("loop-nav-btn")
        self._attractor_btn.add_css_class("loop-nav-action")
        self._attractor_btn.set_tooltip_text(
            "▶ Play — play your Library as TT-TV (full-screen kiosk); it also\n"
            "keeps generating new content. Remix anything you see."
        )
        self._attractor_btn.set_sensitive(False)
        self._attractor_btn.connect("clicked", self._on_open_attractor)

        discover_btn.set_group(create_btn)

        create_btn.connect("toggled", lambda b: b.get_active() and self._on_loop_nav_create())
        discover_btn.connect("toggled", lambda b: b.get_active() and self._on_loop_nav_discover())

        row.append(create_btn)
        row.append(discover_btn)
        row.append(self._attractor_btn)

        # Keyed lookup for tests and for __init__'s default-active line.
        # Remix is no longer a nav place (it's a per-item action); the
        # `_on_loop_nav_remix` METHOD is retained — CreateView's "Start with
        # inspiration" door still calls it via on_inspiration.
        self._loop_nav = {
            "create": create_btn,
            "discover": discover_btn,
        }
        self._loop_nav_create_btn = create_btn
        return row
```

Delete the now-unused nested `_sep(...)` helper (it lived at the top of the old body). Leave `_on_loop_nav_remix` (the method) untouched elsewhere in the file.

- [ ] **Step 4: Remove the now-unused separator CSS.** In `app/main_window.py` `_CSS`, delete the `.loop-nav-arrow { ... }` and `.loop-nav-loop { ... }` rule blocks (added in SP-1, ~line 382-392). Keep `.loop-nav-btn`, `.loop-nav-action`, `.loop-nav-divider`, and the `.loop-nav-btn:checked` blocks. Verify no remaining `add_css_class("loop-nav-arrow")`/`("loop-nav-loop")` references (grep clean).

- [ ] **Step 5: Run the structure test — verify pass.**
Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_main_window_loop_nav.py::test_build_loop_nav_two_places_plus_play -v` → PASS.

- [ ] **Step 6: Fix the remaining loop-nav tests.** In `tests/test_main_window_loop_nav.py`:
  - `test_loop_nav_buttons_are_mutually_exclusive`: the group is now Create+Library only — remove any `_loop_nav["remix"]` reference; assert activating Library deactivates Create and vice-versa.
  - `test_loop_nav_remix_calls_show_muse` and `test_loop_nav_remix_reuses_pipeline_studio_instance`: there is no Remix nav button now. Rewrite them to call the METHOD directly — replace `obj._loop_nav["remix"].set_active(True)` with `obj._on_loop_nav_remix()` (the harness already binds `_on_loop_nav_remix`). The assertions (gallery shows "pipelines", muse child, `_pipelines_btn` active, single PipelineStudio instance) stay — this keeps coverage of the method CreateView's inspiration door depends on.
  - Any assertion of the old `"🔭 Discover"` / `"📺 Watch"` labels → `"🗂 Library"` / `"▶ Play"`.

- [ ] **Step 7: Fix stale assertions in the other two suites.** Run each, and update ONLY label/structure assertions to the new shape (no behavior change):
  - `tests/test_main_window_shell_layout.py` — SP-1 pointed a source-grep at `"row.append(self._attractor_btn)"` / the `"\U0001f4fa Watch"` label; update to the new label `"▶ Play"` if asserted, and confirm the row-append site still matches (it's still `row.append(self._attractor_btn)`).
  - `tests/test_main_window_attractor_model_source.py` — update any `"📺 Watch"`/`"🔭 Discover"` label assertion.
Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_main_window_loop_nav.py tests/test_main_window_shell_layout.py tests/test_main_window_attractor_model_source.py -v` → all PASS.

- [ ] **Step 8: Full-window construction + Create/Library detail-pane regression.**
Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_main_window_create_view_mount.py tests/test_right_stack_dual_renderer.py tests/test_inspire2_wiring.py -q` → PASS (confirms `_build_ui` still assembles and the CreateView `on_inspiration=_on_loop_nav_remix` seam is intact).

- [ ] **Step 9: Bump version + changelog.** `VERSION` → `0.54.0`. Prepend a `debian/changelog` stanza:

```
tt-local-generator (0.54.0) noble; urgency=medium

  * feat(nav): revise the top nav to the two-place model — ✨ Create · 🗂 Library
    — with a ▶ Play companion (TT-TV) beside Library and Pipelines set apart.
    Drops the four-verb loop (arrows/↺) and the Remix nav button; Remix stays a
    per-item action and CreateView's "Start with inspiration" door still opens
    the Muse. (RN-1 of the Unified-Stage revision,
    docs/superpowers/specs/2026-08-02-unified-stage-design.md)

 -- Taylor Singletary <tsingletary@tenstorrent.com>  <DATE>
```

Use `date -R` for the date.

- [ ] **Step 10: Commit.**
```bash
git add app/main_window.py tests/test_main_window_loop_nav.py \
        tests/test_main_window_shell_layout.py tests/test_main_window_attractor_model_source.py \
        VERSION debian/changelog docs/superpowers/plans/2026-08-02-rn1-two-place-nav.md
git commit -m "feat(nav): two-place nav (Create/Library) + ▶ Play (RN-1 of Unified-Stage revision)"
```

### Finalize

Fast slice green (deselect the two known flakes):
`xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module --deselect tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`

## Verification

- Loop-nav / shell-layout / attractor-source / create-mount suites green under xvfb.
- Live (`./tt-gen`): top bar reads **✨ Create · 🗂 Library · ▶ Play** then a divider then **🧩 Pipelines**, Servers ▾ right. Create → full-width Create; Library → galleries + detail pane; ▶ Play opens TT-TV once media exists; Pipelines toggles the studio; no Remix pill, no arrows. CreateView's "Start with inspiration" door still opens the Muse.
