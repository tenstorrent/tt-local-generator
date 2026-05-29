# tt-inference-server v0.15.0 Compatibility Report

Generated: 2026-05-29  
Pinned: 0.11.1 (commit b5589534, image `ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34`)  
Target: v0.15.0 (commit 7fbac637, image `ghcr.io/tenstorrent/tt-media-inference-server:0.15.0-25891d3`)  
Commits ahead: 232 | Files changed: 300

---

## Executive Summary

**All 7 patch steps apply mechanically** — `apply_patches.sh` exits 0 against the v0.15.0 clone, all four bind-mount insertion blocks land correctly in `run_docker_server.py`, and both SkyReels ModelSpecTemplate injections succeed in `model_spec.py`.

**However: upgrading the container image is NOT a simple bump.** All 5 patched `.py` files have substantive upstream conflicts with the new internal API taxonomy (Animate → I2V, VLLM → VLLMForge, new SkyReels variants replaced by I2V variants). If the container image were changed from `0.11.1-bac8b34` to any newer image, our bind-mounted patch files would cause import-time failures.

**The safe upgrade path today:** bump `VENDOR_SHA` and all `REPO_SHA` pins to v0.15.0 for the orchestration layer (run_docker_server.py, model_spec.py, setup scripts) while keeping `--override-docker-image 0.11.1-bac8b34` in all start scripts. This gets us the latest infrastructure improvements without breaking the running container.

---

## Patch Status

| Patch | Dry-run result | Container compat | Action |
|---|---|---|---|
| `run_docker_server.py` — tt_dit bind-mount block | ✅ INSERTED | ✅ Compatible | None |
| `run_docker_server.py` — HF_HOME bind-mount block | ✅ INSERTED | ✅ Compatible | None (still needed — v0.15.0 does not pass HF_HOME into container env) |
| `run_docker_server.py` — media_server_config bind-mount block | ✅ INSERTED | ✅ Compatible | None |
| `model_spec.py` — SkyReels T2V injection | ✅ INSERTED | ✅ Compatible | None |
| `model_spec.py` — SkyReels I2V injection | ✅ INSERTED | ✅ Compatible | None |
| `constants.py` override | ✅ Applies (full replacement) | ⚠️ Container-only | Safe while container stays at 0.11.1-bac8b34; incompatible with 0.14.0+ containers |
| `video_generate_request.py` override | ✅ Applies (full replacement) | ⚠️ Container-only | Safe while container stays at 0.11.1-bac8b34; incompatible with 0.14.0+ containers |
| `dit_runners.py` override | ✅ Applies (full replacement) | ⚠️ Container-only | Safe while container stays at 0.11.1-bac8b34; incompatible with 0.14.0+ containers |
| `runner_fabric.py` override | ✅ Applies (full replacement) | ⚠️ Container-only | Safe while container stays at 0.11.1-bac8b34; incompatible with 0.14.0+ containers |
| `skyreels_runner.py` | ✅ No upstream conflict (file doesn't exist in v0.15.0) | ⚠️ Container-only | Safe while container stays at 0.11.1-bac8b34 |
| `skyreels_i2v_runner.py` | ✅ No upstream conflict (file doesn't exist in v0.15.0) | ⚠️ Container-only | Safe while container stays at 0.11.1-bac8b34 |
| `tt_dit/` pipeline files | ✅ Not in repo (patch tt-metal, not tt-inference-server) | ✅ Compatible | None |

---

## Docker Image Situation

| Script | Current image | Notes |
|---|---|---|
| `start_wan_qb2.sh` | `0.11.1-bac8b34` | Hardcoded `--override-docker-image` |
| All other start scripts | `0.11.1-bac8b34` | Same |
| v0.14.0 (Wan2.2 officially on QB2) | `0.14.0-80180b9` | New container; incompatible with our patches |
| v0.15.0 | `0.15.0-25891d3` | Audio uplift; no new video models |

SkyReels is not present in any official release model spec (v0.12.0–v0.15.0). Our injection is still the only way to run it.

---

## Key Upstream Changes in v0.12.0–v0.15.0

**v0.12.0:** LLM-only uplift (DeepSeek-R1-0528, GPT-OSS-120B). No video changes. Media server image unchanged.

**v0.13.0:** Forge model support for vision models (ResNet-50, VoVNet, MobileNetV2 etc.) via new `tt-media-inference-server-forge` container. No video generation changes.

**v0.14.0 ⭐ Most relevant:** Official Blackhole QuietBox 2 (P300X2) support for **Wan2.2-T2V-A14B** and **FLUX.1-dev**. Media server image bumped to `0.14.0-80180b9`. Also adds Qwen3-32B on T3K. The internal Python API was significantly refactored:
- `TTWan22AnimateRunner` removed; replaced by `TTWan22I2VRunner` and variants (PRODIA, ANISORA, DISTILL, LORA)
- `constants.py` taxonomy changed: `WAN_2_2_ANIMATE` → `WAN_2_2_I2V` (+ 4 variants)
- `VLLM` runner renamed to `VLLMForge`
- `VideoGenerateRequest` companion `VideoI2VGenerateRequest` added with `image_prompts: List[ImagePromptEntry]` for cleaner I2V API
- `SetupConfig.host_hf_cache` added as a first-class field (our `getattr` guard now redundant but harmless)

**v0.15.0:** Helm chart introduction, audio model uplifts (SpeechT5, Whisper on P150+P300X2), media server to `0.15.0-25891d3`. No video generation changes. Infrastructure/maintenance release.

---

## Specific Conflict Details

### `constants.py`
Our patch adds `TT_SKYREELS_V2`, `TT_SKYREELS_V2_I2V`, `TT_WAN_2_2_ANIMATE` entries. Upstream v0.15.0 deleted all of these and added `TT_WAN_2_2_I2V` (+ 4 variants), `VLLMForge`, `BGEM3`, `TRAINING_LORA`. These live inside the container, not the orchestration layer — our bind-mount fully replaces the container's copy so no runtime conflict occurs with the 0.11.1 container. Moving to a new container would require merging.

### `run_docker_server.py` — HF_HOME
v0.15.0 adds `SetupConfig.host_hf_cache` as a CLI flag (`--host-hf-cache`), but does **not** propagate `HF_HOME` or `HF_HUB_CACHE` into the container env vars. Our bind-mount + env-var injection is still the correct approach. If we ever adopt `SetupConfig` fully, our patch could be replaced by passing `--host-hf-cache ~/.cache/huggingface` — but our start scripts don't use `setup_host.py`'s `SetupConfig`, so this is future work.

### `run_docker_server.py` — Insertion anchor
The anchor `"for key, value in docker_env_vars.items():"` still exists at line 391 of the 903-line v0.15.0 file (was ~line 280 of ~700-line 0.11.1 file). The script's structure has grown but the anchor is intact. `apply_patches.sh` successfully inserts all three blocks.

---

## New Features to Consider Adopting

1. **v0.14.0 official QB2 support for Wan2.2 T2V** — The new `0.14.0-80180b9` container has official Blackhole P300X2 support. When we're ready to upgrade the container, this is the primary reason. The `dit_runners.py` and `runner_fabric.py` rewrites in v0.14.0 clean up some of our workarounds.

2. **`VideoI2VGenerateRequest` with `image_prompts`** — The new domain type for I2V requests is cleaner than our extension of `VideoGenerateRequest`. When upgrading to 0.14.0+ containers, this is the API to use for SkyReels I2V.

3. **`SetupConfig.host_hf_cache` flag** — Could eventually replace our manual HF_HOME injection if we adopt the `setup_host.py` workflow.

4. **Helm chart (v0.15.0)** — Not relevant to our direct QB2 usage but useful if tt-local-generator ever runs in a k8s context.

---

## Recommendation

### Safe to do now (no hardware risk)
- Bump `vendor/VENDOR_SHA` to `7fbac63736f10a601b7bf13ce928a0f634b79cc3` (v0.15.0)
- Update `debian/postinst` and documentation to reference v0.15.0
- Re-run `./bin/apply_patches.sh` against the new vendor
- All start scripts keep `--override-docker-image 0.11.1-bac8b34` — container unchanged

### Requires patch rewrite before hardware testing
- Upgrading the media server container to `0.14.0-80180b9` or `0.15.0-25891d3`
- All 5 patched `.py` files need to be rewritten against the new API:
  - `constants.py`: merge our SkyReels/Animate entries with v0.14.0's I2V taxonomy
  - `runner_fabric.py`: add SkyReels runners to v0.14.0's expanded registry
  - `dit_runners.py`: port `TTSkyReelsRunner`/`TTSkyReelsI2VRunner` to use new helper patterns
  - `video_generate_request.py`: adopt `VideoI2VGenerateRequest` for I2V, keep our extra fields for backward compat
  - Re-evaluate whether SkyReels runners still work with the new container internals

### Not yet possible (upstream gap)
- SkyReels is absent from all official release model specs (v0.12.0–v0.15.0). Our injection remains the only path.

---

## Next Steps (after human approval)

### Phase 1 — Vendor bump only (safe, no hardware)
1. `echo "7fbac63736f10a601b7bf13ce928a0f634b79cc3" > vendor/VENDOR_SHA`
2. Run `./bin/setup_vendor.sh && ./bin/apply_patches.sh`
3. Update `debian/changelog` and documentation

### Phase 2 — Container upgrade (requires hardware validation)
1. Rewrite the 5 conflicting patch files against v0.14.0 API
2. Test with `--override-docker-image 0.14.0-80180b9` for Wan2.2 T2V only
3. Validate SkyReels still works or implement updated runners
4. Update all start scripts to remove `--override-docker-image` override
