# Remix without exposing "pipeline mode" — Design

**Date:** 2026-08-19
**Branch:** `feat/pipeline-editor`
**Status:** Draft (design) — pending user review → implementation plan

## Problem / goal

Pipeline authoring isn't ready for primetime, and Taylor doesn't want to keep
iterating on it now — but **Remix is wired to pipelines** and is worth keeping.
Today "pipeline" is a *place* the user can wander into (browse past runs, compose
from a blank canvas, edit a DAG of nodes/models) — all the rough surfaces. The
one polished, ready path is: pick an artifact → 🔀 Remix → choose what to make
→ watch it get made → find it in the Library.

**Goal:** hide the not-ready pipeline surfaces while keeping Remix fully
functional, and make the hide **reversible with a single flag** so pipeline mode
returns unchanged when it's ready — no code deletion, no ongoing iteration.

**Key decision already made (Taylor, 2026-08-19):** keep the **goal chooser**
(the Muse "make this into…" step). Remix stays a two-tap flow (Remix → pick a
goal), not a one-tap canned action.

## The three doors into pipeline mode (today)

Grounded in the current wiring:

1. **🧩 Pipelines nav toggle** (`main_window._pipelines_btn` → `_on_pipelines_toggled`
   → `_show_pipelines`) — mounts `PipelineStudio` and lands on its Discover page
   (browse runs + "start-from-scratch" blank compose).
2. **CreateView's Inspiration door** (`create_view` idea/model/**inspiration**
   doors; `on_inspiration` → `main_window._on_loop_nav_remix` →
   `show_muse(seed_artifact=None)`) — a blank-canvas compose entry on the Create
   surface.
3. **Per-item 🔀 Remix** (`_remix_as_pipeline` on every card/detail →
   `_show_pipelines()` then `show_muse(seed_artifact=…)`) → user picks a goal →
   `_on_muse_goal_chosen` → **RemixView DAG editor** → Run (Stage making-of) →
   result registered to the Library (`_register_pipeline_final`).

The ready parts: the seeded Muse goal chooser, the Stage making-of, and
Library registration. The not-ready parts: the studio-as-place (Discover
browser), blank-canvas compose (doors #1 and #2), and the **RemixView DAG
editor** (the middle of door #3).

## Scope

**In:**
- A single feature flag, **OFF by default**, that hides pipeline mode. When ON,
  today's full behavior is byte-for-byte unchanged.
- With the flag OFF:
  - Door #1: the 🧩 Pipelines nav toggle is not shown.
  - Door #2: CreateView's Inspiration door is not shown (Create has two doors:
    Idea / Model).
  - Door #3 reshaped: 🔀 Remix opens the seeded Muse goal chooser →
    **straight to the run** (Stage), skipping the DAG editor → result lands in
    the Library → leaving the run returns to the **app Library**, never the
    studio's own Discover.
- A light **wording pass** so the user-facing Remix flow (goal chooser + Stage)
  reads as "make this into… / making your remix…", not "pipeline / recipe / DAG
  / step" — *only* on the surfaces still reachable when the flag is OFF.

**Out (explicitly not this pass):**
- Any change to the pipeline engine, run spec, `collect()`, or the Stage
  making-of's internals (Slice 1 stays as shipped — this only changes what's
  *reachable* and how the seeded-remix flow is *routed*).
- Deleting any pipeline code. Everything stays in the tree behind the flag.
- Improving the DAG editor / Discover / blank compose (that's the deferred
  "make pipeline mode ready" work — the whole point is to stop iterating on it).
- Re-theming the Stage or changing its progress behavior.

## Design

### A. One feature flag (the reversibility guarantee)
`app_settings.PIPELINE_MODE_ENABLED: bool` (default **False**), overridable by
env var `TTLG_PIPELINE_MODE` (`"1"/"true"` → True). One import, read at UI
build time. Every hide below is a branch on this flag; ON restores today's UI
exactly. (Placing it in `app_settings` matches where other runtime settings
live; a pure module-level constant + env read keeps it GTK-free and testable.)

### B. Door #1 — the 🧩 Pipelines toggle (main_window)
In `_build_loop_nav` / the loop-nav assembly, only build+append `_pipelines_btn`
(and its divider) when `PIPELINE_MODE_ENABLED`. When OFF, `_pipelines_btn` is
`None`; every existing reference already uses `getattr(self, "_pipelines_btn",
None)` guards (e.g. `_remix_as_pipeline`, `_uncheck_pipelines_toggle_if_active`),
so a `None` toggle is already tolerated — audit and confirm each guard.

### C. Door #2 — CreateView's Inspiration door
`CreateView` is told whether to show the inspiration door. Cleanest: pass an
existing seam — when `main_window` constructs `CreateView`, it already injects
`on_inspiration`. Gate it: when the flag is OFF, pass `on_inspiration=None`, and
have `CreateView` omit the Inspiration door when `on_inspiration is None`
(the doors row shows Idea / Model only). This keeps the flag knowledge in
`main_window` and needs only a "hide the door if no seam" branch in
`create_view` — no new CreateView flag. `collect()` is untouched (the door is
navigation, not a value-bearing widget).

### D. Door #3 — reshape the seeded Remix flow (main_window + pipeline_studio)
The seeded Remix path must reach the goal chooser and the run WITHOUT exposing
the studio's Discover page or the DAG editor.

- **Entry (`main_window._remix_as_pipeline`):** when the flag is OFF, do NOT call
  `_show_pipelines()` (which lands on the studio's Discover). Instead mount the
  studio lazily (extract the lazy-construct half of `_show_pipelines` into a
  helper, e.g. `_ensure_pipeline_studio()`), switch `_gallery_stack` to the
  `"pipelines"` child, and call a scoped entry that goes straight to the Muse:
  `show_muse(seed_artifact=…)` with no Discover stop. When ON, the current
  `_show_pipelines()` + `show_muse` path is unchanged.
- **Goal → run, skipping the editor (`pipeline_studio._on_muse_goal_chosen`):**
  when pipeline mode is OFF, after `write_spec(...)` launch the run directly
  instead of `remix_view.load_seed_spec(...)` + showing the `"remix"` page.
  Factor the runner-start body out of `_on_run_remix` into a shared
  `_launch_run(spec_path, edits)` (today `_on_run_remix` reads
  `remix_view.current_spec()` + applies edits; the muse path passes the freshly
  written seed `spec_path` with **no edits** — a brand-new seed has none). Show
  the `"run"` page (Stage) as today. When ON, keep muse → `"remix"` (editor) →
  run.
  - `PipelineStudio` needs to know the flag. Pass it in the constructor
    (`PipelineStudio(pipeline_mode_enabled=…)`, default True so existing
    tests/standalone keep today's behavior) rather than importing app_settings
    inside the widget — keeps the widget config-injectable and unit-testable.
- **Exit → app Library, not studio Discover:** when the flag is OFF, the run
  page's Back (`LiveRunView` → `PipelineStudio._on_run_back`, today → studio
  `"discover"`) must instead leave the studio entirely and return to the main
  app Library. Add an `on_leave: Callable[[], None] | None` seam to
  `PipelineStudio`; `_on_run_back` calls it when set (flag OFF) instead of
  switching to the studio's Discover. `main_window` wires `on_leave` to hide the
  pipelines page (switch `_gallery_stack` back to the Library gallery, mirroring
  `_hide_pipelines`) and re-activate the Library nav verb. When the flag is ON
  (`on_leave=None`), `_on_run_back` keeps today's → `"discover"` behavior.
  - The result is already registered to the Library on run-done
    (`_register_pipeline_final`, unchanged), so "leave" lands the user where
    their new artifact already is.

### E. Wording pass (flag-OFF-reachable surfaces only)
Light, copy-only: the seeded Muse goal chooser and the Stage should not surface
"pipeline / recipe / DAG / step N" as the primary language — favor "Make this
into… / Making your remix… / phase". Scope strictly to strings the user sees
when the flag is OFF (the goal chooser + the Stage header/labels). No structural
change; `_CSS` byte literals stay ASCII-only; glyphs stay in Python str labels.
(If a label is shared with a flag-ON-only surface, leave it — don't fork copy
for a hidden surface.)

## Reuse map (this is hiding + rerouting, not new power)
- `app_settings` — new `PIPELINE_MODE_ENABLED` constant + env read.
- `main_window._build_loop_nav` / loop-nav assembly — gate `_pipelines_btn`.
- `main_window` CreateView construction — pass `on_inspiration=None` when OFF.
- `main_window._show_pipelines` — split into `_ensure_pipeline_studio()` +
  the show/land behavior; add a scoped "go straight to muse" entry for remix.
- `main_window._remix_as_pipeline` — branch on the flag for the scoped entry.
- `main_window` — an `on_leave` handler that returns to the Library (mirrors
  `_hide_pipelines`).
- `pipeline_studio.PipelineStudio` — `pipeline_mode_enabled` + `on_leave`
  constructor seams; `_on_muse_goal_chosen` branch; `_launch_run` extracted from
  `_on_run_remix`; `_on_run_back` branch.
- `create_view.CreateView` — omit the Inspiration door when `on_inspiration is
  None`.
- Everything else (engine, Stage internals, Library registration, Muse goal
  resolution `recipes.goals_for`) — untouched.

## Testing (pure/GTK-optional where possible)
- **Flag default:** `PIPELINE_MODE_ENABLED` is False by default; env
  `TTLG_PIPELINE_MODE=1` flips it (pure unit test of the read).
- **Doors hidden (flag OFF):** `main_window` builds with no `_pipelines_btn`
  (or it's `None`); CreateView built with `on_inspiration=None` shows only
  Idea/Model doors (widget test).
- **Doors present (flag ON):** regression — the toggle + Inspiration door exist,
  exactly as today (guard against accidentally breaking the ON path).
- **Remix routing (flag OFF):** `_on_muse_goal_chosen` launches the run and
  shows the `"run"` page WITHOUT visiting `"remix"`; `_on_run_back` invokes
  `on_leave` (assert the callback fires) instead of switching to `"discover"`.
  `_launch_run` is called with the seed spec and empty edits.
- **Remix routing (flag ON):** regression — muse → `"remix"` → run, `_on_run_back`
  → `"discover"`, unchanged.
- **`_launch_run` equivalence:** the extracted helper produces the same runner
  start for the `_on_run_remix` path as before (no behavior drift for flag-ON).
- **collect()/run-spec untouched:** the engine and Create's `collect()` are not
  in scope; assert nothing here changes them.
- Update any existing tests that assert the Pipelines toggle / Inspiration door
  always exist to be flag-aware (they should assert per-flag behavior).

## Hard constraints
- **Reversible by one flag.** No pipeline code is deleted; `TTLG_PIPELINE_MODE=1`
  (or flipping the default) restores today's UI unchanged. This is the whole
  point — stop iterating now, resume later by flipping a flag.
- **Engine/spec/Stage untouched.** Slice-1 Stage making-of, the pipeline engine,
  the run spec, and `collect()` are not modified — only reachability and the
  seeded-remix routing change.
- **Palette:** app's main scheme `#4FD1C5`/`#0F2A35`; `_CSS` byte literals
  ASCII-only; glyphs in Python str labels.
- **GTK single-threaded**; no new threads — this is nav/routing + copy.
- **Fail-soft:** a flag-OFF remix that can't resolve a goal still degrades the
  same way it does today (blank/again), never crashes.
- **Version discipline:** minor bump (a user-visible UI change — pipeline mode
  disappears from the shipped UI).

## Open items for the plan (confirm, don't guess)
- **`_launch_run` extraction:** confirm exactly what `_on_run_remix` reads today
  (`remix_view.current_spec()` + `edits`) so the muse-path call (seed
  `spec_path`, empty edits) produces an identical runner start — the one seam
  with real behavior risk.
- **`on_leave` target:** confirm the exact main-app gallery child to return to
  (the Library page name in `_gallery_stack`) and that re-activating the Library
  nav verb after leaving matches `_hide_pipelines`'s existing behavior.
- **Flag home:** confirm `app_settings` is the right module (vs a tiny
  `feature_flags`), and whether the flag should also be surfaced in Preferences
  (recommend NOT — an env/constant dev flag, invisible to users, is enough).
- **Wording scope:** list the exact user-visible strings on the flag-OFF Muse +
  Stage to adjust, confirming none are shared with flag-ON-only surfaces.
