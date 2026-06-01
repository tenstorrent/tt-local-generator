# tt-local-generator — Performance Results

Hardware: **Tenstorrent QB2** (P300X2 — 2× P300 cards, 4× Blackhole chips, (2,2) mesh)
OS: Ubuntu 24.04 | Driver: KMD 2.8.0 | FW: 19.8.0.0
tt-inference-server: **v0.15.0** (image `0.15.0-25891d3`)

---

## Video Generation

| Model | Type | Resolution | Frames | Steps | Warmup | Generation | Notes |
|---|---|---|---|---|---|---|---|
| Wan2.2-T2V-A14B-Diffusers | Text→Video | 832×480 | 33 | 25 | ~6 min (cached) | ~360 s | Validated 2026-05-30 |
| SkyReels-V2-I2V-14B-540P | Image→Video | 480×272 | 9 | 10 | ~22 min (first run) | ~90 s | Validated 2026-05-30 |
| Wan2.2-Animate-14B-Diffusers | Text→Video | 832×480 | 33 | 20 | — | — | In validation |
| SkyReels-V2-DF-1.3B-540P | Text→Video | 480×272 | 33 | — | — | — | Not yet validated |
| Mochi-1-preview | Text→Video | 480×848 | 31 | 20 | ~4 min (cached) | ~6 min | ✅ Validated 2026-05-31 — 1.4MB; quality TBD at 64 steps |
| FLUX.1-schnell | Text→Image | 1024×1024 | 1 | 4 | ~3 min (cached) | **3.09 s** | ✅ Validated 2026-05-31 — no noise |

**Warmup note:** First-run TTNN kernel compilation takes 20–45 min depending on model size.
Subsequent starts load from compiled cache in 5–10 min.

---

## Artgen (LLM Text Generation)

Hardware: **Single P300 card** (chips 0+1, `p300` device type)
Image: `vllm-tt-metal-src-release-ubuntu-22.04-amd64:0.14.0-80180b9-7678b70`

| Model | Params | Device | Time to Ready | Throughput | Notes |
|---|---|---|---|---|---|
| Qwen3-8B | 8B | p300 (2 chips) | ~16 min | ~10 s/response | Validated 2026-05-30 |
| Llama-3.1-8B-Instruct | 8B | p300 (2 chips) | — | — | In validation |
| Qwen3-32B | 32B | p300x2 (4 chips) | — | — | In validation |
| Llama-3.3-70B-Instruct | 70B | p300x2 (4 chips) | — | — | In validation |
| DeepSeek-R1-Distill-Llama-70B | 70B | p300x2 (4 chips) | — | — | In validation |

---

## Utility Plugins (CPU inference via tenstorrent venv)

| Plugin | Model | Hardware | Latency | Notes |
|---|---|---|---|---|
| rmbg | briaai/RMBG-1.4 | CPU | ~15–30 s | Background removal |
| blip | Salesforce/blip-image-captioning-base | CPU | ~5–10 s | Image captioning |
| depth | vinvino02/glpn-kitti | CPU | ~3–5 s | Depth estimation |

---

## Validation Status

| Model | Status | Date | Issues Found |
|---|---|---|---|
| Wan2.2-T2V-A14B-Diffusers | ✅ PASS | 2026-05-30 | Prometheus chmod fix needed |
| SkyReels-V2-I2V-14B-540P | ✅ PASS | 2026-05-30 | Timestep dtype fix; runner map fix |
| Artgen Qwen3-8B | ✅ PASS | 2026-05-30 | Needs 0.14.0 vLLM image; /health empty |
| FLUX.1-schnell | ✅ PASS | 2026-05-31 | 3.09 s/image, no noise — vast improvement over FLUX.1-dev |
| Artgen Llama-3.1-8B-Instruct | ✅ PASS | 2026-05-30 | 5 min ready |
| Artgen Llama-3.3-70B-Instruct | ✅ PASS | 2026-05-30 | 2.5 min ready |
| Artgen Qwen3-32B | ✅ PASS | 2026-05-30 | 2 min ready |
| Mochi-1-preview | ✅ PASS | 2026-05-31 | 20 steps = 6 min; previous timeout was server 1000s default (fixed to 7200s) |
| Wan2.2-Animate-14B-Diffusers | ⚠️ BLOCKED | 2026-05-31 | ttnn.reshape type error in pipeline_wan_i2v.py warmup — needs deeper patch or v0.11.1 image bypass |

---

## Known Issues (v0.15.0)

| Issue | Affected | Fix |
|---|---|---|
| `PermissionError: /tmp/prometheus_multiproc` on first generation | All media server models | `start_*.sh` now auto-`chmod 777` after container start |
| Server returns HTTP 401 without `--no-auth` | All media server models | `--no-auth` added to all `start_*.sh` |
| `FileNotFoundError` for HF cache symlinked to `/mnt/bonus` | QB2 installations with non-standard model storage | `/mnt/bonus` bind-mounted in `apply_patches.sh` |
| VLLM artgen image v0.10.0 rejected by v0.15.0 run.py | Qwen3-8B, Llama artgen models | Pull `0.14.0-80180b9-7678b70`; `start_artgen.sh` auto-selects |
| `Wan2.2-Animate-14B-Diffusers` not in v0.15.0 model spec | start_animate.sh | Added via `apply_patches.sh` step 8 |
| SkyReels I2V timestep dtype mismatch | SkyReels-V2-I2V | Cast to float32 TTNN tensor before transformer forward |
| SkyReels I2V `No model runner found` | SkyReels-V2-I2V | Added to `MODEL_SERVICE_RUNNER_MAP` and `INFERENCE_MODEL_RUNNER_TO_MODEL_NAMES_MAP` |
