# "Video is Video" — condense the video trio, advertise models by benefit

**Date:** 2026-08-03
**Status:** Design approved (Taylor), ready for implementation plan
**Branch:** `feat/pipeline-editor`

## Problem

The Create surface ships our *technical implementation* as its taxonomy. Three
separate top-level medium chips — **Video** (Wan2.2 / SkyReels / Mochi),
**Animate** (Wan2.2-Animate), and **AnimateDiff** — are, to the person making
something, all *"a video."* They differ only in which model/hardware runs and
what settings each needs. Exposing them as three peer chips makes the user think
about our plumbing instead of their intent, and it buries the fact that
**AnimateDiff runs locally with no server to start** — the one option that always
works on a cold box.

Separately, the model picker today shows only a health dot + a raw implementation
string (`Wan2.2-T2V-A14B  (P300X2)`); it never tells the user *what each model is
good for*.

## Goal

Condense the video trio into **one "Video" medium** whose specific model is chosen
in a **benefit-advertising model picker**, with **AnimateDiff as the default when
no server is running**. Establish the per-model "benefit" tagline as a **reusable
pattern** the picker reads (so Image/artgen pickers can adopt it later). Do **not**
re-group the artgen kinds in this pass (ANSI/Palette/Verse/etc. stay one-chip-each).

## Key finding that shapes the design

There are two "AnimateDiff" code paths, and **they are the same engine.** Both the
native `AnimateDiffGenerationWorker` (reached today via the `_on_generate` video
branch when `video_model_key == "animatediff"`) and the artgen `animatediff` medium
(reached via `tt-ctl artgen animatediff` → the plugin) converge on
`app/artgen/generators/animatediff.py::run_subprocess`, which launches
`vendor/tt-animatediff/examples/generate.py` as a **local Blackhole subprocess** —
no server, no chat-LLM, `.gif` output. They diverge only in wrapper, config surface,
and which store/gallery the result files into (native `GenerationRecord` →
video/image gallery vs artgen `MediaRecord` → artgen gallery).

**Decision:** the canonical **"AnimateDiff" Video model routes through the native
`AnimateDiffGenerationWorker`** path — it is already wired into the video branch and
files its result into the Video flow like every other Video model. The **artgen
`animatediff` medium chip retires** from the Create chip row. No generation
capability is lost (same subprocess); only the redundant second entry point goes
away. Existing artgen-`animatediff` records already in the store continue to render
in the artgen gallery unchanged (this is a taxonomy/entry-point change, not a data
migration).

## Design

### 1. Taxonomy — one Video medium (`app/create_mediums.py`)

`default_mediums()` stops emitting **two** chips:
- the native **`animate`** medium (from `_NATIVE_MEDIUMS`), and
- the artgen **`animatediff`** medium (filtered out of the discovered artgen list).

**Video** stays as the native `video` medium. The chip row goes from
`Image · Video · Animate · <artgen kinds…> · AnimateDiff` to
`Image · Video · <artgen kinds…>`. AnimateDiff and Animate are no longer mediums;
they are **models inside Video**.

The other native mediums (`image`, `video`) and all remaining artgen kinds are
untouched.

### 2. Video's model list (`app/create_view.py::_scoped_model_keys`)

For the `video` medium, return the ordered keys:

```
["animatediff", "wan2.2", "mochi", "skyreels", "animate"]
```

- **AnimateDiff first** — the always-ready local model leads the list *and* becomes
  the natural index-0 fallback for auto-selection (see §7).
- `"animate"` is **explicitly appended** for the video medium: its `ServerDef` has
  capability `("animate",)`, so `servers_for_capability("video")` would not return
  it; the video medium's key list adds it by hand (the same way the synthetic
  `"animatediff"` key is added today).

The `__detected__:<id>` sentinel handling for artgen mediums is unchanged (video is
a native medium and does not use it).

### 3. Benefit taglines — the reusable pattern (`app/server_manager.py` + picker)

Add a `benefit: str = ""` field to the `ServerDef` dataclass (default empty →
migration-safe for every existing literal). Populate it for the video-family
servers. For keys that have **no** `ServerDef` (the synthetic `animatediff` key, and
defensively the `__detected__` sentinel), add a module-level `MODEL_BENEFITS` dict.
Expose one helper:

```python
def benefit_for(key: str) -> str:
    """Human 'what is this good for' tagline for a model/server key.
    ServerDef.benefit wins; falls back to MODEL_BENEFITS; '' if unknown."""
```

Draft copy (final wording reviewed with the spec):

| key | friendly name | benefit |
|---|---|---|
| `animatediff` | AnimateDiff | Runs locally on Blackhole — no server to start. Quick looping animation. |
| `wan2.2` | Wan 2.2 | Highest-quality 720p text-to-video. Needs its server running. |
| `skyreels` | SkyReels | Fast video from a seed image (image-to-video). Blackhole. |
| `mochi` | Mochi | Cinematic text-to-video. Needs its server running. |
| `animate` | Animate | Bring a character image to life with a motion video. |

`benefit_for` is the single seam the picker reads; Image and artgen pickers can call
it later without re-forking copy.

### 4. Friendly display names (picker only)

The picker (dropdown + Model door) shows **human names** — "Wan 2.2", "AnimateDiff",
"SkyReels", "Mochi", "Animate" — not the raw implementation strings
(`Wan2.2-T2V-A14B  (P300X2)`, `SkyReels-V2-I2V-14B-540P  (Blackhole)`). A small
display-name mapping (parallel to `MODEL_BENEFITS`, or a `display_name` field on
`ServerDef`) is used **only by the picker**; the raw `ServerDef.label` stays intact
for logs, the Servers control, and `ModelStatusService` (which key off the real
labels/keys). No log or status string changes.

### 5. Picker rendering (`app/create_view.py`)

Both picker surfaces advertise the benefit:

- **Scoped dropdown** — replace the plain string rows with a `Gtk.SignalListItemFactory`
  two-line row: line 1 = `<dot> <friendly name>`, line 2 = dimmed `benefit_for(key)`
  (smaller, muted). The selected-item display can stay compact (name only) if the
  factory's list rows carry the benefit; exact treatment is an implementation detail,
  but the benefit text MUST be visible when choosing.
- **Model door cards** — each model card/button gains its benefit as a subtitle line
  under the friendly name.

The health dot (◌/◐/●) semantics are unchanged. For `animatediff` (local, no server)
the dot reads always-ready `●` (it needs nothing started) — matching how the
synthetic key is treated today.

### 6. Reveal-on-demand Animate inputs (`app/create_view.py`)

The Video form stays clean (prompt + model picker + collapsed Controls). When the
**selected Video model is `animate`**, a compact **"Animate needs"** section slides in
directly under the model picker:

- **Motion video** — file picker (mp4) → `ref_video_path`
- **Character image** — file picker (png/jpg) → `ref_char_path`
- **Mode** — `animation` / `replacement` → `animate_mode`

Selecting any other Video model hides the section. Reuse the existing seed-image /
file-well widgets and the file-dialog async pattern already in the surface. The
section is display-only wiring; its collected values feed generation only when
`animate` is the chosen model (§8).

### 7. Default when nothing runs (`app/create_view.py::_autoselect_running_model_index`)

For the video medium, when **no** video-family server is running, auto-select
**AnimateDiff** (the local no-server model). With `animatediff` at index 0 (§2), the
existing "return 0 when nothing running" fallback already yields it — but make the
intent explicit and also handle the running case correctly:

- The capability lookup for the video medium must consider **both** `"video"` and
  `"animate"` servers (a running Wan2.2 *or* a running Wan2.2-Animate should be the
  preferred auto-selection). `running_or_starting` is consulted for both; the
  most-recently-ready wins; if neither is running, select the `animatediff` entry.
- **Manual picks survive same-medium refreshes** — the existing invariant (only the
  fresh-populate branch auto-selects) is preserved exactly.

### 8. Routing (`app/main_window.py::_native_generate_args`)

The video medium now maps the chosen model to the right worker **in
`_native_generate_args`** (not `_on_generate`), keeping `_on_generate`'s worker
selection untouched:

- `model == "animate"` → emit `model_source="animate"` plus `ref_video_path`,
  `ref_char_path`, `animate_mode` (exactly the args the `animate` medium produces
  today) → `_on_generate` dispatches `AnimateGenerationWorker`.
- `model == "animatediff"` → `model_source="video"`, `video_model_key="animatediff"`,
  build `animatediff_args` (unchanged) → `_on_generate` dispatches
  `AnimateDiffGenerationWorker`.
- any other video model → `model_source="video"` → `GenerationWorker` (unchanged,
  incl. the SkyReels seed-image guard).

This closes the current gap where `AnimateGenerationWorker` was reachable *only* via
`medium.id == "animate"`.

### 9. Ready-to-Run gate (`app/ready_to_run.py`, verify only)

`plan_switch` already maps a selected model key to its required server and treats
AnimateDiff (no real `SERVERS` key) as "no target → nothing to gate." Confirm the
`animate` key resolves to the `animate` server and that selecting `animatediff`
produces `target=None` (runs immediately, no confirm dialog). No new switch churn —
the QB2 confirm-before-switch rule (memory `reference_qb2_card924055_fragility`)
stands untouched.

## Out of scope (this pass)

- Re-grouping the artgen kinds (deferred; picked "Video + establish the pattern").
- Any change to `GenerationWorker` / `AnimateGenerationWorker` / `AnimateDiffGenerationWorker`
  internals, or to `collect()` output shape.
- Migrating existing artgen-`animatediff` records (they keep rendering as-is).

## Invariants / global constraints

- **`collect()` byte-compatibility** — the params dict generation consumes is
  unchanged for every still-existing path; pinned by the existing collect-equality
  tests plus new ones covering the video-model branches.
- **GTK single-thread** — all widget touches on the main thread; file dialogs use the
  async `open_finish` pattern; any off-thread result via `GLib.idle_add`.
- **`_CSS` is a `b"""..."""` ASCII-only bytes literal** — every glyph (dots, icons)
  lives in Python `str` labels, never in the CSS literal.
- **Palette** — tt-vscode-toolkit variant (`#4FD1C5` / `#0F2A35`), unchanged.
- **System python** — `/usr/bin/python3`; tests via
  `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q`.
- **Version discipline** — minor bump (new user-visible taxonomy/UX), `VERSION` +
  `debian/changelog` stanza, update CLAUDE.md's Create-surface section.
- **Local commits only** — do not push.

## Testing strategy

- **Taxonomy:** `default_mediums()` no longer contains `animate` or `animatediff`
  mediums; still contains `image`, `video`, and every other artgen kind.
- **Model list:** `_scoped_model_keys(video)` == `["animatediff","wan2.2","mochi","skyreels","animate"]`.
- **Benefit seam:** `benefit_for` returns the ServerDef benefit, the `MODEL_BENEFITS`
  fallback, and `""` for an unknown key.
- **Default:** with a fake status service reporting nothing running,
  `_autoselect_running_model_index(video, …)` selects the `animatediff` entry; with a
  running `wan2.2` (or `animate`) it selects that key; a manual pick survives a
  same-medium refresh.
- **Reveal:** selecting `animate` shows the Animate-needs section; selecting any other
  model hides it.
- **Routing:** `_native_generate_args(video, params)` yields `model_source=="animate"`
  (+ ref paths) for `model=="animate"`, `model_source=="video"` +
  `video_model_key=="animatediff"` for `model=="animatediff"`, and plain
  `model_source=="video"` otherwise — asserted without running generation.
- **collect() equality:** video-medium `collect()` unchanged by the picker/reveal
  additions.
- Full suite green (deselect the two known env flakes:
  `test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`,
  `test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`).
