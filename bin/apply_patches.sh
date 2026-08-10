#!/usr/bin/env bash
# apply_patches.sh — Apply tt-local-generator patches to a tt-inference-server checkout.
#
# What this does (7 steps):
#   1-2. Copies and wires in patches/tt_dit/ DiT pipeline hotfixes (dev_mode).
#   3-4. Copies and wires in patches/media_server_config/ device config overrides.
#   5.   Injects HF_HOME bind-mount so the container finds ~/.cache/huggingface.
#   6.   Registers SkyReels-V2 T2V and I2V as entries in the 0.18.0 YAML model
#        catalog (workflows/model_specs/dev/video.yaml), the successor to the
#        old inline-Python model-template list in model_spec.py.
#   7.   Registers Wan2.2-Animate-14B-Diffusers the same way, as a YAML entry
#        in workflows/model_specs/dev/video.yaml.
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

# ── Step 6: Register SkyReels-V2 in the 0.18.0 YAML model catalog ────────────
#
# 0.18.0 migrated the model registry from inline-Python model-template
# calls in workflows/model_spec.py to YAML catalogs under
# workflows/model_specs/{prod,dev}/*.yaml, loaded via load_templates_from_yaml().
# --dev-mode (used by the SkyReels start scripts) sets MODEL_SPECS_ENV=dev, so
# the catalog actually read is dev/video.yaml — model_spec.py is no longer the
# insertion point and the old text-injection anchor there is gone for good.
# This step appends the same two SkyReels templates (T2V + I2V) as plain YAML
# entries instead.
MODEL_SPEC_YAML="$TT_INFER/workflows/model_specs/dev/video.yaml"
echo "6. Patching $MODEL_SPEC_YAML (SkyReels T2V + I2V YAML entries)"

python3 - "$MODEL_SPEC_YAML" <<'PYEOF'
import sys, shutil, pathlib

p = pathlib.Path(sys.argv[1])
if not p.exists():
    print(f"ERROR: model spec catalog not found at {p}")
    sys.exit(1)

text = p.read_text()

T2V_MARKER = "Skywork/SkyReels-V2-DF-1.3B-540P-Diffusers"
I2V_MARKER = "Skywork/SkyReels-V2-I2V-14B-540P"

T2V_ENTRY = """
- weights:
    - Skywork/SkyReels-V2-DF-1.3B-540P-Diffusers
  impl: tt_transformers
  min_disk_gb: 20
  min_ram_gb: 32
  model_type: VIDEO
  inference_engine: MEDIA
  env_vars:
    TT_DIT_CACHE_DIR: /home/container_app_user/cache_root/tt_dit_cache
  device_model_specs:
    - device: P150X4
      max_concurrency: 1
      max_context: 65536  # 64 * 1024
      default_impl: true
      override_tt_config:
        trace_region_size: 30000000
    - device: P300X2
      max_concurrency: 1
      max_context: 65536  # 64 * 1024
      default_impl: true
      override_tt_config:
        trace_region_size: 30000000
  status: COMPLETE
"""

I2V_ENTRY = """
- weights:
    - Skywork/SkyReels-V2-I2V-14B-540P
  impl: tt_transformers
  min_disk_gb: 75
  min_ram_gb: 32
  model_type: VIDEO
  inference_engine: MEDIA
  env_vars:
    TT_DIT_CACHE_DIR: /home/container_app_user/cache_root/tt_dit_cache
  device_model_specs:
    - device: P150X4
      max_concurrency: 1
      max_context: 65536  # 64 * 1024
      default_impl: true
      override_tt_config:
        trace_region_size: 30000000
    - device: P300X2
      max_concurrency: 1
      max_context: 65536  # 64 * 1024
      default_impl: true
      override_tt_config:
        trace_region_size: 30000000
  status: COMPLETE
"""

did_anything = False
backup = p.with_suffix(".yaml.bak")

for marker, entry, label in (
    (T2V_MARKER, T2V_ENTRY, "SkyReels-V2-DF-1.3B-540P (T2V)"),
    (I2V_MARKER, I2V_ENTRY, "SkyReels-V2-I2V-14B-540P (I2V)"),
):
    if marker in text:
        print(f"   {label}: already patched — nothing to do")
        continue
    if not did_anything:
        shutil.copy2(p, backup)
    text += entry
    did_anything = True
    print(f"   {label}: appended ✓")

if did_anything:
    p.write_text(text)
    print(f"   (backup: {backup.name})")
else:
    print("   already patched — nothing to do")
PYEOF

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

echo ""
echo "Done. You can now run start_mochi.sh (or any media-server model with --dev-mode)"
echo "and the patches/tt_dit/ files will be bind-mounted automatically."
echo ""
echo "patches/media_server_config/ overrides are applied on every start_wan.sh launch"
echo "(no --dev-mode required)."
