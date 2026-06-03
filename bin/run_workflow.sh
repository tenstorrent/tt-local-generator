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

PYTHON3="${HOME}/.tenstorrent-venv/bin/python3"
if [[ ! -f "$PYTHON3" ]]; then
    PYTHON3=/usr/bin/python3
fi

OUTPUT_DIR="${HOME}/.local/share/tt-local-generator/workflow-runs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTPUT_DIR"
RESULTS_JSON="$OUTPUT_DIR/results.json"
echo "{}" > "$RESULTS_JSON"

# ── Fix 5: Tee all output to a timestamped log file ──────────────────────────
# The popover captures the LOG: prefix line and stores the path in the run record
# so the "Log" button in history rows can open the file in LogViewerWindow.
LOG_FILE="${HOME}/.local/share/tt-local-generator/logs/workflow/$(date +%Y%m%d_%H%M%S)_run.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "LOG:$LOG_FILE"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
log_step() { echo ""; echo "══ $* ══"; }

# ── Hardware management ───────────────────────────────────────────────────────

_current_server=""  # track what's running to avoid unnecessary resets

stop_and_reset() {
    local next_server="${1:-}"
    if [[ "$_current_server" == "$next_server" && -n "$next_server" ]]; then
        log "  Same server ($next_server) — no reset needed"
        return 0
    fi
    if docker ps -q 2>/dev/null | grep -q .; then
        log "  Stopping running containers..."
        docker ps -q | xargs docker stop 2>/dev/null || true
        sleep 3
    fi
    if [[ -n "$_current_server" ]]; then
        log "  Resetting boards (switching from $_current_server → ${next_server:-none})..."
        [[ $DRY_RUN -eq 0 ]] && tt-smi -r 2>/dev/null | head -1 || echo "  [dry-run] tt-smi -r"
        sleep 8
    fi
    _current_server="$next_server"
}

start_server() {
    local server_key="$1"
    local health_url="$2"
    local max_wait_min="${3:-60}"
    log "  Starting server: $server_key"
    [[ $DRY_RUN -eq 1 ]] && { echo "  [dry-run] tt-ctl start $server_key"; return 0; }
    cd "$REPO_ROOT" && ./tt-ctl start "$server_key" 2>&1 | tail -1
    log "  Waiting for $server_key to be ready (up to ${max_wait_min} min)..."
    for i in $(seq 1 $((max_wait_min * 2))); do
        sleep 30
        if curl -sf "$health_url" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('model_ready') or d.get('data') else 1)" 2>/dev/null; then
            log "  ✅ $server_key ready at $((i*30/60))min $((i*30%60))s"
            # Fix prometheus permissions for media servers
            docker exec "$(docker ps -q | head -1)" chmod 777 /tmp/prometheus_multiproc 2>/dev/null || true
            return 0
        fi
        # Fix 2: Emit tagged progress lines every ~2 min so the popover progress
        # label updates during long SkyReels warmup (model compile can take 60 min).
        # Every 4th poll (2 min) we also tail the latest SkyReels container log so
        # operators can see actual compile/load progress without grepping manually.
        if [[ $((i % 4)) -eq 0 ]]; then
            log "[${server_key} warmup] $((i*30/60))min elapsed, waiting for model…"
            # Tail the most recent media_*SkyReels* log file if present
            local skyreels_log
            skyreels_log=$(ls -t "${HOME}/code/tt-local-generator"/media_*SkyReels*.log 2>/dev/null | head -1)
            if [[ -n "$skyreels_log" ]]; then
                # Grab the last meaningful line (non-empty, skip progress bar noise)
                local last_line
                last_line=$(grep -v '^\s*$' "$skyreels_log" 2>/dev/null | grep -v '\[=' | tail -1 || true)
                [[ -n "$last_line" ]] && log "⏳ SkyReels: ${last_line:0:120}"
            fi
        fi
    done
    log "  ❌ $server_key timed out after ${max_wait_min} min"
    return 1
}

# ── Result store ──────────────────────────────────────────────────────────────

set_result() {
    local node_id="$1" key="$2" value="$3"
    python3 -c "
import json, sys
with open('$RESULTS_JSON') as f: d = json.load(f)
d.setdefault('$node_id', {})['$key'] = sys.argv[1]
with open('$RESULTS_JSON', 'w') as f: json.dump(d, f, indent=2)
" "$value"
}

# Store a human-readable label for a node (pulled from spec _comment field).
# The portfolio viewer reads node._label to display alongside thumbnails.
set_node_label() {
    local node_id="$1" label="$2"
    python3 -c "
import json, sys
with open('$RESULTS_JSON') as f: d = json.load(f)
d.setdefault('$node_id', {})['_label'] = sys.argv[1]
with open('$RESULTS_JSON', 'w') as f: json.dump(d, f, indent=2)
" "$label"
}

get_result() {
    local ref="$1"  # e.g. '["1", "image_path"]'
    python3 -c "
import json, sys
ref = json.loads(sys.argv[1])
node_id, key = ref[0], ref[1]
with open('$RESULTS_JSON') as f: d = json.load(f)
print(d.get(node_id, {}).get(key, ''))
" "$ref"
}

node_signal() {
    # Emit a structured signal for the PipelineRunner to parse.
    # Format: NODE:<node_id>:<status>:<detail>
    # status: running | done | skipped | failed
    # PipelineRunner parses with line.split(":", 3) so detail absorbs any
    # remaining colons (e.g. file paths, URLs) without truncation.
    local node_id="$1" status="$2" detail="${3:-}"
    echo "NODE:${node_id}:${status}:${detail}"
    # Track the most-recently-started node so the ERR trap can emit a
    # failure signal if the script exits unexpectedly mid-run.
    # Use if/then (not [[...]] && ...) to avoid a false-return triggering
    # the ERR trap when status is anything other than "running".
    if [[ "$status" == "running" ]]; then _current_node="$node_id"; fi
}

# _current_node: updated by node_signal() on each "running" transition.
# The ERR trap uses this to emit a NODE failure for the active node when
# the script exits due to set -euo pipefail catching an unexpected error
# (e.g. start_server timeout returning 1 while a node is still "running").
_current_node=""

# ERR trap: fires when any command exits non-zero under set -euo pipefail.
# Emits a NODE failed signal so PipelineRunner can mark the in-progress node
# as failed rather than leaving it stuck in the "running" state forever.
# Uses _current_node if set; falls back to sentinel "*" so the runner can
# mark all still-running nodes failed.
trap 'node_signal "${_current_node:-*}" "failed" "script exited unexpectedly (exit $?)"' ERR

# ── Node implementations ──────────────────────────────────────────────────────

node_text_to_image() {
    local node_id="$1" model="$2" prompt="$3" width="$4" height="$5" steps="$6" seed="$7" server="$8"
    log "  Generating image: $model (${width}x${height}, ${steps} steps)"
    [[ $DRY_RUN -eq 1 ]] && { set_result "$node_id" "image_path" "$OUTPUT_DIR/node${node_id}_image.png"; return 0; }

    # Fix 1: Add 2s delay before each FLUX submission to avoid overwhelming the
    # server when multiple image nodes run in sequence (prevents silent 429s).
    sleep 2

    # Fix 4: Retry job submission up to 3 times with 10s backoff on any failure
    # (covers transient network errors and server-side 429 rate limiting).
    local JOB="" attempt
    for attempt in 1 2 3; do
        JOB=$(python3 - "$prompt" "$width" "$height" "$steps" "$seed" "$server" << 'PY'
import sys, json, urllib.request, urllib.error
prompt, w, h, steps, seed, server = sys.argv[1:]
payload = {"prompt": prompt, "width": int(w), "height": int(h),
           "num_inference_steps": int(steps), "seed": int(seed)}
req = urllib.request.Request(f"{server}/v1/images/generations",
    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read()); print(d.get("id", "ERROR:"+str(d)))
except urllib.error.HTTPError as e:
    # Emit the HTTP status so the shell can detect rate limiting
    print(f"HTTP_ERROR:{e.code}")
except Exception as e:
    print(f"ERROR:{e}")
PY
        2>/dev/null || true)
        if [[ -z "$JOB" || "$JOB" == ERROR:* || "$JOB" == HTTP_ERROR:* ]]; then
            log "  ⚠️  Job submission attempt $attempt failed: $JOB"
            if [[ $attempt -lt 3 ]]; then
                log "  Waiting 10s before retry..."
                sleep 10
            fi
        else
            break
        fi
    done

    if [[ -z "$JOB" || "$JOB" == ERROR:* || "$JOB" == HTTP_ERROR:* ]]; then
        log "  ❌ Image generation failed after 3 submission attempts (node $node_id)"
        return 1
    fi

    log "  Job: $JOB"
    OUT="$OUTPUT_DIR/node${node_id}_image.png"
    for i in $(seq 1 40); do
        sleep 30
        # Fix 1: Detect 429 rate limit responses during poll; back off and retry
        # rather than silently dropping the status and leaving $STATUS empty.
        RAW_STATUS=$(curl -s -w '\n%{http_code}' "$server/v1/images/generations/$JOB" 2>/dev/null)
        HTTP_CODE=$(echo "$RAW_STATUS" | tail -1)
        if [[ "$HTTP_CODE" == "429" ]]; then
            log "  ⚠️  Rate limited (429) — waiting 60s before continuing poll"
            sleep 60
            continue
        fi
        STATUS=$(echo "$RAW_STATUS" | head -1 | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || true)
        [[ "$STATUS" == "completed" ]] && break
        [[ "$STATUS" == "failed" ]] && { log "  ❌ Image generation failed (server reported failure)"; return 1; }
        if [[ -z "$STATUS" || "$STATUS" == "?" ]]; then
            log "  ⚠️  Unexpected poll status '$STATUS' HTTP=$HTTP_CODE (node $node_id, poll $i)"
        fi
    done

    if [[ "$STATUS" != "completed" ]]; then
        log "  ⚠️  node $node_id skipped: image job did not complete (final status: $STATUS)"
        return 1
    fi

    curl -sf "$server/v1/images/generations/$JOB/download" -o "$OUT"
    log "  ✅ Image saved: $OUT ($(du -sh "$OUT" | cut -f1))"
    set_result "$node_id" "image_path" "$OUT"
}

node_image_to_video() {
    local node_id="$1" model="$2" prompt="$3" image_path="$4" width="$5" height="$6" frames="$7" steps="$8" seed="$9" server="${10}"
    log "  Generating video: $model (${width}x${height}, ${frames}f)"
    [[ $DRY_RUN -eq 1 ]] && { set_result "$node_id" "video_path" "$OUTPUT_DIR/node${node_id}_video.mp4"; return 0; }

    # Encode image as base64
    B64=$(python3 -c "import base64; print(base64.b64encode(open('$image_path','rb').read()).decode())")
    JOB=$(python3 - "$prompt" "$B64" "$width" "$height" "$frames" "$steps" "$seed" "$server" << 'PY'
import sys, json, urllib.request
prompt, b64, w, h, frames, steps, seed, server = sys.argv[1:]
payload = {"prompt": prompt, "image_prompts": [{"image": b64, "frame_pos": 0}],
           "width": int(w), "height": int(h), "num_frames": int(frames),
           "num_inference_steps": int(steps), "seed": int(seed)}
req = urllib.request.Request(f"{server}/v1/videos/generations/i2v",
    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=30) as r:
    d = json.loads(r.read()); print(d.get("id", "ERROR:"+str(d)))
PY
    )
    log "  Job: $JOB"
    OUT="$OUTPUT_DIR/node${node_id}_video.mp4"
    for i in $(seq 1 40); do
        sleep 30
        STATUS=$(curl -sf "$server/v1/videos/generations/$JOB" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || true)
        [[ "$STATUS" == "completed" ]] && break
        [[ "$STATUS" == "failed" ]] && { log "  ❌ Video generation failed (server reported failure)"; return 1; }
        # Fix 2 (I2V variant) + Fix 3: emit tagged progress lines so the popover
        # label updates live; also surface unexpected poll states.
        if [[ $((i % 4)) -eq 0 ]]; then
            log "[SkyReels I2V] $((i*30))s elapsed, generating…"
        fi
        if [[ -z "$STATUS" || "$STATUS" == "?" ]]; then
            log "  ⚠️  Unexpected poll status '$STATUS' for I2V job $JOB (poll $i)"
        fi
    done

    if [[ "$STATUS" != "completed" ]]; then
        log "  ⚠️  node $node_id skipped: video job did not complete (final status: $STATUS)"
        return 1
    fi

    curl -sf "$server/v1/videos/generations/$JOB/download" -o "$OUT"
    log "  ✅ Video saved: $OUT ($(du -sh "$OUT" | cut -f1))"
    set_result "$node_id" "video_path" "$OUT"
}

node_caption_image() {
    local node_id="$1" src="$2" prompt="${3:-}"
    log "  Captioning image with BLIP: $src"
    [[ $DRY_RUN -eq 1 ]] && { set_result "$node_id" "caption" "The 1964 World's Fair Unisphere stands tall against a bright sky."; return 0; }
    CAPTION=$("$PYTHON3" -c "
import sys; sys.path.insert(0, '$REPO_ROOT/plugins/blip')
import importlib.util
spec = importlib.util.spec_from_file_location('blip_plugin', '$REPO_ROOT/plugins/blip/plugin.py')
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
print(mod.caption_image('$src', '$prompt'))
")
    log "  Caption: $CAPTION"
    set_result "$node_id" "caption" "$CAPTION"
}

node_remove_background() {
    local node_id="$1" src="$2"
    local dest="$OUTPUT_DIR/node${node_id}_fg.png"
    log "  Removing background with RMBG: $src"
    [[ $DRY_RUN -eq 1 ]] && { set_result "$node_id" "fg_path" "$dest"; return 0; }
    "$PYTHON3" -c "
import importlib.util
spec = importlib.util.spec_from_file_location('rmbg_plugin', '$REPO_ROOT/plugins/rmbg/plugin.py')
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
mod.remove_background('$src', '$dest')
"
    log "  ✅ Foreground: $dest"
    set_result "$node_id" "fg_path" "$dest"
}

node_estimate_depth() {
    local node_id="$1" src="$2"
    local dest="$OUTPUT_DIR/node${node_id}_depth.png"
    log "  Estimating depth with GLPN: $src"
    [[ $DRY_RUN -eq 1 ]] && { set_result "$node_id" "depth_path" "$dest"; return 0; }
    "$PYTHON3" -c "
import importlib.util
spec = importlib.util.spec_from_file_location('depth_plugin', '$REPO_ROOT/plugins/depth/plugin.py')
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
mod.estimate_depth('$src', '$dest')
"
    log "  ✅ Depth map: $dest"
    set_result "$node_id" "depth_path" "$dest"
}

node_generate_text() {
    local node_id="$1" model="$2" prompt_template="$3" caption="$4" max_tokens="${5:-120}" server="$6"
    local prompt="${prompt_template//\{caption\}/$caption}"
    log "  Generating text: $model"
    [[ $DRY_RUN -eq 1 ]] && { set_result "$node_id" "poem" "The Unisphere gleams in silver light, / Tomorrow's promise etched in steel."; return 0; }
    TEXT=$(curl -sf "$server/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":$(echo "$prompt" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))")}],\"max_tokens\":$max_tokens,\"chat_template_kwargs\":{\"enable_thinking\":false}}" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'])" 2>/dev/null)
    log "  Poem: ${TEXT:0:100}..."
    set_result "$node_id" "poem" "$TEXT"
}

node_svg_render() {
    local node_id="$1" src="$2" size="${3:-1024}"
    local out="$OUTPUT_DIR/node${node_id}_logo.png"
    log "  Rendering SVG → PNG: $src (${size}px)"
    [[ $DRY_RUN -eq 1 ]] && { set_result "$node_id" "png_path" "$out"; set_result "$node_id" "_label" "SVG render"; touch "$out"; return 0; }
    "$PYTHON3" "$REPO_ROOT/plugins/svg_render/plugin.py" "$src" "$out" "$size" 2>&1 | tail -2
    if [[ -f "$out" ]]; then
        log "  ✅ SVG render: $out ($(du -sh "$out" | cut -f1))"
        set_result "$node_id" "png_path" "$out"
        set_result "$node_id" "_label" "SVG render"
    else
        log "  ⚠️  SVG render produced no output"
        return 1
    fi
}

node_composite() {
    local node_id="$1" background_path="$2" foreground_path="$3" scale="${4:-0.72}"
    local out="$OUTPUT_DIR/node${node_id}_composite.jpg"
    log "  Compositing mark over background (scale=$scale)..."
    [[ $DRY_RUN -eq 1 ]] && { set_result "$node_id" "composite_path" "$out"; set_result "$node_id" "_label" "composite"; touch "$out"; return 0; }
    "$PYTHON3" "$REPO_ROOT/plugins/composite/plugin.py" "$background_path" "$foreground_path" "$out" "$scale" 2>&1 | tail -2
    if [[ -f "$out" ]]; then
        log "  ✅ Composite: $out ($(du -sh "$out" | cut -f1))"
        set_result "$node_id" "composite_path" "$out"
        set_result "$node_id" "_label" "composite"
    else
        log "  ⚠️  Composite produced no output"
        return 1
    fi
}

# ── The 1964 World's Fair pipeline ───────────────────────────────────────────

log_step "1964 World's Fair Experiment"
log "Output directory: $OUTPUT_DIR"
[[ $DRY_RUN -eq 1 ]] && log "DRY RUN — no actual inference will run"

# Fix 3: Track partial failures so we can distinguish "all good" from "some
# nodes skipped" in the final summary.  Node functions emit ⚠️ lines on their
# own, and the popover _on_run_stdout parser picks those up for live display.
_failed_nodes=""

# Guard helper: run a node function; on failure record the node tag and
# continue (set +e scope) rather than aborting the whole pipeline via set -e.
_run_node() {
    local tag="$1"; shift
    set +e
    "$@"
    local rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
        _failed_nodes="${_failed_nodes} ${tag}"
        log "  ⚠️  ${tag} failed (exit $rc) — continuing pipeline"
    fi
    return 0
}

# ── Node 1: Seed image (FLUX.1-schnell) ──────────────────────────────────────
log_step "Node 1: Seed image — FLUX.1-schnell"
node_signal "1" "running" "FLUX.1-schnell"
stop_and_reset "flux"
start_server "flux" "http://localhost:8000/tt-liveness" 30
_run_node "node1(flux-image)" node_text_to_image "1" \
    "FLUX.1-schnell" \
    "SEED_PROMPT_PLACEHOLDER" \
    "1024" "1024" "4" "SEED_PLACEHOLDER" "http://localhost:8000"
set_node_label "1" "seed image"

IMAGE_PATH=$(get_result '["1", "image_path"]')
if [[ -z "$IMAGE_PATH" ]]; then
    node_signal "1" "failed" "image_path empty"
    log "  ⚠️  node1 skipped: image_path is empty — downstream nodes may fail"
else
    node_signal "1" "done" "$IMAGE_PATH"
fi

# ── Nodes 2-4: CPU plugins (no reset) ────────────────────────────────────────
log_step "Node 2: Caption image — BLIP (CPU)"
node_signal "2" "running" "BLIP"
_run_node "node2(blip-caption)" node_caption_image "2" "$IMAGE_PATH" "a cinematic scene showing"
set_node_label "2" "caption"
CAPTION=$(get_result '["2", "caption"]')
if [[ -z "$CAPTION" ]]; then
    node_signal "2" "failed" "caption empty"
else
    node_signal "2" "done" "$CAPTION"
fi

log_step "Node 3: Remove background — RMBG (CPU)"
node_signal "3" "running" "RMBG"
_run_node "node3(rmbg)" node_remove_background "3" "$IMAGE_PATH"
set_node_label "3" "foreground"
FG_PATH=$(get_result '["3", "fg_path"]')
[[ -n "$FG_PATH" ]] && node_signal "3" "done" "$FG_PATH" || node_signal "3" "failed" "no fg_path"

log_step "Node 4: Depth map — GLPN (CPU)"
node_signal "4" "running" "GLPN"
_run_node "node4(depth)" node_estimate_depth "4" "$IMAGE_PATH"
set_node_label "4" "depth map"
DEPTH_PATH=$(get_result '["4", "depth_path"]')
[[ -n "$DEPTH_PATH" ]] && node_signal "4" "done" "$DEPTH_PATH" || node_signal "4" "failed" "no depth_path"

# ── Node 5: Compose video prompt ─────────────────────────────────────────────
log_step "Node 5: Compose video prompt"
node_signal "5" "running" "compose"
VIDEO_PROMPT="$CAPTION, ERA_CONTEXT_PLACEHOLDER, cinematic slow push-in"
set_result "5" "video_prompt" "$VIDEO_PROMPT"
set_node_label "5" "video prompt"
log "  Video prompt: ${VIDEO_PROMPT:0:100}..."
node_signal "5" "done" "$VIDEO_PROMPT"

# ── Node 6: Video (SkyReels I2V) — board reset required ─────────────────────
log_step "Node 6: World's Fair video — SkyReels I2V"
node_signal "6" "running" "SkyReels-V2-I2V"
stop_and_reset "skyreels"
start_server "skyreels" "http://localhost:8000/tt-liveness" 60
_run_node "node6(skyreels-i2v)" node_image_to_video "6" \
    "SkyReels-V2-I2V-14B-540P" \
    "$VIDEO_PROMPT" \
    "$IMAGE_PATH" \
    "960" "544" "97" "20" "SEED_PLACEHOLDER" "http://localhost:8000"
set_node_label "6" "video"
VIDEO_PATH=$(get_result '["6", "video_path"]')
[[ -n "$VIDEO_PATH" ]] && node_signal "6" "done" "$VIDEO_PATH" || node_signal "6" "failed" "no video_path"

# ── Node 7: Poem (Llama-3.3-70B) — board reset required ─────────────────────
log_step "Node 7: Poem — Llama-3.3-70B-Instruct"
node_signal "7" "running" "Llama-3.3-70B"
stop_and_reset "artgen-llama-3.3-70b"
start_server "artgen-llama-3.3-70b" "http://localhost:8002/v1/models" 30
_run_node "node7(llama-poem)" node_generate_text "7" \
    "meta-llama/Llama-3.3-70B-Instruct" \
    "POEM_PROMPT_PLACEHOLDER" \
    "$CAPTION" \
    "120" \
    "http://localhost:8002"
set_node_label "7" "poem"
POEM=$(get_result '["7", "poem"]')
if [[ -z "$POEM" ]]; then
    node_signal "7" "failed" "no poem"
else
    node_signal "7" "done" "${POEM:0:80}"
fi

# ── Node 8: Poem image (FLUX.1-schnell) — board reset required ───────────────
log_step "Node 8: Poem image — FLUX.1-schnell"
node_signal "8" "running" "FLUX.1-schnell"
stop_and_reset "flux"
start_server "flux" "http://localhost:8000/tt-liveness" 30
_run_node "node8(flux-image)" node_text_to_image "8" \
    "FLUX.1-schnell" \
    "$POEM" \
    "1024" "1024" "4" "POEM_SEED_PLACEHOLDER" "http://localhost:8000"
set_node_label "8" "poem image"
IMAGE2_PATH=$(get_result '["8", "image_path"]')
[[ -n "$IMAGE2_PATH" ]] && node_signal "8" "done" "$IMAGE2_PATH" || node_signal "8" "failed" "no image2_path"

# ── Node 9: Add to playlist ───────────────────────────────────────────────────
log_step "Node 9: Save to playlist"
stop_and_reset ""

PLAYLIST_NAME="1964 world's fair experiment"
log "  Adding artifacts to playlist: $PLAYLIST_NAME"

python3 - "$PLAYLIST_NAME" "$IMAGE_PATH" "$IMAGE2_PATH" \
    "$(get_result '["6", "video_path"]')" \
    "$(get_result '["3", "fg_path"]')" \
    "$(get_result '["4", "depth_path"]')" \
    "$CAPTION" "$POEM" "$RESULTS_JSON" << 'PY'
import sys, json, shutil, uuid
from pathlib import Path
from datetime import datetime, timezone

playlist_name, img1, img2, video, fg, depth, caption, poem, results_json = sys.argv[1:]

# Import artifacts into the app's media store and create a playlist
APP_DIR = Path.home() / ".local" / "share" / "tt-local-generator"
IMAGES_DIR = APP_DIR / "images"
VIDEOS_DIR = APP_DIR / "videos"
THUMBS_DIR = APP_DIR / "thumbnails"
for d in (IMAGES_DIR, VIDEOS_DIR, THUMBS_DIR): d.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
try:
    from media_store import media_store as _ms, MediaRecord
    from playlist_store import PlaylistStore

    _ps = PlaylistStore()
    pl = _ps.get_or_create(playlist_name)
    record_ids = []

    def _import(src, media_type, prompt_text, model="workflow"):
        src = Path(src)
        if not src.exists(): return None
        ext = src.suffix
        ts = datetime.now(timezone.utc)
        rid = str(uuid.uuid4())
        ts_str = ts.strftime("%Y%m%d_%H%M%S")
        dest_dir = VIDEOS_DIR if media_type == "video" else IMAGES_DIR
        dest = dest_dir / f"{ts_str}_{rid[:8]}{ext}"
        shutil.copy2(src, dest)
        # Thumbnail: copy for images, first-frame for video
        thumb = THUMBS_DIR / f"{ts_str}_{rid[:8]}.jpg"
        try:
            import subprocess
            subprocess.run(["ffmpeg", "-y", "-i", str(dest),
                "-vf", "scale=200:112:force_original_aspect_ratio=decrease,pad=200:112:(ow-iw)/2:(oh-ih)/2",
                "-frames:v", "1", "-update", "1", "-q:v", "3", str(thumb)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        except Exception:
            shutil.copy2(dest, thumb)
        rec = MediaRecord(
            id=rid, file_path=str(dest), thumbnail_path=str(thumb),
            prompt=prompt_text, media_type=media_type,
            created_at=ts.isoformat(), model_id=model, generator_type=None, starred=0,
            params=json.dumps({"workflow": "1964-worlds-fair", "video_path": str(dest) if mtype == "video" else "", "image_path": str(dest) if mtype != "video" else ""}),
        )
        _ms.add(rec)
        return rid

    # Add artifacts in narrative order
    for path, mtype, prompt in [
        (img1,  "image",  f"1964 World's Fair seed image: {caption[:80]}"),
        (fg,    "image",  "Background removed: World's Fair subject"),
        (depth, "image",  "Depth map: World's Fair scene"),
        (video, "video",  f"SkyReels I2V: {caption[:80]}"),
        (img2,  "image",  f"Poem image: {poem[:80]}"),
    ]:
        rid = _import(path, mtype, prompt)
        if rid: record_ids.append(rid)

    if record_ids:
        _ps.add_records(pl.id, record_ids)

    print(f"\n✅ Playlist '{playlist_name}' created in the app ({len(record_ids)} artifacts)")
    print(f"   Open tt-gen → File → Playlists to view")
except Exception as e:
    print(f"\n⚠️  Could not write to app store: {e}")
    print(f"   Artifacts are in: {str(Path(img1).parent) if Path(img1).exists() else results_json}")

print(f"\n{'─'*60}")
print(f"Caption: {caption}")
print(f"\nPoem:\n{poem}")
print(f"{'─'*60}")
PY

log ""
# Fix 3: Report partial vs full success so the popover and log make it clear
# when some nodes were skipped rather than silently claiming full completion.
if [[ -n "$_failed_nodes" ]]; then
    log "⚠️  Pipeline finished with partial failures:$_failed_nodes"
    log "   Results JSON: $RESULTS_JSON"
else
    log "✅ 1964 World's Fair pipeline complete!"
    log "   Results JSON: $RESULTS_JSON"
fi
