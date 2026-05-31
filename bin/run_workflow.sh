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
        [[ $((i % 4)) -eq 0 ]] && log "  ... $((i*30/60))min elapsed"
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

# ── Node implementations ──────────────────────────────────────────────────────

node_text_to_image() {
    local node_id="$1" model="$2" prompt="$3" width="$4" height="$5" steps="$6" seed="$7" server="$8"
    log "  Generating image: $model (${width}x${height}, ${steps} steps)"
    [[ $DRY_RUN -eq 1 ]] && { set_result "$node_id" "image_path" "$OUTPUT_DIR/node${node_id}_image.png"; return 0; }

    JOB=$(python3 - "$prompt" "$width" "$height" "$steps" "$seed" "$server" << 'PY'
import sys, json, urllib.request
prompt, w, h, steps, seed, server = sys.argv[1:]
payload = {"prompt": prompt, "width": int(w), "height": int(h),
           "num_inference_steps": int(steps), "seed": int(seed)}
req = urllib.request.Request(f"{server}/v1/images/generations",
    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=30) as r:
    d = json.loads(r.read()); print(d.get("id", "ERROR:"+str(d)))
PY
    )
    log "  Job: $JOB"
    OUT="$OUTPUT_DIR/node${node_id}_image.png"
    for i in $(seq 1 40); do
        sleep 30
        STATUS=$(curl -sf "$server/v1/images/generations/$JOB" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null)
        [[ "$STATUS" == "completed" ]] && break
        [[ "$STATUS" == "failed" ]] && { log "  ❌ Image generation failed"; return 1; }
    done
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
        STATUS=$(curl -sf "$server/v1/videos/generations/$JOB" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null)
        [[ "$STATUS" == "completed" ]] && break
        [[ "$STATUS" == "failed" ]] && { log "  ❌ Video generation failed"; return 1; }
        [[ $((i % 4)) -eq 0 ]] && log "  ... $((i*30))s elapsed"
    done
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

# ── The 1964 World's Fair pipeline ───────────────────────────────────────────

log_step "1964 World's Fair Experiment"
log "Output directory: $OUTPUT_DIR"
[[ $DRY_RUN -eq 1 ]] && log "DRY RUN — no actual inference will run"

# ── Node 1: Seed image (FLUX.1-schnell) ──────────────────────────────────────
log_step "Node 1: Seed image — FLUX.1-schnell"
stop_and_reset "flux"
start_server "flux" "http://localhost:8000/tt-liveness" 30
node_text_to_image "1" \
    "FLUX.1-schnell" \
    "The 1964 New York World's Fair, Unisphere gleaming in the sunlight, futuristic pavilions, optimistic crowds in period clothing, Kodachrome colors, cinematic wide shot" \
    "1024" "1024" "4" "1964" "http://localhost:8000"

IMAGE_PATH=$(get_result '["1", "image_path"]')

# ── Nodes 2-4: CPU plugins (no reset) ────────────────────────────────────────
log_step "Node 2: Caption image — BLIP (CPU)"
node_caption_image "2" "$IMAGE_PATH" "a cinematic scene showing"
CAPTION=$(get_result '["2", "caption"]')

log_step "Node 3: Remove background — RMBG (CPU)"
node_remove_background "3" "$IMAGE_PATH"

log_step "Node 4: Depth map — GLPN (CPU)"
node_estimate_depth "4" "$IMAGE_PATH"

# ── Node 5: Compose video prompt ─────────────────────────────────────────────
log_step "Node 5: Compose video prompt"
VIDEO_PROMPT="$CAPTION, 1964 World's Fair, Unisphere, retro-futuristic, Kodachrome, cinematic slow push-in"
set_result "5" "video_prompt" "$VIDEO_PROMPT"
log "  Video prompt: ${VIDEO_PROMPT:0:100}..."

# ── Node 6: Video (SkyReels I2V) — board reset required ─────────────────────
log_step "Node 6: World's Fair video — SkyReels I2V"
stop_and_reset "skyreels"
start_server "skyreels" "http://localhost:8000/tt-liveness" 60
node_image_to_video "6" \
    "SkyReels-V2-I2V-14B-540P" \
    "$VIDEO_PROMPT" \
    "$IMAGE_PATH" \
    "960" "544" "33" "20" "1964" "http://localhost:8000"

# ── Node 7: Poem (Llama-3.3-70B) — board reset required ─────────────────────
log_step "Node 7: Poem — Llama-3.3-70B-Instruct"
stop_and_reset "artgen-llama-3.3-70b"
start_server "artgen-llama-3.3-70b" "http://localhost:8002/v1/models" 30
node_generate_text "7" \
    "meta-llama/Llama-3.3-70B-Instruct" \
    "Write a short, evocative poem (4-6 lines) inspired by this scene: {caption}. Set at the 1964 World's Fair. Use sensory detail, optimism, and a sense of wonder at the future." \
    "$CAPTION" \
    "120" \
    "http://localhost:8002"
POEM=$(get_result '["7", "poem"]')

# ── Node 8: Poem image (FLUX.1-schnell) — board reset required ───────────────
log_step "Node 8: Poem image — FLUX.1-schnell"
stop_and_reset "flux"
start_server "flux" "http://localhost:8000/tt-liveness" 30
node_text_to_image "8" \
    "FLUX.1-schnell" \
    "$POEM" \
    "1024" "1024" "4" "1965" "http://localhost:8000"
IMAGE2_PATH=$(get_result '["8", "image_path"]')

# ── Node 9: Add to playlist ───────────────────────────────────────────────────
log_step "Node 9: Save to playlist"
stop_and_reset ""

PLAYLIST_NAME="1964 world's fair experiment"
log "  Adding artifacts to playlist: $PLAYLIST_NAME"

python3 - "$PLAYLIST_NAME" "$IMAGE_PATH" "$IMAGE2_PATH" \
    "$(get_result '["6", "video_path"]')" \
    "$(get_result '["3", "fg_path"]')" \
    "$(get_result '["4", "depth_path"]')" \
    "$CAPTION" "$POEM" << 'PY'
import sys, json
from pathlib import Path

playlist_name, img1, img2, video, fg, depth, caption, poem = sys.argv[1:]
print(f"\n{'─'*60}")
print(f"Playlist: {playlist_name}")
print(f"{'─'*60}")
print(f"  Seed image:   {img1}  ({Path(img1).stat().st_size // 1024} KB)" if Path(img1).exists() else f"  Seed image:   MISSING")
print(f"  FG mask:      {fg}")
print(f"  Depth map:    {depth}")
print(f"  Video:        {video}")
print(f"  Poem image:   {img2}")
print(f"\n  Caption: {caption}")
print(f"\n  Poem:\n{poem}")
print(f"{'─'*60}")
print(f"\nAll artifacts in: {str(Path(img1).parent) if Path(img1).exists() else '?'}")
print(f"\nTo import into the app:")
print(f"  Copy these files to ~/.local/share/tt-local-generator/videos/ and images/")
print(f"  Then use File → Import in the app to add them to a playlist.")
PY

log ""
log "✅ 1964 World's Fair pipeline complete!"
log "   Results JSON: $RESULTS_JSON"
