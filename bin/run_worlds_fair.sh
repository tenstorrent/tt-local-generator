#!/usr/bin/env bash
# run_worlds_fair.sh — Run the World's Fair pipeline for any of 5 fairs.
#
# Each fair uses a hand-researched, historically specific seed prompt chosen
# to avoid the main iconic structure (no Unisphere, no Trylon, no Tower of the Sun).
# Prompts were selected for strong SkyReels I2V motion seeds:
#   - Bodies with implied motion (rising, leaning, pressing against glass)
#   - Atmospherically interesting lighting (Tesla's glowing tube, Osaka fog)
#   - Closed interiors with depth planes (IBM auditorium, USSR space gallery)
#
# Forge step assessment (2026-06-01):
#   - RMBG removed: background removal destroys scene context for historical
#     scene photography; the background IS the narrative for I2V conditioning.
#   - BLIP removed: manually written prompts already have far richer historical
#     detail than any auto-caption; BLIP would be redundant noise.
#   - Depth estimation kept but conditional: applied only when the prompt
#     describes a closed interior with multiple depth planes; skipped for fog,
#     darkness, and open exteriors where depth is not useful.
#
# Usage:
#   ./bin/run_worlds_fair.sh 1964-ny
#   ./bin/run_worlds_fair.sh 1939-ny
#   ./bin/run_worlds_fair.sh 1893-chicago
#   ./bin/run_worlds_fair.sh 1970-osaka
#   ./bin/run_worlds_fair.sh 1967-montreal
#   ./bin/run_worlds_fair.sh 1964-ny --dry-run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FAIR_KEY="${1:-}"
DRY_RUN=0
[[ "${2:-}" == "--dry-run" ]] && DRY_RUN=1

# ── Fair data ──────────────────────────────────────────────────────────────────
# Each entry: PROMPT | FAIR_LABEL | PLAYLIST_NAME | POEM_CONTEXT | SEED | USE_DEPTH

case "$FAIR_KEY" in
  1964-ny)
    SEED_PROMPT="Kodachrome slide film photograph, 1964. A grandstand of 500 fairgoers in summer clothes — children gripping armrests, women clutching handbags — rises hydraulically on the IBM People Wall into the interior of a vast white egg-shaped auditorium. Curved white walls are studded with repeating IBM logotypes. Overhead, a Charles Eames film projection throws blue-gray light. The motion is almost imperceptible but unmistakable: the floor is moving upward, and half the crowd is grinning."
    FAIR_LABEL="1964 New York World's Fair"
    ERA_CONTEXT="IBM Pavilion, People Wall hydraulic theater, 1964 World's Fair, Kodachrome, cinematic upward motion"
    POEM_CONTEXT="Inside the IBM Pavilion at the 1964 World's Fair, as a bleacher of visitors rises hydraulically into a vast egg-shaped auditorium. Space Age optimism, human wonder at technology, the floor itself becoming the future."
    SEED=19640422
    USE_DEPTH=1   # closed interior with foreground crowd / curved wall background
    PLAYLIST_NAME="World's Fair 1964 NY — IBM People Wall"
    ;;
  1939-ny)
    SEED_PROMPT="Kodachrome slide film photograph, 1939. A seven-foot golden aluminum humanoid robot stands on a low stage inside a glass-walled pavilion, aluminum lips parted mid-sentence, one hand raised with fingers spread for counting. A Westinghouse technician in a white lab coat holds a lit cigarette near the robot's upper lip. Hundreds of spectators press six-deep against the glass exterior, hats craning, a child lifted on a father's shoulders to see over the crowd."
    FAIR_LABEL="1939 New York World's Fair"
    ERA_CONTEXT="Westinghouse Elektro robot, Hall of Electrical Living, 1939 World's Fair, Kodachrome, crowd pressing glass"
    POEM_CONTEXT="At the Westinghouse Hall of Electrical Living, 1939 World's Fair — Elektro the Moto-Man, a seven-foot aluminum robot, speaks and counts on mechanical fingers while hundreds of Depression-era Americans press against the glass to see the future."
    SEED=19390430
    USE_DEPTH=1   # indoor stage with foreground/background glass crowd
    PLAYLIST_NAME="World's Fair 1939 NY — Elektro the Robot"
    ;;
  1893-chicago)
    SEED_PROMPT="Sepia daguerreotype aesthetic, 1893 Chicago Electricity Building. A darkened demonstration room, a crowd pressed three-deep behind a rope barrier. At center: Nikola Tesla in a dark frock coat holds a glass vacuum tube aloft — it blazes with cold blue-white phosphorescent light, lit by no wire, fed by invisible wireless current. Men's faces glow astonished. Women grip their companions' arms. On the bench behind him: the copper Egg of Columbus spinning silently upright in its brass induction ring."
    FAIR_LABEL="1893 Chicago World's Columbian Exposition"
    ERA_CONTEXT="Nikola Tesla wireless demonstration, Electricity Building, 1893 World's Fair, daguerreotype sepia, cold luminous tube in darkness"
    POEM_CONTEXT="In the Electricity Building at the 1893 Chicago World's Fair, Nikola Tesla demonstrates wireless power — a glass vacuum tube blazing with cold blue light held in his bare hand, no wires, no Edison. The crowd's faces are lit from below by something no one has seen before."
    SEED=18930501
    USE_DEPTH=1   # dark interior, rope barrier foreground, Tesla mid, crowd rear
    PLAYLIST_NAME="World's Fair 1893 Chicago — Tesla's Light"
    ;;
  1970-osaka)
    SEED_PROMPT="Fujiko Nakaya's artificial fog sculpture engulfs the Pepsi geodesic sphere at twilight, Expo 70 Osaka, March 1970. Visitors in mod coats and platform shoes dissolve waist-deep into luminous white vapor, lit from below by xenon arc lamps. The dome's ridged surface barely visible through the mist. Fujifilm Velvia grain, saturated cyan-white haze, deep indigo sky."
    FAIR_LABEL="1970 Osaka World Expo"
    ERA_CONTEXT="Pepsi Pavilion fog sculpture, E.A.T. Fujiko Nakaya, Expo 70 Osaka, Fujifilm grain, xenon-lit vapor at twilight"
    POEM_CONTEXT="At the Pepsi Pavilion of Expo 70, Osaka — Fujiko Nakaya's fog sculpture, the world's first, dissolves hundreds of visitors into luminous vapor under xenon arc lamps. Technology made atmospheric, bodies made uncertain, the future made of clouds."
    SEED=19700315
    USE_DEPTH=0   # fog intentionally collapses depth; depth map would fight the aesthetic
    PLAYLIST_NAME="World's Fair 1970 Osaka — Fog Pavilion"
    ;;
  1967-montreal)
    SEED_PROMPT="Inside a cylindrical theatre at the 1967 Montreal World's Fair, hundreds of visitors stand gripping steel guide rails as nine giant curved screens surrounding them fill with aerial footage of Niagara Falls rushing directly overhead. Women in mod wool coats and men in narrow-lapel suits lean into the rails, faces upturned in genuine vertigo. Bold Kodachrome saturation, 1967 Canadian color magazine photography."
    FAIR_LABEL="1967 Montreal Expo"
    ERA_CONTEXT="Disney Circle-Vision 360° theatre, Bell Telephone Pavilion, Expo 67 Montreal, Kodachrome, bodies leaning into guide rails"
    POEM_CONTEXT="In the Bell Telephone Pavilion at Expo 67, Montreal — Walt Disney's Circle-Vision 360° film puts Niagara Falls directly overhead while visitors grip steel rails, their faces tilted up in vertigo. The last thing Disney made before he died. A Canadian documentary as immersive as any ride."
    SEED=19670427
    USE_DEPTH=1   # cylindrical interior with clear spatial layers
    PLAYLIST_NAME="World's Fair 1967 Montreal — Circle-Vision"
    ;;
  *)
    echo "Usage: $0 {1964-ny|1939-ny|1893-chicago|1970-osaka|1967-montreal} [--dry-run]"
    echo ""
    echo "Available fairs:"
    echo "  1964-ny        1964 New York — IBM People Wall hydraulic theater"
    echo "  1939-ny        1939 New York — Elektro the Westinghouse robot"
    echo "  1893-chicago   1893 Chicago — Tesla wireless light demonstration"
    echo "  1970-osaka     1970 Osaka — Pepsi Pavilion fog sculpture (Fujiko Nakaya)"
    echo "  1967-montreal  1967 Montreal — Disney Circle-Vision 360° theatre"
    exit 1
    ;;
esac

PYTHON3="${HOME}/.tenstorrent-venv/bin/python3"
if [[ ! -f "$PYTHON3" ]]; then
    PYTHON3=/usr/bin/python3
fi

OUTPUT_DIR="${HOME}/.local/share/tt-local-generator/workflow-runs/$(date +%Y%m%d_%H%M%S)_${FAIR_KEY}"
mkdir -p "$OUTPUT_DIR"
RESULTS_JSON="$OUTPUT_DIR/results.json"
echo "{}" > "$RESULTS_JSON"

LOG_FILE="${HOME}/.local/share/tt-local-generator/logs/workflow/$(date +%Y%m%d_%H%M%S)_${FAIR_KEY}.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "LOG:$LOG_FILE"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
log_step() { echo ""; echo "══ $* ══"; }

# ── Hardware management ───────────────────────────────────────────────────────

_current_server=""

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
            docker exec "$(docker ps -q | head -1)" chmod 777 /tmp/prometheus_multiproc 2>/dev/null || true
            return 0
        fi
        if [[ $((i % 4)) -eq 0 ]]; then
            log "[${server_key} warmup] $((i*30/60))min elapsed, waiting for model…"
            local skyreels_log
            skyreels_log=$(ls -t "${HOME}/code/tt-local-generator"/media_*SkyReels*.log 2>/dev/null | head -1)
            if [[ -n "$skyreels_log" ]]; then
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
    local ref="$1"
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

    sleep 2

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
    print(f"HTTP_ERROR:{e.code}")
except Exception as e:
    print(f"ERROR:{e}")
PY
        2>/dev/null || true)
        if [[ -z "$JOB" || "$JOB" == ERROR:* || "$JOB" == HTTP_ERROR:* ]]; then
            log "  ⚠️  Submission attempt $attempt failed: $JOB"
            [[ $attempt -lt 3 ]] && sleep 10
        else
            break
        fi
    done

    if [[ -z "$JOB" || "$JOB" == ERROR:* || "$JOB" == HTTP_ERROR:* ]]; then
        log "  ❌ Image generation failed after 3 attempts (node $node_id)"
        return 1
    fi

    log "  Job: $JOB"
    OUT="$OUTPUT_DIR/node${node_id}_image.png"
    local STATUS=""
    for i in $(seq 1 40); do
        sleep 30
        RAW_STATUS=$(curl -s -w '\n%{http_code}' "$server/v1/images/generations/$JOB" 2>/dev/null)
        HTTP_CODE=$(echo "$RAW_STATUS" | tail -1)
        if [[ "$HTTP_CODE" == "429" ]]; then
            log "  ⚠️  Rate limited (429) — waiting 60s"
            sleep 60; continue
        fi
        STATUS=$(echo "$RAW_STATUS" | head -1 | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || true)
        [[ "$STATUS" == "completed" ]] && break
        [[ "$STATUS" == "failed" ]] && { log "  ❌ Server reported failure"; return 1; }
    done

    [[ "$STATUS" != "completed" ]] && { log "  ⚠️  node $node_id skipped: did not complete (status: $STATUS)"; return 1; }

    curl -sf "$server/v1/images/generations/$JOB/download" -o "$OUT"
    log "  ✅ Image saved: $OUT ($(du -sh "$OUT" | cut -f1))"
    set_result "$node_id" "image_path" "$OUT"
}

node_image_to_video() {
    local node_id="$1" model="$2" prompt="$3" image_path="$4" width="$5" height="$6" frames="$7" steps="$8" seed="$9" server="${10}"
    log "  Generating video: $model (${width}x${height}, ${frames}f, ${steps} steps)"
    [[ $DRY_RUN -eq 1 ]] && { set_result "$node_id" "video_path" "$OUTPUT_DIR/node${node_id}_video.mp4"; return 0; }

    B64=$(python3 -c "import base64; print(base64.b64encode(open('$image_path','rb').read()).decode())")
    JOB=$(python3 - "$prompt" "$B64" "$width" "$height" "$frames" "$steps" "$seed" "$server" << 'PY'
import sys, json, urllib.request
prompt, b64, w, h, frames, steps, seed, server = sys.argv[1:]
payload = {"prompt": prompt, "image": b64, "width": int(w), "height": int(h),
           "num_frames": int(frames), "num_inference_steps": int(steps), "seed": int(seed)}
req = urllib.request.Request(f"{server}/v1/video/generations",
    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=60) as r:
    d = json.loads(r.read()); print(d.get("id", "ERROR"))
PY
    2>/dev/null || true)

    if [[ -z "$JOB" || "$JOB" == ERROR* ]]; then
        log "  ❌ Video job submission failed: $JOB"
        return 1
    fi

    log "  Job: $JOB"
    OUT="$OUTPUT_DIR/node${node_id}_video.mp4"
    local STATUS=""
    for i in $(seq 1 240); do
        sleep 30
        STATUS=$(curl -s "$server/v1/video/generations/$JOB" 2>/dev/null | \
            python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || true)
        [[ "$STATUS" == "completed" ]] && break
        [[ "$STATUS" == "failed" ]] && { log "  ❌ Video generation failed"; return 1; }
        if [[ $((i % 4)) -eq 0 ]]; then
            log "  [video] ${i}×30s elapsed, status=$STATUS"
            local skyreels_log
            skyreels_log=$(ls -t "${HOME}/code/tt-local-generator"/media_*SkyReels*.log 2>/dev/null | head -1)
            if [[ -n "$skyreels_log" ]]; then
                local last_line
                last_line=$(grep -v '^\s*$' "$skyreels_log" 2>/dev/null | grep -v '\[=' | tail -1 || true)
                [[ -n "$last_line" ]] && log "⏳ SkyReels: ${last_line:0:120}"
            fi
        fi
    done

    [[ "$STATUS" != "completed" ]] && { log "  ⚠️  node $node_id skipped: video did not complete (status: $STATUS)"; return 1; }

    curl -sf "$server/v1/video/generations/$JOB/download" -o "$OUT"
    log "  ✅ Video saved: $OUT ($(du -sh "$OUT" | cut -f1))"
    set_result "$node_id" "video_path" "$OUT"
}

node_estimate_depth() {
    local node_id="$1" src="$2"
    local dest="$OUTPUT_DIR/node${node_id}_depth.png"
    log "  Estimating depth: GLPN-KITTI (CPU)"
    [[ $DRY_RUN -eq 1 ]] && { set_result "$node_id" "depth_path" "$dest"; return 0; }

    "$PYTHON3" -c "
import sys
sys.path.insert(0, '$REPO_ROOT/plugins/depth')
from plugin import estimate_depth
estimate_depth('$src', '$dest')
print('done')
" 2>&1 | tail -3
    if [[ -f "$dest" ]]; then
        log "  ✅ Depth map: $dest"
        set_result "$node_id" "depth_path" "$dest"
    else
        log "  ⚠️  Depth estimation produced no output"
        return 1
    fi
}

node_generate_text() {
    local node_id="$1" model="$2" prompt_template="$3" caption="$4" max_tokens="$5" server="$6"
    log "  Generating text: $model"
    [[ $DRY_RUN -eq 1 ]] && { set_result "$node_id" "poem" "The future blazes in a vacuum tube, / No wire, no Edison, no fear. / Tesla's light asks nothing of the dark — / It simply appears."; return 0; }

    FULL_PROMPT="${prompt_template/\{caption\}/$caption}"
    TEXT=$(python3 - "$FULL_PROMPT" "$model" "$max_tokens" "$server" << 'PY'
import sys, json, urllib.request
prompt, model, max_tokens, server = sys.argv[1:]
payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
           "max_tokens": int(max_tokens), "temperature": 0.85}
req = urllib.request.Request(f"{server}/v1/chat/completions",
    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=60) as r:
    d = json.loads(r.read())
    print(d["choices"][0]["message"]["content"].strip())
PY
    2>/dev/null || true)

    if [[ -z "$TEXT" ]]; then
        log "  ⚠️  Text generation returned empty"
        return 1
    fi
    log "  ✅ Text: ${TEXT:0:80}…"
    set_result "$node_id" "poem" "$TEXT"
}

# ── Pipeline ──────────────────────────────────────────────────────────────────

log_step "$FAIR_LABEL Pipeline"
log "Output directory: $OUTPUT_DIR"
log "Seed prompt: ${SEED_PROMPT:0:120}…"
[[ $DRY_RUN -eq 1 ]] && log "DRY RUN — no actual inference will run"

_failed_nodes=""

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
stop_and_reset "flux"
start_server "flux" "http://localhost:8000/tt-liveness" 30
_run_node "node1(flux-image)" node_text_to_image "1" \
    "FLUX.1-schnell" \
    "$SEED_PROMPT" \
    "1024" "1024" "4" "$SEED" "http://localhost:8000"
set_node_label "1" "seed image"

IMAGE_PATH=$(get_result '["1", "image_path"]')
if [[ -z "$IMAGE_PATH" ]]; then
    log "  ⚠️  node1 skipped: image_path is empty — downstream nodes may fail"
fi

# ── Node 2: Depth map (GLPN, CPU) — conditional on USE_DEPTH ─────────────────
if [[ "$USE_DEPTH" == "1" && -n "$IMAGE_PATH" ]]; then
    log_step "Node 2: Depth map — GLPN-KITTI (CPU)"
    log "  Scene type: closed interior — depth conditioning useful"
    _run_node "node2(glpn-depth)" node_estimate_depth "2" "$IMAGE_PATH"
    set_node_label "2" "depth map"
else
    log_step "Node 2: Depth map — SKIPPED"
    log "  Scene type: fog/exterior — depth would fight the atmospheric aesthetic"
    set_node_label "2" "depth map (skipped)"
fi

# ── Node 3: Compose video prompt ─────────────────────────────────────────────
log_step "Node 3: Compose video prompt"
VIDEO_PROMPT="$ERA_CONTEXT, cinematic slow push-in, photorealistic"
set_result "3" "video_prompt" "$VIDEO_PROMPT"
set_node_label "3" "video prompt"
log "  Video prompt: ${VIDEO_PROMPT:0:120}"

# ── Node 4: Video (SkyReels I2V) ─────────────────────────────────────────────
log_step "Node 4: World's Fair video — SkyReels V2 I2V (97 frames)"
stop_and_reset "skyreels"
start_server "skyreels" "http://localhost:8000/tt-liveness" 60
_run_node "node4(skyreels-i2v)" node_image_to_video "4" \
    "SkyReels-V2-I2V-14B-540P" \
    "$VIDEO_PROMPT" \
    "$IMAGE_PATH" \
    "960" "544" "97" "20" "$SEED" "http://localhost:8000"
set_node_label "4" "video"

# ── Node 5: Poem (Llama-3.3-70B) ─────────────────────────────────────────────
log_step "Node 5: Poem — Llama-3.3-70B-Instruct"
stop_and_reset "artgen-llama-3.3-70b"
start_server "artgen-llama-3.3-70b" "http://localhost:8002/v1/models" 30
_run_node "node5(llama-poem)" node_generate_text "5" \
    "meta-llama/Llama-3.3-70B-Instruct" \
    "Write a short, evocative poem (4-6 lines) about this scene: $POEM_CONTEXT. Use specific historical detail. Focus on wonder, strangeness, and the gap between the future imagined and the future that arrived." \
    "$ERA_CONTEXT" \
    "150" \
    "http://localhost:8002"
set_node_label "5" "poem"
POEM=$(get_result '["5", "poem"]')

# ── Node 6: Poem image (FLUX.1-schnell) ──────────────────────────────────────
log_step "Node 6: Poem image — FLUX.1-schnell"
stop_and_reset "flux"
start_server "flux" "http://localhost:8000/tt-liveness" 30
_run_node "node6(flux-image)" node_text_to_image "6" \
    "FLUX.1-schnell" \
    "$POEM" \
    "1024" "1024" "4" "$(( SEED + 1 ))" "http://localhost:8000"
set_node_label "6" "poem image"
IMAGE2_PATH=$(get_result '["6", "image_path"]')

# ── Node 7: Add to playlist ───────────────────────────────────────────────────
log_step "Node 7: Save to playlist"
stop_and_reset ""

log "  Adding artifacts to playlist: $PLAYLIST_NAME"

DEPTH_PATH=$(get_result '["2", "depth_path"]')
VIDEO_PATH=$(get_result '["4", "video_path"]')

python3 - "$PLAYLIST_NAME" "$IMAGE_PATH" "$IMAGE2_PATH" \
    "$VIDEO_PATH" "$DEPTH_PATH" "$POEM" "$RESULTS_JSON" "$FAIR_KEY" << 'PY'
import sys, json, shutil, uuid
from pathlib import Path
from datetime import datetime, timezone

playlist_name, img1, img2, video, depth, poem, results_json, fair_key = sys.argv[1:]

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

    def _import(src, media_type, prompt_text):
        src = Path(src)
        if not src or not src.exists(): return None
        ext = src.suffix
        ts = datetime.now(timezone.utc)
        rid = str(uuid.uuid4())
        ts_str = ts.strftime("%Y%m%d_%H%M%S")
        dest_dir = VIDEOS_DIR if media_type == "video" else IMAGES_DIR
        dest = dest_dir / f"{ts_str}_{rid[:8]}{ext}"
        shutil.copy2(src, dest)
        thumb = THUMBS_DIR / f"{ts_str}_{rid[:8]}.jpg"
        try:
            import subprocess
            subprocess.run(["ffmpeg", "-y", "-i", str(dest),
                "-vf", "scale=200:112:force_original_aspect_ratio=decrease,pad=200:112:(ow-iw)/2:(oh-ih)/2",
                "-frames:v", "1", "-update", "1", "-q:v", "3", str(thumb)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        except Exception:
            try: shutil.copy2(dest, thumb)
            except Exception: pass
        params_dict = {"workflow": f"worlds-fair-{fair_key}"}
        if media_type == "video":
            params_dict["video_path"] = str(dest)
        else:
            params_dict["image_path"] = str(dest)
        rec = MediaRecord(
            id=rid, file_path=str(dest), thumbnail_path=str(thumb),
            prompt=prompt_text, media_type=media_type,
            created_at=ts.isoformat(), model_id="workflow", generator_type=None, starred=0,
            params=json.dumps(params_dict),
        )
        _ms.add(rec)
        return rid

    artifact_count = 0
    for path, mtype, lbl in [
        (img1,  "image", f"{playlist_name}: seed image"),
        (depth, "image", f"{playlist_name}: depth map"),
        (video, "video", f"{playlist_name}: SkyReels I2V"),
        (img2,  "image", f"{playlist_name}: poem image"),
    ]:
        rid = _import(path, mtype, lbl)
        if rid:
            record_ids.append(rid)
            artifact_count += 1

    if record_ids:
        _ps.add_records(pl.id, record_ids)

    print(f"\n✅ Playlist '{playlist_name}' ready ({artifact_count} artifacts)")
    print(f"PLAYLIST:{artifact_count}:{playlist_name}")
    print(f"\nPoem:\n{poem}")
except Exception as e:
    import traceback
    print(f"\n⚠️  Could not write to app store: {e}")
    traceback.print_exc()
    print(f"   Artifacts in: {results_json}")
PY

log ""
if [[ -n "$_failed_nodes" ]]; then
    log "⚠️  Pipeline finished with partial failures:$_failed_nodes"
    log "   Results: $RESULTS_JSON"
else
    log "✅ $FAIR_LABEL pipeline complete!"
    log "   Results: $RESULTS_JSON"
fi
