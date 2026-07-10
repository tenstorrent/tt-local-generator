# Pipeline node coverage: artgen + AnimateDiff (SP-A design)

Date: 2026-07-11
Status: approved (design), pending implementation plan
Branch: feat/pipeline-editor (stacked on the multichip 0.12.0 work + UI fixes)

## Context

This is sub-project A of a three-part effort to revive Pipeline mode:
- **SP-A (this spec)** — teach the workflow runner new node types so all the art
  types can participate in pipelines.
- **SP-B** — author 4 funky cross-modal example pipelines and run them on QB2.
- **SP-C** — the dedicated 5-mode Pipeline Studio GTK app.

Today the workflow runner (`bin/run_workflow.sh`, 662 lines) supports 10 node
`class_type`s — `TTLGTextToImage`, `TTLGImageToVideo`, `TTLGGenerateText`,
`TTLGCaptionImage`, `TTLGRemoveBackground`, `TTLGEstimateDepth`, `TTLGSVGRender`,
`TTLGComposite`, `TTLGPromptCompose`, `TTLGAddToPlaylist`. It does **not** support
the artgen generators (verse, palette, ansi, codeart, constellation, geometric,
skyline, landscape, circuit, freeform) or AnimateDiff as pipeline nodes. SP-B's
funky pipelines need exactly those. SP-A adds them.

## Runner execution model (what we build on)

`run_workflow.sh` runs a spec (ComfyUI-API-v1 JSON: numbered nodes, each with a
`class_type` and `inputs`). Its contract, which SP-A follows exactly:

- Each node type has a bash function `_node_<name> <node_id> <args...>`.
- **Wire inputs** are resolved with `_resolve_input '["<src_id>","<key>"]'` →
  the stored output of an upstream node.
- **Outputs** are published with `set_result <node_id> <key> <value>` into a JSON
  state keyed `node_id → key → value` — the only mechanism by which later nodes
  wire to earlier ones.
- Every node function has a `DRY_RUN` early-branch that publishes placeholder
  outputs (so the whole graph is runnable/testable without hardware).
- Progress is emitted as `NODE:<id>:<status>:<detail>` lines (parsed by
  `PipelineRunner`); node labels via `set_result <id> _label <text>`.
- The runner already switches backing servers (media diffusion server ↔ artgen
  vLLM ↔ none/blackhole) between steps and runs `tt-smi -r` when crossing that
  boundary — LLM-backed artgen nodes reuse this.

Spec validation lives in `app/workflow_compat.py` (`COMPATIBILITY_MAP` +
`validate_spec`); unknown `class_type`s are rejected, so new node types must be
registered there.

## New node type 1: `TTLGArtgenGenerate` (generic artgen-plugin node)

One node type runs **any** artgen plugin (chosen over five per-plugin node types:
DRY in the runner and auto-covers every current and future plugin with no new
per-plugin code).

**Inputs**
- `plugin` (required): one of the artgen plugin names — `verse`, `palette`,
  `ansi`, `codeart`, `constellation`, `geometric`, `skyline`, `landscape`,
  `circuit`, `freeform` (any registered artgen generator).
- Plugin-specific params, passed through to the artgen CLI, e.g.:
  - verse: `form`, `theme`, `count`
  - palette: `mood`, `count`
  - codeart: `language`, `inspiration`, `style`, `should_compile`
  - ansi: `subject`, `width`, `colors`, `ansi_style`
  - constellation: `culture`, `stars`, `lore`
- Any of these may be a **wired** input (e.g. `theme` ← an upstream node's `text`).

**Dispatch** — `_node_artgen`:
- Runs `"$PYTHON3" app/artgen/cli.py <plugin> [--<param> <value>...] --output <out>`
  where `<out> = $OUTPUT_DIR/node<id>_<plugin>.<ext>` (ext from the plugin's
  `output_ext`). LLM-backed plugins auto-discover the artgen endpoint
  (`detect_artgen_endpoint`) — so the runner must ensure the artgen vLLM server is
  the current backend before an artgen node (same server-switch it already does
  for `TTLGGenerateText`).
- The artgen CLI must support headless single-artifact generation to `--output`
  (it does — the codeart work exercised `app/artgen/cli.py <gen>`). If a plugin
  name isn't a registered artgen generator, fail the node with a clear message.

**Outputs** (`set_result`):
- `text` — the generated text, for text artifacts (verse, codeart, freeform) and,
  for palette, a short "name + hex list" summary usable in prompts.
- `artifact_path` — the saved file (`.txt` / `.py` / `.ans` / `.json` / `.svg`).
- `png_path` — a rendered raster, only where a raster render exists (ansi →
  rendered image, constellation/svg → via `svg_render`). Omitted otherwise.
- `_label` — `"<plugin>"` (drives the node card's type/model chip in SP-C).

**DRY_RUN**: publish a placeholder `text` and touch a placeholder `artifact_path`
(and `png_path` for raster plugins), so downstream nodes wire correctly.

## New node type 2: `TTLGAnimateDiff`

AnimateDiff as a node, including the new multi-chip Remix / Coherent modes.

**Inputs**
- `prompt` (leaf or wired), `negative_prompt`, `frames`, `steps`, `seed`,
  `temporal_alpha`, `lightning` (default true).
- `mode` — `off` | `remix` | `coherent` (default `off`).
- Remix params: `per_chip_prompts` (list), `seed_spread`, `ramp`
  (`none|temporal|motion`), `stitch_order` (`interleave|concatenate`).

**Dispatch** — `_node_animatediff`:
- `app/artgen/cli.py` **already** has an `animatediff` branch that drives
  `run_subprocess(...)` and writes a GIF to `--output` (cli.py:147-205). It just
  doesn't forward the new multichip params yet — it passes only
  prompt/frames/steps/seed/negative_prompt/temporal_alpha. So SP-A does NOT add a
  new wrapper; instead it:
  1. adds the multichip flags to the animatediff generator's `add_args`
     (`--mode`, `--per-chip-prompt` (repeatable), `--seed-spread`, `--ramp`,
     `--stitch-order`) so the CLI parses them, and
  2. forwards them from cli.py's animatediff branch into `run_subprocess(...)`
     (mapping to `multichip_mode`, `per_chip_prompts`, `seed_spread`, `ramp`,
     `stitch_order`).
  `_node_animatediff` then just runs
  `"$PYTHON3" app/artgen/cli.py animatediff --output <gif> --mode <mode> …`.
- Runs on Blackhole directly (no media server); the runner must ensure the media
  container is stopped and chips are free before this node (it already stops
  containers when switching to the blackhole/no-server backend).
- Both new node types therefore dispatch through the single `app/artgen/cli.py`
  — no new entrypoint files.

**Outputs**: `gif_path` = `$OUTPUT_DIR/node<id>_anim.gif`; `_label` = `"AnimateDiff"`.

**DRY_RUN**: touch a placeholder `gif_path`.

## Spec validation (`workflow_compat.py`)

Add `TTLGArtgenGenerate` and `TTLGAnimateDiff` to `COMPATIBILITY_MAP` at the native
tier so `validate_spec` accepts specs using them. Document the output-key contract
(above) next to the map so SP-B and SP-C author against it.

## Output-key contract (the wiring surface SP-B/SP-C rely on)

| Node | Key outputs |
|---|---|
| `TTLGArtgenGenerate` | `text`, `artifact_path`, `png_path` (raster plugins only) |
| `TTLGAnimateDiff` | `gif_path` |

Typical wires this enables:
- verse/codeart `text` → `TTLGPromptCompose {var}` / `TTLGTextToImage.prompt` /
  `TTLGAnimateDiff.prompt`.
- palette `text` (name+hex) → prompt composition; palette `artifact_path` (.json)
  → carried as an artifact for the composite/codex page.
- ansi/constellation `png_path` → `TTLGComposite` / display.
- animatediff `gif_path` → `TTLGAddToPlaylist` / display.

## Testing

All hardware-free (DRY_RUN + unit):
- `workflow_compat`: `validate_spec` accepts specs using `TTLGArtgenGenerate`
  (each plugin) and `TTLGAnimateDiff`; rejects an unknown plugin name.
- Runner DRY_RUN: a small fixture spec exercising an artgen text node → PromptCompose
  → and an AnimateDiff node dry-runs end-to-end and publishes the documented output
  keys (assert the state JSON / result files). Reuse the runner's existing DRY_RUN
  path and `set_result`/`_resolve_input`.
- `animatediff_cli.py`: arg-parsing unit test (maps flags → the `run_subprocess`
  kwargs, `run_subprocess` mocked) — mirrors the multichip routing tests.

Real hardware execution of these node types is SP-B, not SP-A.

## Non-goals (SP-A)

- No GUI (that's SP-C). No authoring/running the funky pipelines (SP-B).
- No new artgen plugins — SP-A only makes existing generators runnable as nodes.
- No change to the 10 existing node types' behavior.

## Sequencing (SP-A plan)

1. Extend the animatediff generator `add_args` with multichip flags + forward
   them in `cli.py`'s animatediff branch to `run_subprocess`; unit test the
   flag→kwarg mapping (run_subprocess mocked).
2. `TTLGArtgenGenerate` runner function (`_node_artgen`) + DRY_RUN + output keys.
3. `TTLGAnimateDiff` runner function (`_node_animatediff`, dispatches
   `cli.py animatediff …`) + DRY_RUN.
4. `workflow_compat.py` registration + output-key docs + validation tests.
5. A DRY_RUN fixture spec + end-to-end dry-run test proving wiring.
6. Changelog note (no version bump until a release cut; SP-A is infrastructure on
   the 0.12.0 branch).
