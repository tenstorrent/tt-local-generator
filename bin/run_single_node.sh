#!/usr/bin/env bash
# run_single_node.sh — Re-run a single node from an existing pipeline run.
#
# Reads the existing results.json for input context, executes the specified
# node, and writes the new output back to results.json.
#
# Usage:
#   ./bin/run_single_node.sh <results_json_path> <node_id>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_JSON="${1:-}"
NODE_ID="${2:-}"

if [[ -z "$RESULTS_JSON" || -z "$NODE_ID" ]]; then
    echo "Usage: $0 <results.json> <node_id>"
    exit 1
fi

if [[ ! -f "$RESULTS_JSON" ]]; then
    echo "ERROR: results.json not found: $RESULTS_JSON"
    exit 1
fi

OUTPUT_DIR="$(dirname "$RESULTS_JSON")"
PYTHON3="${HOME}/.tenstorrent-venv/bin/python3"
[[ ! -f "$PYTHON3" ]] && PYTHON3=/usr/bin/python3

LOG_FILE="${HOME}/.local/share/tt-local-generator/logs/workflow/$(date +%Y%m%d_%H%M%S)_retry_node${NODE_ID}.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "LOG:$LOG_FILE"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

set_result() {
    local node_id="$1" key="$2" value="$3"
    python3 -c "
import json, sys
with open('$RESULTS_JSON') as f: d = json.load(f)
d.setdefault('$node_id', {})['$key'] = sys.argv[1]
with open('$RESULTS_JSON', 'w') as f: json.dump(d, f, indent=2)
" "$value"
}

get_result() {
    local ref="$1"
    python3 -c "
import json, sys
ref = json.loads(sys.argv[1])
node_id, key = ref[0], ref[1]
with open('$RESULTS_JSON') as f: d = json.load(f)
print(d.get(node_id, {}).get(key, ''))
" "$ref"
}

node_signal() {
    local node_id="$1" status="$2" detail="${3:-}"
    echo "NODE:${node_id}:${status}:${detail}" 2>/dev/null || true
    if [[ "$status" == "running" ]]; then _current_node="$node_id"; fi
}

_current_node=""
trap '[[ -n "$_current_node" ]] && node_signal "$_current_node" "failed" "retry exited unexpectedly" || true' ERR

log "Retrying node $NODE_ID from: $RESULTS_JSON"
node_signal "$NODE_ID" "running" "retry"

case "$NODE_ID" in
    1|8)
        PROMPT=$(get_result '["1", "prompt"]' 2>/dev/null || echo "")
        [[ -z "$PROMPT" ]] && { log "ERROR: no prompt available for image retry"; node_signal "$NODE_ID" "failed" "no prompt"; exit 1; }
        OUT="$OUTPUT_DIR/node${NODE_ID}_image_retry.png"
        RESULT=$(python3 "$REPO_ROOT/bin/_submit_image.py" "$PROMPT" "1964" "$OUT" 2>/dev/null) || RESULT=""
        if [[ "$RESULT" == "DONE" ]]; then
            set_result "$NODE_ID" "image_path" "$OUT"
            node_signal "$NODE_ID" "done" "$OUT"
        else
            node_signal "$NODE_ID" "failed" "FLUX submission failed"
        fi
        ;;
    6)
        IMAGE_PATH=$(get_result '["1", "image_path"]' 2>/dev/null || echo "")
        VIDEO_PROMPT=$(get_result '["5", "video_prompt"]' 2>/dev/null || echo "")
        [[ -z "$IMAGE_PATH" ]] && { node_signal "6" "failed" "no seed image"; exit 1; }
        JOB=$(python3 "$REPO_ROOT/bin/_submit_video.py" "${VIDEO_PROMPT:-retry}" "$IMAGE_PATH" "1964" 2>/dev/null) || JOB=""
        if [[ -n "$JOB" && "$JOB" != ERROR* ]]; then
            OUT="$OUTPUT_DIR/node6_video_retry.mp4"
            for i in $(seq 1 120); do
                sleep 30
                STATUS=$(curl -s "http://localhost:8000/v1/videos/generations/$JOB" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "?")
                [[ "$STATUS" == "completed" ]] && break
                [[ "$STATUS" == "failed" ]] && { node_signal "6" "failed" "generation failed"; exit 1; }
            done
            if curl -sf "http://localhost:8000/v1/videos/generations/$JOB/download" -o "$OUT"; then
                set_result "6" "video_path" "$OUT"
                node_signal "6" "done" "$OUT"
            else
                node_signal "6" "failed" "download failed"
            fi
        else
            node_signal "6" "failed" "submission failed: $JOB"
        fi
        ;;
    7)
        CAPTION=$(get_result '["2", "caption"]' 2>/dev/null || echo "a scene from a world's fair")
        TEXT=$(python3 "$REPO_ROOT/bin/_gen_poem.py" "$CAPTION" 2>/dev/null) || TEXT=""
        if [[ -n "$TEXT" ]]; then
            set_result "7" "poem" "$TEXT"
            node_signal "7" "done" "${TEXT:0:80}"
        else
            node_signal "7" "failed" "empty response from Llama"
        fi
        ;;
    *)
        log "Node $NODE_ID retry not implemented for standalone re-run"
        node_signal "$NODE_ID" "failed" "retry not implemented for node $NODE_ID"
        exit 1
        ;;
esac

log "Node $NODE_ID retry complete"
