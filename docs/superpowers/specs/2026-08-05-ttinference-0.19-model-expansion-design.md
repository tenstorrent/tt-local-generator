# tt-inference-server 0.19 Get-Current + Media Model Expansion — Design

**Date:** 2026-08-05
**Branch:** `feat/pipeline-editor`
**Status:** Approved (brainstorm), pending implementation plan

## Problem / goal

For the upcoming major release, bring tt-local-generator's tt-inference-server
support up to date and expand the media model surface. Analysis of upstream
`tenstorrent/tt-inference-server` (we vendor **v0.18.0** / `d5913e8`; latest is
**v0.19.0** / `399ce0b`) found:

- v0.19.0 is an **LLM-only** point release ("Llama-3.1-8B uplift on P300", new
  vLLM image). The MEDIA catalog (`video.yaml`/`image.yaml`/`model_spec.py`) is
  **byte-identical** 0.18→0.19 — no media rebase is triggered.
- Our `apply_patches.sh` Steps 7/8/9 (Wan2.2-Animate / DeepSeek / SDXL via the
  inline `model_spec.py` catalog) have been **dead since 0.18.0's YAML
  migration** killed the anchor — a long-standing latent breakage.
- A compiled artifact (`patches/media_server_config/config/__pycache__/
  constants.cpython-312.pyc`) is committed by mistake.
- The upstream **shipped catalog** (P300X2-ready, needs NO patch) already
  contains media models we don't surface — notably **Wan2.2-I2V** (native
  image-to-video) and **FLUX.1-dev** (higher-quality image).

## Scope (locked in brainstorm)

**In:**
1. **Get-current / hygiene** (no hardware needed): fix the dead Animate patch
   (Step 7 → a `video.yaml` YAML append, mirroring Step 6); retire dead Steps
   8/9; remove the stray `.pyc` (+ gitignore); bump the artgen vLLM image to
   `0.19.0-b204341-9bd099c`; re-pin `VENDOR_SHA` to `399ce0b`; fix stale
   comments/docs.
2. **Wan2.2-I2V-A14B** (`Wan-AI/Wan2.2-I2V-A14B-Diffusers`, P300X2, in-catalog):
   a new **image-to-video** model in the Video medium's picker.
3. **FLUX.1-dev** (`black-forest-labs/FLUX.1-dev`, P300X2, in-catalog): a new
   image model beside FLUX.1-schnell.

**Out / deferred (with rationale):**
- **SDXL-inpainting** — needs an input-image + mask-drawing UI the app lacks;
  a follow-up feature (mask affordance), not surfaced this release.
- **Media bind-mount patch drift-check** — the `patches/media_server_config/**`,
  `patches/tt_dit/**`, `patches/models/experimental/tt_dit/**` payloads target
  the media **Docker image** (`tt-media-inference-server:0.18.0-c49bb76`), which
  v0.19.0 did NOT move. A true drift check is a separate `docker create/cp`
  extraction task per CLAUDE.md's "Patch philosophy", NOT triggered by v0.19.0.
- **VLM captioning** (gemma-3-27b P300X2) — only 27B/EXPERIMENTAL/vLLM-engine;
  speculative future ("TTLGCaptionImage" adapter).
- **audio_tts / cnn / embedding** catalogs, SD3.5-large, Qwen-Image, galaxy-only
  Wan2.2-I2V variants — no P300 support or out of scope for a creative app.

## Follow-up track (approved): patch-strategy modernization

tt-vscode-toolkit's `content/lessons/monkeypatch-ttnn.md` (validated on p300c) +
its `content/templates/monkeypatch/tt_patches.py` harness codify the rule
**"patch to CHANGE behavior, wrap to ADD behavior; editing the source tree is the
last resort"**, judged on *smallest trace* + *upgrade-safety* (fail loud, don't
drift silently). Our `patches/media_server_config/**`, `patches/tt_dit/**`, and
`patches/models/experimental/tt_dit/**` are whole-file bind-mount copies — the
last-resort strategy at scale — which is exactly why they drift silently against
each media-image refresh and why `apply_patches.sh` Steps 7/8/9 rotted
undetected. **Approved direction (separate effort, not this release):** migrate
the "add a symbol / change a default / register a runner" patches to a runtime
`tt_patches.py`-style harness applied before import (via the media container's
entrypoint / `sitecustomize`), with `version_at_most` guards + a `verify()`/
fail-loud missing-target gate so a stale-or-absorbed patch SCREAMS instead of
drifting. Highest-ROI/lowest-risk first: `constants.py` symbol/default overrides,
`dit_runners.py` trace-region bumps, `runner_fabric.py` runner registration.
Genuinely-deep `tt_dit` pipeline bug-fixes may stay whole-file copies for now, but
gain a fail-loud version guard. See [[reference_media_patch_monkeypatch_strategy]].

## Hard constraints

- **HARDWARE VALIDATION IS THE USER'S, ON QB2.** Nothing here can be validated
  from the dev session; the app changes are code/config modeled on the existing
  working start scripts + the shipped upstream catalog. Automated tests cover
  the pure/wiring logic (ServerDef present, picker lists the model, routing maps
  it, `collect()` unchanged), NOT actual generation.
- **Fragile QB2 chip (see `reference_qb2_card924055_fragility`):** minimize
  backend-switch churn; new models route through the existing **confirm-before-
  switch** ready-to-run gate (`app/ready_to_run.py`), never auto-switch.
- **`collect()`/`_collect_params()` byte-identical** — new models are additive
  picker entries; adding them must not change the generated params for existing
  models (collect-equality guard).
- `_CSS`/byte literals ASCII-only.
- Version discipline: bump `VERSION` (0.75.0 → **0.76.0** — a normal minor for
  this feature; the **1.0.0 major stamp is a release-cut decision**, not made
  here). Prepend a `debian/changelog` stanza.
- Patch philosophy: no unnecessary divergence; the Animate registration moves to
  the YAML era (a `video.yaml` append), not a revived `model_spec.py` hack.

## Components

### A. Get-current / hygiene

- **`bin/apply_patches.sh`:**
  - **Step 7 (Wan2.2-Animate):** replace the dead `model_spec.py`
    `ModelSpecTemplate(...)` injection with an idempotent **append of the
    Wan2.2-Animate entry to `workflows/model_specs/dev/video.yaml`** — the exact
    same shape/idempotency guard (skip if the weights string already present) as
    Step 6's SkyReels append. `MODEL_SPEC_YAML` already points at that file.
  - **Retire Steps 8 (DeepSeek P300X2) & 9 (SDXL version bump):** delete them —
    their `model_spec.py` anchors no longer exist in the YAML era. (If either
    model still needs a spec tweak, it becomes a `video.yaml`/`image.yaml` append
    like Step 6/7; neither is required for the models we surface today.)
  - Confirm Steps 2/4/5 (`run_docker_server.py` env anchor, line ~465) and Step
    6 (SkyReels `video.yaml` append) are untouched and still valid.
- **Stray artifact:** `git rm patches/media_server_config/config/__pycache__/
  constants.cpython-312.pyc`; add `__pycache__/` + `*.pyc` under `patches/` to
  `.gitignore`.
- **`bin/start_artgen.sh`:** add `…:0.19.0-b204341-9bd099c` as the **preferred**
  vLLM image (probe-first, keeping the existing fallbacks) so artgen picks up the
  Llama-3.1-8B P300 uplift + newer tt-metal base.
- **`vendor/VENDOR_SHA`** → `399ce0b5c98067fd41cc3ba978d2742b15e8ac4e`;
  **`bin/snapshot_vendor.sh`** DEFAULT_SHA + the stale
  `tt-media-inference-server:0.11.1-bac8b34` comment (→ `0.18.0-c49bb76`) and the
  vLLM-image comment updated.
- **Docs:** CLAUDE.md "Vendored tt-inference-server" → v0.19.0; "Model registry
  migrated to YAML" → Animate now a `video.yaml` append, Steps 8/9 retired; note
  the two new media models.

### B. Wan2.2-I2V-A14B (image-to-video)

- **In the shipped catalog** (`dev/video.yaml`, P300X2, COMPLETE) → **no patch**;
  `run.py --model Wan-AI/Wan2.2-I2V-A14B-Diffusers` runs on the existing media
  image.
- **`bin/start_wan_i2v.sh`** (new) — modeled on `bin/start_skyreels_i2v.sh`
  (I2V) + `bin/start_wan_qb2.sh` (Wan family / QB2 P300X2 env); same
  `--dev-mode`, `--env-file`, media image `0.18.0-c49bb76`, `--gui`/`--stop`
  handling.
- **`app/server_manager.py`:** new `ServerDef(key="wan2.2-i2v", label="Wan2.2-I2V
  (P300X2)", script="start_wan_i2v.sh", capabilities=("video",),
  runner_key=<confirm>, benefit=<i2v tagline>)`. `runner_key` (the port-8000
  `runner_in_use` value) MUST be confirmed against what the media server reports
  (skyreels uses `tt-skyreels-v2-i2v`; likely `tt-wan2.2-i2v`) — a plan-time
  verification; a wrong value silently breaks the health/runner check.
  Add `MODEL_DISPLAY_NAMES["wan2.2-i2v"]` + benefit.
- **Create wiring:** Wan2.2-I2V is an image→video model, so it lives in the
  **Video medium's** scoped picker alongside SkyReels-I2V (both consume a seed
  image). Reuse the existing seed-image plumbing (`ref`/seed image path the
  SkyReels-I2V / Animate paths already use); `MainWindow._native_generate_args`
  routes `model_id`/`video_model_key == "wan2.2-i2v"` to `model_source="video"`
  I2V args. No new input modality.
- **`.deb`:** new `tt-model-wan2-i2v` (weights `Wan-AI/Wan2.2-I2V-A14B-Diffusers`,
  ~large) — modeled on the existing `tt-model-*` packages
  (`.templates`/`.config`/`.postinst` using `download_model.sh`), sharing the
  same debconf HF-token key. Add a `debian/control` stanza + `debian/rules`
  symlink if needed.

### C. FLUX.1-dev (higher-quality image)

- **In the shipped catalog** (`dev/image.yaml`, P300X2) → **no patch**.
- **`bin/start_flux_dev.sh`** (new) — modeled on `bin/start_flux.sh` (schnell),
  `--model black-forest-labs/FLUX.1-dev`, media image `0.18.0-c49bb76`.
- **`app/server_manager.py`:** new `ServerDef(key="flux-dev", label="FLUX.1-dev
  (P300X2)", script="start_flux_dev.sh", capabilities=("image",),
  runner_key=<confirm, likely "tt-flux.1-dev">, benefit="Higher-fidelity image;
  more steps than schnell")`. Add display name + benefit.
- **Create wiring:** Image medium picker beside FLUX.1-schnell;
  `_native_generate_args` image path maps `image_model_key == "flux-dev"` to its
  server key. **Dev needs more inference steps + guidance than schnell** (schnell
  is a 1–4-step distilled model; dev is ~28–50 steps with real guidance) — set
  dev-appropriate default steps/guidance where the app supplies them (confirm the
  media server's own defaults vs. what the app passes; don't send schnell's
  1–4-step default to dev).
- **`.deb`:** new `tt-model-flux-dev` (`black-forest-labs/FLUX.1-dev`, **gated
  model** — HF token; ~34 GB), modeled on the existing `tt-model-flux` package.

## Testing (no hardware)

- **server_manager:** `SERVERS` has `wan2.2-i2v` (cap video) + `flux-dev` (cap
  image) with correct label/benefit/display-name; `servers_for_capability("video")`
  includes wan2.2-i2v, `("image")` includes flux-dev.
- **Create picker:** the Video picker lists Wan2.2-I2V; the Image picker lists
  FLUX.1-dev (via `_scoped_model_keys`/`_populate_model_dropdown`), each with its
  benefit tagline + status dot.
- **Routing:** `_native_generate_args` maps `wan2.2-i2v` → video I2V args (seed
  image), `flux-dev` → image args; a **collect-equality** test proves existing
  models' generated params are byte-identical (new entries are additive).
- **apply_patches.sh:** a test (or the existing Step-6 test's pattern) that the
  Animate append targets `dev/video.yaml` and is idempotent; Steps 8/9 gone.
- **.deb:** `debian/control` parses; the new packages are `Architecture: all`
  with the shared debconf token key; `download_model.sh --repo <weights>
  --check-only` recognizes them (mirror the existing model-package tests if any).
- Full suite green with the documented flake deselects.

**Explicitly NOT tested here (hardware, user-run on QB2):** that the new start
scripts actually launch the media server, that `runner_key` matches the server's
reported value, and that generation produces output. These are QB2 acceptance
steps in the plan's "manual validation" section.

## Critical files

- `bin/apply_patches.sh` — Animate→`video.yaml`; retire Steps 8/9.
- `bin/start_wan_i2v.sh`, `bin/start_flux_dev.sh` — NEW (modeled on
  `start_skyreels_i2v.sh` / `start_flux.sh`).
- `bin/start_artgen.sh` — vLLM image → 0.19.0.
- `bin/snapshot_vendor.sh`, `vendor/VENDOR_SHA` — re-pin v0.19.0 + comments.
- `app/server_manager.py` — two new `ServerDef`s + display/benefit.
- `app/main_window.py` / `app/create_view.py` — Create picker + `_native_generate_args`
  routing for the two new keys (Video I2V seed-image reuse; Image dev steps).
- `debian/control`, `debian/rules`, `debian/tt-model-wan2-i2v.*`,
  `debian/tt-model-flux-dev.*` — NEW model-download packages.
- `.gitignore`, remove the stray `.pyc`.
- `CLAUDE.md`, `debian/changelog`, `VERSION` (→0.76.0).

## Open items for the plan (confirm, don't guess)

- The exact `runner_key` the media server reports for Wan2.2-I2V + FLUX.1-dev
  (port-8000 `runner_in_use`) — confirm from the start-script/server output or
  the vendored media config; a wrong value breaks the health check.
- FLUX.1-dev default steps/guidance the app should pass (vs schnell's 1–4).
- Whether Wan2.2-I2V and FLUX.1-dev need any per-model start-script args
  (trace_region_size etc.) beyond what the media image derives from the catalog.
