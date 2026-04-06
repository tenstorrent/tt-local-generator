#!/usr/bin/env bash
# apply_patches.sh — Apply tt-local-generator patches to a tt-inference-server checkout.
#
# What this does:
#   1. Copies patches/tt_dit/ into <tt-inference-server>/patches/tt_dit/ so the
#      hotpatched DiT pipeline files are available for bind-mounting by run.py.
#
#   2. Inserts the tt_dit hotpatch block into
#      <tt-inference-server>/workflows/run_docker_server.py if it is not already
#      present.  The block teaches run_docker_server.py to bind-mount any .py files
#      under patches/tt_dit/ over the corresponding paths inside the container.
#
# Usage:
#   ./apply_patches.sh                        # uses ~/code/tt-inference-server
#   ./apply_patches.sh /path/to/tt-inference-server
#   ./apply_patches.sh --help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCHES_SRC="$SCRIPT_DIR/patches"

# ── Args ─────────────────────────────────────────────────────────────────────

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    sed -n '2,12p' "$0" | sed 's/^# \?//'
    exit 0
fi

TT_INFER="${1:-$HOME/code/tt-inference-server}"

if [[ ! -d "$TT_INFER" ]]; then
    echo "ERROR: tt-inference-server not found at $TT_INFER"
    echo "Usage: $0 [/path/to/tt-inference-server]"
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
echo "Done. You can now run start_mochi.sh (or any media-server model with --dev-mode)"
echo "and the patches/tt_dit/ files will be bind-mounted automatically."
