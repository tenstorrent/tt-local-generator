# tt-local-generator — developer notes

## Pipeline UX overhaul (v0.75.0)

Nine-task program closing the gap between what Pipeline Studio's step cards
*could* show and what they actually did: raw `class_type`s instead of plain
language, no per-step model choice, a log-only view of a running pipeline,
and a dead-end "run finished" screen whose deliverable never reached the
Library. Every seam below is GTK-free/pure where the task allowed, so the
logic is unit-tested without a display; only the widget glue lives in
`pipeline_studio.py`/`main_window.py`.

- **Self-explaining steps** (`app/intent_vocab.py`): `flow_line(intent) ->
  str` renders a plain "Takes a prompt → makes an image" line per step
  (source nodes read "Makes …" with no "Takes" clause) from `Intent.
  input_kind`/`output_kind` via the `_KIND_NOUN` map. `Intent.summary` is an
  optional one-sentence, tool-agnostic description ("Turns your words into
  an image.") populated for the marquee generative intents. `capability_for_
  intent(class_type) -> str | None` (the `_CAPABILITY_FOR_INTENT` map) is the
  seam that tells a step card which `server_manager` capability (if any) its
  model picker should scope to — `None` for intents with no model dimension
  (caption/rmbg/depth/prompt-compose/etc.), so those step cards never grow a
  picker at all.
- **`app/model_picker.py`** — a shared, GTK-optional per-capability model
  picker, deliberately a NEW module rather than extracting CreateView's own
  scoped dropdown (avoids risking a Create regression; migrating CreateView
  onto this module is an explicit, not-yet-done follow-up — it keeps its own
  picker for now). `picker_entries(capability, snapshot=None,
  has_service=False) -> list[(key, display_name, benefit, dot)]` is the pure
  core (mirrors `server_manager.display_name_for`/`benefit_for` +
  `ModelStatusService`'s dot glyphs; `capability=="animatediff"` is a single
  synthetic always-● entry, since AnimateDiff has no `ServerDef` to poll).
  `ModelPickerRow(Gtk.Box)` wraps it in a `Gtk.DropDown` + benefit sub-label,
  subscribing to `ModelStatusService` for live dot updates and guarding
  against the double `notify::selected` fire GTK4's `set_model()` triggers
  on every rebuild (`_suppress_change`, so a live status push can never look
  like a spurious user re-pick). `pipeline_studio.py` builds one
  `ModelPickerRow` per step whose intent has a `capability_for_intent`
  match, keyed by node_id in `self._model_pickers`.
- **`_backend_for` parity** (`app/pipeline_engine.py`): a step's picker is
  worthless if the run doesn't honor it. `_backend_for` now tries an EXACT
  `server_manager` key match against the node's `inputs["model"]` first
  (`TTLGTextToImage`/`TTLGImageToVideo`), falling back to the old
  substring-sniffing (`"flux" in m`, `"wan" in m`, …) only when the picked
  value doesn't match a real key — so a `ModelPickerRow` selection always
  routes to the actual server it named, never a same-family guess.
- **Live run progress** (`app/pipeline_progress.py` + `LiveRunView` in
  `pipeline_studio.py`): `ProgressState` is a pure reducer — `update(node_id,
  status, detail)` folds one event, `current_index`/`done_count`/
  `running_node`/`phase(node_id)` are read back to drive widgets;
  `current_index` counts nodes in FIRST-started order, not declaration
  order, so "Step N of M" matches what's actually happening on screen.
  `LiveRunView` replaces each running step's status glyph with a real
  `Gtk.Spinner`, shows the latest `detail` as a phase sub-label, and ticks a
  per-step elapsed-time label (`GLib.timeout_add(1000, …)`, cancelled with a
  final freeze — not a reset — on done/failed). The verbose log is demoted
  to a collapsed `Gtk.Expander` now that the spinner/phase/elapsed row
  carries the at-a-glance status.
- **Final-result hero** (`app/pipeline_view_model.py` + `OpenView` in
  `pipeline_studio.py`): `final_index_for(run) -> int | None` resolves which
  step is the run's "Here's what you made" deliverable. `_HERO_KINDS` was
  `{"image", "video"}`; AnimateDiff's `gif_path` output and every visual
  artgen artifact (`TTLGArtgenGenerate`'s generic `artifact_path`, whose
  intent alone can't distinguish a visual finale from prose) were silently
  UN-heroable — a real bug, not hypothetical, since it also meant no Library
  registration (below). Fixed: `_HERO_KINDS` now includes `"gif"`, and
  `_ARTGEN_VISUAL_EXTS` (`.svg`/`.ans`/`.json`/`.gif`/`.png`/`.jpg`/`.jpeg`/
  `.webp`) promotes a visual-looking "any"-kind artgen artifact to hero by
  its own file extension, deliberately excluding `.txt`/`.py` (verse/
  codeart prose keeps falling through to the existing text-only hero path).
  `OpenView._build_hero` renders the resolved step exactly like an ordinary
  step row (fan-out grid / single thumb frame / text block — never a new
  rendering path), just larger, with ⛶ Fullscreen / ⤓ Save / ↪ In Library /
  🔀 Remix actions. This hero is still the fixed-contract static
  `_build_thumb_frame`, not `artgen_render`'s rich/animated rendering — see
  the "Known, accepted static-degrade" note under "Media showcase
  everywhere" below.
- **Library registration** (`MainWindow._register_pipeline_final`, wired as
  `PipelineStudio`'s `on_run_complete` callback, fired once per run from
  `LiveRunView`'s "run-done"): resolves the hero step via `final_index_for`
  and classifies its artifact by extension into ONE of the two record
  shapes every other Library-writing path already produces — raster
  (`.png`/`.jpg`/`.jpeg`) or `.mp4` become a native `history_store.
  GenerationRecord` via the same gallery-add path `_on_finished` uses;
  artgen kinds (`.gif`/`.svg`/`.ans`/`.json`/`.py`/`.md`) become a
  `media_store.MediaRecord` (`generator_type="pipeline"`, provenance
  `_pipeline_run_id`/`recipe` in `params`) mirroring `_create_generate_
  artgen`/the `ansi-image` transform branch. Fail-soft end to end (no
  resolvable hero, missing file, unrecognized extension, or any raised
  error just means "register nothing," never a crash on the run-done
  screen the user is looking at); `self._registered_pipeline_runs` (a set of
  run ids) guards against double-registering the same run.
- **Legacy portfolio view retired.** `app/pipeline_portfolio_view.py` (the
  pre-Task-7 "done runs" grid) was dead/unwired code — nothing in
  `main_window.py` ever constructed it — and was deleted outright along with
  its test file rather than kept around unreachable; `OpenView`'s hero
  (above) is the surviving, already-live run-showcase surface.

## Remix is pipelines (v0.73.0)

Remix used to mean two different things depending on which surface you clicked
from (a quick popover vs. "Remix as pipeline…"). It now means exactly ONE
thing everywhere: **seed a pipeline from an artifact, via the Muse.**

- **One affordance, one destination.** `GenerationCard`, `DetailPanel`,
  `ArtgenGallery`'s card, and `ArtgenDetail` each show a single "🔀 Remix"
  button (tooltip "Remix this into a pipeline"), all wired to the same
  `_remix_as_pipeline` handler in `main_window.py`, which opens Pipeline
  Studio's Muse seeded with that artifact. `remix_popover.py` (`RemixPopover`)
  and `remix_dispatch.py` (`dispatch_remix`) **remain in-tree and
  unit-tested but are unwired** — `MainWindow._on_remix_card`/
  `_dispatch_remix` and all `RemixPopover`/`on_remix` call sites were
  deleted; nothing in `main_window.py` constructs a `RemixPopover` anymore.
- **Palette is now a seedable kind.** `artgen_kind.artgen_seed_kind(file_path,
  generator_type)` returns `"palette"` when `generator_type == "palette"`
  (before this, a saved palette JSON wasn't a recognized remix-seed kind at
  all), so a palette artifact can be dropped into the Muse like any image,
  video, or text artifact.
- **Cross-type adapter registry** (`app/intent_vocab.py`): `ADAPTERS: dict[
  (seed_kind, input_kind), class_type]` plus `adapter_for(seed_kind,
  input_kind) -> str | None`. Ships one entry —
  `("palette", "text"): "TTLGPaletteToPrompt"` — but is designed to grow
  (e.g. a future `("image", "text"): "TTLGCaptionImage"`) without touching
  any call site. `intent_vocab.INTENTS["TTLGPaletteToPrompt"]` is a
  **source-style** Intent (`input_key=None`, `input_kind=None`,
  `output_kind="text"`, `outputs=("prompt",)`) — it has no runtime input
  because its value is computed once, at seed time, not at run time.
- **`TTLGPaletteToPrompt`** is a pure engine handler
  (`pipeline_engine._h_palette_to_prompt`, `@register("TTLGPaletteToPrompt")`)
  that does nothing but echo `inputs["prompt"]` straight through — the actual
  composition (palette JSON -> prompt text, optionally LLM-polished) already
  happened when the pipeline was seeded, so the node has **zero runtime
  LLM/backend dependency** (mirrors the existing `TTLGPromptCompose`
  template-substitution handler's "pure at run time" shape). The composed
  text lands in the step's `inputs.prompt` as an ordinary, hand-editable
  literal — nothing about it is special-cased in the step-card field editor.
- **`app/palette_prompt.py`** (GTK-free): `load_palette(path) -> dict | None`
  (parses a palette JSON, `None` on any failure — missing/unreadable/not an
  object) and `literal_prompt(palette) -> str` — the deterministic,
  guaranteed-to-work fallback: up to the first 6 hex colors plus the lore
  sentence, joined as `"palette: #hex1 #hex2 ..., <lore>"`. This is the same
  colors+lore extraction that used to be trapped inside
  `remix_popover._build_hint`, now a shared pure helper.
  **`prompt_client.llm_polish_or_none(source, seed_text) -> str | None`** is
  the optional upgrade path: polishes the literal into more natural phrasing
  via the prompt-gen LLM, returning `None` (not raising) if no LLM is
  reachable — the adapter always has a usable prompt either way.
- **`recipes.build_seed_spec(goal, *, seed_artifact=None, prepend_steps=())`**
  gained `prepend_steps: tuple[tuple[class_type, params_dict], ...]` — steps
  inserted before the goal's own `recipe_steps`. `seed_spec` chains each
  step's primary output into the next step's input, so a prepended
  text-output adapter wires straight into the goal's first step's text
  input with no special-casing. **`recipes.goals_for(seed_output_kind=...)`**
  now also offers a goal when the seed's kind doesn't directly match the
  goal's first step's `input_kind` but `adapter_for(seed_output_kind,
  first_input)` resolves one — i.e. the Muse offers AnimateDiff-shaped goals
  for a palette seed, not just text-shaped ones.
- **Muse `compose_fn` seam** (`pipeline_studio.py`): `MuseView.__init__`
  takes an optional `compose_fn: Callable[[medium, literal, on_done], None]`.
  `PipelineStudio`'s `_compose_fn` runs `prompt_client.llm_polish_or_none`
  on a background `threading.Thread` and posts the result back via
  `GLib.idle_add(on_done, polished or literal)` — the same off-thread +
  idle_add shape as `main_window._create_inspire_fn`. `MuseView._choose_goal`
  detects when the chosen goal needs the palette adapter
  (`adapter_for(seed_kind, first_input) == "TTLGPaletteToPrompt"`), shows a
  "Composing a prompt from your palette…" message, and on `on_done` prepends
  a `("TTLGPaletteToPrompt", {"prompt": <text>, "_source_palette": path})`
  step via `build_seed_spec(..., prepend_steps=(step,))` before emitting
  `goal-chosen`. `compose_fn=None` (tests/standalone) falls back to the
  synchronous literal — the adapter path never blocks on an LLM being
  available. Every other goal (kind matches directly, or no adapter
  registered) keeps the original synchronous build-and-emit path unchanged.
- **Regression lock:** `tests/test_palette_to_animatediff_e2e.py` builds a
  palette-seeded AnimateDiff spec via `build_seed_spec(..., prepend_steps=...)`
  end to end (headless, no GTK — pure spec assembly + a dry `pipeline_engine`
  handler call) and asserts the adapter's `prompt` output wire lands on
  AnimateDiff's `prompt` input (`spec["2"]["inputs"]["prompt"] == ["1",
  "prompt"]`), the topo order resolves, and the dry-run handler output
  contains both the palette's hex color and its lore text.

## "👁 Watch" hardware-activity viz (experiment, v0.67.0 · honest N-chip v0.68.0)

`app/activity_viz.py` — `ActivityVizWidget(Gtk.Box)`, an OPTIONAL "watch the
chip work while you generate" widget for the Create surface. Embeds the
self-contained **tensix-viz** Canvas animation (bundled at
`app/assets/tensix-viz/{tensix-viz.js,tensix-viz.css}`, copied from
`~/code/tensix-viz`, **zero external deps** — 0 fetch/import/CDN, inlined into
one `about:blank` HTML doc) in a `WebKit.WebView` (JS enabled), reusing the
`artgen_detail` **realize-deferral** pattern (`evaluate_javascript`/`load_html`
before realize is a silent no-op → queue in `_pending_js`, flush on `"realize"`;
backlog bounded to 32 so a never-realized active viz can't leak telemetry
calls). User loved the MVP and asked to develop it — see
[[project_watch_activity_viz]].

**Honest per-chip display (v0.68.0).** The widget draws ONE tensix-viz per REAL
chip under `/sys/class/tenstorrent/` (`chip_count()`), capped at `_CHIP_CAP`
(4) so a big system stays a legible corner instrument — 4-chip QB2 → a 2×2 grid
(`grid_layout(n)` → `(cols, canvas_w, canvas_h)`). It does NOT use tensix-viz's
`CardViz`/`SystemViz` (those hide their inner per-chip `TensixViz` instances);
instead the inlined init script builds N canvases + a tiny `window.__viz`
**facade** exposing `activate(mode)` (fans out, staggered), `setMemoryStats(s)`
(all chips), and the net-new `setChipStats(i, s)` (ONE chip) — so each chip can
be fed its OWN clock. `read_chip_clocks()` returns position-aligned per-chip
AICLK (None for an unreadable chip, so index i always maps to chip i), and the
1 s tap feeds `setChipStats(i, {dram_bw, l1_fill})` per chip. Maximally honest:
each drawn chip pulses with its own real clock.

Two decoupled controls: `set_mode(medium)` → `viz.activate(<mode>)` picks the
animation MODE (`mode_for_medium`: image→`diffusion`, video/animate→`video`,
animatediff→`diffusion`, any other artgen→`thinking`, else→`inference`; idle
when None) and updates the header caption; `set_running(bool)` starts/stops the
telemetry tap. They're separate so the **live readout ticks the whole time Watch
is shown, not only mid-job** (`set_active`/`set_idle` remain as mode+running
aliases). A compact **header** shows `◉ <mode>` (left) + a live power/clock
readout (right, "N/total" when the display is capped) + a `✕` dismiss
(`on_close` callback → flips the Watch toggle off).

**Click the title to cycle modes (v0.69.0).** A `Gtk.GestureClick` on the
header's mode label steps `cycle_mode()` through `_CYCLE_MODES` (all tensix-viz
modes) — a manual override for exploring the animations. `set_mode`/`cycle_mode`
share `_apply_mode(mode_str)`; the lifecycle auto-driver reasserts the medium's
mode on the next job. The gesture is on the label (which fills the header) not
the whole header, so it never conflicts with the `✕` button.

**Telemetry signal = per-chip POWER draw, not AICLK (v0.68.1).** The MVP fed
sysfs AICLK into `setMemoryStats`, but AICLK on Blackhole is effectively binary
(~800 idle / 1350 boosted) and often pins at 1350 even at rest — so the memory
layer barely moved during a job (the "AnimateDiff shows no difference" report).
**Power is the graded, honest signal** (~18 W idle → 150 W+ under diffusion):
`sample_telemetry(display, actual)` prefers per-chip power via
`read_chip_power_watts()` (a `tt-smi -s --snapshot_no_tty` subprocess, ~0.3 s,
parsed by pure `parse_powers`), converted to an **activity scalar 0..1** by
`power_activity` (`_POWER_FLOOR_W`=15 … `_POWER_CEILING_W`=110, `**_POWER_CURVE`
=0.6 perceptual boost), and **falls back to sysfs AICLK** (idle-relative
800→1350 via `_clock_activity`) when tt-smi is absent.

**Expressive data flow (v0.70.0).** The tap returns an activity scalar; the
main-thread `_apply_sample` shapes it into flow via pure `shape_flow(activity,
active)` → `(dram_bw, l1_fill, writeback)`. `active` = the animation mode isn't
idle (a job is showing); an active job gets a FLOOR (dram≥0.35, writeback≥0.15)
so DRAM↔L1 particle flow is clearly visible and then intensifies with real load,
instead of the raw power value *suppressing* flow below the mode preset's own
liveliness (the earlier "hard-override made it quieter than the canned preset"
trap). **Bidirectional flow** needs a tensix-viz change: `setMemoryStats` now
accepts an optional `writeback` (L1→DRAM return-particle density) override —
committed in the sister repo `~/code/tensix-viz` (`src/chip.js`, `node build.js`,
83 tests) and re-bundled into `app/assets/tensix-viz/`. Keep the bundle in sync
by editing the source + rebuilding, never hand-editing the generated bundle. The subprocess CANNOT run on the GTK thread, so the tap
is a **background daemon thread** (`_telemetry_loop`, `_TELEMETRY_INTERVAL_S`=1.5
s, `stop.wait` for prompt cancel) that hands each sample to the main thread via
`GLib.idle_add(self._apply_sample, …)` (which updates the readout + evals
`setChipStats(i, …)` per chip; a late sample after stop is ignored via
`_tel_running`). `pyluwen` (fast Rust telemetry) is venv-only and the app runs
system `/usr/bin/python3`, so tt-smi is the portable read path. All pure helpers
(`mode_for_medium`, `chip_count`, `read_chip_clocks`, `read_aiclk_peak_mhz`,
`parse_powers`, `power_intensity`, `sample_telemetry`, `grid_layout`) are
GTK-free and unit-tested; `arch="blackhole"` (QB2 is Blackhole).

**Fail-soft + optional by construction.** OFF by default. No WebKit → inert
stub (`_WEBKIT_OK` guard; header still shows, readout reads "—"); no chips →
draw one idle chip and the tap no-ops; every `evaluate_javascript` is wrapped so
a bad tick just skips. **Built LAZILY** — `CreateResultPanel.__init__` never
constructs it (WebKit is a heavy JS-engine/web-process cost, and 7 test files
build CreateResultPanel; eager construction also segfaults the WebKit bwrap
sandbox under nested-sandbox CI). Instead:

- The result pane is wrapped in a `Gtk.Overlay` (`CreateView._build_panes`,
  `self._result_overlay`) so the viz can be pinned to the **bottom-right**
  corner (halign END/valign END + bottom/end margins), locked into the frame.
  **Corner-pin invariant:** the widget sets a fixed size + `hexpand/vexpand
  False` (and so does its inner WebView) — an expanding overlay child gets
  stretched to fill the pane instead of pinned (the MVP bug: it floated over
  the content in the top-left).
- CTA row has a `👁 Watch` `Gtk.ToggleButton` → `_on_watch_toggled` →
  `_ensure_activity_viz()` builds the widget ONCE on first reveal, adds it to
  the overlay, wires `viz.on_close` to the toggle, and injects it via
  `CreateResultPanel.set_activity_viz(viz)`.
- `CreateResultPanel` DRIVES the mode from its own lifecycle so the animation
  can never drift from what's cooking: `show_pending`→`_drive_activity_active`
  (only when `_activity_visible`), `show_finished`/`show_error`/`_show_empty`→
  `_drive_activity_idle` (calms animation, keeps the clock ticking while
  shown). `set_activity_visible(bool)` toggles the viz + `set_running` +
  initial mode; turning Watch on mid-generation animates the in-flight medium
  immediately.

**Invariant preserved:** `_collect_params()`/`collect()` are untouched — the
viz is pure decoration in the result pane, never a value-bearing widget. The
paned end child is now a `Gtk.Overlay` wrapping the result scroller (was the
scroller directly) — the only structural change, guarded by
`test_create_view.py::test_paned_holds_scrolling_form_and_docked_result_detail_pane`.
Tests: `tests/test_activity_viz.py` (pure helpers + a `_FakeViz` drives the
CreateResultPanel wiring without WebKit; a regression guard asserts
`CreateResultPanel.__init__` stays WebKit-free). Live rendering is
user-verified on the real display (WebKit works there; the nested-sandbox
`bwrap: Permission denied` crash is CI-only).

## artgen LLM endpoint discovery

`artgen.detect_artgen_endpoint()` (`app/artgen/__init__.py`) picks the chat
server for generative art. Hardcoded ports (artgen=8002, prompt-server=8001)
matter only for servers the *app* starts. For models started any other way it
sweeps local ports (`_SCAN_PORT_RANGE`, override via `TTLG_ARTGEN_SCAN_PORTS`)
for any OpenAI-compatible `/v1/models` responder.

Resolution order: `preferred_url` → artgen (8002) → swept ports → prompt-gen
(8001, tiny Qwen3-0.6B) **last**. The prompt-gen fallback is deliberately last
so a real chat model always beats it — the original bug was a vLLM Llama-3.3-70B
on 8003 losing to Qwen3-0.6B on 8001 because 8003 was never probed. The known
diffusion port (8000) and the two explicit ports are excluded from the sweep.
`mcp_server._make_call_fn` routes through the same function for consistency.

**Single source of truth for "is a model on".** The artgen panel's health dot
(`ArtgenPanel._check_health_bg`) also calls `detect_artgen_endpoint()`, so the
indicator can never disagree with where generation requests actually go. It
caches the last-found base URL and re-pings only that on each 5 s poll (via
`detect_model`), re-sweeping the full port range only when the endpoint drops.
Regression: previously the dot pinged the fixed configured port (8002) for the
dropdown's model key, so a model on any other port read "offline" while
generation worked fine.

## ANSI art — 3-pass pipeline

`AnsiGenerator` in `app/artgen/generators/ansi.py` overrides `generate_artifact`
to run three sequential LLM calls instead of one. This is the first implementation
of the multi-pass remix pattern that will be generalised in remix-mode.

**Why three passes:** A single 40×20 canvas already asks the model to manage 800
decisions. Planning spatial composition and color simultaneously causes models to
output palette strips instead of imagery — horizontal bands of one color per zone.
Separating concerns gives each pass a task the model handles reliably.

**Pass flow:**

1. `_build_ascii_prompt()` → `call_fn(..., max_tokens=1024)` → `_normalize_grid()`
   - Plain ASCII chars only; no color, no block chars
   - Style-specific spatial hints (BBS: void rows top/bottom, neon subject center;
     landscape: sky top / terrain bottom; scene: foreground/midground/background)

2. `_build_refine_prompt(ascii_art)` → `call_fn(..., max_tokens=1024)` → `_normalize_grid()`
   - Replaces dense chars with `█ ▀ ▄ ▌ ▐ ░ ▒ ▓`; exact layout preserved
   - `_normalize_grid` strips think-blocks, fences, pads/truncates to exact width×height

3. `_build_colorize_prompt(block_art)` → `call_fn(..., max_tokens=8192)`
   - Wraps every character as `\033[38;5;Nm█` (foreground + block char)
   - BBS color guide: neon-on-void (`_COLOR_GUIDE_BBS`); scene/landscape: `_COLOR_GUIDE_SCENE`
   - Space chars use `\033[38;5;232m\033[0m` (near-black foreground, remain invisible)

**`generate_artifact` hook:** `ArtGenerator` base class defines a single-pass default
(`build_prompt` → `call_fn` → `parse_output` → `post_process`). Override `generate_artifact`
to implement multi-pass. The `call_fn` closure is built by `_make_call_fn` in `cli.py`
and by the artgen panel; it accepts `max_tokens=` per-call so each pass can have its own
token budget.

**`--simulate`:** `build_prompt()` returns the pass-1 ASCII prompt, so dry-run still works.

**`--ansi-style bbs`:** BBS canvas is fixed at 40×20. Color guide specifies electric cyan
(51, 87), toxic green (46, 82), hot magenta (201, 199), gold (226, 220). Zone rules
constrain rows 1-2 and 18-20 to near-black void (232–234), rows 3-17 to the neon subject.

## Right-click transform: image → ANSI art (`plugins/ansi-image/`, v0.51.0)

`plugins/ansi-image/plugin.py` is a "forge transform" utility plugin
(`x-ttlg.utility: true`, not a generator) — a pure-Pillow, in-process
image → ANSI-art converter reusing `artgen_render._XTERM256_HEX` (the same
256-entry xterm palette the ANSI renderer already uses, so quantization here
can never drift from what the viewer draws). One color per cell only —
`\x1b[38;5;Nm█`, foreground + full block — because `artgen_render.
parse_ansi_grid` doesn't support two-color half-block cells; this is exactly
the format the 3-pass `ansi` LLM generator emits too. `is_available()` is a
bare `import PIL` check (Pillow, already a dependency elsewhere); no
subprocess, no LLM, fully deterministic for a given input. Two color modes:
`colors=256` (default, indices 16-255, the 6×6×6 cube + grayscale ramp — best
for photographic gradients) or `colors=16` (indices 0-15, the classic DOS/BBS
palette, for an intentional retro look).

**Wired into the right-click transform menu** (`app/main_window.py`) as
`"ansi-image"`, registered in the same three places as rmbg/blip/depth
(`GenerationCard._on_right_click`'s `all_transforms`, `MainWindow.__init__`'s
health pre-warm probe thread, and `_transform_available`'s dynamic plugin
loader — no additional registration needed there since it dispatches by key
+ path convention). It is a SPECIAL CASE in `MainWindow._run_transform`,
because it produces a different kind of record than the other three:

- rmbg/blip/depth call a plugin fn shaped `fn(src, dest)` or `fn(src) -> str`
  (the `_META` dict maps key → `(fn_name, ext_or_None, label)`) and always
  return a native `history_store.GenerationRecord` (`media_type="image"`)
  for the Image gallery.
- `ansi-image`'s `image_to_ansi(src, cols=80, colors=256) -> str` takes no
  destination and returns text, so `_run_transform` special-cases the key
  entirely: writes the `.ans` file itself (`artgen_thumb.make_artgen_path`),
  renders its thumbnail (`artgen_thumb.make_thumbnail` — the `.ans` branch
  already draws a real color-grid PNG), and builds a
  `media_store.MediaRecord` (`media_type="artgen"`,
  `generator_type="ansi-image"`, `params` carrying `_source_id`/`_transform`
  for provenance) — the exact record-construction pattern
  `_create_generate_artgen` uses for Create's artgen mediums, right down to
  the `rec.media_file_path` duck-typed alias and `_ms.add()` +
  `_ms.ensure_auto_playlists()`. It still writes the same structured
  `_TRANSFORMS_LOG_DIR` log file as every other transform.
- `MainWindow._on_transform_finished` branches on the returned record's
  `media_type`: `"artgen"` refreshes `self._artgen_gallery` (same as
  `_on_create_artgen_done`) and is NEVER appended to `self._store` or handed
  to `self._image_gallery` (wrong type, wrong gallery — those only ever see
  `GenerationRecord`s). The rmbg/blip/depth path is byte-for-byte unchanged.

Icon/label polish: `"ansi-image"` was added to `create_mediums.
_ARTGEN_LABELS_ICONS` (`("ANSI Art", "▓")`) and `_ARTGEN_KIND`
(`"image"`, same as the LLM `"ansi"` generator — both render as a color
grid), and to `artgen_gallery._TYPE_EMOJI` (`"▓"`) for the gallery card
badge.

## Media showcase everywhere (`app/artgen_render.py`, v0.48.0)

A full media-type × display-context audit found the rich rendering logic for
each artgen kind — ANSI grid parsing, palette swatches, the animated-gif
driver, codeart/markdown formatting — copy-pasted across 3-4 places
(`artgen_detail.py`, `artgen_watch.py`, `artgen_gallery.py`, TT-TV's
`attractor.py`) and drifted apart. Worst case: TT-TV's ANSI parser only
understood the legacy `\x1b[48;5;Nm ` (bg+space) escape form, so every ANSI
artifact made with the current generator (which emits only
`\x1b[38;5;Nm█`, fg+block) rendered as raw escape-code gibberish — a live
bug, not a hypothetical one.

**`app/artgen_render.py` is now the single leaf module** every context
imports from — it may import `gi`/Gtk/GdkPixbuf/GLib and stdlib only, and
must never import `artgen_detail`/`artgen_watch`/`artgen_gallery`/
`create_view`/`attractor` (the reverse is fine; that would be a cycle). It
provides:
- `parse_ansi_grid(raw) -> list[list[(char, fg_hex_or_None, bg_hex_or_None)]]`
  — the ONE parser that understands BOTH ANSI pixel formats the `ansi`
  generator has emitted over time (legacy bg+space and current fg+block),
  plus 8/16-color SGR, 256-color, truecolor, and SGR-0 reset. Every other
  ANSI-consuming context builds on this instead of re-walking escape codes.
- `ansi_to_html`, `palette_to_html`, `md_to_html`, `code_to_html` — HTML
  document builders for the "reading view" (`code_to_html` is deliberately
  NOT routed through `md_to_html`'s prose/markdown pipeline — that dedents
  and reflows text, which destroys Python's syntactically-significant
  whitespace; codeart gets a plain HTML-escaped `<pre>` instead).
- `derive_title`, `luminance` — small pure helpers shared by detail views.
- `AnimatedGifWidget` (a self-driving `Gtk.Picture` that cancels its own
  `GLib.timeout_add` timer on unrealize) and `drive_gif_animation` (the
  same iterator-driving logic for callers that reuse one persistent
  `Gtk.Picture` across records, e.g. `ArtgenDetail`/`ArtgenWatch`).

**The per-context showcase guarantee:** every one of `CreateResultPanel`
(`create_view.py`), `ArtgenDetail`, `ArtgenWatch`, `artgen_gallery`'s card
content (`make_card_content`), and the TT-TV attractor now renders each
artgen media type's RICH form — vector `Gtk.Picture` for svg, swatch grid
for palette json, colored character grid for ansi, formatted reading view
for verse/md, monospace indentation-preserved view for codeart `.py`,
animated `GdkPixbufAnimationIter` for gif (never the GStreamer `Gtk.Video`
path, which is documented elsewhere in this file as unreliable for gif) —
in every context, not just the one context someone happened to build first.
`CreateResultPanel` in particular never shows "Result file not found" for
an artgen kind that actually generated successfully.

**Known, accepted static-degrade:** `pipeline_studio`'s node/hero
thumbnails are a fast, fixed-contract pixbuf-grid surface (`_build_thumb_frame`)
— a placeholder/static tile for non-raster artgen types there is
intentional, not a gap to close. They may eventually call
`artgen_thumb.make_thumbnail` for a nicer static tile, but full rich/
animated rendering doesn't fit that contract and is out of scope. (The standalone `pipeline_portfolio_view.py` this
paragraph used to also name was deleted outright in the v0.75.0 Pipeline UX
overhaul — it was dead/unwired code, never mounted anywhere; the run-final
showcase job it would have done belongs to `OpenView`'s own hero, which
predates it. See the v0.75.0 section below. `OpenView`'s hero is still this
same static-thumb-frame contract, not `artgen_render`'s rich/animated
rendering; it now additionally counts "gif" and visual artgen kinds
[svg/ans/json/png/jpg] as heroable via `pipeline_view_model._HERO_KINDS`/
`_ARTGEN_VISUAL_EXTS`, where before only image/video were.)

**`artgen_thumb.make_thumbnail` now produces real thumbnails for every
type it's asked to preview**, not just raster/svg: `.json` (palette) parses
`colors: [{"hex": ...}, ...]` and draws a real swatch-grid PNG; `.ans`
parses via `parse_ansi_grid` and draws a real color-grid PNG (never falls
back to text-rendering the raw escape bytes — an unparseable/empty `.ans`
degrades straight to the honest placeholder instead); `.py` codeart (and
`.md`) get the existing monospace text-render (previously `.py` wasn't in
the text allow-list at all and fell all the way through to the grey 1×1
placeholder). Before this, `.json`/`.ans` fell into the generic text branch
and got their raw syntax/escape bytes text-rendered as if they were prose
— exactly the kind of garbage-PNG bug this module exists to prevent for
binary formats; any blind `thumbnail_path` consumer (the Create recents
strip, the attractor's slot fallback, pipeline-studio previews) now gets an
honest preview.

## Unified gallery interaction (v0.49.0)

All galleries — native `GalleryWidget` (video/image/animate) AND `ArtgenGallery`
— now share ONE interaction model: browse thumbnails → hover-preview →
**single-click opens the detail in the shared right pane** → **double-click (or
the pane's `⛶ Fullscreen` button) opens a maximized full-screen view**.

- **Dual-renderer right pane.** `MainWindow._detail_wrap` holds `self._right_stack`
  (a `Gtk.Stack`): child `"native"` = `self._detail` (`DetailPanel`, video/image/gif),
  child `"artgen"` = `self._artgen_detail` (a shared `ArtgenDetail`). Native card
  selection (`_on_card_selected`) shows `"native"`; artgen selection
  (`_on_artgen_card_selected`, wired via `ArtgenGallery.on_card_activated`) shows
  `"artgen"`. `_set_detail_pane_visible` (Task 1) is the single visibility toggle
  (the ✕ dismiss bar + `win.toggle-detail` both route through it) — do NOT assume
  `self._detail.get_parent() is _detail_wrap` anymore (the Stack sits between them).
- **Artgen off the in-page overlay.** `ArtgenGallery` is grid-only again; the
  `_detail_overlay`/`_grid_page`/`_show_detail` crash-workaround is gone. The right
  pane is a *sibling subtree* of the grid, so switching it never unmaps a
  `FlowBoxChild` mid-dispatch (the segfault class the overlay existed for). Artgen
  card clicks now use the SAME mechanism as native cards: `_flow` is
  `SelectionMode.NONE` + one `Gtk.GestureClick` per card (single → `on_card_activated`,
  double → `ArtgenViewerWindow`) — no more FlowBox `child-activated`.
- **Full-screen.** Native video/image via `VideoPlayerWindow`/`ImageViewerWindow`
  (double-click a card or the pane's ⛶). **GIFs animate full-screen**:
  `VideoPlayerWindow` has a GIF branch using `artgen_render.AnimatedGifWidget`
  (`GdkPixbufAnimationIter`) instead of the seek-unreliable `Gtk.Video`. Artgen
  media uses the net-new **`app/artgen_viewer.py::ArtgenViewerWindow`**
  (svg/gif/ansi/palette/verse/markdown/code), which shares ONE ext→renderer
  dispatch with `ArtgenDetail` via `artgen_render` (no duplicated render logic;
  WebKit reading-views use the realize-deferral pattern).
- `ArtgenDetail` gained the `⛶ Fullscreen` button and dropped its vestigial
  "← Gallery" back button (the grid is always visible on the left now); its
  `on_back` callback survives only to collapse the pane when a delete empties the
  list. Switching the right pane away from artgen calls `ArtgenDetail.pause_animation()`
  so a hidden artgen GIF's timer doesn't tick forever. Delete-sync is asymmetric:
  grid hover-🗑 (`_on_artgen_card_deleted`) only clears the pane if it shows that
  record; the detail's own 🗑 (`_on_artgen_detail_deleted`) calls
  `ArtgenGallery.remove_record` to sync the grid.

## Create surface (role zones, scoped models, modifier pills)

The **Create** loop-nav verb opens `CreateView` (`app/create_view.py`), the
role-grouped generation surface (v0.28.0). Three key ideas, each backed by a
small unit — deliberately shared so pipeline field configuration can adopt them
later:

- **`app/field_roles.py`** — a pure (no-GTK) taxonomy. Every field has a **role**
  (`ROLE_BRIEF` / `ROLE_DIRECTION` / `ROLE_CONTROL` → the three zones) and a
  **marker** (`MARK_WORDS` ✎ raw text the model renders · `MARK_INTERPRETED` ✨
  a value the model/LLM decides from · `MARK_EXACT` ⚙ deterministic, never read
  by the model). `classify_native`, `classify_artgen`, `classify_pipeline_field`
  are the single source of truth. Glyphs live in `MARKER_GLYPH` — Python strings
  only, never inside a `b"""` CSS literal.
- **`RoleZonePanel`** (in `app/create_param_panels.py`) — wraps any
  `CreateParamPanel`, reads its `field_specs()`, and **re-parents** the panel's
  already-built field widgets into the brief / Direction / collapsed-Controls
  zones. It never rebuilds widgets, so `RoleZonePanel.collect()` is a verbatim
  passthrough to `panel.collect()`. **Migration invariant:** that dict must stay
  byte-for-byte compatible with what generation consumes — guarded by
  `test_role_zone_panel.py`'s collect-equality tests. The `kind=="model"` field
  is excluded here; CreateView's scoped dropdown owns model selection.
- **`ModifierPills`** (same file) — the Direction zone's chip palette. Banks come
  from `chip_config.load_chips(medium.kind)`; tapping an add-chip creates a
  removable pill (the add-chip hides until removed), and `applied_text()` is
  appended to the brief at generate time.

**Models:** no persistent full-width strip (it overflowed — retired in 0.28.0).
Within a medium, a scoped `Gtk.DropDown` lists only that medium's models. The
"Start with a model" door is a grouped, wrapping grid (Image / Video / Animate /
Text) classified by each `ServerDef.capabilities` via
`_CAPABILITY_TO_MODEL_DOOR_GROUP` — **not** by `_server_key_to_medium_id` (that
"first artgen medium" heuristic mis-files the chat-LLM backends under Animate;
regression-guarded). Text cards return to the Idea door without changing the
active medium.

**LLM-free artgen mediums self-select as their own model (v0.47.3).** Every
artgen medium used to be treated as chat-LLM-backed by `_scoped_model_keys`
(cap="artgen" -> list the chat servers) — wrong for a self-contained
generator like AnimateDiff (Blackhole diffusion GIF, no LLM involved at all).
`Medium.uses_llm` (`create_mediums.py`, threaded from `ArtGenerator.uses_llm`
via `discover_mediums`'s `uses_llm_for` param / `default_mediums()`'s real
`artgen.get(name).uses_llm` lookup) marks this per generator. An artgen
medium with `uses_llm=False` gets a single self-entry in its scoped dropdown
(`[medium.id]`, label = `medium.label`, dot always "●", canonical `None` so
`collect()`'s "model" override stays a no-op) instead of the chat-server
list — being the only entry, it auto-selects, so the user is never asked to
pick a model AnimateDiff never uses. LLM-backed artgen mediums (verse/ansi/
landscape/…) are unaffected. Gotcha found mid-fix: `artgen.get(name)`
resolves to whatever `plugins/<name>/plugin.py` defines (back-filled by
`artgen._load_generators()` from `plugin_loader`), NOT the `@register`ed
class in `app/artgen/generators/<name>.py` — for animatediff, `uses_llm =
False` had to be set on BOTH classes (the plugin.py one is the one that
actually matters at runtime; the generators/ one had already-registered
side effects too, and matches the module the rest of this doc treats as
canonical for its 3-pass-pipeline / hardware logic).

**Width discipline:** the whole surface is wrapped in
`gtk_layout.wrap_centered` (`MaxWidthBin`, extracted from `pipeline_studio`), and
every multi-item row is a wrapping `Gtk.FlowBox` — width overflow is structurally
impossible. Palette stays the tt-vscode-toolkit variant (`#4FD1C5`/`#0F2A35`).

The legacy per-model tabs / ControlPanel / ArtgenPanel remain the reachable
fallback until a real-generation smoke test on hardware; deleting them is a
separate step.

**Pipeline editor adoption (sub-project 2, v0.29.0).** The same vocabulary now
drives `RemixView`'s node field editing (`app/pipeline_studio.py`), so a field
means the same thing in Create and in Remix/Compose. `field_roles` gained a
deepened `classify_pipeline_field` (classifies a `spec_remix.ParamField` by
kind/value/key) and a pure `marker_prefix(marker)` label formatter shared by both
surfaces. In each step card, fields are classified, ordered brief -> direction ->
control, marker-prefixed (✎/✨/⚙ + tooltip), and the control fields sit under a
per-card collapsed `Gtk.Expander` "Controls (N)". Brief text fields get a
contextual `ModifierPills` (imported from `create_param_panels`; bank chosen by
`intent_for(class_type).output_kind` via `_bank_kind_for_output` -- image/video/
gif->animate, text/None->no bank), and `_collect_edits` folds each field's
`applied_text()` into its value at Run time. **Edit-contract invariant preserved:**
`_field_widgets`/`_field_meta` are populated for every field regardless of zone,
and with no retype + no applied pill a field stays out of the edit diff (untouched
run reproduces exactly). Known follow-up: `ModifierPills` re-reads
`config/prompt_chips.yaml` per render (fail-soft, uncached) on both surfaces --
cache `load_chips_for_kind` if render latency ever matters.

**In-place results (v0.31.0).** Create is a two-pane surface: the form beside a
`CreateResultPanel` (`app/create_view.py`), laid out in a `Gtk.FlowBox`
(min1/max2 per line) so it's side-by-side when wide and stacked when narrow,
inside `wrap_centered` at `_TWO_PANE_MAX_WIDTH` (1440, a true ceiling -> no
overflow). Hitting Create shows a live pending state in the panel (spinner +
elapsed), resolving in place to the finished image/video/text the instant it's
done, and prepending to a session recents strip (cap 6). This is the
[[project-see-result-immediately]] principle. Wiring: `main_window` marks a
Create-launched job with `self._create_job_active` and forwards the lifecycle to
`self._create_view._result_panel` -- native jobs via `_on_generate`'s
progress/finished/error callbacks (the gallery pending card is SKIPPED for
Create jobs, but the finished record still lands in the gallery/store, so
Discover is unchanged -- the panel is additive), artgen jobs via
`_on_create_artgen_finished`/`_fail_create_job` on the `tt-ctl` worker thread.
**Every terminal path clears the flag** -- `_fail_create_job(reason)` is called
on all `_on_generate` early returns (server busy / low disk / AnimateDiff-busy)
and on artgen failure, so the panel never stays stuck on "pending" and the
window-global flag never bleeds into an unrelated next job. Non-Create jobs
(attractor/TT-TV) never touch the panel and keep their gallery pending card.
**Per-chip progress (v0.72.0):** multi-chip AnimateDiff (remix mode) streams
each chip's log lines prefixed `chipN:` (from `_run_multi_chip` in
`artgen/generators/animatediff.py` — process-level parallelism, one 1×1
MeshDevice process per chip). `CreateResultPanel.show_progress` parses
`_CHIP_LINE_RE` and renders one live row per chip in `_pending_chip_box` (all
chips at once) below the coordinator status line, instead of the chip lines
interleaving into a single flickering label. `_chip_status` (index→latest line)
is the persistent job state so `_render_pending` restores every row on a
return-to-pending; reset on each `show_pending`. Plain (single-chip) runs never
populate it, so the box stays hidden and the classic single status line is
unchanged. (The 👁 Watch viz already showed all chips at once — this brings the
textual progress to parity.)
**Queue progress (v0.69.0):** `_start_next_queued` re-engages the panel for
each queued job (sets `_create_job_active` + `show_pending` with the medium
resolved by `_medium_for_queue_item`/`CreateView.medium_by_id`), so the panel's
pending→progress→finished keeps running through a whole Theme-Set/queue drain —
`_on_finished` clears the flag before draining, so without this only the first
job showed in the panel. `from_attractor` items are excluded (they must not
hijack the Create preview). Note: the artgen `MediaRecord` gets a `media_file_path` alias set so the
panel's renderer (which reads that name, matching `GenerationRecord`) resolves
the artifact; `MediaStore.add` reads only declared fields, so it's inert for
persistence.

**✨ Inspire — restored on every creative prompt entry (v0.50.0, regression
fix).** SP-3d-5 deleted `ControlPanel`/`ArtgenPanel` (the per-field ✦ Inspire
buttons went with them) in favor of the Create/Discover/Remix shell, but
nothing grew a replacement — Create's idea door kept an Inspire button but
(bug) always generated fresh, never reading the field first; every other
prompt entry (artgen params, pipeline step fields) had none at all. Fixed as
ONE shared implementation instead of forking it per surface:

- **`create_param_panels.attach_inspire_button(entry, prompt_type_getter,
  inspire_fn, *, label=, tooltip=)`** — the single seam. Click reads
  `entry.get_text().strip()` as the seed: empty -> fresh generation; non-empty
  -> the backend polishes/remixes those exact words. Calls
  `inspire_fn(prompt_type, seed_text, on_result, on_error)`; both callbacks
  are wrapped in `GLib.idle_add` so a same-thread test fake is as safe as a
  real background thread, and a synchronously-raising `inspire_fn` is caught
  (fail-soft). `MainWindow._create_inspire_fn(prompt_type, seed_text,
  on_result, on_error)` is the one real implementation (backed by
  `prompt_client.generate_prompt`) — every surface below reuses it, never a
  forked prompt-gen path.
- **Create idea door** (`CreateView._on_inspire_clicked`) — now reads
  `_prompt_entry`'s current text as the seed before calling `_inspire_fn`
  (previously hardcoded `""`).
- **`ArtgenParamPanel`** (`create_param_panels.py`) — takes optional
  `inspire_fn`/`prompt_type_getter` constructor params (default `None` -> no
  ✨ buttons, migration-safe). Every field whose `_ArgSpec.kind == "str"` AND
  `field_roles.classify_artgen(spec).role == ROLE_BRIEF`
  (`_artgen_field_wants_inspire`) gets a button appended inside its row
  (travels with the row if `RoleZonePanel` re-parents it). This is
  deliberately narrower than "any str field": structured/enum-like config
  strings (circuit's `--inputs`/`--gates`/`--circuit-style`, landscape's
  `--palette`) and negations (`negative_prompt`) are excluded — reusing
  `classify_artgen` as the single source of truth means the ✨ eligibility
  test can never drift from `RoleZonePanel`'s own Brief/Direction zoning.
  `classify_artgen` gained `"freeform"` (freeform's whole-prompt field) and
  `"mood"` (palette's mood/theme seed) to its recognized-creative-dest set —
  both are genuine prose the old ArtgenPanel gave Inspire to, but had fallen
  through to Direction/interpreted. `CreateView._swap_panel` threads its own
  `_inspire_fn`/`_inspire_prompt_type` into every artgen medium's panel.
- **`RemixView`** (`pipeline_studio.py`) — takes an optional `inspire_fn`
  constructor param (same default-`None` contract), threaded from
  `PipelineStudio(inspire_fn=...)` -> `MainWindow._show_pipelines` (passes
  `self._create_inspire_fn`). `_build_field_row` attaches a button to a
  BRIEF-role `kind=="text"` field (excluding `_NEGATIVE_FIELD_KEYS` —
  `negative_prompt`/`avoid`, mirroring `ModifierPills`' own exclusion, but
  WITHOUT `ModifierPills`' "needs a chip bank" requirement — Inspire is
  useful on a text-output node too). `_prompt_type_for_output(output_kind)`
  maps the owning node's `Intent.output_kind` (image/video/gif->animate) to
  the `generate_prompt()` source string, defaulting to "video".
- **Hard invariant preserved:** `collect()` (bare `ArtgenParamPanel`,
  `RoleZonePanel`-wrapped, and `RemixView._collect_edits`) is byte-for-byte
  identical whether or not `inspire_fn` was supplied — the button is
  decoration inside a field's row, never the value-bearing widget those
  methods read. Pinned by dedicated collect-equality tests (as well as the
  pre-existing `test_role_zone_panel.py` suite, unaffected by this change).

## Per-artgen-type modifier pills (v0.74.0)

Every artgen medium's Direction zone used to either inherit generic photo/
video pills or show none at all — palette, verse, ansi, landscape, codeart,
and freeform all looked the same as Image/Video's chip banks even though
none of their categories (Camera/Shot, Lighting, Motion/Mood, …) apply to a
color palette or a poem. Fixed with a per-type loader instead of forking
`ModifierPills` per medium:

- **`chip_config.load_chips_for_artgen(artgen_type, config_path=None)`**
  (`app/chip_config.py`) — chip banks for one artgen TYPE: that type's own
  categories (loaded via the existing `load_chips(tab, ...)`, just with
  `tab=artgen_type` instead of `video`/`image`/`animate`) plus the shared
  cross-type `"artgen"` mood bank, deduped by category name (type-specific
  wins on a name collision). A type with no curated categories of its own
  still gets the shared bank, so nothing renders empty.
- **YAML convention** (`config/prompt_chips.yaml`) — a category's `for:`
  list now also accepts artgen type keys (`palette`, `verse`, `ansi`,
  `landscape`, `codeart`, `freeform`) alongside the native `video`/`image`/
  `animate` keys, plus the special shared key `artgen` (currently just the
  "Feeling" category — Content/Nostalgic/Whimsical/… moods that read
  naturally across every artgen type). A category or chip omitting `for:`
  still defaults to the native tabs only (`_ALL_TABS = {video, image,
  animate}` in `load_chips`) — an artgen medium is NEVER handed a category
  meant for native mediums just because it forgot to scope itself; every
  artgen-facing category in the YAML explicitly opts in via `for:`.
- **`ChipEntry.surprise` + `chip_config.surprise_pool(category)`** — a chip
  can be declared `surprise: true` in YAML instead of a fixed `text:`
  (`text` becomes optional exactly when `surprise` is set — `load_chips`
  raises if both are missing). `surprise_pool` returns the `.text` of every
  *non*-surprise chip in that category — the pool a Surprise tap draws from.
  `ModifierPills._build_category_box` renders a `surprise=True` entry as a
  `🎲`-styled add-chip (`create-addchip-surprise` CSS class) wired to
  `_apply_surprise` instead of `_apply_entry`: `_pick_surprise(pool)` (a
  pure, GTK-free `random.choice` helper) picks one pool entry, and a fresh
  `ChipEntry` is appended to the applied-pills row so it reads as a normal
  removable pill. Unlike a regular add-chip (which hides itself once
  applied — the existing de-dup rule), the Surprise chip is NEVER hidden,
  so it stays tappable for another random pick.
- **`ModifierPills(kind, artgen=True)`** (`app/create_param_panels.py`) —
  the widget's one new constructor flag. `artgen=True` routes construction
  through `load_chips_for_artgen_kind(kind)` (a thin seam over
  `chip_config.load_chips_for_artgen`, mirroring the existing
  `load_chips_for_kind` seam for native mediums) instead of
  `load_chips_for_kind(kind)`. Everything downstream (category Expanders,
  add-chip buttons, applied-pills row, `applied_text()`) is unchanged code
  path — only which categories get loaded differs.
- **`RoleZonePanel` keys artgen mediums by their own type, not output
  `kind`.** Previously every artgen medium's Direction bank was
  `ModifierPills(medium.kind)` — palette and landscape both have
  `kind=="image"`, so they shared one generic "image" bank. Now:
  `medium.source == "artgen"` -> `ModifierPills(medium.id, artgen=True)`
  (keyed by the medium's own id, e.g. `"palette"`/`"landscape"`, each
  getting its own curated banks); every native medium is unchanged —
  `ModifierPills(medium.kind)`, `artgen=False` (the default).
- **`collect()` untouched, as always.** The pills are still pure decoration
  appended to the prompt text via `applied_text()` — no value-bearing
  widget changed shape. Pinned by the existing collect-equality suites
  (`test_role_zone_panel.py`, `test_create_param_panels.py`) plus new
  loader-call assertions for the artgen path.

## Video is Video (v0.61.0)

The video trio — Wan2.2/Mochi/SkyReels, Wan2.2-Animate, and the AnimateDiff
artgen generator — used to be three separate Create chips (Video / Animate /
AnimateDiff, each its own medium). They are now ONE **Video** medium; Animate
and AnimateDiff are *models* inside Video's scoped picker, selected the same
way Wan2.2/Mochi/SkyReels already were (`create_mediums.py` drops the
`"animate"` `Medium` and skips `"animatediff"` in `discover_mediums`'s artgen
loop — folded in, not deleted).

- **Benefit-advertising picker.** `server_manager.benefit_for(key)` /
  `display_name_for(key)` is the friendly-name/tagline seam every Video
  picker entry renders through (`CreateView`'s scoped dropdown and the Model
  door, `create_view.py`). `ServerDef.benefit`/label win when present;
  `MODEL_BENEFITS`/`MODEL_DISPLAY_NAMES` cover keys with no `ServerDef` at all
  (the synthetic `"animatediff"` entry) or whose raw label reads as an
  implementation string rather than a picker-friendly name.
- **AnimateDiff is the no-server default.** When nothing on the video/animate
  hardware group is already running, Video's scoped dropdown auto-selects
  AnimateDiff (index 0) instead of whatever `_DEFAULT_VIDEO_KEY` used to pick —
  it needs no server start, so a Create job always has an immediate path to a
  result. `_autoselect_running_model_index` checks BOTH the `"video"` and
  `"animate"` capabilities for Video (unlike other mediums' single-capability
  check) so an already-running Animate server is still preferred over the
  AnimateDiff default.
- **Animate's inputs reveal on demand.** Picking the Animate model in Video's
  scoped dropdown reveals an inline "Animate needs" section (motion video /
  character image / mode) built from the same `create_param_panels.
  build_path_picker_row`/`build_mode_toggle_row` helpers `AnimateParamPanel`
  used before the merge — no duplicated FileDialog wiring. `_collect_params()`
  folds these into the params dict ONLY when the Animate model is selected;
  every other Video/Image job's `collect()` output is byte-for-byte unchanged
  (collect-equality guard test).
  Section is hidden for every other model.
- **Routing:** `MainWindow._native_generate_args` recognizes
  `model_key == "animate"` (via `_VIDEO_MODEL_ID_TO_KEY`) and returns
  `model_source="animate"` args/kwargs (`ref_video_path`/`ref_char_path`/
  `animate_mode` pulled from the reveal-on-demand fields), routing to the same
  `AnimateGenerationWorker` the old dedicated Animate chip used — the two
  AnimateDiff code paths (native worker vs. artgen plugin) are the same
  underlying engine, so merging the chip lost no capability.
- **Retired:** the standalone `"animate"` `Medium` chip and the artgen
  `"animatediff"` chip are gone from Create's doors/possibilities wall.
  Existing artgen-`animatediff` `MediaRecord`s already in history are
  UNAFFECTED and continue to render in the artgen gallery exactly as before —
  this is a Create-surface taxonomy change, not a data-model or storage change.

## Possibilities wall (v0.53.0)

`app/possibilities.py` — `PossibilitiesWall`, a full-width "Start something"
wall mounted as the FIRST child of `CreateView`'s form column
(`CreateView.__init__`, before the doors row), one exemplar tile per
`mediums_fn()` medium. Each tile's art resolves in a three-tier priority so
the wall is never empty and never a hard dependency on shipped samples:

1. **YOUR latest** piece of that medium (`media_store` query by
   `media_type`/`generator_type`, newest first) — the wall gets richer as you
   create.
2. a **curated** sample — a record from a "demo"/favorites-style playlist
   (`curated_playlist_matcher`, default matches name substrings
   demo/sample/showcase/favorite). A future optional curated-samples `.deb`
   that drops records into the same `media_store` on install is a natural
   source for this tier; nothing in the wall hard-depends on one existing —
   it's discovered by playlist name, not a special package hook.
3. a per-kind **gradient + icon** (`poss-grad-*` CSS classes) — always works,
   no assets, so a fresh profile with an empty store still shows a full wall
   instead of a blank page.

**Seeds the composer, does not replace it.** Tapping a tile (or "✨ Surprise
me") calls `CreateView._on_possibility_picked(medium, idea)`, which only
selects the medium chip (`self._chip_buttons[medium.id].set_active(True)` —
the same "toggled" path a manual chip click takes), switches to the idea
door, and fills `_prompt_entry`'s text. It never sets a generation param
directly, so `_collect_params()` is byte-for-byte identical whether a tile
was picked or the same medium + prompt were set by hand — pinned by
`tests/test_create_view_possibilities.py::test_collect_params_unchanged_by_pick`.
Constructed defensively in `CreateView.__init__` (try/except around
`PossibilitiesWall(...)`, `self._possibilities = None` on failure, skipped in
the mount) so a wall failure (e.g. the real `media_store` singleton raising
during art resolution) can never break Create.

## Model status (single source of truth)

`app/model_status.py` — `ModelStatusService`, a **GUI-free** single source of
truth for server/model state (v0.32.0, SP-1 of the coherent-shell program).
One poll thread merges managed-server health (`server_manager.status_all`) with
the artgen port-sweep (`artgen.detect_artgen_endpoint` -> any `artgen`/`prompt`
capability server reads ready when a chat endpoint is up on any port), tracks a
`starting` state (app-initiated via `note_starting()`, plus inferred-starting
when a server's `health_url` port is open but health hasn't passed), and resolves
each `server_manager.SERVERS` key to `Status.OFF/STARTING/READY/ERROR` via the
pure `_resolve(...)`. Design notes:
- **GUI-free**: no `gi` import; `server_manager`/`artgen` imports are LAZY (inside
  the default `health_fn`/`detect_fn` callables) so the module imports standalone.
- **Injectable**: `health_fn`/`detect_fn`/`clock`/`port_probe`/`poll_interval`/
  `start_timeout` are constructor params -> tests drive `_tick()` directly with
  fakes, no threads/sleeps/sockets.
- **Lock discipline**: `_tick` does all I/O (health/detect/port probes) OUTSIDE
  `self._lock`, takes the lock only to read/mutate `_starting`/`_ready_at` and
  swap `_statuses`, and calls `_notify()` AFTER releasing (since `_notify` ->
  `snapshot()` re-acquires the non-reentrant lock). Subscribers get change-only
  notifications; a raising subscriber never breaks the loop.
- **Consumers**: `snapshot()`/`status(key)`/`subscribe(cb)` and capability helpers
  `ready_keys(cap)` (most-recently-ready first) / `starting_keys(cap)` /
  `running_or_starting(cap)`.
- **SP-2 wiring (v0.33.0):** `MainWindow` constructs + `start()`s the service on
  open, `stop()`s it in `do_close_request`, hooks `note_starting`/`note_stopping`
  at the server start/stop sites, and injects it into `CreateView`
  (`status_service=`). CreateView subscribes (poll-thread callback -> `GLib.idle_add`
  -> `_on_status_snapshot`), renders 3-state dots (◌/◐/●) via `_status_glyph` +
  `_model_dot_glyph` on both the scoped dropdown and the Model door, and
  auto-selects `running_or_starting(cap)` in `_populate_model_dropdown` (cap keyed
  by `medium.id` -- the Animate medium's `kind` is "gif"; only in the fresh-populate
  branch so a manual pick is preserved per the v0.28.1 fix). `status_service=None`
  keeps CreateView's old boolean `status_all` fallback (tests/standalone).
- **Still on their own pollers until SP-3 deletes them:** `MainWindow._health_loop`
  (footer row + statusbar), `_refresh_servers_popover` (Servers popover),
  `artgen_panel._check_health_bg`. SP-3 retires the vestiges and stands up one
  surviving status control on the service.
- **Running chat model identity (v0.47.0, 3 tasks).** The old artgen/prompt
  reconciliation marked EVERY `("artgen","prompt")`-capability key READY the
  moment any chat endpoint answered `/v1/models` anywhere — a Qwen3-8B on
  port 8002 made Qwen3-32B/Llama-3.3-70B/etc. all read "ready" too, even
  though only one was actually loaded. Fixed end to end:
  - **Task 1** — `match_model_id(detected_id, servers)` (`app/model_status.py`)
    normalizes both the detected `/v1/models` id and each candidate
    `ServerDef.model_id`/`label` (last `/`-segment, lowercased, punctuation
    stripped) and resolves the ONE `server_manager.SERVERS` key that
    detected id belongs to, or `None` if it matches nothing registered.
  - **Task 2** — `_tick()` calls `match_model_id` once per poll and only
    marks *that* key detect-healthy; every other artgen/prompt key falls
    back to its own (normally-absent) `health_fn` entry, so per-model
    readiness is now correct. The resolved identity is exposed via
    `running_artgen_model() -> ArtgenModelInfo(model_id, url, matched_key) |
    None` (`matched_key` is `None` for a model started outside this app that
    doesn't match any `ServerDef` — "something IS running, we just don't
    have a name for it").
  - **Task 3** — `CreateView` surfaces this: `_model_dot_glyph` (already
    per-key) now lights ● for only the matched server; when
    `running_artgen_model().matched_key is None`,
    `_detected_model_key()` synthesizes a `__detected__:<model_id>` sentinel
    that `_scoped_model_keys`/`_model_door_groups` inject as ONE additional
    SELECTABLE entry — labeled `"<model_id> (detected)"`, always ●, in both
    the Text/artgen scoped dropdown and the Model door's "Text" group.
    `_autoselect_running_model_index` prefers the matched key when known,
    else this synthetic entry, on a fresh medium populate (manual picks
    still survive same-medium refreshes, per the v0.28.1 fix). The sentinel
    is display/selection-only: it carries `canonical=None` in
    `_model_dropdown_entries`, and artgen mediums never have a "model" key
    in `collect()` at all, so it can never leak into a generation call —
    guarded by a collect()-equality test
    (`tests/test_create_view_detected_model.py`).

## Retiring the vestiges (SP-3, DONE — v0.46.0)

The app now rests entirely on the Create / Discover / Remix shell. The old
per-medium tabs + ControlPanel + ArtgenPanel's generation sidebar +
Generative-Art tab + duplicate server UI — all staged for deletion "only once
every capability has a new home" — are gone. Decisions honored: server
control is the compact top-bar `Servers ▾` wired to `ModelStatusService`;
seed-image/i2i, "Inspire me" prompt-gen, attractor/TT-TV launch, the
generation queue, and the status bar/server-log were all migrated (not
dropped) before their old homes were deleted.

- **SP-3a done (v0.34.0): `_on_generate` decoupled from ControlPanel.** It takes
  `video_model_key`/`image_model_key`/`animatediff_args` params and reads NO
  `self._controls.get_*` for model selection; module defaults `_DEFAULT_VIDEO_KEY`
  /`_DEFAULT_IMAGE_KEY`/`_ANIMATEDIFF_DEFAULTS` mirror ControlPanel's old defaults.
  All callers (legacy generate/enqueue, Create `_create_generate_native`, queue,
  attractor) pass the model explicitly; the Create `_controls._video_model` sync
  hack (v0.27.1) is gone. The legacy generate call site + the attractor path still
  read `_controls` (legitimately — those ARE ControlPanel-driven); they go with
  ControlPanel in SP-3d, where the attractor also needs a new model source.
- **SP-3b done (v0.35.0): standalone `ServersControl`** (`app/servers_control.py`)
  — `Servers ▾` popover (start/stop/restart, 3-state dots from the service via
  `subscribe`, not polling) + server-log, lifted out of ControlPanel and mounted
  persistently (`servers_button` in the top bar; `log_widget` on `root_box` — NOT
  under `_ctrl_wrapper`, so it survives Discover + the SP-3d delete). ControlPanel's
  `_servers_btn`/`_server_status_box`/`_srv_log` are hidden; `_refresh_servers_popover`
  poll is unreachable (2 of 3 legacy pollers effectively gone). One aggregate dot
  = the bottom `_hw_statusbar` (a `TODO(SP-3d)` marks re-pointing it at the service
  when `_health_loop` retires).
- **DECIDED: native AnimateDiff MIGRATES into Create** (never drop) — distinct
  from the artgen `animatediff` plugin (also kept). SP-3c gives Create a native
  AnimateDiff path carrying full `get_animatediff_args` config.
- **SP-3d done in stages (v0.42.0–v0.44.0):** 3d-1/3d-2 rehomed every
  SURVIVING `self._controls.*` read onto `ModelStatusService`/CreateView
  (`_current_medium_source`/`_current_medium_model_key`/
  `_running_generation_server`/`_resolve_attractor_model`, per
  `.superpowers/sdd/sp3d-audit.md` §1); 3d-3 deleted the 5 dead `set_busy(...)`
  call sites; 3d-4 collapsed the window to a 2-pane layout and folded Watch-
  TT-TV/Pipelines/Servers ▾ into the loop-nav row (ControlPanel's
  `toolbar_box`/`footer_box` are no longer mounted anywhere, though the class
  itself is still constructed).
- **SP-3d-6 done (v0.45.0), executed BEFORE 3d-5 (reorder — the legacy
  pollers call ControlPanel setters, so they had to retire first):** the
  three legacy health pollers (`_health_loop`/`_artgen_health_loop`/
  `_prompt_gen_health_loop`, each its own thread pinging a different port) are
  gone. `_hw_statusbar` is now driven entirely by
  `self._status_service.subscribe(...)` (`_on_status_snapshot`/
  `_render_status_snapshot`), resolving the `TODO(SP-3d)` marker at its
  construction site — the same aggregation policy `ServersControl` uses
  (READY > STARTING > ERROR > OFF), grouped by `server_manager.
  servers_for_capability`.
- **SP-3d-5 done (v0.46.0), the FINALE — `ControlPanel`/`ArtgenPanel`/
  medium-tabs/Gen-Art tab deleted outright.** `ControlPanel` (~2650 lines) +
  `AdvancedSettingsDialog` (its only client) + the medium-tab source toggle +
  the Generative-Art tab are gone; every remaining `self._controls.*` read
  was legacy-only (theme/inspire/prompt-gen setters, SHOT panel, startup
  pre-select) and went with it. `ArtgenPanel`'s generation sidebar (redundant
  with Create's own artgen mediums) and its `_check_health_bg` poller are
  deleted with the class; Discover's "artgen" `_gallery_stack` page is now
  the standalone `ArtgenGallery` it always wrapped (`self._artgen_gallery`),
  wired identically to the three native `GalleryWidget`s. `_on_source_change`
  is replaced by `_sync_gallery_to_source`/`_uncheck_pipelines_toggle_if_active`
  — same gallery-switch/context-menu/pipelines-toggle behavior, minus the
  ControlPanel-era "collapse the left/right panes for artgen" special case
  (moot now that "artgen" is a plain gallery page, not a wide sidebar+preview
  layout). Orphaned module-level dicts (`_MODEL_TO_SOURCE`/`_MODEL_TO_VIDEO_KEY`/
  `_MODEL_DISPLAY_SERVER`/`_MODEL_TO_SERVER_KEY`/`_MODEL_TO_CAP`/
  `_MODEL_TO_IMAGE_KEY`) removed too.
  - **ACCEPTED, FLAGGED loss:** artgen **auto-generate** (`art-autogen`/
    `art-autogen-delay` menu actions, `ArtgenPanel.toggle_auto_gen`/
    `set_auto_gen_delay`) is gone — ArtgenPanel-sidebar-only, overlapped the
    surviving TT-TV attractor. Recoverable from git if wanted back.
  - **DISCOVERED GAP the SP-3d audit missed:** the quick "🔀 Remix" popover
    (`RemixPopover` → `MainWindow._dispatch_remix` →
    `remix_dispatch.dispatch_remix`, a pre-Create-shell feature from
    `docs/superpowers/specs/2026-05-26-remix-ui-design.md`) depended on
    `ControlPanel.switch_to_source`/`populate_prompts` and
    `ArtgenPanel.set_generator`/`set_theme`. `_dispatch_remix` now opens
    Pipeline Studio's Muse seeded with the popover's resolved artifact instead
    (the same bridge "🧩 Remix as pipeline…" uses) rather than leave a
    dangling call into deleted classes — an ACCEPTED, FLAGGED UX regression
    (loses the popover's own target-type switch + inline single-step
    regenerate). `remix_dispatch.dispatch_remix` itself is untouched and still
    unit-tested, just no longer called from `main_window.py`.
  - **ANOTHER GAP the SP-3d audit missed, FIXED in v0.47.2:** "wired
    identically to the three native `GalleryWidget`s" (line above) was true
    for `on_remix`/`on_remix_as_pipeline` but NOT for `on_card_activated` —
    `main_window.py` never set it on `self._artgen_gallery`, so clicking an
    artgen card silently did nothing (the native galleries' click path goes
    through `DetailPanel`, which can't render artgen content anyway — SVG/
    ANSI/palette/markdown). This orphaned `artgen_detail.py`/`ArtgenDetail`
    entirely (no test file existed for it either). Fixed by making
    `ArtgenGallery` self-contained again: it now owns an internal grid/detail
    `Gtk.Stack` and un-orphans `ArtgenDetail` as its own in-page preview
    (`_on_card_activated` defaults to `show_record` + switching the stack,
    still calling any externally-wired `on_card_activated` additively;
    detail delete/star/remix/remix-as-pipeline route back onto the gallery's
    existing hover-action behavior). No `main_window.py` change was needed —
    `on_remix`/`on_remix_as_pipeline` already flowed through;
    `on_card_activated`/`on_card_deleted` staying unset degrades gracefully.
    See `tests/test_artgen_gallery_preview.py`.
  - Full details, every deleted symbol's grep-clean proof, and every test
    file touched: `.superpowers/sdd/task-5-report.md`.

## Version discipline

**Always increment the version when landing changes.** The version in `VERSION`
(at repo root) is the single source of truth — it drives the `.deb` package
version and `tt-ctl --version`. Without a bump, the CI build produces a `.deb`
with the same version string as the previous release, making releases
indistinguishable and `apt` upgrades silent no-ops.

- Patch bump (`0.2.1` → `0.2.2`): bug fixes, docs, word bank additions, any
  non-breaking change.
- Minor bump (`0.2.x` → `0.3.0`): new user-visible feature or UI change.
- Major bump: breaking change to config, API, or install layout.

When bumping:
1. Edit `VERSION` (single line, no prefix).
2. Prepend a new stanza to `debian/changelog` (use `dch` or edit manually).
3. Commit both files together on a dedicated `bump/version-X.Y.Z` branch and
   open a PR — version bumps should be their own commit so the git log is
   unambiguous about what shipped when.

## Running the app

```bash
./tt-gen                                            # recommended launcher
/usr/bin/python3 app/main.py [--server http://localhost:8000]  # direct
```

Use the **system** python3 (`/usr/bin/python3`), not a venv. GTK4 bindings
(`python3-gi`) are installed as system packages and are invisible inside venvs.

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0  # if missing
```

## Starting / stopping the inference server

From the GUI, use the **Servers ▾** toolbar dropdown or the **▶ Start** / **■ Stop**
buttons in the server control row. Start is context-aware: Video tab starts
`start_wan_qb2.sh` (QB2/P300x2), `start_mochi.sh`, or `start_skyreels.sh` depending
on the selected video model; Animate tab starts `start_animate.sh`; Image tab starts
`start_flux.sh`. Script output streams into a collapsible log panel that closes when
the health check confirms the server is ready.

From the terminal (all scripts are in `bin/`):

```bash
cd ~/code/tt-local-generator
./bin/start_wan_qb2.sh                       # Wan2.2-T2V on QB2 (P300x2)
./bin/start_wan_qb2.sh --stop                # stop the running server container
./bin/start_wan.sh                           # Wan2.2-T2V on P150x4
./bin/start_skyreels_i2v.sh                  # SkyReels-V2-I2V-14B-540P on QB2
./bin/start_animate.sh                       # Wan2.2-Animate-14B on QB2 (P300x2)
./bin/start_mochi.sh                         # Mochi-1 on QB2 (weights needed)
./bin/start_flux.sh                          # FLUX.1-schnell image server on QB2
./bin/start_sdxl.sh                          # SDXL via cpp_server backend on QB2
./bin/start_artgen.sh                        # Artgen LLM (Qwen3-8B default, port 8002)
./bin/start_artgen.sh --model Llama-3.3-70B-Instruct   # 70B artgen LLM
./bin/start_artgen.sh --model Qwen3-32B                # 32B artgen LLM
./bin/start_prompt_gen.sh                    # Qwen3-0.6B prompt server (CPU, port 8001)
```

Or via the CLI:

```bash
./tt-ctl start wan2.2          # non-blocking; same as start_wan_qb2.sh --gui
./tt-ctl stop  wan2.2
./tt-ctl start all             # wan2.2 + prompt-server (QB2 / P300X2 recommended set)
./tt-ctl start --single-chip    # artgen-qwen3-8b + prompt-server (single Blackhole card or CPU-only)
./tt-ctl servers               # live health of every managed service
```

All scripts accept `--gui` (non-blocking, skips the interactive tail).
The server is ready when the log prints `Application startup complete`.

### Animate mode (Wan2.2-Animate-14B)

The **💃 Animate** source toggle activates Wan2.2-Animate-14B, a video-to-video
character animation model. Unlike the text-to-video T2V mode, it requires:

- **Motion video** — an MP4 supplying the motion pattern
- **Character image** — PNG/JPG of the character to animate
- **Mode** — `animation` (character mimics the motion) or `replacement` (character
  replaces the person in the video)

The text prompt is optional (style guidance only). `start_animate.sh` binds the
modified `tt-media-server` files from `~/code/tt-inference-server/tt-media-server/`
into the container and upgrades `diffusers>=0.34.0` before starting uvicorn
(Phase 1: Diffusers CPU/CUDA path — TT hardware support pending).

### SkyReels mode (SkyReels-V2-DF-1.3B-540P)

The **SkyReels** video model button selects SkyReels-V2-DF-1.3B-540P, a fast
diffusion transformer that runs on **Blackhole** hardware (P150X4 or P300X2).
Key parameters:

- **Resolution** — 480×272 (540P) native
- **Frame count** — configurable: 9 / 33 / 65 / 97 frames (Preferences → SkyReels)
  Valid counts follow `(N-1) % 4 == 0`. Default: 33 frames (~1.4 s at 24 fps).
- **`skyreels_num_frames`** setting in `app_settings.py` / Preferences dialog.
- `GenerationWorker` accepts `num_frames=` and forwards it to `api_client`.
- `start_skyreels.sh` requires `apply_patches.sh` to be run first (Step 6 appends
  the SkyReels T2V/I2V entries to the 0.18.0 YAML catalog,
  `workflows/model_specs/dev/video.yaml`, and copies runner patches). Prior to
  0.18.0 this injected a `ModelSpecTemplate(...)` into `model_spec.py` directly;
  that file is no longer the registry's source of truth — see the "Vendored
  tt-inference-server" section below.

## Directory layout

All Python source lives in `app/`, shell scripts in `bin/`.

```
tt-local-generator/
  app/                   ← Python source
  bin/                   ← shell scripts (start_*.sh, apply_patches.sh)
  patches/               ← hotpatch files applied by bin/apply_patches.sh
  vendor/                ← shallow clone of tt-inference-server (gitignored)
  docker/                ← Docker image archive (Git LFS, ~7.4 GB)
  tests/                 ← pytest test suite (107 tests)
  tt-gen                 ← GUI launcher
  tt-ctl                 ← CLI (status, history, start/stop services)
```

## Architecture

| File | Purpose |
|---|---|
| `app/main.py` | `Gtk.Application` entry point |
| `app/main_window.py` | All GTK4 widgets and `MainWindow` |
| `app/worker.py` | `GenerationWorker` — pure Python, no GUI imports |
| `app/api_client.py` | HTTP client for the inference server |
| `app/server_manager.py` | Start/stop/health for all managed services (no GTK) |
| `app/history_store.py` | Persistent JSON history + file path management |

`worker.py`, `api_client.py`, `server_manager.py`, and `history_store.py` have
**zero GUI dependencies** — keep them that way.

## Server management (`server_manager.py`)

`app/server_manager.py` is the single source of truth for all managed services.
It is imported by both `tt-ctl` and `main_window.py`. Add new services there by
adding a `ServerDef` to `SERVERS`. Current services: `wan2.2`, `mochi`, `skyreels`,
`flux`, `animate`, `prompt-server`. The key `"all"` starts the recommended set
(`wan2.2` + `prompt-server`).

```python
from server_manager import start, stop, restart, health, status_all, SERVERS

start("wan2.2")           # launch Wan2.2 server (non-blocking --gui mode)
stop("prompt-server")     # send --stop to the prompt-gen script
health("wan2.2")          # {"wan2.2": True/False}
status_all()              # {"wan2.2": True, "prompt-server": False, ...}
```

Path resolution: `_REPO_ROOT = Path(__file__).resolve().parent.parent` (app/ → repo root).
All script paths are `_BIN / sdef.script` where `_BIN = _REPO_ROOT / "bin"`.

## GTK threading discipline (CRITICAL)

GTK is strictly single-threaded. **Never call any GTK method from a background
thread.** Doing so causes silent data corruption or hard crashes that are
difficult to debug.

### The rule

Every UI update from a worker thread must be posted to the main thread via:

```python
GLib.idle_add(callback, *args)
```

`idle_add` schedules `callback(*args)` to run on the GLib main loop (main
thread) at the next idle moment. The callback **must return `False`** (or
`GLib.SOURCE_REMOVE`) to run once; return `True` to keep repeating.

### Pattern used in this app

`GenerationWorker.run_with_callbacks()` takes three plain Python callables.
`MainWindow` wraps each one in `GLib.idle_add` when it passes them in:

```python
gen.run_with_callbacks(
    on_progress=lambda msg: GLib.idle_add(self._on_progress, msg, pending),
    on_finished=lambda rec: GLib.idle_add(self._on_finished, rec),
    on_error=lambda msg:    GLib.idle_add(self._on_error, msg),
)
```

The `_on_progress`, `_on_finished`, `_on_error` methods then touch widgets
freely because they run on the main thread.

### GLib.timeout_add

`PendingCard` uses `GLib.timeout_add(1000, self._tick)` for the elapsed-time
counter. This fires on the main thread — no `idle_add` needed inside `_tick`.
Cancel it with `GLib.source_remove(timer_id)` when the card is replaced.

### Health worker

The health-check loop uses `threading.Thread` + `daemon=True`. It posts results
via `GLib.idle_add(self._on_health_result, ready)`. The `_health_stop` event
lets `do_close_request` cleanly signal the thread to exit.

## FileDialog (GTK4 async API)

GTK4's `Gtk.FileDialog` is async — it takes a callback, not a return value:

```python
dlg = Gtk.FileDialog()
dlg.open(parent_window, cancellable, callback)  # returns immediately

def callback(dlg, result):
    try:
        gfile = dlg.open_finish(result)
    except Exception:
        return   # user cancelled
    path = gfile.get_path()
```

Always wrap `open_finish` / `save_finish` in try/except — they raise if the
user cancels.

## Queue system

`MainWindow._queue` is a `list[_QueueItem]`. After `_on_finished` runs,
`_start_next_queued()` pops the front item and calls `_on_generate()` directly.
`ControlPanel.update_queue_display()` rebuilds the visible list; call it from
the main thread only (always safe since queue mutations happen in response to
button clicks or `_on_finished`).

## PyGObject gotchas

- **No `set_data`/`get_data` on widgets**: PyGObject deliberately blocks GObject's
  C-level data methods. Store arbitrary Python values as plain attributes instead:
  ```python
  cb.job = job_dict   # yes
  cb.set_data("job", job_dict)  # RuntimeError
  ```

## Assets

`app/assets/` contains:
- `tenstorrent.png` — 32×32 app icon (pulled from tenstorrent.com/favicon.ico)
- `ai.tenstorrent.tt-video-gen.desktop` — XDG desktop entry for GNOME/KDE launchers

`setup_ubuntu.sh` copies both into the correct XDG locations automatically.
To install manually:
```bash
cp app/assets/tenstorrent.png ~/.local/share/icons/hicolor/32x32/apps/ai.tenstorrent.tt-video-gen.png
cp app/assets/ai.tenstorrent.tt-video-gen.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications
```

## Video hover / looping

`Gtk.Video.set_loop(True)` is unreliable when playback is driven by calling
`get_media_stream().play()` directly — it bypasses GTK's internal
`notify::ended` → seek(0) → play() loop restart logic.

**Fix in place**: `GenerationCard._play_hover_stream()` lazily connects a
`notify::ended` handler (`_on_stream_ended`) the first time a stream is played,
then manually seeks to 0 and restarts. `_loop_connected` guards against double-
connecting.

The stream itself is created lazily by GStreamer and `get_media_stream()` returns
`None` until the `Gtk.Video` widget has been realized. `_play_hover_stream()`
retries via `GLib.timeout_add(100, ...)` if the stream is not yet available.

## GTK Application single-instance behaviour

`Gtk.Application` uses D-Bus to enforce a single instance per `application_id`
by default. If any process has already registered `ai.tenstorrent.tt-video-gen`
on the session bus, a second `./tt-gen` invocation silently exits (code 0)
without ever firing `activate`.

**Fix in place**: `main.py` calls `app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)`
so every launch is independent. If the app is not opening, also check for a
stale process: `pgrep -a python3 | grep main.py`.

## Stale .pyc cache

If the app crashes with a traceback pointing to a line number that doesn't
match the source, the bytecode cache is stale (e.g. from an earlier version).
Clear it with:
```bash
find ~/code/tt-local-generator/app -name "*.pyc" -delete
find ~/code/tt-local-generator/app -name "__pycache__" -type d -exec rm -rf {} +
```

## Running tests

```bash
# Full suite — xvfb-run provides a virtual X11 display so GTK widget tests run
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q

# Headless fallback (no display available) — GTK widget tests are skipped
/usr/bin/python3 -m pytest tests/ -q
```

`xvfb-run` is pre-installed on Ubuntu 24.04 (`apt install xvfb` if missing).
Three pre-existing, environment-level flakes are expected and should be
deselected in full-suite runs (all three pass in isolation / are unrelated to
app code): `test_forge_transforms::test_on_transform_finished_appends_and_refreshes`,
`test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module` (a
`cffi`/`cairosvg` version-mismatch that only surfaces under full-suite import
ordering), and `test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen`
(confirmed by multiple reviewers this session to reproduce on the base branch,
unrelated to app changes — passes in isolation, fails only under full-suite
import ordering). Plus one environment skip (`test_regression_guards` when
`docs/assets/` is absent).

Tests are in `tests/` at repo root. Each file does `sys.path.insert(0, str(Path(__file__).parent.parent / "app"))` to import from `app/`. Tests mock all subprocess and network calls.

## Vendored `tt-inference-server`

`vendor/tt-inference-server/` is a shallow git clone of the upstream repo (gitignored due to 143 GB working tree). The pinned commit SHA is in `vendor/VENDOR_SHA`.

**Pinned at v0.19.0** (`399ce0b`, since v0.76.0). v0.19.0 is an **LLM-only**
point release (Llama-3.1-8B P300 uplift, new vLLM image) — the MEDIA catalog
(`video.yaml`/`image.yaml`/`model_spec.py`) is byte-identical to 0.18.0, so the
media Docker image stays `tt-media-inference-server:0.18.0-c49bb76` and the media
bind-mount patches were NOT rebased. Only the **artgen vLLM** image preference
moved (`start_artgen.sh` → `0.19.0-b204341-9bd099c`, older tags kept as
fallbacks).

```bash
cat vendor/VENDOR_SHA            # see what's pinned
./bin/apply_patches.sh           # apply patches/ to vendor/
```

The `.env` file at `vendor/tt-inference-server/.env` is passed to Docker containers via `--env-file`. Key variables:
- `TT_DIT_CACHE_DIR` — caches compiled TT weights across container restarts (~66 GB after first run)
- `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1` — prevents HF network access during startup (weights are bind-mounted from host cache)

The `patches/` directory contains:
- `patches/media_server_config/config/constants.py` — overrides P300X2 device config, request timeouts, adds missing v0.17.0 symbols (CANARY_TASK_IDS etc.)
- `patches/media_server_config/tt_model_runners/dit_runners.py` — adds TTWan22AnimateRunner, SkyReels log-map entries, Mochi cache-dir env, Flux trace-region bump
- `patches/media_server_config/tt_model_runners/runner_fabric.py` — routes SkyReels and Animate model runners
- `patches/media_server_config/tt_model_runners/skyreels_runner.py` / `skyreels_i2v_runner.py` — SkyReels T2V and I2V runners
- `patches/media_server_config/domain/video_generate_request.py` — request-model extensions
- `patches/tt_dit/` — pipeline fixes (bind-mounted only in dev_mode)

### Model registry migrated to YAML in 0.18.0

0.18.0 replaced the inline-Python `ModelSpecTemplate(...)` list in
`workflows/model_spec.py` with YAML catalogs under
`workflows/model_specs/{prod,dev}/*.yaml`, loaded by `load_templates_from_yaml()`.
`--dev-mode` (used by all `start_*.sh` scripts) sets `MODEL_SPECS_ENV=dev`, so the
catalog actually consulted is `dev/*.yaml` (video models → `dev/video.yaml`).

**Consequence:** `apply_patches.sh`'s old text-injection anchor in `model_spec.py`
(`"]\n\n# ... image_templates"`) no longer exists — `model_spec.py` isn't read
for video models anymore. This broke SkyReels registration after the 0.18.0
upgrade: the old Step 6/7 printed "ERROR: could not find insertion anchor" and
SkyReels never registered, so `run.py --model SkyReels-V2-I2V-14B-540P` said
"invalid choice". The fix (Step 6 in `apply_patches.sh`) now appends
the SkyReels T2V/I2V entries directly to `dev/video.yaml` as YAML text — same
idempotent pattern (skip if the weights string is already present), just a
different target file and format. `MODEL_SPEC_YAML` points at that file.

**Steps 7-9 repaired in v0.76.0.** Step 7 (Wan2.2-Animate) was rewritten to
append to `dev/video.yaml` exactly like Step 6 (idempotent skip-if-weights-
present guard, same `MODEL_SPEC_YAML` target) instead of the dead
`model_spec.py` `ModelSpecTemplate(...)` injection. Steps 8 (DeepSeek P300X2
version bump) and 9 (SDXL version bump) were **retired outright** — their
`model_spec.py` anchors no longer exist in the YAML era and neither model is
surfaced today. If either needs re-registering later, give it the same
`video.yaml`/`image.yaml` append treatment as Steps 6/7 rather than reviving the
`model_spec.py` anchor.

### Media model expansion (v0.76.0): Wan2.2-I2V + FLUX.1-dev

Both models are already in the shipped upstream catalog (P300X2, COMPLETE), so
neither needs a patch — the work was app wiring + a start script + a weights
`.deb` each.

- **Wan2.2-I2V-A14B** (image-to-video) — `server_manager` key `wan2.2-i2v`
  (cap `video`, `runner_key="tt-wan2.2-i2v"`), `bin/start_wan_i2v.sh` (modeled on
  `start_wan_qb2.sh`: non-dev mode, media image `0.18.0-c49bb76`, P300X2 — it's
  in-catalog so needs no bind-mount patch, unlike SkyReels-I2V). Lives in the
  **Video** picker beside SkyReels-I2V and reuses the same seed-image guard
  (`_native_generate_args` raises `_NativeGenerateGuardError` for
  `("skyreels", "wan2.2-i2v")` when no seed image). Weights: `tt-model-wan2-i2v`
  (ungated).
- **FLUX.1-dev** (higher-fidelity image) — `server_manager` key `flux-dev`
  (cap `image`, `runner_key="tt-flux.1-dev"`), `bin/start_flux_dev.sh` (a thin
  wrapper: `exec start_flux.sh --dev "$@"` — `start_flux.sh` already supported
  `--dev`). In the **Image** picker beside FLUX.1-schnell. No per-model step/
  guidance branching — the app's existing image default (20 steps / guidance
  3.5) is already dev-appropriate, which keeps `collect()` byte-identical.
  Weights: `tt-model-flux-dev` (gated). The FLUX `.deb`s were reconciled so each
  maps to its server: `tt-model-flux` → ungated FLUX.1-schnell,
  `tt-model-flux-dev` → gated FLUX.1-dev (previously `tt-model-flux` confusingly
  downloaded FLUX.1-dev).

Both `_VIDEO_MODEL_IDS`/`_IMAGE_MODEL_IDS` maps are duplicated in
`main_window.py` AND `create_param_panels.py` — new keys must be added to BOTH
(inverse `*_ID_TO_KEY` maps are derived). `pipeline_engine._backend_for` gained
`_match_server_key()` (exact-match pass BEFORE substring-sniffing) so `flux`
can't shadow `flux-dev` and `wan2.2` can't shadow `wan2.2-i2v`.

**Both `runner_key`s (`tt-wan2.2-i2v`, `tt-flux.1-dev`) are taken from the
vendored `ModelRunners` enum + `runner_fabric.py` but are HARDWARE-CONFIRM-
PENDING** — on QB2, start each server and `curl /tt-liveness`; if `runner_in_use`
differs, update the `ServerDef.runner_key` (a wrong value silently reads the
server as unhealthy). Nothing in this expansion was validatable from the dev
session — the automated tests cover wiring only (ServerDef present, picker lists
it, routing maps it, collect() unchanged), never actual generation.

### Patch verification (v0.77.0) — fail loud on drift

Silent patch drift is what let `apply_patches.sh` Steps 7/8/9 rot undetected
after the 0.18.0 YAML migration. The fix is a **fail-loud verification harness**,
not a change to the bind-mount strategy (bind-mounting whole new modules is
correct per tt-vscode-toolkit's `monkeypatch-ttnn.md`, which cites this repo as
its canonical example):

- **`app/patch_manifest.py`** (pure/stdlib) — the single declarative source of
  truth: one `PatchEntry` per patch/injector step. `inject`/`append` entries are
  hand-declared with their anchors; `bind_mount` entries are auto-discovered by
  walking `patches/{media_server_config,tt_dit,models}` with the same dest
  formula the mount loops use. 18 entries today; `manifest_issues()` is the
  internal-consistency check.
- **`app/patch_verify.py`** (pure/stdlib) — host-side probes borrowing the
  toolkit's `PatchError`/`version_at_most`/`verify` philosophy (NOT its
  in-process `wrap`/`set_default` — we add no container hook). Per kind: `inject`
  → anchor string present in the vendored target; `append` → target file exists
  (the model_spec.py→video.yaml move IS the drift); `bind_mount` → patch source
  exists + `py_compile`s, with a soft `version_ceiling` "may be absorbed" warning.
  CLI: `python3 app/patch_verify.py --vendor <tree>` (exits non-zero on drift) /
  `--manifest-only`.
- **`apply_patches.sh` gates on it** up front (Step 0) — a drifted anchor aborts
  the whole run loudly instead of half-patching. (The per-step inject aborts
  already existed; this adds all-checks-up-front + bind-mount coverage.)
- **Build path matched:** CI (`release-deb.yml`) now **applies AND verifies**
  patches after the vendor snapshot, so the shipped `.deb` vendor is actually
  patched (it previously shipped **unpatched** — nothing on the packaged path ran
  `apply_patches.sh`). `debian/rules` has a verify-before-ship gate (+ a
  `tt_dit_patches_dir` marker grep asserting the injects were applied). The two
  new `app/` modules package for free via the existing `cp -r app …`.
  `snapshot_vendor.sh` stamps `vendor/VENDOR_VERSION` (the `version_at_most`
  input); `quickstart.sh` surfaces drift status.
- **Deferred (declared hooks, not built):** true image-diff drift detection
  (`docker create/cp` the media image, diff each `bind_mount` patch against
  upstream to catch *moved*/*absorbed* patches) plugs into the manifest's
  `bind_mount` `dest`/`version_ceiling` fields. See
  `docs/superpowers/specs/2026-08-10-patch-verification-harness-design.md`.

### Patch philosophy — minimize divergence from upstream

**Goal: always use the latest and greatest features in each tt-inference-server release.**
Patches are a compatibility shim, not a fork. Keep the surface area as small as possible:

- **Rebase patches onto each new image version.** When upgrading the Docker image,
  diff the new image's files against the current patch and drop any lines that are
  now in upstream. `docker create <new-image> && docker cp … /tmp/` to extract files.
- **Never copy-and-modify upstream runners whole-cloth.** Add only what is missing
  (new runner class, log-map entry, env var, constant override). Everything else
  stays as the image shipped it.
- **The canonical check:** `diff <image-extracted-file> patches/…/<file>` should
  show only the lines we intentionally added. Anything else is drift that should be
  removed.
- **Sync patches/ → vendor/ after every edit.** `apply_patches.sh` does this, but
  if you edit a patch file by hand, also `cp patches/… vendor/tt-inference-server/patches/…`
  immediately — the bind-mount uses the *vendor* copy, not the *patches/* copy.

## Prompt generator

A three-tier algorithmic prompt generator lives alongside the UI. It runs
independently of the video server and works even when no TT hardware is
available.

### Files

| File | Purpose |
|---|---|
| `app/generate_prompt.py` | CLI generator — algo → Markov → LLM polish |
| `app/word_banks.py` | All word banks as Python lists + sampling helpers |
| `app/prompt_server.py` | FastAPI server exposing Qwen3-0.6B on port 8001 |
| `bin/start_prompt_gen.sh` | Start/stop the prompt server |
| `app/prompts/prompt_generator.md` | System prompt for interactive LLM use |
| `app/prompts/markov_seed.txt` | Seed corpus for the Markov chain (tagged by type) |
| `app/prompts/markov_output.txt` | Accumulate good outputs here to grow the corpus |

### Three-tier design

**Tier 1 — Algorithmic** (`--mode algo`, always available):
`word_banks.py` contains every category as a Python list. `generate_prompt.py`
calls `random.choice()` on each slot independently. Selection happens in code,
not by the LLM, so diversity is guaranteed regardless of model size.

**Tier 2 — Markov** (`--mode markov`, requires `markovify`):
Trained on `prompts/markov_seed.txt` (and `markov_output.txt` if it exists).
Produces novel sentence-level recombinations — useful for unexpected register
collisions. Falls back to algo if the corpus is too small or markovify isn't
installed.

**Tier 3 — LLM polish** (`--enhance`, default on):
Sends the tier-1/2 slug to Qwen3-0.6B (port 8001) with a short polishing
prompt. The LLM only makes the output flow naturally — it does not re-select
elements. Falls back gracefully (returns the raw slug) if the server is down.

### CLI usage

```bash
# Default: algo + LLM polish, video type
python3 app/generate_prompt.py

# Markov mode, image type
python3 app/generate_prompt.py --type image --mode markov

# Algo only, no LLM, five prompts
python3 app/generate_prompt.py --count 5 --no-enhance

# Plain text output (no JSON wrapper)
python3 app/generate_prompt.py --raw

# All types
python3 app/generate_prompt.py --type video      # for Wan2.2 / Mochi
python3 app/generate_prompt.py --type image      # for FLUX / SD
python3 app/generate_prompt.py --type animate    # for Wan2.2-Animate
python3 app/generate_prompt.py --type skyreels   # for SkyReels-V2
```

### JSON output schema

```json
{
  "prompt": "Final polished prompt string",
  "type":   "video" | "image" | "animate" | "skyreels",
  "source": "llm" | "markov" | "algo",
  "slug":   "Raw pre-polish slug (always present)"
}
```

### Starting the prompt server

```bash
./bin/start_prompt_gen.sh          # start in background, wait for ready
./bin/start_prompt_gen.sh --stop   # stop
./bin/start_prompt_gen.sh --gui    # start silently (no tail, for GUI use)
# Or: ./tt-ctl start prompt-server
```

The server loads Qwen3-0.6B on CPU (~2.9 GB RSS, ~19 tok/s on Ryzen 7 9700X).
It runs on port 8001 and does not touch the TT chips, so it coexists with any
video generation server on port 8000.

Health check: `curl -s http://localhost:8001/health`
→ `{"status":"ok","model_ready":true}`

### Wiring into the UI

The generator is a standalone subprocess — the UI calls it and parses JSON.

**Minimal integration** (one prompt on demand):

```python
import subprocess, json

def generate_prompt(prompt_type="video", mode="markov"):
    result = subprocess.run(
        [
            "python3",
            "/home/ttuser/code/tt-local-generator/app/generate_prompt.py",
            "--type", prompt_type,
            "--mode", mode,
        ],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)["prompt"]
```

**Threading** — run the subprocess in a background thread (not the GTK main
thread). Post the result back with `GLib.idle_add` per the GTK threading rule
above:

```python
import threading
from gi.repository import GLib

def _fetch_prompt_async(prompt_entry, prompt_type="video"):
    def worker():
        prompt = generate_prompt(prompt_type)
        if prompt:
            GLib.idle_add(prompt_entry.set_text, prompt)
    threading.Thread(target=worker, daemon=True).start()
```

**Auto-start the server** (optional): call `start_prompt_gen.sh --gui` from the
app startup sequence (same pattern as the video server). Poll `/health` until
`model_ready` is true before enabling the "✨ Generate prompt" button.

**Prompt type mapping**:

| UI tab / source | `--type` |
|---|---|
| Video (Wan2.2, Mochi) | `video` |
| Video (SkyReels) | `skyreels` |
| Image (FLUX, SD) | `image` |
| Animate (Wan2.2-Animate) | `animate` |

### Growing the Markov corpus

Append good generated prompts to `prompts/markov_output.txt` in the same
tagged format (`video|...`, `image|...`, `animate|...`). The model is rebuilt
fresh on each `generate_prompt.py` run, so additions take effect immediately.
This file is intentionally gitignored — it accumulates machine-specific history.

### Extending the word banks

Edit `word_banks.py` directly — add entries to any list. More unusual / specific
entries outperform common ones (the model anchors to surprising items). After
editing, the changes take effect on the next `generate_prompt.py` call with no
restart needed. The `prompts/prompt_generator.md` system prompt is separate and
used only for interactive LLM chat (not by `generate_prompt.py`).

---

## Known issues / history

- **ffmpeg stdin hang**: ffmpeg inherited terminal stdin from the process and
  blocked waiting for `[q]`. Fixed by passing `stdin=subprocess.DEVNULL` in
  `_extract_thumbnail`. Also add `-update 1` to avoid image-sequence warnings.

- **Inference server interactive prompt**: `setup_host.py` globs snapshot root
  for `model*.safetensors`; Wan2.2 weights live in subdirectories so the check
  always fails and prompts interactively. Fixed in `start_wan.sh` by setting
  `MODEL_SOURCE=huggingface` and `JWT_SECRET` env vars.

- **Wrong entry point**: the correct entry is `python3 run.py` in the
  `tt-inference-server` repo, not `python3 -m workflows.run_workflows`
  (that module imports `benchmarking` which isn't on the path).

- **Prompt server shows "algo only" from remote Mac**: `start_prompt_gen.sh`
  was hardcoding `--host 127.0.0.1`, binding the server to loopback only.
  Connections from a Mac client via `--server http://quietbox:8000` were
  refused at the network level. Fixed by changing to `--host 0.0.0.0`
  (configurable via `PROMPT_HOST` env var). Restart the server on quietbox
  after pulling this change.

---

## .deb packaging (Ubuntu 24.04)

**What happened:** Analysed dependency taxonomy and implemented full `debian/`
packaging infrastructure for Ubuntu 24.04 (Noble).

**Original prompt:** "Analyse what it would take to package tt-local-generator
as a .deb for Ubuntu 24.04, with embedded tt-inference-server. Identify which
deps fit dpkg, which can reference tt-installer, and how to communicate deps
outside both ecosystems."

### Files added

| File | Purpose |
|---|---|
| `debian/control` | Package metadata, Depends/Recommends/Suggests |
| `debian/rules` | debhelper build rules (dh 13) |
| `debian/postinst` | Docker CE apt setup, pip extras, .env seed, image pull, checklist |
| `debian/prerm` | Stop managed services before removal |
| `debian/conffiles` | Mark vendor .env as user-editable (preserved on upgrade) |
| `debian/changelog` | Debian changelog (version 0.1.0, noble) |
| `debian/compat` | debhelper compat level 13 |
| `debian/copyright` | Apache-2.0 copyright declaration |
| `bin/snapshot_vendor.sh` | Snapshot Python-only files from tt-inference-server into vendor/ |

### Files modified

- `app/assets/ai.tenstorrent.tt-video-gen.desktop` — `Exec=` updated from
  hardcoded `~/code/…/tt-gen` to `/usr/bin/tt-local-gen`

### Dependency taxonomy summary

- **Tier 1 (dpkg):** python3-gi, python3-requests, ffmpeg, GStreamer stack, gir1.2-gtk-4.0
- **Tier 2 (external apt):** docker-ce — added by postinst; `Recommends: docker-ce | docker.io`
- **Tier 3 (pip-only):** markovify — installed by postinst via `pip --break-system-packages`
- **Tier 4 (tt-installer):** torch, transformers, ttkmd — `Recommends: tt-installer`; prompt-server warns if absent
- **Tier 5 (out-of-band):** Docker image (~15 GB, pulled by postinst), Wan2.2 weights (~118 GB, checklist item)

### Build command (run on QB2 target)

```bash
# 1. Snapshot the vendor Python files
./bin/snapshot_vendor.sh --src ~/code/tt-inference-server

# 2. Build the .deb
dpkg-buildpackage -us -uc -b

# 3. Lint
lintian ../tt-local-generator_0.1.0_amd64.deb

# 4. Install
sudo apt install ../tt-local-generator_0.1.0_amd64.deb
```

### Known issues / next steps

- **`snapshot_vendor.sh` placeholder SHA:** `DEFAULT_SHA` in `bin/snapshot_vendor.sh`
  is a placeholder. Replace with the real git SHA of the `0.15.0-25891d3` image's
  source commit before building for distribution.
- **`vendor/VENDOR_SHA`:** The `vendor/` directory is gitignored. Either remove
  the gitignore entry before a release build, or run `snapshot_vendor.sh` as part
  of the CI pipeline.
- **`debian/compat` vs `debhelper-compat` in control:** Both declare compat 13.
  debhelper ≥ 12 recommends using only the `Build-Depends: debhelper-compat (= 13)`
  form; the `debian/compat` file is kept for compatibility with older toolchains.
- **Testing:** Active install testing must happen on QB2 (Ubuntu 24.04 amd64).
  The Mac dev machine cannot run `dpkg-buildpackage` natively.

---

## .deb model packages (0.2.0)

**What happened:** Added four binary model-download packages (`tt-model-wan2-t2v`,
`tt-model-flux`, `tt-model-mochi`, `tt-model-qwen3`) that download HuggingFace
weights at install time, with a shared debconf HF token question.

**Original prompt:** "Create virtual/meta .deb packages — one per inference mode —
that download the required HuggingFace model weights after collecting/sourcing a
HF_TOKEN via debconf when not already present."

### New files (13)

| File | Purpose |
|---|---|
| `bin/download_model.sh` | Shared HF downloader: `--repo`, `--token`, `--skip-if-exists`, `--check-only` |
| `debian/tt-model-wan2-t2v.templates` | debconf password question (`tt-local-generator/hf-token`) |
| `debian/tt-model-wan2-t2v.config` | Token discovery → pre-set or prompt |
| `debian/tt-model-wan2-t2v.postinst` | Download `Wan-AI/Wan2.2-T2V-A14B-Diffusers` (~118 GB) |
| `debian/tt-model-flux.templates` | Same debconf question (gated-model notice in description) |
| `debian/tt-model-flux.config` | Same token discovery pattern |
| `debian/tt-model-flux.postinst` | Download `black-forest-labs/FLUX.1-dev` (~34 GB) |
| `debian/tt-model-mochi.templates` | Same debconf question |
| `debian/tt-model-mochi.config` | Same token discovery pattern |
| `debian/tt-model-mochi.postinst` | Download `genmo/mochi-1-preview` (~20 GB) |
| `debian/tt-model-qwen3.templates` | Same debconf question (prompt always suppressed) |
| `debian/tt-model-qwen3.config` | Token optional; `db_fset seen true` so no prompt |
| `debian/tt-model-qwen3.postinst` | Download `Qwen/Qwen3-0.6B` (~1.2 GB) |

### Modified files (3)

| File | Change |
|---|---|
| `debian/control` | Four new `Package:` stanzas (Architecture: all) |
| `debian/rules` | Symlink `download_model.sh` → `/usr/bin/tt-local-gen-download-model` |
| `debian/changelog` | Bump to 0.2.0 |

### Design decisions

- **Shared debconf key:** All four `.templates` files declare the same key
  (`tt-local-generator/hf-token`). debconf merges by name, so a single `apt install`
  of multiple packages prompts once.
- **Immediate wipe:** Each postinst calls `db_reset` right after `db_get` — the
  token lives in `passwords.dat` for seconds only.
- **`runuser`:** postinst runs as root; the download script is invoked as
  `$SUDO_USER` so weights land in the correct user's `~/.cache/huggingface/hub/`.
- **Non-fatal downloads:** If the download fails, postinst prints retry instructions
  and exits 0 — the package stays installed and other packages aren't rolled back.
- **Qwen3 special case:** `tt-model-qwen3.config` always sets `seen=true` because
  the model is fully public. A token found in the environment is still forwarded
  for rate-limit avoidance.

### Build (same as 0.1.0, produces five .deb files)

```bash
./bin/snapshot_vendor.sh --src ~/code/tt-inference-server
dpkg-buildpackage -us -uc -b
# Produces: tt-local-generator_0.2.0_amd64.deb
#           tt-model-wan2-t2v_0.2.0_all.deb
#           tt-model-flux_0.2.0_all.deb
#           tt-model-mochi_0.2.0_all.deb
#           tt-model-qwen3_0.2.0_all.deb
```

### Manual re-download helper

```bash
# Re-run a failed download without reinstalling the package:
tt-local-gen-download-model --repo Wan-AI/Wan2.2-T2V-A14B-Diffusers
tt-local-gen-download-model --repo black-forest-labs/FLUX.1-dev --token hf_xxxx
tt-local-gen-download-model --repo Qwen/Qwen3-0.6B --skip-if-exists

# Check whether a model is already cached:
tt-local-gen-download-model --repo genmo/mochi-1-preview --check-only
```

---

## macOS remote-client video playback (in progress — 2026-04-14)

**Symptom:** Gtk.Video shows ⊘ (broken-media icon), ▶ Play does nothing, hover
preview is blank. "Open externally" and "Export" both work (files are valid MP4s).

**Root cause hypothesis:** `libmedia-gstreamer.dylib` — the GTK4↔GStreamer bridge —
is absent from the Homebrew `gtk4` bottle. Without it `get_media_stream()` returns a
`GtkMediaStream` already in error state; `stream.play()` silently no-ops.

**Diagnostics added:**
- `bin/test_macos.sh` — comprehensive check: GStreamer elements, `libmedia-gstreamer`
  presence, `GST_PLUGIN_PATH`, gst-launch smoke test against a real MP4.
- `DetailPanel._toggle_play` now calls `stream.get_error()` before `stream.play()`
  and prints the GLib error message + hint to stderr when the stream is errored.
- `DetailPanel.show_record` registers a `notify::error` handler via a 200 ms
  `GLib.timeout_add` so async pipeline errors also appear on stderr.

**Key check — run on the Mac:**
```bash
./bin/test_macos.sh        # look at section [ 6 ] for libmedia-gstreamer
```

**Likely fix if `libmedia-gstreamer` is missing:**
```bash
brew install --build-from-source gtk4   # rebuilds gtk4 with GStreamer backend enabled
```
GTK4 Homebrew bottles are pre-built before GStreamer is present, so the backend is
compiled out. Building from source after `brew install gstreamer gst-plugins-*` picks
it up.

**`_llm_available()` timeout** raised 2 s → 3 s (`app/generate_prompt.py`) so remote
Qwen servers on LAN don't get false-negative health checks.

**Gallery ordering** fixed: `_load_history` now sorts merged local+remote records by
`created_at` descending so downloaded records appear chronologically, not at the top.

---

## Ready-to-Run gate (RN-S, v0.55.0)

`app/ready_to_run.py` (GUI-free) decides whether a Create job's required server
needs starting/switching: `plan_switch(selected_model_key, status_of) ->
SwitchPlan(target, conflict, needs_reset)`. `target` is `None` when the
selection maps to no real `server_manager.SERVERS` key (empty selection, or a
synthetic/self-contained medium like AnimateDiff) — nothing to gate.
`conflict` is a currently READY/STARTING server sharing `target`'s hardware
group (media: video/image/animate all share the port-8000 diffusion server;
artgen servers share the port-8002 slot) that would have to be stopped +
reset first.

`MainWindow._ensure_server_ready_then(medium, params)` is the gate in front of
`_on_create_generate`'s dispatch: ready/no-target -> `_launch_create_job`
(the extracted former dispatch body) runs immediately, same as before; not
ready -> `_confirm_start_server` shows a dialog naming the stop/reset/start
plan, and only on **explicit accept** does `_perform_switch_then` run the
switch on a background thread (stop conflict -> `pipeline_engine._tt_smi_reset()`
if needed -> `server_manager.start` -> poll `is_healthy` -> `_launch_create_job`).
All widget touches go through `GLib.idle_add`; `ServersControl`'s log and
`ModelStatusService.note_starting/note_stopping` are reused, not duplicated.

**Hard safety rule, non-negotiable:** the switch only ever executes after the
user accepts the confirm dialog — never auto-run. Backend-switch churn has
hard-locked this box before (see `reference_qb2_card924055_fragility` in
memory); confirm-before-switch is the guard against that.

**Bug found and fixed while wiring this up:** `ready_to_run.conflicting_server`
originally did `str(status_of(key)).lower()` — but `model_status.Status` is a
`(str, Enum)` whose `__str__` (Python 3.11+) returns `"Status.READY"`, not
`"ready"`, so the live gate (fed real `Status` values by
`ModelStatusService.status()`) would never have detected a conflict. Task 1's
own tests only ever passed plain strings, masking it. Fixed by dropping the
`str()` wrapper — `.lower()` alone works on both a plain string and a `Status`
member (it operates on the underlying str data, unaffected by the Enum's
`__str__` override).

**Follow-ons (not done here):** Pipeline Studio already switches servers
between steps with its own UX — worth reconciling with this confirm-dialog
pattern later for consistency. A readiness-clarity pass on the Create option
labels themselves is optional; the status dots already convey it.
