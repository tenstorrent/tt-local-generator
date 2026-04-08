#!/usr/bin/env bash
# bin/snapshot_vendor.sh — Snapshot the Python-only files from tt-inference-server
#                          into vendor/tt-inference-server/ for deb packaging.
#
# The vendored snapshot ships only the launcher and workflow modules needed by
# start_wan_qb2.sh (and friends) to kick off inference jobs inside Docker.
# Model weights, git history, and compiled artifacts are NOT included — the deb
# stays at ~1–5 MB Python source rather than the 143 GB full working tree.
#
# Usage:
#   ./bin/snapshot_vendor.sh                                # use pinned SHA
#   ./bin/snapshot_vendor.sh --sha <commit-sha>             # override SHA
#   ./bin/snapshot_vendor.sh --src /path/to/tt-inference-server  # use local clone
#
# After running this script, commit vendor/ (it is gitignored by default;
# remove the gitignore entry before committing for a deb build) and then run:
#   dpkg-buildpackage -us -uc -b

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

VENDOR_DIR="$REPO_ROOT/vendor/tt-inference-server"
VENDOR_SHA_FILE="$REPO_ROOT/vendor/VENDOR_SHA"

# SHA pinned to the same image as DOCKER_IMAGE in start_wan_qb2.sh:
#   ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34
# Update this when bumping the inference server version.
DEFAULT_SHA="bac8b3471c8b1234567890abcdef1234567890ab"   # placeholder — set real SHA
UPSTREAM_REPO="https://github.com/tenstorrent/tt-inference-server.git"

# ── Parse flags ───────────────────────────────────────────────────────────────
USE_LOCAL_SRC=""
OVERRIDE_SHA=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sha)
            OVERRIDE_SHA="$2"; shift 2 ;;
        --src)
            USE_LOCAL_SRC="$2"; shift 2 ;;
        --help|-h)
            sed -n '2,/^set /p' "$0" | grep '^#' | sed 's/^# \?//'
            exit 0 ;;
        *)
            echo "Unknown flag: $1" >&2; exit 1 ;;
    esac
done

TARGET_SHA="${OVERRIDE_SHA:-$DEFAULT_SHA}"

# ── Files / directories to include from tt-inference-server ───────────────────
# Only the Python launcher and workflow modules are needed at runtime.
# Heavy assets (weights, docker archives, benchmarking/) are excluded.
INCLUDE_PATHS=(
    "run.py"
    "workflows/"
    "tt_metal_impl/"
    "requirements.txt"
)

# ── Snapshot from a local clone ───────────────────────────────────────────────
_snapshot_from_local() {
    local SRC="$1"
    echo "Snapshotting from local clone: $SRC"

    rm -rf "$VENDOR_DIR"
    mkdir -p "$VENDOR_DIR"

    for path in "${INCLUDE_PATHS[@]}"; do
        if [[ -e "$SRC/$path" ]]; then
            # Preserve directory structure; -r for directories.
            cp -r "$SRC/$path" "$VENDOR_DIR/"
            echo "  + $path"
        else
            echo "  (skipped: $path — not found in source)"
        fi
    done

    # Copy the .env.default template (used by postinst to seed .env on first
    # install).  Source order: dedicated .env.default, then .env.example.
    for candidate in "$SRC/.env.default" "$SRC/.env.example" "$SRC/.env"; do
        if [[ -f "$candidate" ]]; then
            cp "$candidate" "$VENDOR_DIR/.env.default"
            echo "  + .env.default  (from $candidate)"
            break
        fi
    done

    # Record which commit this snapshot came from.
    local ACTUAL_SHA
    ACTUAL_SHA="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "$ACTUAL_SHA" > "$VENDOR_SHA_FILE"
    echo "Snapshot complete. SHA: $ACTUAL_SHA"
    echo "Saved to: $VENDOR_SHA_FILE"
}

# ── Snapshot via shallow clone from GitHub ────────────────────────────────────
_snapshot_from_git() {
    local SHA="$1"
    local TMPDIR
    TMPDIR="$(mktemp -d)"
    trap "rm -rf '$TMPDIR'" EXIT

    echo "Shallow-cloning $UPSTREAM_REPO at $SHA …"
    git clone --depth 1 --no-checkout "$UPSTREAM_REPO" "$TMPDIR/repo"
    git -C "$TMPDIR/repo" fetch --depth 1 origin "$SHA"
    git -C "$TMPDIR/repo" checkout "$SHA"

    _snapshot_from_local "$TMPDIR/repo"

    # Overwrite the SHA file with the explicit requested SHA.
    echo "$SHA" > "$VENDOR_SHA_FILE"
    echo "Pinned SHA: $SHA"
}

# ── Main ──────────────────────────────────────────────────────────────────────
echo "=== tt-inference-server vendor snapshot ==="
echo "Target vendor dir: $VENDOR_DIR"

if [[ -n "$USE_LOCAL_SRC" ]]; then
    if [[ ! -d "$USE_LOCAL_SRC" ]]; then
        echo "ERROR: --src path does not exist: $USE_LOCAL_SRC" >&2
        exit 1
    fi
    _snapshot_from_local "$USE_LOCAL_SRC"
else
    # Try the developer checkout first (common on QB2 dev machines).
    DEV_CLONE="$HOME/code/tt-inference-server"
    if [[ -d "$DEV_CLONE/.git" ]]; then
        echo "Found developer clone at $DEV_CLONE — using it (no network needed)."
        _snapshot_from_local "$DEV_CLONE"
    else
        echo "No local clone found. Fetching from GitHub…"
        _snapshot_from_git "$TARGET_SHA"
    fi
fi

echo ""
echo "Next steps:"
echo "  1. Review the snapshot:  ls -lR $VENDOR_DIR"
echo "  2. Check the pinned SHA: cat $VENDOR_SHA_FILE"
echo "  3. Build the deb:        dpkg-buildpackage -us -uc -b"
echo "  4. Install:              sudo apt install ./tt-local-generator_*.deb"
