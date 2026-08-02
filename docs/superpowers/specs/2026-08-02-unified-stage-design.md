# Unified Stage — design spec (v3)

**Status:** approved direction (mockup iterated live: `~/ttlg_unified_stage.html`,
v1→v3). Decomposed into sequenced sub-projects; each ships independently and
leaves the app working.

**Lineage:** continues `2026-07-13-create-surface-design.md` (the Create /
Curate-Discover / Remix loop nav) and the SP-1..SP-3d "coherent shell" program
(ModelStatusService, retiring ControlPanel/ArtgenPanel). This spec is the next
arc: make the shell feel like ONE creative instrument the person conducts.

---

## The thesis

The person is the **conductor** of an AI-driven instrument — not a spectator who
taps a tile and watches a trophy appear. Three principles, learned from the live
mockup review:

1. **One viewport, always the biggest thing on screen.** Your work fills the full
   width — a grid when browsing, one piece filling that same space when you focus
   it. **Browse is never confined to a sidebar.** No `left-controls / right-stage`
   split, no per-region toggles that each govern only half the window.
2. **The loop is the spine:** **Create → Curate/Discover → Watch → Remix ↺** — do
   it all again. All four verbs are legible and always inviting, and the loop
   **closes even from empty**: Watch (TT-TV) is inspiration on tap with zero saved
   media, and every watched piece is one tap from Remix → Create.
3. **Possibilities are always present.** Opening to nothing must not be a blank
   grid + a lone prompt + an intimidating gear. Creative options live up front —
   a "Start something" wall of per-medium exemplars ("make one like this") — whether
   you have 0 pieces or 600. Advanced controls **summon** on demand; they never
   squat on half the screen.

Corollaries: every result is a **launchpad** (a "From here" console: adjust &
re-run, make variations, remix, send to a pipeline), not a dead end. Copy is
placeholder throughout — do not over-invest in wording.

## Non-goals / constraints

- **Display/orchestration only.** Generation paths and `CreateView._collect_params()`
  stay byte-for-byte (guarded by existing CTA/collect tests). The redesign
  re-houses and re-composes widgets; it does not change what generation consumes.
- **Palette:** tt-vscode-toolkit variant (`#4FD1C5` / `#0F2A35`) — the editor
  surface, not the docs-site forest-teal. (See memory `reference_palette_split`.)
- **GTK discipline:** off-thread → `GLib.idle_add`; gif timers on the main thread,
  cancelled on unrealize; `_CSS` `b"""..."""` byte literals stay ASCII-only (glyphs
  in Python string labels only).
- **Crash-safety:** never destroy a widget synchronously inside its own
  signal/event-controller dispatch (the documented use-after-free class behind the
  gallery-gif and TT-TV-Escape crashes) — defer with `GLib.idle_add`. The focus
  view must be a **sibling subtree**, never an ancestor, of the grid it overlays.
- System `/usr/bin/python3`; tests via `xvfb-run --auto-servernum`. Bump `VERSION`
  + prepend `debian/changelog` per sub-project. Local commits only; do not push.

---

## Sub-project sequence

Ordered by dependency and risk. Each is its own spec-slice → plan → SDD cycle.
The app stays shippable after every one.

### SP-1 — The four-verb loop nav (spine) — *safe, first*
Reframe the top nav row into the legible cycle **✨ Create → 🔭 Discover → 📺 Watch
→ 🔀 Remix ↺**, with arrow glyphs between and a ↺ that says "go again"; **🧩
Pipelines set apart** (a divider, styled as the advanced tool); Servers ▾ pinned
right (already done). Buttons render as separate pills, not a joined segmented
control. Behaviors unchanged: Create/Discover switch `_gallery_stack`; Watch opens
the attractor; Remix opens the Muse; Pipelines toggles the studio. No layout-
internals surgery. **Deliverable the user sees:** the loop reads as a loop.

### SP-2 — "Start something" possibilities wall + empty-state — *self-contained, high-vibe*
Give the **Create surface a possibilities wall**: a full-width, wrapping grid of
per-medium exemplar cards (live sample thumbnail + medium name + example idea +
▶ Start), plus a **✨ Surprise me**, and a loop hint. Tapping a card seeds the
existing composer (selects the medium chip + fills `_prompt_entry`, reusing
`_select_medium`/`_activate_model_card` plumbing) — never bypassing `collect()`.
This is also the **empty-state**: a fresh install shows the wall, not a blank
form. Watch stays reachable as the zero-media escape hatch. Lives inside
`CreateView` (or a new `possibilities.py` widget mounted in the Create surface);
does NOT restructure `inner_paned`.

### SP-3 — Full-width viewport + focus-in-place — *structural, highest risk*
Retire the browsing-time `left-gallery / right-detail` Paned split. The gallery
grid fills the full width; focusing a card renders that piece **in place**
(filling the viewport) with a "From here" console + a filmstrip to step
neighbours — reusing the existing `DetailPanel`/`ArtgenDetail` renderers, moved
from the side pane into a focus overlay that is a **sibling** of the grid. Feed a
unified grid option from `media_store.query()` (true cross-type, newest-first).
This is the fragile one (gallery crash history) — its own careful plan, verify the
crash repros.

### SP-4 — Persistent composer bar — *moderate*
Extract a **persistent bottom composer** (prompt + ✨ Inspire + medium picker +
⚙ Advanced + ✨ Create) present across surfaces, feeding the same generation path
(`_collect_params()` unchanged). Demote the gear to a quiet "Advanced" affordance.

### SP-5 — Summoned controls dock — *moderate*
Advanced params/direction as a right **slide-over** (scrim + Escape/✕ dismiss),
summoned from the composer's "⚙ Advanced" — never a permanent half-screen. Reuses
`RoleZonePanel`/`ArtgenParamPanel` (built once, re-parented — the established
pattern), so `collect()` is unchanged.

### SP-6 — Watch embedded + "From here" console wiring + loop close — *higher*
Embed the attractor's render/advance machinery as an in-viewport **Watch surface**
(the attractor content is already decoupled from its Window chrome via constructor
callbacks — lift `media_area` + slot/advance out of the top-level Window), with a
watch-bar (Remix this / Make my own / ★ Save / ⏭ Next). Wire Watch → Remix →
Create so the loop closes on-surface. Finalize the console actions on focus.

---

## Testing posture per sub-project

Each sub-project: TDD, `xvfb-run` widget tests for the new/changed widgets;
regression tests pinning `collect()`/`_collect_params()` equality wherever a
composer/panel is re-housed; live-display smoke on `./tt-gen` for the interaction
(and, for SP-3/SP-6, re-run the gallery-gif and TT-TV-Escape crash repros).
Deselect the two known environment flakes
(`test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`,
`test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`).
