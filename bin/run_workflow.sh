#!/usr/bin/env bash
# run_workflow.sh — Execute a tt-local-generator workflow JSON spec.
#
# Interprets the ComfyUI-compatible node graph in docs/examples/workflows/
# and runs each step using tt-ctl, the plugin system, and direct API calls.
#
# Board-reset discipline:
#   - CPU plugins (blip, rmbg, depth) never touch chips — no reset
#   - Switching between media server models → stop + tt-smi -r
#   - Switching media server → artgen VLLM (or vice versa) → stop + tt-smi -r
#   - Sequential requests to the SAME running server → no reset
#
# Usage:
#   ./bin/run_workflow.sh docs/examples/workflows/1964-worlds-fair.json
#   ./bin/run_workflow.sh docs/examples/workflows/1964-worlds-fair.json --dry-run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKFLOW="${1:-}"
DRY_RUN=0
[[ "${2:-}" == "--dry-run" ]] && DRY_RUN=1

if [[ -z "$WORKFLOW" ]]; then
    echo "Usage: $0 <workflow.json> [--dry-run]"
    exit 1
fi

if [[ ! -f "$WORKFLOW" ]]; then
    echo "ERROR: workflow file not found: $WORKFLOW"
    exit 1
fi

if [[ -z "${PYTHON3:-}" ]]; then
    PYTHON3="${HOME}/.tenstorrent-venv/bin/python3"
    [[ -f "$PYTHON3" ]] || PYTHON3=/usr/bin/python3
fi

OUTPUT_DIR="${HOME}/.local/share/tt-local-generator/workflow-runs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTPUT_DIR"

# ── Fix 5: Tee all output to a timestamped log file ──────────────────────────
# The popover captures the LOG: prefix line and stores the path in the run record
# so the "Log" button in history rows can open the file in LogViewerWindow.
LOG_FILE="${HOME}/.local/share/tt-local-generator/logs/workflow/$(date +%Y%m%d_%H%M%S)_run.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "LOG:$LOG_FILE"

# ── Thin shim over app/pipeline_engine.py ────────────────────────────────────
# The engine loads the spec, topo-orders nodes by wire dependencies, dispatches
# each node's class_type to a registered handler, and emits the same NODE:/
# PLAYLIST: signals this script used to emit by hand (see app/pipeline_engine.py
# for the full node-handler implementations, ported 1:1 from the old hardcoded
# bash node_*() functions). The engine's stdout flows through the `tee` above,
# so pipeline_runner.py still sees the LOG: line (already echoed) and every
# NODE: line unchanged. We exit with the engine's own exit status.
ENGINE_ARGS=("$WORKFLOW" --output-dir "$OUTPUT_DIR")
[[ $DRY_RUN -eq 1 ]] && ENGINE_ARGS+=(--dry-run)
exec "$PYTHON3" "$REPO_ROOT/app/pipeline_engine.py" "${ENGINE_ARGS[@]}"
