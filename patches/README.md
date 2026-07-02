# patches/ — Hotpatch Inventory

Hotpatches are files that replace or supplement code inside the
`tt-inference-server` Docker image at container startup. They are injected
via two bind-mount mechanisms in `vendor/tt-inference-server/workflows/run_docker_server.py`:

- **`patches/media_server_config/`** → `~/tt-metal/server/` (FastAPI server layer:
  runners, request models, constants). Applied unconditionally for every model.
- **`patches/models/`** → `~/tt-metal/models/` (TTNN model internals). Applied
  unconditionally; files are mapped per their directory structure.
- **`patches/tt_dit/`** → `~/tt-metal/models/tt_dit/` (pipeline files for specific
  models). Applied only when `--dev-mode` is passed to `run_docker_server.py`
  (done by `start_animate.sh`; not by the default server start path).

**Sync rule:** After editing any patch file, immediately copy it to the
corresponding location under `vendor/tt-inference-server/` — the bind-mount
uses the *vendor* copy, not this directory. Run `bin/apply_patches.sh` to
sync the full tree, or `cp patches/… vendor/tt-inference-server/patches/…`
for a single file.

---

## media_server_config/config/constants.py

**Destination:** `~/tt-metal/server/config/constants.py`

Replaces the image's constants module wholesale. Key additions over the
upstream 0.17.0 image:

| Addition | Purpose |
|---|---|
| `SupportedModels.MOTIF_IMAGE_6B_PREVIEW` | HuggingFace repo ID for Motif |
| `SupportedModels.SKYREELS_V2_DF_1_3B_540P` / `…_I2V_14B_540P` | SkyReels repo IDs |
| `SupportedModels.Z_IMAGE_TURBO` | Tongyi-MAI Z-Image-Turbo repo ID |
| `ModelNames.MOTIF_IMAGE_6B_PREVIEW` / `SKYREELS_*` / `Z_IMAGE_TURBO` | Display names |
| `ModelRunners.TT_MOTIF_IMAGE_6B_PREVIEW` / `TT_SKYREELS_V2` / `TT_SKYREELS_V2_I2V` / `TT_Z_IMAGE_TURBO` | Runner key strings |
| `CANARY_TASK_IDS` and related v0.17.0 symbols | Missing from 0.9.0 image; referenced at import time |
| `WAN22_NUM_FRAMES = 81` | Frame count for Wan2.2 T2V / I2V |
| P300X2 device mesh config entries | `(2,2)` mesh entries for FLUX, Motif, SkyReels, Z-Image-Turbo with `request_processing_timeout_seconds` tuned per model |
| `request_processing_timeout_seconds` bumps | Z-Image-Turbo first-run TTNN compilation takes ~90 min; timeout raised from 6000 → 14400 s |

---

## media_server_config/domain/video_generate_request.py

**Destination:** `~/tt-metal/server/domain/video_generate_request.py`

Extends `VideoGenerateRequest` with fields needed by SkyReels I2V and
Wan2.2-Animate runners that are absent from the upstream request model:

- `num_frames: Optional[int]` — configurable frame count for SkyReels
  (valid values: `(N-1) % 4 == 0`; default via settings)
- `image: Optional[str]` — base64 image for I2V / Animate conditioning
- `mode: Optional[str]` — Animate mode (`"animation"` or `"replacement"`)

---

## media_server_config/tt_model_runners/dit_runners.py

**Destination:** `~/tt-metal/server/tt_model_runners/dit_runners.py`

Replaces the image's dit_runners module. Key additions:

| Addition | Details |
|---|---|
| `TTWan22AnimateRunner` | Runner for Wan2.2-Animate-14B (character animation). Loads I2V pipeline with Animate checkpoint; passes `mode` and `image` from the request. |
| `TTMotifImage6BPreviewRunner` | Runner for Motif-Image-6B-Preview. On P300X2 (2,2 mesh): creates a (1,1) single-chip submesh, disables traced inference (`traced=False`), and uses CPU PyTorch T5 encoder as diagnostic workaround. |
| `TTFlux1Runner` trace region | `trace_region_size` raised to `64_000_000` (from hardcoded value) — FLUX needs ~50.6 MB of trace buffers on P300X2; the upstream default causes an OOM during warmup. |
| SkyReels log-map entries | `dit_runner_log_map` dict extended with SkyReels runner keys to avoid `KeyError` at import time. |
| Mochi `TT_DIT_CACHE_DIR` env | `TTMochi1Runner.create_pipeline()` sets `TT_DIT_CACHE_DIR` from environment before calling `create_pipeline` — the Mochi pipeline reads this for compiled-weight caching. |

**Status note (Motif):** The single-chip submesh + CPU T5 workaround does not
produce correct images — the Motif transformer (DiT denoiser) produces invalid
latents on P300X2 in all tested configurations. This is an upstream issue;
see `pipeline_motif.py` notes below.

---

## media_server_config/tt_model_runners/runner_fabric.py

**Destination:** `~/tt-metal/server/tt_model_runners/runner_fabric.py`

Replaces the image's runner registry. Adds entries to `AVAILABLE_RUNNERS` for:

- `ModelRunners.TT_SKYREELS_V2` → `TTSkyReelsRunner` (from `skyreels_runner.py`)
- `ModelRunners.TT_SKYREELS_V2_I2V` → `TTSkyReelsI2VRunner` (from `skyreels_i2v_runner.py`)
- `ModelRunners.TT_WAN22_ANIMATE` → `TTWan22AnimateRunner` (from `dit_runners.py`)

These are intentionally separate from `dit_runners.py` imports to avoid triggering
the `dit_runner_log_map` key lookup for non-WAN model_runner strings.

---

## media_server_config/tt_model_runners/skyreels_runner.py

**Destination:** `~/tt-metal/server/tt_model_runners/skyreels_runner.py`

New file (not in upstream image). Server runner for **SkyReels-V2-DF-1.3B-540P**
(text-to-video, 540P, Blackhole).

Design notes:
- SkyReels-V2-DF-1.3B-540P is architecturally weight-compatible with
  `WanTransformer3DModel` (same attention, FFN, and conditioning structure).
- Runner delegates to `SkyReelsPipeline` (from `patches/tt_dit/`).
- Configured for P300X2 with (1,4) linear mesh and 33-frame default (configurable
  via `skyreels_num_frames` in app settings).
- Kept as a standalone file to avoid `dit_runner_log_map` KeyError on import.

---

## media_server_config/tt_model_runners/skyreels_i2v_runner.py

**Destination:** `~/tt-metal/server/tt_model_runners/skyreels_i2v_runner.py`

New file (not in upstream image). Server runner for **SkyReels-V2-I2V-14B-540P**
(image-to-video, 540P, Blackhole).

Design notes:
- I2V-14B checkpoint is in raw WAN 2.1 format (not diffusers). Uses
  `model_type="i2v"` with `in_channels=36` (16 noisy + 20 image/mask channels).
- TTNN acceleration uses the same `WanTransformer3DModel` backbone as WAN 2.2 T2V.
- Runner accepts `image` (base64 PNG/JPEG) from `VideoGenerateRequest`.
- Kept as a standalone file for the same import-safety reason as `skyreels_runner.py`.

---

## models/experimental/tt_dit/encoders/t5/model_t5.py

**Destination:** `~/tt-metal/models/experimental/tt_dit/encoders/t5/model_t5.py`

Bug fix for the TTNN T5 text encoder. Extracted verbatim from the running container,
with one targeted fix:

**Fix:** `orig_shape` referenced before assignment at line ~435.

`orig_shape` is only set inside `if tensor_parallel.factor > 1` (the unsqueeze
path for multi-chip all_gather). On single-chip (tp=1) the unsqueeze never
happens, `orig_shape` is never assigned, and the unconditional reshape at
line 435 crashes with `UnboundLocalError`.

**Fix applied:** The reshape block is guarded with the same `if tensor_parallel.factor > 1`
condition. For tp=1, `dense_out` already has the correct `(batch, seq_len, hidden)`
shape from `o_proj` and is returned directly.

---

## models/experimental/tt_dit/pipelines/motif/pipeline_motif.py

**Destination:** `~/tt-metal/models/experimental/tt_dit/pipelines/motif/pipeline_motif.py`

Extracted from the running container with two targeted fixes:

**Fix 1 — pybind11 ABI crash in `create_pipeline()`:**
`f"{mesh_device.shape}"` produces a pybind11 object repr that crashes the
`default_config` dict lookup. Fixed by wrapping with `tuple()`:
`tuple(mesh_device.shape)` at lines 94 and 289 (and all subsequent lookups).

**Fix 2 — missing (2,2) entry in `default_config`:**
The upstream `default_config` only covers `(2,4)` (T3K/P150X8) and `(4,8)`
(Galaxy). P300X2 uses a `(2,2)` mesh and would raise `KeyError`. Added a
`(2,2)` entry with single-chip submesh settings (tp=1, sp=1, cfg=1).

**Known issue — Motif transformer produces invalid latents on P300X2:**
All tested configurations (tp=2 full mesh, tp=4 with (1,4) reshape, tp=1
single-chip, single-chip + CPU PyTorch T5) produce noise or black output.
A diagnostic using the CPU PyTorch VAE decoder (`self._torch_vae`) confirmed
that the denoised latents themselves are invalid (all-zero output from a
correctly functioning VAE means zeroed/garbage transformer output). The
TTNN VAE decoder and T5 encoder are not the root cause. This is an upstream
issue: the Motif transformer DiT on P300X2 is unvalidated.

---

## tt_dit/pipelines/mochi/pipeline_mochi.py

**Destination:** `~/tt-metal/models/tt_dit/pipelines/mochi/pipeline_mochi.py`

**Note:** Applied only in `--dev-mode` (not by default server start).

Extracted from the running container; adds:
- `TT_DIT_CACHE_DIR` guard: warns at startup if the env var is unset and
  `reload_dit_model=True`, instead of silently recompiling on every start.

---

## tt_dit/pipelines/skyreels_v2/__init__.py

**Destination:** `~/tt-metal/models/tt_dit/pipelines/skyreels_v2/__init__.py`

**Note:** Applied only in `--dev-mode`.

Package init for the SkyReels-V2 TTNN pipeline package. Empty except for a
module docstring — required so Python treats the directory as a package when
`pipeline_skyreels.py` and `pipeline_skyreels_i2v.py` are imported.

---

## tt_dit/pipelines/skyreels_v2/pipeline_skyreels.py

**Destination:** `~/tt-metal/models/tt_dit/pipelines/skyreels_v2/pipeline_skyreels.py`

**Note:** Applied only in `--dev-mode`.

New file. TTNN pipeline for **SkyReels-V2-DF-1.3B-540P** text-to-video.

Key design points:
- Reuses `WanTransformer3DModel` TTNN backend — SkyReels-V2-DF weight keys are
  structurally identical to WAN 2.2 T2V (same AdaLN-Zero, self/cross-attention,
  FFN, conditioning embedder layout).
- SkyReels-only keys (`fps_embedding.*`, `fps_projection.*`) are silently dropped
  during weight loading.
- `_map_raw_skyreels_to_diffusers()` remaps raw checkpoint keys to diffusers-
  compatible names expected by the TTNN transformer.
- Outputs `np.ndarray` of shape `(1, T, H, W, C)` in `[0, 1]` range, matching
  the server runner interface.

---

## tt_dit/pipelines/skyreels_v2/pipeline_skyreels_i2v.py

**Destination:** `~/tt-metal/models/tt_dit/pipelines/skyreels_v2/pipeline_skyreels_i2v.py`

**Note:** Applied only in `--dev-mode`.

New file. TTNN pipeline for **SkyReels-V2-I2V-14B-540P** image-to-video.

Key design points:
- Checkpoint is in raw WAN 2.1 I2V format (not diffusers); `_map_raw_wan_i2v_to_diffusers()`
  handles key remapping.
- `model_type="i2v"` sets `in_channels=36`: 16 noisy latent channels + 20
  image/mask conditioning channels (VAE latents of the conditioning frame + binary mask).
- Image conditioning flows through the 36-channel spatial concatenation path,
  not through a separate cross-attention branch.
- `SkyReelsI2VTTNNTransformer` is a drop-in replacement for the diffusers
  transformer, used by `SkyReelsI2VPipeline`.

---

## tt_dit/pipelines/wan/pipeline_wan.py

**Destination:** `~/tt-metal/models/tt_dit/pipelines/wan/pipeline_wan.py`

**Note:** Applied only in `--dev-mode`.

Replaces the image's `pipeline_wan.py`. Primary fix:

**Fix — `_wan22_pipeline_args` prompt format:**
The upstream 0.17.0 image's `WanPipeline.__call__()` was updated to accept
`prompts: list[str]` instead of `prompt: str`. Our patch passes `"prompts": [prompt]`
(list-wrapped) to match the new signature. Without this fix, every Wan2.2 T2V
generation fails with a `TypeError` on the pipeline call.

---

## tt_dit/pipelines/wan/pipeline_wan_i2v.py

**Destination:** `~/tt-metal/models/tt_dit/pipelines/wan/pipeline_wan_i2v.py`

**Note:** Applied only in `--dev-mode`.

Extracted from the running container with one targeted fix:

**Fix — `cond_latents` type mismatch in `get_model_input()`:**
`prepare_latents()` returns `cond_latents` as a `torch.Tensor` (via `tt_y`),
but `get_model_input()` immediately passes it to `unflatten()` which calls
`ttnn.reshape()` — requiring a TTNN tensor. The upstream code crashes with
`TypeError: expected TTNN tensor`.

**Fix applied:** `get_model_input()` checks `isinstance(cond_latents, torch.Tensor)`
and converts to TTNN bf16 with `ttnn.from_torch()` before the reshape and concat.
All other code is identical to the container image.

---

## tt_dit/pipelines/wan/pipeline_wan_animate.py

**Destination:** `~/tt-metal/models/tt_dit/pipelines/wan/pipeline_wan_animate.py`

**Note:** Applied only in `--dev-mode`.

New file. TTNN pipeline for **Wan2.2-Animate-14B** (character animation /
video-to-video).

Key design points:
- Animate-14B is a fine-tune of I2V-A14B; the transformer and VAE architectures
  are identical — only checkpoint weights differ.
- Extra state dict keys present in the Animate checkpoint but absent from the
  TTNN `WanTransformer3DModel` are dropped via `strict=False`:
  `face_encoder.*`, `motion_encoder.*`, `face_adapter.*`,
  `added_kv_proj.*`, `condition_embedder.image_embedder.*`.
- `WanPipelineAnimate` subclasses `WanPipelineI2V`; the `mode` parameter
  (`"animation"` or `"replacement"`) is forwarded to the pipeline call.
- A motion video (MP4) supplies the motion pattern; a character image (PNG/JPEG)
  is the subject. The text prompt is optional (style guidance only).
