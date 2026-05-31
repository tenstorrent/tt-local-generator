#!/usr/bin/env bash
# apply_patches.sh — Apply tt-local-generator patches to a tt-inference-server checkout.
#
# What this does (7 steps):
#   1-2. Copies and wires in patches/tt_dit/ DiT pipeline hotfixes (dev_mode).
#   3-4. Copies and wires in patches/media_server_config/ device config overrides.
#   5.   Injects HF_HOME bind-mount so the container finds ~/.cache/huggingface.
#   6-7. Injects SkyReels-V2 T2V and I2V ModelSpecTemplates into model_spec.py.
#
# By default this patches vendor/tt-inference-server/ (set up by setup_vendor.sh).
# Use --dev to patch ~/code/tt-inference-server instead (your dev checkout).
#
# Usage:
#   ./apply_patches.sh                        # patch vendor/tt-inference-server
#   ./apply_patches.sh --dev                  # patch ~/code/tt-inference-server
#   ./apply_patches.sh /path/to/tt-inference-server   # patch an explicit path
#   ./apply_patches.sh --help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCHES_SRC="$REPO_ROOT/patches"

# ── Args ─────────────────────────────────────────────────────────────────────

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    sed -n '2,15p' "$0" | sed 's/^# \?//'
    exit 0
fi

VENDOR_DIR="$REPO_ROOT/vendor/tt-inference-server"
DEV_DIR="$HOME/code/tt-inference-server"

if [[ "${1:-}" == "--dev" ]]; then
    # Explicit dev-checkout mode — never set up or modify vendor/
    TT_INFER="$DEV_DIR"
    if [[ ! -d "$TT_INFER" ]]; then
        echo "ERROR: dev checkout not found at $TT_INFER"
        echo "Clone it first: git clone https://github.com/tenstorrent/tt-inference-server.git $TT_INFER"
        exit 1
    fi
elif [[ -n "${1:-}" && "${1:-}" != --* ]]; then
    # Explicit path
    TT_INFER="$1"
    if [[ ! -d "$TT_INFER" ]]; then
        echo "ERROR: tt-inference-server not found at $TT_INFER"
        exit 1
    fi
else
    # Default: vendor/ — set it up now if absent
    if [[ ! -d "$VENDOR_DIR/.git" ]]; then
        echo "vendor/tt-inference-server not found — running setup_vendor.sh first…"
        echo ""
        "$SCRIPT_DIR/setup_vendor.sh"
        echo ""
    fi
    TT_INFER="$VENDOR_DIR"
fi

if [[ ! -d "$TT_INFER" ]]; then
    echo "ERROR: tt-inference-server not found at $TT_INFER"
    exit 1
fi

RDS="$TT_INFER/workflows/run_docker_server.py"
if [[ ! -f "$RDS" ]]; then
    echo "ERROR: $RDS not found — is $TT_INFER a valid tt-inference-server checkout?"
    exit 1
fi

echo "Applying patches to: $TT_INFER"
echo ""

# ── Step 1: Copy patches/tt_dit/ ─────────────────────────────────────────────

TT_DIT_DST="$TT_INFER/patches/tt_dit"
TT_DIT_SRC="$PATCHES_SRC/tt_dit"

echo "1. Copying $TT_DIT_SRC → $TT_DIT_DST"

# Walk each file so we can back up existing ones individually.
find "$TT_DIT_SRC" -name "*.py" | while read -r src_file; do
    rel="${src_file#$TT_DIT_SRC/}"
    dst_file="$TT_DIT_DST/$rel"
    dst_dir="$(dirname "$dst_file")"
    mkdir -p "$dst_dir"

    if [[ -f "$dst_file" ]]; then
        if diff -q "$src_file" "$dst_file" > /dev/null 2>&1; then
            echo "   unchanged: patches/tt_dit/$rel"
        else
            backup="${dst_file}.bak"
            cp "$dst_file" "$backup"
            echo "   updated:   patches/tt_dit/$rel  (backup: ${backup#$TT_INFER/})"
            cp "$src_file" "$dst_file"
        fi
    else
        echo "   created:   patches/tt_dit/$rel"
        cp "$src_file" "$dst_file"
    fi
done

echo ""

# ── Step 2: Patch run_docker_server.py ───────────────────────────────────────

echo "2. Patching $RDS"

# The block we need to insert (must match exactly what run_docker_server.py expects).
# Insertion point: immediately before "    for key, value in docker_env_vars.items():"
TT_DIT_BLOCK='    # Apply media-server tt_dit hotpatches: files under patches/tt_dit/ are
    # bind-mounted at their relative paths inside ~/tt-metal/models/tt_dit/.
    # Used to patch DiT pipeline code (e.g. mesh-shape assertions in pipeline_mochi.py)
    # for hardware topologies not yet in the released image.  Only active in dev_mode.
    if runtime_config.dev_mode:
        tt_dit_patches_dir = Path(repo_root_path) / "patches" / "tt_dit"
        if tt_dit_patches_dir.is_dir():
            for patch_file in sorted(tt_dit_patches_dir.rglob("*.py")):
                rel = patch_file.relative_to(tt_dit_patches_dir)
                dst = f"{user_home_path}/tt-metal/models/tt_dit/{rel}"
                docker_command += [
                    "--mount", f"type=bind,src={patch_file},dst={dst},readonly",
                ]
                logger.info(f"Hotpatch (tt_dit): {rel} -> {dst}")'

python3 - "$RDS" "$TT_DIT_BLOCK" <<'PYEOF'
import sys, shutil, pathlib, textwrap

rds_path = pathlib.Path(sys.argv[1])
block = sys.argv[2]

text = rds_path.read_text()

# Idempotency check — skip if the block is already present.
if "tt_dit_patches_dir" in text:
    print("   already patched — nothing to do")
    sys.exit(0)

# Anchor: the line that immediately follows where the block should live.
ANCHOR = "    for key, value in docker_env_vars.items():"

if ANCHOR not in text:
    print(f"ERROR: could not find insertion anchor in {rds_path}")
    print(f"  Expected to find: {ANCHOR!r}")
    print("  The file may have changed upstream.  Apply the block manually.")
    sys.exit(1)

# Back up before modifying.
backup = rds_path.with_suffix(".py.bak")
shutil.copy2(rds_path, backup)
print(f"   backup: {backup.name}")

# Insert the block (plus a trailing blank line) before the anchor.
new_text = text.replace(ANCHOR, block + "\n\n" + ANCHOR, 1)
rds_path.write_text(new_text)
print("   inserted tt_dit hotpatch block ✓")
PYEOF

echo ""

# ── Step 3: Copy patches/media_server_config/ ────────────────────────────────

MSC_SRC="$PATCHES_SRC/media_server_config"
MSC_DST="$TT_INFER/patches/media_server_config"

echo "3. Copying $MSC_SRC → $MSC_DST"

find "$MSC_SRC" -name "*.py" | while read -r src_file; do
    rel="${src_file#$MSC_SRC/}"
    dst_file="$MSC_DST/$rel"
    dst_dir="$(dirname "$dst_file")"
    mkdir -p "$dst_dir"

    if [[ -f "$dst_file" ]]; then
        if diff -q "$src_file" "$dst_file" > /dev/null 2>&1; then
            echo "   unchanged: patches/media_server_config/$rel"
        else
            backup="${dst_file}.bak"
            cp "$dst_file" "$backup"
            echo "   updated:   patches/media_server_config/$rel  (backup: ${backup#$TT_INFER/})"
            cp "$src_file" "$dst_file"
        fi
    else
        echo "   created:   patches/media_server_config/$rel"
        cp "$src_file" "$dst_file"
    fi
done

echo ""

# ── Step 4: Insert media_server_config bind-mount block ───────────────────────

echo "4. Patching $RDS (media_server_config)"

# Unconditional (no dev_mode guard): these config overrides must work with the
# production image startup used by start_wan.sh.  Files under
# patches/media_server_config/ mirror the container's ~/tt-metal/server/ tree
# and are bind-mounted over the corresponding paths at container start.
MSC_BLOCK='    # Apply media-server config patches: .py files under patches/media_server_config/
    # mirror ~/tt-metal/server/ inside the container and are bind-mounted at startup.
    # Unlike dev_mode hotpatches this block is unconditional — it works with the
    # production image (no --dev-mode flag required).  Use this for config overrides
    # such as per-device timeouts that are missing from the upstream image.
    _media_config_patches_dir = Path(repo_root_path) / "patches" / "media_server_config"
    if _media_config_patches_dir.is_dir():
        for _patch_file in sorted(_media_config_patches_dir.rglob("*.py")):
            _rel = _patch_file.relative_to(_media_config_patches_dir)
            _dst = f"{user_home_path}/tt-metal/server/{_rel}"
            docker_command += [
                "--mount", f"type=bind,src={_patch_file},dst={_dst},readonly",
            ]
            logger.info(f"Config patch (media_server_config): {_rel} -> {_dst}")'

python3 - "$RDS" "$MSC_BLOCK" <<'PYEOF'
import sys, shutil, pathlib

rds_path = pathlib.Path(sys.argv[1])
block = sys.argv[2]

text = rds_path.read_text()

if "_media_config_patches_dir" in text:
    print("   already patched — nothing to do")
    sys.exit(0)

ANCHOR = "    for key, value in docker_env_vars.items():"

if ANCHOR not in text:
    print(f"ERROR: could not find insertion anchor in {rds_path}")
    sys.exit(1)

backup = rds_path.with_suffix(".py.bak")
shutil.copy2(rds_path, backup)
print(f"   backup: {backup.name}")

new_text = text.replace(ANCHOR, block + "\n\n" + ANCHOR, 1)
rds_path.write_text(new_text)
print("   inserted media_server_config bind-mount block ✓")
PYEOF

echo ""
# ── Step 5: Insert HF_HOME bind-mount block ───────────────────────────────────

echo "5. Patching $RDS (HF_HOME mount)"

# When the model loading code calls from_pretrained("Wan-AI/Wan2.2-...") with the
# HF repo ID (not a local path), the HF library resolves it via HF_HOME.  Without
# setting HF_HOME inside the container, the library defaults to the container's own
# empty ~/.cache/huggingface, causing a download attempt or offline-mode failure.
# This block mounts the full host HF cache directory and sets HF_HOME so that the
# cached model (118 GB) is found by repo ID without any network access.
HF_HOME_BLOCK='    # Mount the full host HF cache as HF_HOME inside the container.
    # Model loading code calls from_pretrained("Wan-AI/Wan2.2-T2V-A14B-Diffusers")
    # with the HF repo ID; the HF library resolves this via HF_HOME.  Without this,
    # HF defaults to the empty container cache and tries to download, or fails offline.
    if setup_config and getattr(setup_config, "host_hf_cache", None):
        _hf_home_dst = f"{user_home_path}/hf_home_cache"
        docker_command += [
            "--mount", f"type=bind,src={setup_config.host_hf_cache},dst={_hf_home_dst}",
        ]
        docker_env_vars["HF_HOME"] = _hf_home_dst
        # Also mount /mnt/bonus so that symlinks in the HF cache that point
        # to /mnt/bonus (a separate model storage disk) resolve inside the container.
        import pathlib as _pl
        _bonus = _pl.Path("/mnt/bonus")
        if _bonus.is_dir():
            docker_command += [
                "--mount", f"type=bind,src={_bonus},dst={_bonus},readonly",
            ]
            logger.info(f"Bonus disk mount: {_bonus} -> {_bonus}")
        logger.info(f"HF_HOME mount: {setup_config.host_hf_cache} -> {_hf_home_dst}")'

python3 - "$RDS" "$HF_HOME_BLOCK" <<'PYEOF'
import sys, shutil, pathlib

rds_path = pathlib.Path(sys.argv[1])
block = sys.argv[2]

text = rds_path.read_text()

if "hf_home_cache" in text:
    print("   already patched — nothing to do")
    sys.exit(0)

ANCHOR = "    for key, value in docker_env_vars.items():"

if ANCHOR not in text:
    print(f"ERROR: could not find insertion anchor in {rds_path}")
    sys.exit(1)

backup = rds_path.with_suffix(".py.bak")
shutil.copy2(rds_path, backup)
print(f"   backup: {backup.name}")

new_text = text.replace(ANCHOR, block + "\n\n" + ANCHOR, 1)
rds_path.write_text(new_text)
print("   inserted HF_HOME bind-mount block ✓")
PYEOF

echo ""

# ── Step 6: Inject SkyReels into model_spec.py ───────────────────────────────

MODEL_SPEC="$TT_INFER/workflows/model_spec.py"
echo "6. Patching $MODEL_SPEC (SkyReels ModelSpecTemplate)"

python3 - "$MODEL_SPEC" <<'PYEOF'
import sys, shutil, pathlib

p = pathlib.Path(sys.argv[1])
text = p.read_text()

MARKER = "Skywork/SkyReels-V2-DF-1.3B-540P-Diffusers"
if MARKER in text:
    print("   already patched — nothing to do")
    sys.exit(0)

# Insertion anchor: last entry before image_templates comment.
ANCHOR = "]\n\n# =============================================================================\n# image_templates"
if ANCHOR not in text:
    print(f"ERROR: could not find insertion anchor in {p}")
    sys.exit(1)

SKYREELS_ENTRY = """\
    # SkyReels-V2-DF-1.3B-540P — Blackhole (P150X4) only.
    # Weights: ~12GB.  540P = 480x272 native res.
    ModelSpecTemplate(
        weights=["Skywork/SkyReels-V2-DF-1.3B-540P-Diffusers"],
        tt_metal_commit="555f240",
        impl=tt_transformers_impl,
        min_disk_gb=20,
        min_ram_gb=16,
        model_type=ModelType.VIDEO,
        inference_engine=InferenceEngine.MEDIA.value,
        device_model_specs=[
            DeviceModelSpec(
                device=DeviceTypes.P150X4,
                max_concurrency=1,
                max_context=64 * 1024,
                default_impl=True,
            ),
            DeviceModelSpec(
                device=DeviceTypes.P300X2,
                max_concurrency=1,
                max_context=64 * 1024,
                default_impl=True,
            ),
        ],
        status=ModelStatusTypes.COMPLETE,
    ),
"""

backup = p.with_suffix(".py.bak")
shutil.copy2(p, backup)
new_text = text.replace(ANCHOR, SKYREELS_ENTRY + ANCHOR, 1)
p.write_text(new_text)
print(f"   inserted SkyReels ModelSpecTemplate ✓  (backup: {backup.name})")
PYEOF

# ── Step 7: Inject SkyReels I2V into model_spec.py ───────────────────────────

echo "7. Patching $MODEL_SPEC (SkyReels I2V ModelSpecTemplate)"

python3 - "$MODEL_SPEC" <<'PYEOF'
import sys, shutil, pathlib

p = pathlib.Path(sys.argv[1])
text = p.read_text()

MARKER = "Skywork/SkyReels-V2-I2V-14B-540P"
if MARKER in text:
    print("   already patched — nothing to do")
    sys.exit(0)

# Find the SkyReels T2V template by its weights string, then walk forward
# counting parenthesis depth to locate the TRUE closing paren of that template.
# (A naive text.find("),\n") would match a DeviceModelSpec close inside the
# template, inserting the I2V entry in the middle of device_model_specs.)
ANCHOR_SR = "Skywork/SkyReels-V2-DF-1.3B-540P-Diffusers"
ANCHOR_FALLBACK = "]\n\n# =============================================================================\n# image_templates"

if ANCHOR_SR in text:
    idx = text.find(ANCHOR_SR)
    # Walk back to find the "ModelSpecTemplate(" that contains ANCHOR_SR
    block_start = text.rfind("ModelSpecTemplate(", 0, idx)
    if block_start == -1:
        print("ERROR: could not locate start of SkyReels T2V block")
        sys.exit(1)
    # Walk forward counting paren depth to find the matching close paren
    depth = 0
    pos = block_start
    insert_pos = -1
    while pos < len(text):
        ch = text[pos]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                # pos points at the closing ) of ModelSpecTemplate(...)
                # Insert after the following ",\n"
                insert_pos = pos + 1
                while insert_pos < len(text) and text[insert_pos] in ',\n':
                    insert_pos += 1
                break
        pos += 1
    if insert_pos == -1:
        print("ERROR: could not locate closing paren of SkyReels T2V block")
        sys.exit(1)
    anchor_used = "after SkyReels T2V block"
elif ANCHOR_FALLBACK in text:
    insert_pos = text.find(ANCHOR_FALLBACK)
    anchor_used = "before image_templates"
else:
    print(f"ERROR: could not find insertion anchor in {p}")
    print("  Apply the I2V ModelSpecTemplate block manually.")
    sys.exit(1)

I2V_ENTRY = """\
    # SkyReels-V2-I2V-14B-540P — Blackhole (P150X4 / P300X2) only.
    # Weights: ~58 GB (14 sharded safetensors, raw WAN 2.1 format).
    # Also requires WAN 2.2 A14B diffusers checkpoint for VAE/T5 architecture.
    # Input: text prompt + conditioning image.  Output: 960x544 by default.
    ModelSpecTemplate(
        weights=["Skywork/SkyReels-V2-I2V-14B-540P"],
        tt_metal_commit="555f240",
        impl=tt_transformers_impl,
        min_disk_gb=75,
        min_ram_gb=32,
        model_type=ModelType.VIDEO,
        inference_engine=InferenceEngine.MEDIA.value,
        device_model_specs=[
            DeviceModelSpec(
                device=DeviceTypes.P150X4,
                max_concurrency=1,
                max_context=64 * 1024,
                default_impl=True,
            ),
            DeviceModelSpec(
                device=DeviceTypes.P300X2,
                max_concurrency=1,
                max_context=64 * 1024,
                default_impl=True,
            ),
        ],
        status=ModelStatusTypes.COMPLETE,
    ),
"""

backup = p.with_suffix(".py.bak")
shutil.copy2(p, backup)
new_text = text[:insert_pos] + I2V_ENTRY + text[insert_pos:]
p.write_text(new_text)
print(f"   inserted SkyReels I2V ModelSpecTemplate ✓  ({anchor_used}, backup: {backup.name})")
PYEOF

# ── Step 8: Inject Wan2.2-Animate-14B-Diffusers into model_spec.py ───────────

echo "8. Patching $MODEL_SPEC (Wan2.2-Animate-14B-Diffusers ModelSpecTemplate)"

python3 - "$MODEL_SPEC" <<'PYEOF'
import sys, shutil, pathlib

p = pathlib.Path(sys.argv[1])
text = p.read_text()

MARKER = "Wan-AI/Wan2.2-Animate-14B-Diffusers"
if MARKER in text:
    print("   already patched — nothing to do")
    sys.exit(0)

# Insert after the SkyReels I2V entry (or before image_templates as fallback)
ANCHOR = "Skywork/SkyReels-V2-I2V-14B-540P"
FALLBACK = "]\n\n# =============================================================================\n# image_templates"

if ANCHOR in text:
    idx = text.find(ANCHOR)
    block_start = text.rfind("ModelSpecTemplate(", 0, idx)
    depth, pos, insert_pos = 0, block_start, -1
    while pos < len(text):
        ch = text[pos]
        if ch == '(': depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                insert_pos = pos + 1
                while insert_pos < len(text) and text[insert_pos] in ',\n':
                    insert_pos += 1
                break
        pos += 1
    anchor_used = "after SkyReels I2V block"
elif FALLBACK in text:
    insert_pos = text.find(FALLBACK)
    anchor_used = "before image_templates"
else:
    print(f"ERROR: could not find insertion anchor in {p}")
    sys.exit(1)

ANIMATE_ENTRY = """\
    # Wan2.2-Animate-14B-Diffusers — character animation fine-tune on Wan2.2 I2V.
    # Weights cached locally; same architecture as Wan2.2-I2V-A14B-Diffusers.
    # Uses the I2V runner via the media server.
    ModelSpecTemplate(
        weights=["Wan-AI/Wan2.2-Animate-14B-Diffusers"],
        version="0.15.0",
        tt_metal_commit="7fbac63",
        impl=tt_transformers_impl,
        min_disk_gb=60,
        min_ram_gb=32,
        model_type=ModelType.VIDEO,
        inference_engine=InferenceEngine.MEDIA.value,
        device_model_specs=[
            DeviceModelSpec(
                device=DeviceTypes.P300X2,
                max_concurrency=1,
                max_context=64 * 1024,
                default_impl=True,
                override_tt_config={
                    "trace_region_size": 30000000,
                },
            ),
        ],
        status=ModelStatusTypes.COMPLETE,
    ),
"""

backup = p.with_suffix(".py.bak")
shutil.copy2(p, backup)
new_text = text[:insert_pos] + ANIMATE_ENTRY + text[insert_pos:]
p.write_text(new_text)
print(f"   inserted Wan2.2-Animate ModelSpecTemplate ✓  ({anchor_used}, backup: {backup.name})")
PYEOF

# ── Step 9: Bump DeepSeek-R1-Distill-Llama-70B P300X2 version to 0.14.0 ──────
# The upstream spec pins it at v0.10.0 which the v0.15.0 run.py rejects.
# The 0.14.0 image works for all Blackhole LLMs.

echo "9. Patching $MODEL_SPEC (DeepSeek-R1-Distill-Llama-70B P300X2 version bump)"

python3 - "$MODEL_SPEC" <<'PYEOF'
import sys, shutil, pathlib, re

p = pathlib.Path(sys.argv[1])
text = p.read_text()

TARGET_ID = 'id_tt-transformers_DeepSeek-R1-Distill-Llama-70B_p300x2'
if TARGET_ID not in text:
    print("   model spec entry not found — skipping")
    sys.exit(0)

# Find the ModelSpec for this ID and bump its version
OLD = "DeepSeek-R1-Distill-Llama-70B_p300x2"
if '"0.14.0"' in text and OLD in text:
    # Check if the p300x2 entry already has 0.14.0
    idx = text.find(OLD)
    chunk = text[max(0,idx-500):idx+500]
    if '"0.14.0"' in chunk:
        print("   already patched — nothing to do")
        sys.exit(0)

# Inject a new 0.14.0 template for DeepSeek on P300X2 if not already present
MARKER = "Wan-AI/Wan2.2-Animate-14B-Diffusers"
if MARKER not in text:
    print("   insertion anchor not found — skipping")
    sys.exit(0)

idx = text.find(MARKER)
block_start = text.rfind("ModelSpecTemplate(", 0, idx)
depth, pos, insert_pos = 0, block_start, -1
while pos < len(text):
    ch = text[pos]
    if ch == '(': depth += 1
    elif ch == ')':
        depth -= 1
        if depth == 0:
            insert_pos = pos + 1
            while insert_pos < len(text) and text[insert_pos] in ',\n':
                insert_pos += 1
            break
    pos += 1

DEEPSEEK_ENTRY = """\
    # DeepSeek-R1-Distill-Llama-70B — P300X2 at v0.14.0 (upstream is v0.10.0 which run.py rejects).
    ModelSpecTemplate(
        weights=["deepseek-ai/DeepSeek-R1-Distill-Llama-70B"],
        version="0.14.0",
        tt_metal_commit="80180b9",
        impl=tt_transformers_impl,
        min_disk_gb=150,
        min_ram_gb=64,
        model_type=ModelType.LLM,
        inference_engine=InferenceEngine.VLLM.value,
        device_model_specs=[
            DeviceModelSpec(
                device=DeviceTypes.P300X2,
                max_concurrency=1,
                max_context=32768,
                default_impl=True,
            ),
        ],
        status=ModelStatusTypes.COMPLETE,
    ),
"""

backup = p.with_suffix(".py.bak")
shutil.copy2(p, backup)
new_text = text[:insert_pos] + DEEPSEEK_ENTRY + text[insert_pos:]
p.write_text(new_text)
print(f"   inserted DeepSeek-R1 P300X2 v0.14.0 entry ✓")
PYEOF

# ── Step 10: Bump SDXL/cpp-server models to v0.15.0 on P300X2 ────────────────
# stable-diffusion-xl-base-1.0 (and the img2img / inpaint variants) are pinned
# at v0.11.1 which run.py v0.15.0 rejects.  The cpp_server auto-activates for
# these models when SERVER_MODE=cpp is set; bumping to v0.15.0 allows run.py
# to launch them while the cpp binary inside the 0.15.0 image handles serving.

echo "10. Patching $MODEL_SPEC (SDXL cpp-server models version bump)"

python3 - "$MODEL_SPEC" <<'PYEOF'
import sys, shutil, pathlib

p = pathlib.Path(sys.argv[1])
text = p.read_text()

OLD = '''        weights=[
            "stabilityai/stable-diffusion-xl-base-1.0",
            "stabilityai/stable-diffusion-xl-base-1.0-img-2-img",
        ],
        version="0.11.1",
        tt_metal_commit="bac8b34",'''

NEW = '''        weights=[
            "stabilityai/stable-diffusion-xl-base-1.0",
            "stabilityai/stable-diffusion-xl-base-1.0-img-2-img",
        ],
        version="0.15.0",  # cpp-server path — bumped from 0.11.1 by apply_patches.sh
        tt_metal_commit="7fbac63",'''

if OLD not in text:
    if NEW in text:
        print("   already patched — nothing to do")
    else:
        print("   SDXL template not found — skipping")
    sys.exit(0)

backup = p.with_suffix(".py.bak")
shutil.copy2(p, backup)
p.write_text(text.replace(OLD, NEW, 1))
print("   SDXL template version 0.11.1 → 0.15.0 ✓")
PYEOF

echo ""
echo "Done. You can now run start_mochi.sh (or any media-server model with --dev-mode)"
echo "and the patches/tt_dit/ files will be bind-mounted automatically."
echo ""
echo "patches/media_server_config/ overrides are applied on every start_wan.sh launch"
echo "(no --dev-mode required)."
