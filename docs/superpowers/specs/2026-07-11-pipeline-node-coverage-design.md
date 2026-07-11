# Pipeline execution engine + node coverage (SP-A design, revised)

Date: 2026-07-11 (revised after discovering the runner is a stub)
Status: approved (design), pending implementation plan
Branch: feat/pipeline-editor (stacked on the multichip 0.12.0 work + UI fixes)

## The correction that prompted this revision

The first draft of this spec assumed `bin/run_workflow.sh` was a generic spec
interpreter with a `class_type` dispatch we'd extend. **It isn't.** Investigation
found:

- `run_workflow.sh` takes a spec path but **ignores the spec's nodes** — its 8
  steps are hardcoded with literal `SEED_PROMPT_PLACEHOLDER` / `SEED_PLACEHOLDER`
  args that nothing ever substitutes. It's a non-functional stub/template.
- `run_worlds_fair.sh` (554 lines) is the **real, working** runner — fully
  hardcoded for the 5 fairs, with hand-written seed prompts and reimplemented node
  functions. That's what produced the World's Fair generations.
- `app/pipeline_runner.py` launches `run_workflow.sh <spec>` and parses its
  `NODE:`/`LOG:`/`PLAYLIST:` stdout signals. So the app drives the stub. The
  existing 66 pipeline tests cover signal-parsing / retry / store logic — **not**
  actual node execution.

**There is no working generic pipeline engine.** No spec, not even World's Fair,
runs through `run_workflow.sh` today. SP-A must build that engine. The two new
node types (artgen, AnimateDiff) are a small addition on top of it.

## Goal

A generic **Python** pipeline engine that executes any ComfyUI-API-v1 spec, proven
first on the existing 1964 World's Fair spec (known-good content), then extended
with artgen + AnimateDiff node coverage.

## Architecture

New module `app/pipeline_engine.py` (pure-ish Python; shells out for the actual
work). No GTK imports (mirrors `pipeline_runner.py`'s discipline).

- **`load_spec(path) -> Spec`** — parse the JSON (numbered nodes: `class_type` +
  `inputs`, where an input value that is a `[node_id, key]` list is a wire).
- **`topo_order(spec) -> list[node_id]`** — order nodes by wire dependencies
  (Kahn's algorithm); raise on cycles / dangling wire refs.
- **`run(spec, *, dry_run, emit) -> results`** — for each node in order:
  1. resolve inputs: leaf params as-is; wires via `results[src_id][key]`.
  2. dispatch on `class_type` to a **handler** `handle_<type>(node_id, inputs, ctx)`.
  3. store `results[node_id][key] = value` for downstream wiring.
  4. `emit("NODE:<id>:running|done|failed:<detail>")`, `emit("LOG:<path>")`,
     `emit("PLAYLIST:...")` — the exact strings `pipeline_runner.py` already parses.
  - `dry_run=True` → each handler returns documented placeholder outputs (no
    hardware, no API calls) — the whole graph is runnable/testable offline.
- **Backend/server management**: the engine groups consecutive nodes by required
  backend (media diffusion server ↔ artgen vLLM ↔ blackhole/none) and switches +
  `tt-smi -r` only at boundaries (porting `run_worlds_fair.sh`'s `stop_and_reset` /
  `start_server` logic). CPU plugins (blip/rmbg/depth) need no switch.
- **`run_workflow.sh` becomes a thin shim**: `exec python3 app/pipeline_engine.py
  "$spec" [--dry-run]`, preserving `pipeline_runner.py`'s interface unchanged
  (it still gets `NODE:`/`LOG:` on stdout).

### Handlers to port (from the two bash scripts → Python)

The existing 10 `class_type`s, porting the working logic (the bash scripts already
contain the exact API calls as python heredocs):

| class_type | work | key outputs |
|---|---|---|
| `TTLGTextToImage` | media-server `/v1/images/generations` (FLUX/SDXL/…) | `image_path` |
| `TTLGImageToVideo` | media-server video job (SkyReels/Wan i2v) | `video_path` |
| `TTLGGenerateText` | artgen vLLM chat (`call_llm`) | `text` (a.k.a. poem/caption) |
| `TTLGCaptionImage` | `plugins/blip/plugin.py` (CPU) | `caption` |
| `TTLGRemoveBackground` | `plugins/rmbg/plugin.py` (CPU) | `fg_path` |
| `TTLGEstimateDepth` | `plugins/depth/plugin.py` (CPU) | `depth_path` |
| `TTLGPromptCompose` | string template `{var}` substitution | `prompt` |
| `TTLGSVGRender` | `plugins/svg_render/plugin.py` | `png_path` |
| `TTLGComposite` | `plugins/composite/plugin.py` | `image_path` |
| `TTLGAddToPlaylist` | media-store playlist add | `playlist_id` |

Handler signatures are uniform (`node_id, inputs: dict, ctx`) so a dispatch table
(`HANDLERS: dict[str, callable]`) maps `class_type → handler` — the extension point
the first draft wrongly assumed already existed.

## Milestone 1 (prove it): run the 1964 World's Fair spec

- **Dry-run**: `pipeline_engine.py docs/examples/workflows/1964-worlds-fair.json
  --dry-run` orders the 8 nodes correctly, resolves every wire, and publishes the
  documented output keys — asserted by a unit test (no hardware).
- **Real run on QB2**: the same spec executes end-to-end and produces the seed
  image → caption/depth/rmbg → composed video, and caption → poem → poem-image —
  matching what `run_worlds_fair.sh` produces. This is the acceptance gate for the
  engine before any new node types.

Seed prompts: the 1964 spec's node-1 `prompt` field is already populated (unlike
the stub's placeholder), so the engine reads it directly.

## Milestone 2 (node coverage): artgen + AnimateDiff handlers

Once Milestone 1 passes, add two handlers to the dispatch table:

**`TTLGArtgenGenerate`** — generic artgen-plugin node (recommended over five typed
nodes: DRY, auto-covers every current/future plugin).
- inputs: `plugin` (verse|palette|ansi|codeart|constellation|…) + that plugin's
  params (may be wired). dispatch: `app/artgen/cli.py <plugin> … --output <out>`
  (needs the artgen vLLM backend for LLM-backed plugins — the engine's server
  switch handles it).
- outputs: `text` (verse/codeart/freeform; for palette a name+hex summary),
  `artifact_path` (.txt/.py/.ans/.json/.svg), `png_path` (raster plugins only).

**`TTLGAnimateDiff`** — AnimateDiff incl. multi-chip Remix/Coherent **and the
AnimateDiff-Evolved-inspired fold-in** (see below).
- inputs: `prompt` (or wired), `frames`, `steps`, `seed`, `mode`
  (off|remix|coherent), `per_chip_prompts`, `seed_spread`, `ramp`, `stitch_order`,
  **`prompt_schedule`** (prompt-travel keyframes), **`loop`** (none|seamless).
- dispatch: `app/artgen/cli.py animatediff --output <gif> --mode …`. Runs on
  Blackhole (no media server).
- output: `gif_path`.

### AnimateDiff-Evolved fold-in (Milestone 2)

An audit of the sister project `~/code/tt-animatediff` (which we own and re-release
freely — see the animatediff-sister memory) determined which ComfyUI-AnimateDiff-
Evolved concepts are achievable. Folding in three, deferring one:

1. **CLI parity fix (Task 6).** `app/artgen/cli.py`'s animatediff branch only
   forwards `prompt/frames/steps/seed/negative_prompt/temporal_alpha` — it drops
   every advanced param `add_args` already declares (`mode`, `lightning`, chain,
   `motion_adapter*`, and the multichip `per_chip_prompt`/`seed_spread`/`ramp`/
   `stitch_order`). Task 6 closes this gap (forward all of them) and adds the two
   new flags below. Zero pipeline risk; unblocks `tt-ctl`-driven testing.

2. **Prompt travel (Task 6b — headline).** Different prompts across the frame
   timeline via interpolated conditioning. The tt-animatediff denoise loop is
   already per-frame (`temporal_attention.py` / `ttnn_motion_pipeline.py`) but
   broadcasts one text embedding to every frame; the change is a per-frame
   embedding path: `encode_prompt` accepts keyframe (prompt, frame) pairs and
   builds N interpolated embeddings; the loop indexes `embeds[i]`. This is a
   **sister-repo pipeline change** (+`--prompt-schedule` on `generate.py`) that we
   commit and re-release, then thread through the wrapper. It generalizes multi-chip
   Remix's per-chip prompts (spatial) onto the time axis (temporal).

3. **Seamless-loop crossfade (Task 6c).** A `loop=seamless` option that crossfades
   the last K frames into the first K post-hoc — wrapper-only (a helper beside
   `_stitch_gifs` in `app/artgen/generators/animatediff.py`), no model change.

**Deferred (its own milestone):** sliding **context windows** for long video —
a multi-day rewrite of the core "all frames every step" denoise loop in two
tt-animatediff files. Genuinely valuable for long clips but too large to fold in.
Motion-LoRA support is skipped (no scaffolding exists; low value/effort now).
`temporal_alpha`/`motion_adapter_alpha` motion-blend knobs already exist and are
wired end-to-end (incl. per-chip ramp) — no work needed.

Both handlers register in `workflow_compat.py`'s `COMPATIBILITY_MAP` (native tier)
so `validate_spec` accepts specs using them; the output-key contract is documented
there for SP-B/SP-C to wire against.

## Testing

Hardware-free where possible:
- **Engine unit tests**: `topo_order` (correct order, cycle detection, dangling
  wire), wire resolution, dispatch-table coverage, `--dry-run` of the 1964 spec
  publishes the documented keys, signal emission format matches
  `pipeline_runner.py`'s parser.
- **workflow_compat**: `validate_spec` accepts the new node types (+ each artgen
  plugin), rejects unknown plugins.
- **cli.py multichip flags**: flag→`run_subprocess` kwarg mapping (mocked).
- **Hardware acceptance (QB2)**: Milestone-1 real run of the 1964 spec; a small
  Milestone-2 real run exercising one artgen node + one AnimateDiff node.

## Non-goals (SP-A)

- No GUI (SP-C). No authoring/running the funky pipelines (SP-B) — though the
  engine is what makes them runnable.
- No new artgen plugins.
- No behavior change to what `run_worlds_fair.sh` produces (the engine is validated
  against it, not required to replace it).

## Sequencing (SP-A plan)

**Milestone 1 — engine, proven on World's Fair:**
1. `pipeline_engine.py` skeleton: `load_spec`, `topo_order`, dispatch table,
   `run(dry_run, emit)`, signal emission — with all handlers stubbed to dry-run.
2. Port the 10 existing handlers (API/LLM/CPU-plugin/compose/composite) from the
   bash scripts to Python; server-switch/`tt-smi -r` boundary logic.
3. `run_workflow.sh` → thin shim over the engine; confirm `pipeline_runner.py`
   still parses signals (unit).
4. Dry-run + unit tests on the 1964 spec (order/wires/keys/signals).
5. **QB2 acceptance run of the 1964 spec** (engine produces the real chain).

**Milestone 2 — node coverage:**
6. cli.py: multichip flags on the animatediff generator + forward to run_subprocess (+unit).
7. `TTLGArtgenGenerate` handler + `TTLGAnimateDiff` handler + dry-run outputs.
8. `workflow_compat.py` registration + output-key docs + validation tests.
9. Small QB2 run exercising one artgen + one AnimateDiff node.
10. Changelog note (infrastructure on the 0.12.0 branch; no version bump yet).
