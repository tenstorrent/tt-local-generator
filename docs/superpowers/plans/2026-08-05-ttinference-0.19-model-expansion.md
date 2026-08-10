# tt-inference-server 0.19 Get-Current + Media Model Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring tt-local-generator current with tt-inference-server v0.19.0 (hygiene + dead-patch repair) and surface two already-shipped media models — Wan2.2-I2V (image-to-video) and FLUX.1-dev (higher-quality image) — end to end (start script → server_manager → Create picker/routing → `.deb`).

**Architecture:** All new-model support is **additive**: the two models are already in the vendored upstream media catalog (`dev/video.yaml` / `dev/image.yaml`, P300X2, COMPLETE) so **no patch registers them** — the work is start scripts + a `ServerDef` each + picker/routing wiring + a weights-download `.deb` each. Hardware validation is the user's, on QB2; automated tests cover only pure/wiring logic (`ServerDef` present, picker lists it, routing maps it, `collect()` byte-identical). The hygiene track repairs `apply_patches.sh` Steps 7/8/9 (dead since 0.18.0's model_spec.py→YAML migration) and re-pins the vendor SHA.

**Tech Stack:** Python 3 (system `/usr/bin/python3`), GTK4/PyGObject, bash start scripts, Debian packaging (debhelper 13, debconf), pytest (via `xvfb-run`).

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the spec.

- **Hardware validation is the USER'S, on QB2.** No task may claim a start script "works" or a `runner_key` "matches" — those are QB2 acceptance steps in Task 9. Automated tests cover wiring only, never generation.
- **Fragile QB2 chip** (`reference_qb2_card924055_fragility`): new models route through the existing **confirm-before-switch** ready-to-run gate (`app/ready_to_run.py`); **never auto-switch backends.** No task adds an auto-start/auto-switch path.
- **`collect()` / `_collect_params()` byte-identical**: new models are additive picker entries; adding them MUST NOT change the generated params dict for any existing model. Every app-wiring task ends with a collect-equality assertion.
- **`_CSS` / `b"""..."""` byte literals are ASCII-only** (non-ASCII → SyntaxError). Glyphs live only in Python `str` labels, never inside a bytes CSS literal.
- **GTK is single-threaded**: worker threads touch widgets only via `GLib.idle_add`. (No task here should need a new thread; flag if one seems required.)
- **System python**: run/test with `/usr/bin/python3`, never a venv.
- **Version discipline**: `VERSION` → `0.76.0` (normal minor; the 1.0.0 major stamp is a release-cut decision, NOT made here). Prepend a `debian/changelog` stanza. (Done once, in Task 9 — do not bump per task.)
- **Patch philosophy — minimize divergence**: the Animate registration moves to the YAML era (a `video.yaml` append mirroring Step 6), NOT a revived `model_spec.py` hack. Prefer wrapping/reuse over whole-file copies (e.g. `start_flux_dev.sh` wraps `start_flux.sh --dev`).
- **Canonical media image** for media start scripts is `ghcr.io/tenstorrent/tt-media-inference-server:0.18.0-c49bb76` (v0.19.0 did NOT move the media image — it is an LLM-only release). Only the artgen **vLLM** image bumps to `0.19.0-b204341-9bd099c`.
- **Local commits only. Do NOT push, open a PR, or merge.** Frequent commits per task.
- **Known-flake deselects** for full-suite runs (Task 9):
  `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`,
  `tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`,
  `tests/test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen`.

## Confirmed values (extracted from the repo; no guessing)

These were verified against the working tree and the vendored media config; tasks below use them verbatim.

- **`runner_key` (port-8000 `runner_in_use`)** — present in vendored `patches/media_server_config/config/constants.py` `ModelRunners` enum and wired in `runner_fabric.py` `AVAILABLE_RUNNERS`:
  - Wan2.2-I2V → **`tt-wan2.2-i2v`** (`ModelRunners.TT_WAN_2_2_I2V`)
  - FLUX.1-dev → **`tt-flux.1-dev`** (`ModelRunners.TT_FLUX_1_DEV`)
  - **Both are strong (in the shipped image config) but flagged for hardware confirm in Task 9.** A wrong value silently reads the server as unhealthy (`_check_sdef` asserts `data["runner_in_use"] == sdef.runner_key`).
- **`start_flux.sh` already accepts `--dev`** → `MODEL="FLUX.1-dev"` and already pins the `0.18.0-c49bb76` media image → `start_flux_dev.sh` is a thin wrapper (Task 4), not a copy.
- **`start_wan_qb2.sh`** pins `0.18.0-c49bb76`, **non-dev mode** (its header: "dev mode breaks device init on this image"), P300X2. Wan2.2-I2V is in-catalog (no bind-mount patch) so it needs **no `--dev-mode`** either → `start_wan_i2v.sh` mirrors `start_wan_qb2.sh` (Task 3), NOT the `--dev-mode`-using `start_skyreels_i2v.sh`.
- **Existing `tt-model-flux` package already downloads `black-forest-labs/FLUX.1-dev`** (gated). See Task 8 for the reconciliation (flagged for user veto).
- **Model-id maps are duplicated** in `app/main_window.py` AND `app/create_param_panels.py` (`_VIDEO_MODEL_IDS` / `_IMAGE_MODEL_IDS`). Both copies must gain each new key or routing silently defaults. `_VIDEO_MODEL_ID_TO_KEY` / `_IMAGE_MODEL_ID_TO_KEY` in `main_window.py` are derived (`{v: k …}`) — no manual edit.
- **Picker enumeration** (`create_view._scoped_model_keys`): a new `ServerDef` with `capabilities=("video",)` / `("image",)` appears automatically via `server_manager.servers_for_capability(cap)`. The Video branch hand-wraps the list: `keys = ["animatediff"] + keys + ["animate"]` — so a new video key lands in the middle slice, breaking the exact-list test (Task 5 updates it).

---

## Task 1: `apply_patches.sh` — Animate → `video.yaml` append; retire Steps 8/9

**Files:**
- Modify: `bin/apply_patches.sh` (Step 7 rewrite ~409-490; delete Steps 8 ~492-568 and 9 ~570-609; header docstring ~1-13)
- Create: `tests/test_apply_patches_animate.py`

**Interfaces:**
- Consumes: Step 6's idempotent-append shape (the `MODEL_SPEC_YAML="$TT_INFER/workflows/model_specs/dev/video.yaml"` target, the marker-based "skip if weights already present" guard, single-backup-once).
- Produces: nothing consumed by later tasks.

**Context:** Step 7 (Animate) currently injects a `ModelSpecTemplate(...)` into `workflows/model_spec.py`, whose anchor died in the 0.18.0 YAML migration — dead code that prints "could not find insertion anchor." Steps 8 (DeepSeek) and 9 (SDXL) target the same dead anchor AND Step 8 depends on Step 7's injected marker. All three are LLM/experimental models we don't surface. Rewrite Step 7 to append the Animate entry to `dev/video.yaml` exactly like Step 6 appends SkyReels; delete Steps 8/9 outright.

The Animate fields to translate (from the dead `ANIMATE_ENTRY` `ModelSpecTemplate`): weights `Wan-AI/Wan2.2-Animate-14B-Diffusers`, `impl: tt_transformers`, `min_disk_gb: 60`, `min_ram_gb: 32`, `model_type: VIDEO`, `inference_engine: MEDIA`, single device `P300X2` (`max_concurrency: 1`, `max_context: 65536`, `default_impl: true`, `override_tt_config.trace_region_size: 30000000`), `status: COMPLETE`. Include the `env_vars.TT_DIT_CACHE_DIR` line the SkyReels YAML entries carry (consistency within `video.yaml`).

- [ ] **Step 1: Write the failing test**

`tests/test_apply_patches_animate.py` — a pure text/idempotency test on a temp `video.yaml`, mirroring how Step 6 mutates. It shells the Step-7 python fragment? No — extract the logic by invoking the same append behavior directly on a temp file via a tiny helper the test defines inline (the test asserts the *shape*, since the script itself is bash). Simplest robust form: the test runs the real script fragment by copying Step 7's python heredoc body into a temp module is brittle — instead, assert against the SCRIPT TEXT + a functional temp-file run of the extracted python. Use this concrete approach:

```python
import subprocess, sys, textwrap
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "bin" / "apply_patches.sh"

def test_step7_targets_video_yaml_not_model_spec_py():
    text = SCRIPT.read_text()
    # Step 7 must append to the YAML catalog, not the dead model_spec.py anchor.
    assert 'workflows/model_specs/dev/video.yaml' in text
    assert 'Wan-AI/Wan2.2-Animate-14B-Diffusers' in text
    # The dead ModelSpecTemplate injection for Animate is gone.
    assert 'ModelSpecTemplate(' not in text or 'Wan2.2-Animate' not in _animate_block(text)

def test_steps_8_and_9_are_retired():
    text = SCRIPT.read_text()
    assert 'DeepSeek-R1-Distill-Llama-70B' not in text
    assert 'stable-diffusion-xl-base-1.0-img-2-img' not in text
    # Header no longer advertises 9 steps.
    assert 'Step 8' not in text and 'Step 9' not in text

def test_animate_yaml_append_is_idempotent(tmp_path):
    # Extract Step 7's python heredoc and run it twice against a temp video.yaml.
    body = _extract_step7_python(SCRIPT.read_text())
    yaml = tmp_path / "video.yaml"
    yaml.write_text("- weights:\n    - Existing/Model\n")
    for _ in range(2):
        subprocess.run([sys.executable, "-c", body, str(yaml)], check=True)
    text = yaml.read_text()
    assert text.count("Wan-AI/Wan2.2-Animate-14B-Diffusers") == 1  # appended once

def _extract_step7_python(script_text):
    # The Step 7 heredoc is delimited by <<'PYEOF' ... PYEOF after the Animate echo.
    start = script_text.index("Wan2.2-Animate-14B-Diffusers YAML entry")
    heredoc_open = script_text.index("<<'PYEOF'", start) + len("<<'PYEOF'")
    heredoc_close = script_text.index("PYEOF", heredoc_open)
    return textwrap.dedent(script_text[heredoc_open:heredoc_close]).strip()

def _animate_block(text):
    i = text.find('Wan2.2-Animate')
    return text[max(0, i-200):i+200] if i >= 0 else ''
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_apply_patches_animate.py -v`
Expected: FAIL — Step 7 still references `model_spec.py`/`ModelSpecTemplate`; Steps 8/9 strings still present.

- [ ] **Step 3: Rewrite Step 7 in `bin/apply_patches.sh`**

Replace the entire Step 7 block (the `MODEL_SPEC="$TT_INFER/workflows/model_spec.py"` echo + its `PYEOF` heredoc) with a `video.yaml` append modeled on Step 6:

```bash
# ── Step 7: Register Wan2.2-Animate-14B in the 0.18.0 YAML model catalog ─────
#
# Was a model_spec.py ModelSpecTemplate injection; that anchor died in the
# 0.18.0 YAML migration (see Step 6). Now an idempotent append to
# dev/video.yaml, the same shape as the SkyReels append above.
echo "7. Patching $MODEL_SPEC_YAML (Wan2.2-Animate-14B-Diffusers YAML entry)"

python3 - "$MODEL_SPEC_YAML" <<'PYEOF'
import sys, shutil, pathlib

p = pathlib.Path(sys.argv[1])
if not p.exists():
    print(f"ERROR: model spec catalog not found at {p}")
    sys.exit(1)

text = p.read_text()
MARKER = "Wan-AI/Wan2.2-Animate-14B-Diffusers"
ENTRY = """
- weights:
    - Wan-AI/Wan2.2-Animate-14B-Diffusers
  impl: tt_transformers
  min_disk_gb: 60
  min_ram_gb: 32
  model_type: VIDEO
  inference_engine: MEDIA
  env_vars:
    TT_DIT_CACHE_DIR: /home/container_app_user/cache_root/tt_dit_cache
  device_model_specs:
    - device: P300X2
      max_concurrency: 1
      max_context: 65536  # 64 * 1024
      default_impl: true
      override_tt_config:
        trace_region_size: 30000000
  status: COMPLETE
"""

if MARKER in text:
    print("   Wan2.2-Animate-14B: already patched — nothing to do")
    sys.exit(0)

backup = p.with_suffix(".yaml.bak")
shutil.copy2(p, backup)
p.write_text(text + ENTRY)
print(f"   Wan2.2-Animate-14B: appended ✓  (backup: {backup.name})")
PYEOF
```

(The `MODEL_SPEC_YAML` var is already defined in Step 6 and is in scope. Ensure Step 7's echo string contains the literal `Wan2.2-Animate-14B-Diffusers YAML entry` the test's `_extract_step7_python` anchors on.)

- [ ] **Step 4: Delete Steps 8 and 9**

Remove the entire Step 8 (`# ── Step 8: Bump DeepSeek …` through its `PYEOF`) and Step 9 (`# ── Step 9: Bump SDXL …` through its `PYEOF`) blocks. If a `MODEL_SPEC="$TT_INFER/workflows/model_spec.py"` assignment is now unreferenced anywhere in the script, delete it too.

- [ ] **Step 5: Update the header docstring**

In the `bin/apply_patches.sh` header comment (~1-13), change any "9 steps" wording and the Step 7/8/9 descriptions to reflect: Step 7 is now a `video.yaml` append (like Step 6); Steps 8/9 are retired. Keep the Step 2/4/5/6 descriptions.

- [ ] **Step 6: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_apply_patches_animate.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bin/apply_patches.sh tests/test_apply_patches_animate.py
git commit -m "fix(patches): Step 7 Animate → video.yaml append; retire dead Steps 8/9"
```

---

## Task 2: Hygiene — stray `.pyc`, artgen vLLM image, vendor SHA re-pin

**Files:**
- Delete (git): `patches/media_server_config/config/__pycache__/constants.cpython-312.pyc`
- Modify: `.gitignore`; `bin/start_artgen.sh` (image-selection block ~40-51); `vendor/VENDOR_SHA`; `bin/snapshot_vendor.sh` (~27-31)

**Interfaces:** none consumed/produced by other tasks.

**Context:** Pure config/hygiene, no app code, no test (bash + text). Verify each edit by reading the file back.

- [ ] **Step 1: Remove the stray compiled artifact and ignore future ones**

```bash
git rm patches/media_server_config/config/__pycache__/constants.cpython-312.pyc
```

Append to `.gitignore` (repo root), if not already present:

```gitignore
# Compiled bytecode inside patches/ (never track)
patches/**/__pycache__/
patches/**/*.pyc
```

- [ ] **Step 2: Bump the preferred artgen vLLM image**

In `bin/start_artgen.sh`, prepend a new probe branch ABOVE the existing `0.14.0-80180b9-7678b70` probe so the 0.19.0 image is preferred when present, keeping all existing branches as fallbacks:

```bash
_GHCR="ghcr.io/tenstorrent/tt-inference-server/vllm-tt-metal-src-release-ubuntu-22.04-amd64"
# Prefer the newest v0.19.0 image (Llama-3.1-8B P300 uplift, newer tt-metal base).
# Fall back to older pulled images if 0.19.0 is not yet present.
if docker image inspect "$_GHCR:0.19.0-b204341-9bd099c" &>/dev/null 2>&1; then
    _QB2_IMAGE="$_GHCR:0.19.0-b204341-9bd099c"
elif docker image inspect "$_GHCR:0.14.0-80180b9-7678b70" &>/dev/null 2>&1; then
    _QB2_IMAGE="$_GHCR:0.14.0-80180b9-7678b70"
elif docker image inspect "$_GHCR:0.11.1-bac8b34-7c6685a" &>/dev/null 2>&1; then
    _QB2_IMAGE="$_GHCR:0.11.1-bac8b34-7c6685a"
else
    # qb2_launch is v0.10.0 — run.py will reject it with a modern vendor.
    # If this is hit, pull one of the images above first.
    _QB2_IMAGE="$_GHCR:qb2_launch-555f240-22be241"
fi
```

- [ ] **Step 3: Re-pin the vendor SHA**

Set `vendor/VENDOR_SHA` (single line, no trailing prose) to:

```
399ce0b5c98067fd41cc3ba978d2742b15e8ac4e
```

- [ ] **Step 4: Update `bin/snapshot_vendor.sh` SHA + stale comments**

Replace the `DEFAULT_SHA` line and its comment (~27-31):

```bash
# SHA pinned to tt-inference-server v0.19.0 (LLM-only point release).
# Media image is unchanged from 0.18.0: tt-media-inference-server:0.18.0-c49bb76
# Artgen vLLM image: vllm-tt-metal-src-release …:0.19.0-b204341-9bd099c
# Update this when bumping the inference server version.
DEFAULT_SHA="399ce0b5c98067fd41cc3ba978d2742b15e8ac4e"
```

- [ ] **Step 5: Verify**

Read back `.gitignore`, `bin/start_artgen.sh` (the block), `vendor/VENDOR_SHA`, `bin/snapshot_vendor.sh` (the block) and confirm each edit landed. Confirm the `.pyc` is gone: `git status --porcelain patches/` shows the deletion staged and no other `.pyc` tracked.

- [ ] **Step 6: Commit**

```bash
git add -A .gitignore bin/start_artgen.sh vendor/VENDOR_SHA bin/snapshot_vendor.sh patches/
git commit -m "chore(vendor): re-pin v0.19.0, artgen vLLM 0.19.0 image, drop stray .pyc"
```

---

## Task 3: `bin/start_wan_i2v.sh` — new Wan2.2-I2V start script

**Files:**
- Create: `bin/start_wan_i2v.sh` (modeled on `bin/start_wan_qb2.sh`)

**Interfaces:**
- Produces: script filename `start_wan_i2v.sh` (consumed by the Task 5 `ServerDef.script`).

**Context:** Wan2.2-I2V-A14B is in the shipped `dev/video.yaml` (P300X2, COMPLETE) → NO patch, NO `--dev-mode`. Model this on `start_wan_qb2.sh` (same media image `0.18.0-c49bb76`, non-dev mode, P300X2, `--host-hf-cache`, `--gui`/`--stop`), changing only the model id, the log glob, and the header. Do NOT model it on `start_skyreels_i2v.sh` (that one needs `--dev-mode` for a bind-mounted pipeline patch Wan2.2-I2V does not have).

- [ ] **Step 1: Read the template**

Read `bin/start_wan_qb2.sh` in full. Note: header (~1-19), `HF_CACHE`/`DOCKER_IMAGE`/`LOG_DIR`/`LOG_GLOB` (~34-37), the `--stop`/`--gui` flag handling, the `run.py` invocation, the health-wait/tail logic.

- [ ] **Step 2: Create `bin/start_wan_i2v.sh`**

Copy `start_wan_qb2.sh` verbatim, then apply exactly these changes:

1. Header: retitle to "Start the Wan2.2-I2V-A14B-Diffusers (image-to-video) server on P300x2 (QB2)"; keep the same "Non-dev mode … known-working configuration" notes and the `0.18.0-c49bb76` image line.
2. The `run.py --model` value → `Wan-AI/Wan2.2-I2V-A14B-Diffusers`.
3. `LOG_GLOB="media_*_Wan2.2-I2V-A14B-Diffusers_p300x2_server.log"`.
4. Any other literal occurrence of `Wan2.2-T2V-A14B-Diffusers` (log messages, `--stop` filter comments) → `Wan2.2-I2V-A14B-Diffusers`.
5. Keep the `DOCKER_IMAGE`, `--host-hf-cache "$HF_CACHE"`, `--tt-device p300x2`, `--engine media`, `--docker-server`, `--no-auth`, non-`--dev-mode` invocation identical in shape.

Make it executable:

```bash
chmod +x bin/start_wan_i2v.sh
```

- [ ] **Step 3: Verify shape (syntax + flags, NOT a live launch)**

```bash
bash -n bin/start_wan_i2v.sh          # syntax check only
grep -c 'Wan2.2-T2V' bin/start_wan_i2v.sh   # expect 0 — no stale T2V references
grep -c 'dev-mode' bin/start_wan_i2v.sh     # expect 0 — I2V is in-catalog, non-dev
grep 'Wan-AI/Wan2.2-I2V-A14B-Diffusers' bin/start_wan_i2v.sh   # model + log glob present
```

Expected: `bash -n` clean; T2V count 0; dev-mode count 0; model string present. (A real launch is a QB2 acceptance step in Task 9.)

- [ ] **Step 4: Commit**

```bash
git add bin/start_wan_i2v.sh
git commit -m "feat(bin): start_wan_i2v.sh — Wan2.2-I2V-A14B on P300X2 (non-dev)"
```

---

## Task 4: `bin/start_flux_dev.sh` — thin wrapper over `start_flux.sh --dev`

**Files:**
- Create: `bin/start_flux_dev.sh`

**Interfaces:**
- Produces: script filename `start_flux_dev.sh` (consumed by the Task 6 `ServerDef.script`).

**Context:** `start_flux.sh` already accepts `--dev` → `MODEL="FLUX.1-dev"` and already pins `0.18.0-c49bb76`. Per the patch-philosophy constraint (prefer reuse over copies), `start_flux_dev.sh` is a thin wrapper that forwards all args plus `--dev`. This means a fix to `start_flux.sh` never has to be mirrored, and the `--gui`/`--stop` flags the app passes compose through.

- [ ] **Step 1: Create `bin/start_flux_dev.sh`**

```bash
#!/usr/bin/env bash
# start_flux_dev.sh — Start the FLUX.1-dev image server on P300x2 (QB2).
#
# Thin wrapper over start_flux.sh, which already supports --dev (FLUX.1-dev,
# higher quality, ~34 GB gated weights) and pins the 0.18.0-c49bb76 media image.
# Forwarding here keeps a single source of truth for the FLUX launch logic —
# any fix to start_flux.sh applies to both schnell and dev automatically.
#
# All flags (--gui, --stop, --restart, …) pass straight through.
set -euo pipefail
_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$_DIR/start_flux.sh" --dev "$@"
```

```bash
chmod +x bin/start_flux_dev.sh
```

- [ ] **Step 2: Verify shape**

```bash
bash -n bin/start_flux_dev.sh                        # syntax check
grep -q 'start_flux.sh" --dev' bin/start_flux_dev.sh # forwards --dev
```

Expected: clean; the `exec … start_flux.sh --dev "$@"` line present. Confirm `start_flux.sh --stop` semantics still make sense through the wrapper (the `--stop` filters by the shared media image `ancestor`, so `start_flux_dev.sh --stop` stops the same container — acceptable; note in Task 9's acceptance checklist).

- [ ] **Step 3: Commit**

```bash
git add bin/start_flux_dev.sh
git commit -m "feat(bin): start_flux_dev.sh — FLUX.1-dev wrapper over start_flux.sh --dev"
```

---

## Task 5: Wan2.2-I2V app wiring — ServerDef, picker, routing

**Files:**
- Modify: `app/server_manager.py` (`SERVERS` list; `MODEL_DISPLAY_NAMES`)
- Modify: `app/main_window.py` (`_VIDEO_MODEL_IDS`; `_native_generate_args` video seed-image guard)
- Modify: `app/create_param_panels.py` (`_VIDEO_MODEL_IDS` mirror)
- Modify: `tests/test_create_view_video_models.py` (exact-list assertion)
- Create/Modify: `tests/test_server_manager_wan_i2v.py`, `tests/test_native_generate_args.py` (append cases)

**Interfaces:**
- Consumes: `start_wan_i2v.sh` (Task 3).
- Produces: `server_manager.SERVERS["wan2.2-i2v"]` (cap `("video",)`, `runner_key="tt-wan2.2-i2v"`); canonical model id `"wan2.2-i2v-a14b"` ↔ server key `"wan2.2-i2v"` in both `_VIDEO_MODEL_IDS` copies.

**Context:** Wan2.2-I2V is an image→video model → lives in the **Video** medium's scoped picker beside SkyReels-I2V; both require a seed image. The routing must raise the same `_NativeGenerateGuardError` SkyReels raises when `seed_image_path` is empty.

- [ ] **Step 1: Write failing server_manager test**

`tests/test_server_manager_wan_i2v.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import server_manager as sm

def test_wan_i2v_serverdef_present_and_video_capable():
    sd = sm.SERVERS["wan2.2-i2v"]
    assert sd.script == "start_wan_i2v.sh"
    assert sd.capabilities == ("video",)
    assert sd.runner_key == "tt-wan2.2-i2v"
    assert sd.health_url == "http://localhost:8000/tt-liveness"
    assert sm.SERVERS["wan2.2-i2v"] in sm.servers_for_capability("video")

def test_wan_i2v_display_name_and_benefit():
    assert sm.display_name_for("wan2.2-i2v") == "Wan2.2 I2V"
    assert sm.benefit_for("wan2.2-i2v")  # non-empty tagline
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_server_manager_wan_i2v.py -v`
Expected: FAIL — `KeyError: 'wan2.2-i2v'`.

- [ ] **Step 3: Add the `ServerDef` + display name**

In `app/server_manager.py`, add to the `SERVERS` list (insert immediately AFTER the `skyreels` `ServerDef` so the Video picker groups the two I2V models):

```python
        ServerDef(
            key="wan2.2-i2v",
            label="Wan2.2-I2V-A14B  (Blackhole)",
            script="start_wan_i2v.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-wan2.2-i2v",
            capabilities=("video",),
            benefit="Animate a still image into video (image-to-video). Blackhole P300X2.",
        ),
```

Add to `MODEL_DISPLAY_NAMES`:

```python
    "wan2.2-i2v": "Wan2.2 I2V",
```

- [ ] **Step 4: Run to verify server_manager test passes**

Run: `/usr/bin/python3 -m pytest tests/test_server_manager_wan_i2v.py -v`
Expected: PASS.

- [ ] **Step 5: Write failing routing test (append to `tests/test_native_generate_args.py`)**

Follow the existing fixture style (`_mw()`, `VIDEO = Medium(id="video", …)`):

```python
def test_video_wan_i2v_with_seed_image_routes_video():
    obj = _mw()
    params = {"prompt": "hi", "model": "wan2.2-i2v-a14b",
              "seed_image_path": "/tmp/seed.png"}
    _args, kwargs = obj._native_generate_args(VIDEO, params)
    assert kwargs["model_source"] == "video"
    assert kwargs["video_model_key"] == "wan2.2-i2v"

def test_video_wan_i2v_without_seed_image_raises():
    obj = _mw()
    params = {"prompt": "hi", "model": "wan2.2-i2v-a14b", "seed_image_path": ""}
    with pytest.raises(mw._NativeGenerateGuardError):
        obj._native_generate_args(VIDEO, params)
```

(Import `pytest` at the top if not already imported.)

- [ ] **Step 6: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_native_generate_args.py -k wan_i2v -v`
Expected: FAIL — `wan2.2-i2v-a14b` maps to the `"wan2"` default (no key), no guard raised.

- [ ] **Step 7: Add the model-id maps (BOTH files) and the seed-image guard**

In `app/main_window.py`, add to `_VIDEO_MODEL_IDS`:

```python
    "wan2.2-i2v":   "wan2.2-i2v-a14b",
```

In `app/create_param_panels.py`, add the identical entry to its `_VIDEO_MODEL_IDS` mirror.

In `app/main_window.py` `_native_generate_args`, extend the SkyReels seed-image guard in the video branch to also cover `wan2.2-i2v`:

```python
        if model_key in ("skyreels", "wan2.2-i2v") and not params.get("seed_image_path"):
            raise _NativeGenerateGuardError(
                "This image-to-video model requires a starting image — add one "
                "to the seed image well before generating."
            )
```

(`_VIDEO_MODEL_ID_TO_KEY` is derived from `_VIDEO_MODEL_IDS`, so `"wan2.2-i2v-a14b"` → `"wan2.2-i2v"` resolves automatically; the rest of the video branch already sets `model_source="video"`, `video_model_key=model_key`.)

- [ ] **Step 8: Run to verify routing test passes**

Run: `/usr/bin/python3 -m pytest tests/test_native_generate_args.py -k wan_i2v -v`
Expected: PASS.

- [ ] **Step 9: Update the picker exact-list test**

In `tests/test_create_view_video_models.py`, update the exact-list assertion in `test_video_scoped_keys_animatediff_first_and_animate_present` to include the new key in its insertion position (after `skyreels`):

```python
    assert keys == ["animatediff", "wan2.2", "mochi", "skyreels", "wan2.2-i2v", "animate"]
```

Add a round-trip assertion mirroring the existing `wan2.2-animate-14b` one:

```python
    assert cv._VIDEO_MODEL_ID_TO_KEY["wan2.2-i2v-a14b"] == "wan2.2-i2v"
    assert create_param_panels._VIDEO_MODEL_IDS["wan2.2-i2v"] == "wan2.2-i2v-a14b"
```

(Match the module import alias the file already uses for `create_param_panels`.)

- [ ] **Step 10: Collect-equality guard**

Confirm the existing collect-equality tests (e.g. `tests/test_native_generate_args.py`'s unchanged-model cases, `test_create_view.py`) still pass unchanged — new entries are additive. Run:

```bash
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_native_generate_args.py tests/test_create_view_video_models.py -v
```

Expected: all PASS (existing `test_video_plain_model_unchanged` / `test_video_animatediff_model_unchanged` untouched).

- [ ] **Step 11: Commit**

```bash
git add app/server_manager.py app/main_window.py app/create_param_panels.py \
        tests/test_server_manager_wan_i2v.py tests/test_native_generate_args.py \
        tests/test_create_view_video_models.py
git commit -m "feat(create): wire Wan2.2-I2V into the Video picker + I2V seed-image routing"
```

---

## Task 6: FLUX.1-dev app wiring — ServerDef, picker, routing

**Files:**
- Modify: `app/server_manager.py` (`SERVERS`; `MODEL_DISPLAY_NAMES`)
- Modify: `app/main_window.py` (`_IMAGE_MODEL_IDS`)
- Modify: `app/create_param_panels.py` (`_IMAGE_MODEL_IDS` mirror)
- Create/Modify: `tests/test_server_manager_flux_dev.py`, `tests/test_native_generate_args.py` (append), and the image-picker test file if one exists (else add a case)

**Interfaces:**
- Consumes: `start_flux_dev.sh` (Task 4).
- Produces: `server_manager.SERVERS["flux-dev"]` (cap `("image",)`, `runner_key="tt-flux.1-dev"`); canonical model id `"flux.1-dev"` ↔ server key `"flux-dev"` in both `_IMAGE_MODEL_IDS` copies.

**Context:** The image branch of `_native_generate_args` already supplies `num_inference_steps` default 20 and `guidance_scale` 3.5 — these are dev-appropriate (NOT schnell's nominal 1-4), so **no per-model step branching is needed** and collect-equality is trivially preserved. (A true per-model default-step hook is a flagged follow-up in Task 9's notes, not built here.)

- [ ] **Step 1: Write failing server_manager test**

`tests/test_server_manager_flux_dev.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import server_manager as sm

def test_flux_dev_serverdef_present_and_image_capable():
    sd = sm.SERVERS["flux-dev"]
    assert sd.script == "start_flux_dev.sh"
    assert sd.capabilities == ("image",)
    assert sd.runner_key == "tt-flux.1-dev"
    assert sd.health_url == "http://localhost:8000/tt-liveness"
    assert sm.SERVERS["flux-dev"] in sm.servers_for_capability("image")

def test_flux_dev_display_name_and_benefit():
    assert sm.display_name_for("flux-dev") == "FLUX.1-dev"
    assert sm.benefit_for("flux-dev")  # non-empty tagline
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_server_manager_flux_dev.py -v`
Expected: FAIL — `KeyError: 'flux-dev'`.

- [ ] **Step 3: Add the `ServerDef` + display name**

In `app/server_manager.py`, add to `SERVERS` immediately AFTER the `flux` `ServerDef`:

```python
        ServerDef(
            key="flux-dev",
            label="FLUX.1-dev",
            script="start_flux_dev.sh",
            health_url="http://localhost:8000/tt-liveness",
            runner_key="tt-flux.1-dev",
            capabilities=("image",),
            benefit="Higher-fidelity image (more steps than schnell). Blackhole P300X2.",
        ),
```

Add to `MODEL_DISPLAY_NAMES`:

```python
    "flux-dev": "FLUX.1-dev",
```

- [ ] **Step 4: Run to verify server_manager test passes**

Run: `/usr/bin/python3 -m pytest tests/test_server_manager_flux_dev.py -v`
Expected: PASS.

- [ ] **Step 5: Write failing routing test (append to `tests/test_native_generate_args.py`)**

The file's `VIDEO` medium is defined; add an `IMAGE = Medium(id="image", …)` near it if not present (mirror the `VIDEO` construction), then:

```python
def test_image_flux_dev_routes_image_source():
    obj = _mw()
    params = {"prompt": "hi", "model": "flux.1-dev"}
    _args, kwargs = obj._native_generate_args(IMAGE, params)
    assert kwargs["model_source"] == "image"
    assert kwargs["image_model_key"] == "flux-dev"

def test_image_flux_schnell_unchanged():
    obj = _mw()
    params = {"prompt": "hi", "model": "flux.1-schnell"}
    _args, kwargs = obj._native_generate_args(IMAGE, params)
    assert kwargs["image_model_key"] == "flux"
```

- [ ] **Step 6: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_native_generate_args.py -k flux -v`
Expected: FAIL — `flux.1-dev` maps to the `"flux"` default.

- [ ] **Step 7: Add the model-id maps (BOTH files)**

In `app/main_window.py`, add to `_IMAGE_MODEL_IDS`:

```python
    "flux-dev":       "flux.1-dev",
```

In `app/create_param_panels.py`, add the identical entry to its `_IMAGE_MODEL_IDS` mirror. (`_IMAGE_MODEL_ID_TO_KEY` derives the inverse; the image branch already maps `image_model_key` from the key.)

- [ ] **Step 8: Run to verify routing test passes**

Run: `/usr/bin/python3 -m pytest tests/test_native_generate_args.py -k flux -v`
Expected: PASS (both new case and the schnell-unchanged case).

- [ ] **Step 9: Image-picker enumeration test**

Confirm a new image key appears in the Image scoped picker. If `tests/test_create_view_video_models.py` (or a sibling) has an image-picker exact-list test, update it to include `flux-dev` (it appends after `flux` via `servers_for_capability("image")` order — no hand-wrapping on the image branch, so it lands wherever the `SERVERS` order places it, i.e. right after `flux`). If no image-picker list test exists, add one mirroring the video one:

```python
def test_image_scoped_keys_include_flux_dev():
    view = cv.CreateView.__new__(cv.CreateView)
    view._active_medium = IMAGE
    view._status_service = None
    keys = view._scoped_model_keys(IMAGE)
    assert "flux" in keys and "flux-dev" in keys
```

(Reuse the file's `IMAGE` medium / import style; define `IMAGE` if absent.)

- [ ] **Step 10: Collect-equality + run**

```bash
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_native_generate_args.py tests/test_server_manager_flux_dev.py tests/test_create_view_video_models.py -v
```

Expected: all PASS; existing image-model behavior unchanged.

- [ ] **Step 11: Commit**

```bash
git add app/server_manager.py app/main_window.py app/create_param_panels.py \
        tests/test_server_manager_flux_dev.py tests/test_native_generate_args.py \
        tests/test_create_view_video_models.py
git commit -m "feat(create): wire FLUX.1-dev into the Image picker + routing"
```

---

## Task 7: `.deb` package `tt-model-wan2-i2v`

**Files:**
- Create: `debian/tt-model-wan2-i2v.postinst`, `debian/tt-model-wan2-i2v.config`, `debian/tt-model-wan2-i2v.templates`
- Modify: `debian/control` (new stanza + add to the `tt-local-generator-models-all` meta-package Depends)

**Interfaces:** none consumed by app tasks; parallels the existing `tt-model-*` pattern.

**Context:** `Wan-AI/Wan2.2-I2V-A14B-Diffusers` is **ungated** (like `Wan-AI/Wan2.2-T2V-A14B-Diffusers`), so model this on **`tt-model-wan2-t2v`** (ungated: token optional, no license-acceptance messaging), NOT on the gated `tt-model-flux`. `download_model.sh --repo <weights> --skip-if-exists --token "$TOKEN"` is the shared helper; the debconf key `tt-local-generator/hf-token` is shared across all model packages.

- [ ] **Step 1: Read the ungated template**

Read `debian/tt-model-wan2-t2v.postinst`, `.config`, `.templates` (the ungated precedent). Note the `runuser -u "$REAL_USER"` download call, the `db_reset` immediately after `db_get`, the `exit 0` on every failure path.

- [ ] **Step 2: Create the three maintainer scripts**

`debian/tt-model-wan2-i2v.postinst` — copy `tt-model-wan2-t2v.postinst` verbatim, changing only:

```sh
MODEL_REPO="Wan-AI/Wan2.2-I2V-A14B-Diffusers"
```

and any human-readable "Wan2.2-T2V"/"text-to-video" strings → "Wan2.2-I2V"/"image-to-video". Keep the `DOWNLOAD_HELPER`, debconf guard, `runuser`, and fail-soft `exit 0` shape identical.

`debian/tt-model-wan2-i2v.config` — copy `tt-model-wan2-t2v.config` verbatim (ungated token-search + optional prompt; shared key). No changes beyond any model-name string.

`debian/tt-model-wan2-i2v.templates` — copy `tt-model-wan2-t2v.templates` verbatim, changing the model name in the Description to Wan2.2-I2V and the size note (~large; use the same "~118 GB"-style phrasing as T2V unless you know the exact size — if unknown, say "large").

- [ ] **Step 3: Add the `debian/control` stanza**

After the `tt-model-wan2-t2v` stanza, add:

```
Package: tt-model-wan2-i2v
Architecture: all
Depends: tt-local-generator, debconf | debconf-2.0, python3-pip
Description: Wan2.2-I2V-A14B model weights for tt-local-generator
 Downloads Wan-AI/Wan2.2-I2V-A14B-Diffusers from HuggingFace into
 ~/.cache/huggingface/hub/. Required for the Wan2.2 image-to-video model
 in the Video generation surface.
 .
 The model is publicly available; a HuggingFace account is not required,
 though providing a token avoids API rate limits.
```

Add `tt-model-wan2-i2v,` to the `tt-local-generator-models-all` meta-package `Depends:` list.

- [ ] **Step 4: Verify `debian/control` parses**

```bash
/usr/bin/python3 -c "import deb822" 2>/dev/null && \
  /usr/bin/python3 -c "from debian import deb822; list(deb822.Deb822.iter_paragraphs(open('debian/control')))" \
  || dpkg-parsechangelog -l debian/changelog >/dev/null; echo "control readable"
```

If `python3-debian` is absent, fall back to a structural check: every `Package:` line is followed by `Architecture:` and `Description:`. Confirm the new stanza and the meta-package Depends line are well-formed.

- [ ] **Step 5: Commit**

```bash
git add debian/tt-model-wan2-i2v.postinst debian/tt-model-wan2-i2v.config \
        debian/tt-model-wan2-i2v.templates debian/control
git commit -m "feat(deb): tt-model-wan2-i2v weights package (ungated)"
```

---

## Task 8: `.deb` package `tt-model-flux-dev` + reconcile `tt-model-flux`

**Files:**
- Create: `debian/tt-model-flux-dev.postinst`, `debian/tt-model-flux-dev.config`, `debian/tt-model-flux-dev.templates`
- Modify: `debian/control` (new stanza + meta-package Depends); **(flagged)** `debian/tt-model-flux.*` retarget to schnell

**Interfaces:** none consumed by app tasks.

**Context — reconciliation (FLAGGED for user veto at the spec-review/final-review gate):** The existing `tt-model-flux` package **already downloads `black-forest-labs/FLUX.1-dev`** (gated), despite being named/described as the generic "flux" image package for the schnell-default server. This plan:

1. **Adds `tt-model-flux-dev`** (gated, `black-forest-labs/FLUX.1-dev`) — the canonical package for the new `flux-dev` server. Modeled on the current `tt-model-flux` (which is already the gated-dev shape).
2. **Retargets `tt-model-flux` → `black-forest-labs/FLUX.1-schnell`** (ungated, small) so the two packages map cleanly to the two servers (`flux`↔schnell, `flux-dev`↔dev) and there is no redundant 34 GB download. This is the recommended coherent end state for a major release.

**If the user vetoes step 2** (wants `tt-model-flux` left downloading dev), skip the retarget sub-steps and instead just make `tt-model-flux-dev` `Depends: tt-model-flux` (or note the redundancy). Present both the retarget and this fallback to the user at review; do not silently choose.

- [ ] **Step 1: Create `tt-model-flux-dev` (gated) from the current `tt-model-flux`**

Copy `debian/tt-model-flux.postinst`/`.config`/`.templates` to the `tt-model-flux-dev.*` names verbatim (they already target `black-forest-labs/FLUX.1-dev` and carry the gated-model token prompt). No content change needed beyond confirming `MODEL_REPO="black-forest-labs/FLUX.1-dev"` in the postinst.

- [ ] **Step 2: Add the `tt-model-flux-dev` control stanza**

```
Package: tt-model-flux-dev
Architecture: all
Depends: tt-local-generator, debconf | debconf-2.0, python3-pip
Description: FLUX.1-dev model weights (~34 GB) for tt-local-generator
 Downloads black-forest-labs/FLUX.1-dev from HuggingFace. Required for the
 FLUX.1-dev model in the Image generation surface.
 .
 FLUX.1-dev is a gated model — you must accept its license at
 https://huggingface.co/black-forest-labs/FLUX.1-dev and provide a
 HuggingFace token with read access before installing this package.
```

Add `tt-model-flux-dev,` to the meta-package `Depends:`.

- [ ] **Step 3: (FLAGGED) Retarget `tt-model-flux` → schnell**

In `debian/tt-model-flux.postinst`: `MODEL_REPO="black-forest-labs/FLUX.1-schnell"`; change "FLUX.1-dev"/"gated"/"~34 GB" strings → "FLUX.1-schnell"/"publicly available"/"~small".
In `debian/tt-model-flux.config`: keep the token-search but it may prompt optionally — mirror the ungated `tt-model-wan2-t2v.config` (token optional, `db_fset seen true` when found) since schnell is ungated.
In `debian/tt-model-flux.templates`: rewrite the Description for schnell (ungated, no license acceptance required; token only for rate-limit avoidance).
In `debian/control` `tt-model-flux` stanza: rewrite the Description to schnell (ungated).

- [ ] **Step 4: Verify `debian/control` parses**

Same check as Task 7 Step 4. Confirm both new/edited stanzas are well-formed and the meta-package lists both `tt-model-flux` and `tt-model-flux-dev`.

- [ ] **Step 5: Commit**

```bash
git add debian/tt-model-flux-dev.* debian/tt-model-flux.* debian/control
git commit -m "feat(deb): tt-model-flux-dev (gated dev) + retarget tt-model-flux → schnell"
```

(If the retarget is vetoed, commit only the `tt-model-flux-dev` additions with an amended message.)

---

## Task 9: Finalize — version, changelog, docs, full suite, QB2 acceptance checklist

**Files:**
- Modify: `VERSION`; `debian/changelog`; `CLAUDE.md`

**Interfaces:** consumes everything above.

- [ ] **Step 1: Bump `VERSION`**

Set `VERSION` (single line) to:

```
0.76.0
```

- [ ] **Step 2: Prepend a `debian/changelog` stanza**

Add a new top stanza (use `dch` or edit manually), version `0.76.0`, distribution `noble`, summarizing: re-pinned vendor to v0.19.0; repaired dead apply_patches Steps 7/8/9 (Animate → video.yaml, retired 8/9); artgen vLLM image → 0.19.0; added Wan2.2-I2V and FLUX.1-dev media models (start scripts, ServerDefs, Create wiring, weights packages); reconciled the flux packages. Keep the existing trailer format (maintainer + date line) consistent with the prior stanza.

- [ ] **Step 3: Update `CLAUDE.md`**

- "Vendored tt-inference-server" section → v0.19.0 (LLM-only point release; media image unchanged `0.18.0-c49bb76`; vendor SHA `399ce0b`).
- "Model registry migrated to YAML in 0.18.0" section → Steps 8/9 retired; Step 7 (Animate) is now a `dev/video.yaml` append like Step 6.
- Add a short "0.76.0 — media model expansion" note: Wan2.2-I2V (Video picker, I2V seed-image, `start_wan_i2v.sh`, `tt-model-wan2-i2v`) and FLUX.1-dev (Image picker, `start_flux_dev.sh` wraps `start_flux.sh --dev`, `tt-model-flux-dev`; `tt-model-flux` retargeted to schnell if the flagged reconcile was accepted).
- Note the two `runner_key`s used (`tt-wan2.2-i2v`, `tt-flux.1-dev`) and that they are hardware-confirm-pending.

- [ ] **Step 4: Full suite (documented deselects)**

```bash
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q \
  --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module \
  --deselect tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes \
  --deselect tests/test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen
```

Expected: green (plus the `test_regression_guards` env-skip when `docs/assets/` is absent). Fix any real regressions before committing.

- [ ] **Step 5: Commit**

```bash
git add VERSION debian/changelog CLAUDE.md
git commit -m "chore: VERSION 0.76.0 + changelog + docs for v0.19 get-current + I2V/FLUX-dev"
```

- [ ] **Step 6: QB2 hardware-acceptance checklist (USER-RUN — the controller performs these on the box, controlling backend churn per the fragile-chip rule; NOT part of the automated suite)**

Document these in the commit body / a short note; they are the real validation:

1. `./bin/apply_patches.sh` runs clean end to end; Step 7 appends the Animate entry to `dev/video.yaml` idempotently (second run says "already patched"); no "could not find insertion anchor" errors.
2. **Confirm `runner_key`s.** Start each new server via `tt-ctl` / the start script with `--gui`, then `curl -s http://localhost:8000/tt-liveness` and confirm `runner_in_use` == `tt-wan2.2-i2v` (Wan2.2-I2V) and `tt-flux.1-dev` (FLUX.1-dev). If either differs, update the `ServerDef.runner_key` and re-run the server_manager tests. **Stop/reset the backend between models** (`--stop` + `pipeline_engine._tt_smi_reset()` if needed) to minimize churn on the fragile chip.
3. Wan2.2-I2V: with a seed image, a Video generation produces an mp4; without a seed image, Create raises the guard (no server start).
4. FLUX.1-dev: an Image generation produces a higher-fidelity image than schnell at the app's default steps; confirm the media server accepts the app's step/guidance values (note the effective default-step count for the flagged follow-up).
5. `start_flux_dev.sh --stop` stops the FLUX container (shared media image ancestor).

**Flagged follow-ups (NOT built here):** (a) per-model default inference-steps for FLUX.1-dev if 20 proves too low on hardware; (b) the flux-package retarget decision if the user vetoed Task 8 Step 3; (c) media bind-mount patch drift-check (separate `docker create/cp` effort per CLAUDE.md patch philosophy).

---

## Self-Review (plan author)

**Spec coverage:**
- §A get-current/hygiene → Task 1 (Steps 7/8/9), Task 2 (.pyc, artgen image, VENDOR_SHA, snapshot_vendor), Task 9 (docs). ✓
- §B Wan2.2-I2V → Task 3 (start script), Task 5 (ServerDef+picker+routing), Task 7 (.deb). ✓
- §C FLUX.1-dev → Task 4 (start script), Task 6 (ServerDef+picker+routing), Task 8 (.deb). ✓
- Testing section (server_manager, picker, routing, collect-equality, apply_patches, .deb parse) → Tasks 1/5/6/7/8. ✓
- Open items (runner_key confirm, FLUX steps, per-model start args) → resolved with extracted values + Task 9 hardware-acceptance confirmation. ✓
- VERSION 0.76.0 + changelog + confirm-before-switch (unchanged; no auto-switch added). ✓

**Placeholder scan:** `runner_key` values are concrete (`tt-wan2.2-i2v`/`tt-flux.1-dev`) with a hardware-confirm step, not a `<confirm>` placeholder. FLUX step-default resolved (app already sends 20). The one genuine product decision (flux-package retarget) is explicitly flagged for user veto with a concrete fallback, not left vague.

**Type/name consistency:** canonical ids `wan2.2-i2v-a14b`/`flux.1-dev` and server keys `wan2.2-i2v`/`flux-dev` are used consistently across Tasks 5/6 and their tests; both `_VIDEO_MODEL_IDS`/`_IMAGE_MODEL_IDS` copies are edited; derived inverse maps are noted as auto. Picker exact-list update accounts for the video hand-wrap insertion position.
