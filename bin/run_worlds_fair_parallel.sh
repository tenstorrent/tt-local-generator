#!/usr/bin/env bash
# run_worlds_fair_parallel.sh — Run all 5 World's Fair pipelines in parallel,
# batching requests to each model so every chip-load is amortized across all 5 fairs.
#
# Execution order (each model loaded exactly once):
#
#   Phase 1 — FLUX.1-schnell: 5 seed images in parallel
#   Phase 2 — CPU depth maps: 4 depth maps in parallel (1970-osaka skipped)
#   Phase 3 — SkyReels I2V: 5 videos in parallel (one at a time on single GPU cluster,
#              but submitted together so they queue and run back-to-back without reload)
#   Phase 4 — Llama-3.3-70B: 5 poems in parallel
#   Phase 5 — FLUX.1-schnell: 5 poem images in parallel
#   Phase 6 — Import all artifacts to 5 playlists
#
# Total time: ~1.5–2 hrs vs ~4–5 hrs sequential
#
# Usage:
#   ./bin/run_worlds_fair_parallel.sh
#   ./bin/run_worlds_fair_parallel.sh --dry-run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

PYTHON3="${HOME}/.tenstorrent-venv/bin/python3"
[[ ! -f "$PYTHON3" ]] && PYTHON3=/usr/bin/python3

RUN_ROOT="${HOME}/.local/share/tt-local-generator/workflow-runs/$(date +%Y%m%d_%H%M%S)_5fairs"
mkdir -p "$RUN_ROOT"

LOG_FILE="${HOME}/.local/share/tt-local-generator/logs/workflow/$(date +%Y%m%d_%H%M%S)_5fairs.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "LOG:$LOG_FILE"

log()      { echo "[$(date '+%H:%M:%S')] $*"; }
log_step() { echo ""; echo "══ $* ══"; }

# ── Fair definitions ──────────────────────────────────────────────────────────

declare -A SEED_PROMPT ERA_CONTEXT POEM_CONTEXT PLAYLIST_NAME SEED USE_DEPTH

SEED_PROMPT[1964-ny]="Kodachrome slide film photograph, 1964. A grandstand of 500 fairgoers in summer clothes — children gripping armrests, women clutching handbags — rises hydraulically on the IBM People Wall into the interior of a vast white egg-shaped auditorium. Curved white walls are studded with repeating IBM logotypes. Overhead, a Charles Eames film projection throws blue-gray light. The motion is almost imperceptible but unmistakable: the floor is moving upward, and half the crowd is grinning."
ERA_CONTEXT[1964-ny]="IBM Pavilion, People Wall hydraulic theater, 1964 World's Fair, Kodachrome, cinematic upward motion"
POEM_CONTEXT[1964-ny]="Inside the IBM Pavilion at the 1964 World's Fair, as a bleacher of visitors rises hydraulically into a vast egg-shaped auditorium. Space Age optimism, human wonder at technology, the floor itself becoming the future."
PLAYLIST_NAME[1964-ny]="World's Fair 1964 NY — IBM People Wall"
SEED[1964-ny]=19640422
USE_DEPTH[1964-ny]=1

SEED_PROMPT[1939-ny]="Kodachrome slide film photograph, 1939. A seven-foot golden aluminum humanoid robot stands on a low stage inside a glass-walled pavilion, aluminum lips parted mid-sentence, one hand raised with fingers spread for counting. A Westinghouse technician in a white lab coat holds a lit cigarette near the robot's upper lip. Hundreds of spectators press six-deep against the glass exterior, hats craning, a child lifted on a father's shoulders to see over the crowd."
ERA_CONTEXT[1939-ny]="Westinghouse Elektro robot, Hall of Electrical Living, 1939 World's Fair, Kodachrome, crowd pressing glass"
POEM_CONTEXT[1939-ny]="At the Westinghouse Hall of Electrical Living, 1939 World's Fair — Elektro the Moto-Man, a seven-foot aluminum robot, speaks and counts on mechanical fingers while Depression-era Americans press against the glass to see the future."
PLAYLIST_NAME[1939-ny]="World's Fair 1939 NY — Elektro the Robot"
SEED[1939-ny]=19390430
USE_DEPTH[1939-ny]=1

SEED_PROMPT[1893-chicago]="Sepia daguerreotype aesthetic, 1893 Chicago Electricity Building. A darkened demonstration room, a crowd pressed three-deep behind a rope barrier. At center: Nikola Tesla in a dark frock coat holds a glass vacuum tube aloft — it blazes with cold blue-white phosphorescent light, lit by no wire, fed by invisible wireless current. Men's faces glow astonished. Women grip their companions' arms. On the bench behind him: the copper Egg of Columbus spinning silently upright in its brass induction ring."
ERA_CONTEXT[1893-chicago]="Nikola Tesla wireless demonstration, Electricity Building, 1893 World's Fair, daguerreotype sepia, cold luminous tube in darkness"
POEM_CONTEXT[1893-chicago]="In the Electricity Building at the 1893 Chicago World's Fair, Nikola Tesla demonstrates wireless power — a glass vacuum tube blazing with cold blue light held in his bare hand, no wires, no Edison. The crowd's faces are lit from below by something no one has seen before."
PLAYLIST_NAME[1893-chicago]="World's Fair 1893 Chicago — Tesla's Light"
SEED[1893-chicago]=18930501
USE_DEPTH[1893-chicago]=1

SEED_PROMPT[1970-osaka]="Fujiko Nakaya's artificial fog sculpture engulfs the Pepsi geodesic sphere at twilight, Expo 70 Osaka, March 1970. Visitors in mod coats and platform shoes dissolve waist-deep into luminous white vapor, lit from below by xenon arc lamps. The dome's ridged surface barely visible through the mist. Fujifilm Velvia grain, saturated cyan-white haze, deep indigo sky."
ERA_CONTEXT[1970-osaka]="Pepsi Pavilion fog sculpture, E.A.T. Fujiko Nakaya, Expo 70 Osaka, Fujifilm grain, xenon-lit vapor at twilight"
POEM_CONTEXT[1970-osaka]="At the Pepsi Pavilion of Expo 70, Osaka — Fujiko Nakaya's fog sculpture, the world's first, dissolves hundreds of visitors into luminous vapor under xenon arc lamps. Technology made atmospheric, bodies made uncertain, the future made of clouds."
PLAYLIST_NAME[1970-osaka]="World's Fair 1970 Osaka — Fog Pavilion"
SEED[1970-osaka]=19700315
USE_DEPTH[1970-osaka]=0

SEED_PROMPT[1967-montreal]="Inside a cylindrical theatre at the 1967 Montreal World's Fair, hundreds of visitors stand gripping steel guide rails as nine giant curved screens surrounding them fill with aerial footage of Niagara Falls rushing directly overhead. Women in mod wool coats and men in narrow-lapel suits lean into the rails, faces upturned in genuine vertigo. Bold Kodachrome saturation, 1967 Canadian color magazine photography."
ERA_CONTEXT[1967-montreal]="Disney Circle-Vision 360° theatre, Bell Telephone Pavilion, Expo 67 Montreal, Kodachrome, bodies leaning into guide rails"
POEM_CONTEXT[1967-montreal]="In the Bell Telephone Pavilion at Expo 67, Montreal — Walt Disney's Circle-Vision 360° film puts Niagara Falls directly overhead while visitors grip steel rails, their faces tilted up in vertigo. The last thing Disney made before he died. A Canadian documentary as immersive as any ride."
PLAYLIST_NAME[1967-montreal]="World's Fair 1967 Montreal — Circle-Vision"
SEED[1967-montreal]=19670427
USE_DEPTH[1967-montreal]=1

FAIRS=(1964-ny 1939-ny 1893-chicago 1970-osaka 1967-montreal)

# ── Per-fair output dirs + results ───────────────────────────────────────────

for FAIR in "${FAIRS[@]}"; do
    mkdir -p "$RUN_ROOT/$FAIR"
    echo "{}" > "$RUN_ROOT/$FAIR/results.json"
done

_RW="$REPO_ROOT/bin/_results_rw.py"

set_result()    { python3 "$_RW" set_result  "$RUN_ROOT/$1/results.json" "$2" "$3" "$4"; }
set_node_label(){ python3 "$_RW" set_label   "$RUN_ROOT/$1/results.json" "$2" "$3"; }
get_result()    { python3 "$_RW" get_result  "$RUN_ROOT/$1/results.json" "$2" "$3"; }

# ── Hardware management ───────────────────────────────────────────────────────

_current_server=""

stop_and_reset() {
    local next_server="${1:-}"
    if [[ "$_current_server" == "$next_server" && -n "$next_server" ]]; then
        return 0
    fi
    if docker ps -q 2>/dev/null | grep -q .; then
        log "  Stopping containers..."
        docker ps -q | xargs docker stop --timeout 8 2>/dev/null || \
            docker ps -q | xargs docker kill 2>/dev/null || true
        sleep 2
    fi
    if [[ -n "$_current_server" ]]; then
        log "  Resetting boards ($_current_server → ${next_server:-none})..."
        [[ $DRY_RUN -eq 0 ]] && tt-smi -r 2>/dev/null | head -1 || echo "  [dry-run] tt-smi -r"
        sleep 8
    fi
    _current_server="$next_server"
}

start_server() {
    local server_key="$1" health_url="$2" max_wait_min="${3:-60}"
    log "  Starting $server_key..."
    [[ $DRY_RUN -eq 1 ]] && { echo "  [dry-run] start $server_key"; return 0; }
    cd "$REPO_ROOT" && ./tt-ctl start "$server_key" 2>&1 | tail -1
    log "  Waiting for $server_key (up to ${max_wait_min} min)..."
    for i in $(seq 1 $((max_wait_min * 2))); do
        sleep 30
        if curl -sf "$health_url" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('model_ready') or d.get('data') else 1)" 2>/dev/null; then
            log "  ✅ $server_key ready at $((i*30/60))min $((i*30%60))s"
            docker exec "$(docker ps -q | head -1)" chmod 777 /tmp/prometheus_multiproc 2>/dev/null || true
            return 0
        fi
        if [[ $((i % 4)) -eq 0 ]]; then
            log "  [${server_key} warmup] $((i*30/60))min elapsed…"
            local skyreels_log; skyreels_log=$(ls -t "${HOME}/code/tt-local-generator"/media_*SkyReels*.log 2>/dev/null | head -1)
            if [[ -n "$skyreels_log" ]]; then
                local last; last=$(grep -v '^\s*$' "$skyreels_log" 2>/dev/null | grep -v '\[=' | tail -1 || true)
                [[ -n "$last" ]] && log "⏳ SkyReels: ${last:0:120}"
            fi
        fi
    done
    log "  ❌ $server_key timed out"; return 1
}

# ── Image generation (submit + poll, file-based job ID handoff) ───────────────
# Job IDs are written to $RUN_ROOT/$fair/job_nodeN.txt so we never fight the
# tee stdout redirect when trying to capture output via $(...).

# FLUX is synchronous: _submit_image.py saves the image and prints "DONE".
# We use a status file ($job_file) so "poll_image" just checks if the file was written.
submit_image() {
    local fair="$1" node_id="$2" prompt="$3" seed="$4"
    local out="$RUN_ROOT/$fair/node${node_id}_image.png"
    local job_file="$RUN_ROOT/$fair/job_node${node_id}.txt"
    log "  [$fair] Generating image node $node_id..."
    [[ $DRY_RUN -eq 1 ]] && { touch "$out"; echo "DONE" > "$job_file"; return 0; }
    sleep 1
    local RESULT="" attempt
    for attempt in 1 2 3; do
        RESULT=$(python3 "$REPO_ROOT/bin/_submit_image.py" "$prompt" "$seed" "$out" 2>/dev/null) || RESULT=""
        [[ "$RESULT" == "DONE" ]] && break
        log "  [$fair] image attempt $attempt failed: $RESULT — retrying..."
        sleep 10
    done
    if [[ "$RESULT" != "DONE" ]]; then
        log "  [$fair] ❌ image node $node_id failed after 3 attempts"
        echo "FAILED" > "$job_file"; return 1
    fi
    log "  [$fair] ✅ image node $node_id done ($(du -sh "$out" | cut -f1))"
    echo "DONE" > "$job_file"
    set_result "$fair" "$node_id" "image_path" "$out"
    set_node_label "$fair" "$node_id" "seed image"
}

poll_image() {
    # For synchronous FLUX: image is already saved by submit_image.
    # This is a no-op barrier — just waits for the job_file to confirm completion.
    local fair="$1" node_id="$2" out="$3" label="$4"
    local job_file="$RUN_ROOT/$fair/job_node${node_id}.txt"
    local JOB; JOB=$(cat "$job_file" 2>/dev/null || echo "")
    if [[ "$JOB" == "DONE" && -f "$out" ]]; then
        set_result "$fair" "$node_id" "image_path" "$out"
        set_node_label "$fair" "$node_id" "$label"
        return 0
    fi
    [[ "$JOB" == "FAILED" || -z "$JOB" ]] && { log "  [$fair] ⚠️ image node $node_id not available"; return 1; }
    # Should not reach here with synchronous FLUX
    local STATUS=""
    for i in $(seq 1 40); do
        sleep 30
        STATUS=$(curl -s "http://localhost:8000/v1/images/generations/$JOB" 2>/dev/null | \
            python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || true)
        [[ "$STATUS" == "completed" ]] && break
        [[ "$STATUS" == "failed" ]] && { log "  [$fair] ❌ image node $node_id failed"; return 1; }
    done
    [[ "$STATUS" != "completed" ]] && { log "  [$fair] ⚠️ image node $node_id timed out"; return 1; }
    curl -sf "http://localhost:8000/v1/images/generations/$job/download" -o "$out"
    log "  [$fair] ✅ image node $node_id: $(du -sh "$out" | cut -f1)"
    set_result "$fair" "$node_id" "image_path" "$out"
    set_node_label "$fair" "$node_id" "$label"
}

# ── Video generation (submit + poll) ─────────────────────────────────────────

submit_video() {
    local fair="$1" node_id="$2" prompt="$3" image_path="$4" seed="$5"
    local job_file="$RUN_ROOT/$fair/job_node${node_id}.txt"
    log "  [$fair] Submitting video node $node_id..."
    [[ $DRY_RUN -eq 1 ]] && { echo "DRYRUN" > "$job_file"; return 0; }
    local JOB; JOB=$(python3 "$REPO_ROOT/bin/_submit_video.py" "$prompt" "$image_path" "$seed" 2>/dev/null) || true
    if [[ -z "$JOB" || "$JOB" == ERROR* ]]; then
        log "  [$fair] ❌ video submission failed: $JOB"
        echo "FAILED" > "$job_file"; return 1
    fi
    log "  [$fair] video node $node_id job: $JOB"
    echo "$JOB" > "$job_file"
}

poll_video() {
    local fair="$1" node_id="$2"
    local job_file="$RUN_ROOT/$fair/job_node${node_id}.txt"
    local JOB; JOB=$(cat "$job_file" 2>/dev/null || echo "")
    [[ "$JOB" == "DRYRUN" ]] && return 0
    [[ -z "$JOB" || "$JOB" == "FAILED" ]] && { log "  [$fair] ⚠️ video skipped (no job)"; return 1; }
    local out="$RUN_ROOT/$fair/node${node_id}_video.mp4"
    local STATUS=""
    for i in $(seq 1 240); do
        sleep 30
        STATUS=$(curl -s "http://localhost:8000/v1/video/generations/$job" 2>/dev/null | \
            python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || true)
        [[ "$STATUS" == "completed" ]] && break
        [[ "$STATUS" == "failed" ]] && { log "  [$fair] ❌ video failed"; return 1; }
        if [[ $((i % 4)) -eq 0 ]]; then
            log "  [$fair] video ${i}×30s, status=$STATUS"
            local skyreels_log; skyreels_log=$(ls -t "${HOME}/code/tt-local-generator"/media_*SkyReels*.log 2>/dev/null | head -1)
            [[ -n "$skyreels_log" ]] && {
                local last; last=$(grep -v '^\s*$' "$skyreels_log" 2>/dev/null | grep -v '\[=' | tail -1 || true)
                [[ -n "$last" ]] && log "⏳ SkyReels: ${last:0:120}"
            }
        fi
    done
    [[ "$STATUS" != "completed" ]] && { log "  [$fair] ⚠️ video timed out"; return 1; }
    curl -sf "http://localhost:8000/v1/video/generations/$job/download" -o "$out"
    log "  [$fair] ✅ video: $(du -sh "$out" | cut -f1)"
    set_result "$fair" "$node_id" "video_path" "$out"
    set_node_label "$fair" "$node_id" "video"
}

# ── Text generation ───────────────────────────────────────────────────────────

gen_poem() {
    local fair="$1" node_id="$2" poem_context="$3"
    log "  [$fair] Generating poem..."
    [[ $DRY_RUN -eq 1 ]] && { set_result "$fair" "$node_id" "poem" "The future blazes in a vacuum tube — dry run."; set_node_label "$fair" "$node_id" "poem"; return 0; }
    local TEXT; TEXT=$(python3 "$REPO_ROOT/bin/_gen_poem.py" "$poem_context" 2>/dev/null) || true
    if [[ -z "$TEXT" ]]; then
        log "  [$fair] ⚠️ poem empty"; return 1
    fi
    log "  [$fair] ✅ poem: ${TEXT:0:80}…"
    set_result "$fair" "$node_id" "poem" "$TEXT"
    set_node_label "$fair" "$node_id" "poem"
}

# ── Depth map (CPU, parallel-safe) ───────────────────────────────────────────

gen_depth() {
    local fair="$1" node_id="$2" src="$3"
    local dest="$RUN_ROOT/$fair/node${node_id}_depth.png"
    log "  [$fair] Estimating depth (CPU)..."
    [[ $DRY_RUN -eq 1 ]] && { set_result "$fair" "$node_id" "depth_path" "$dest"; set_node_label "$fair" "$node_id" "depth map"; touch "$dest"; return 0; }
    "$PYTHON3" -c "
import sys
sys.path.insert(0, '$REPO_ROOT/plugins/depth')
from plugin import estimate_depth
estimate_depth('$src', '$dest')
" 2>&1 | tail -2
    if [[ -f "$dest" ]]; then
        log "  [$fair] ✅ depth: $(du -sh "$dest" | cut -f1)"
        set_result "$fair" "$node_id" "depth_path" "$dest"
        set_node_label "$fair" "$node_id" "depth map"
    else
        log "  [$fair] ⚠️ depth produced no output"
    fi
}

# ── Import to playlist ────────────────────────────────────────────────────────

import_playlist() {
    local fair="$1"
    local pname="${PLAYLIST_NAME[$fair]}"
    local img1; img1=$(get_result "$fair" "1" "image_path")
    local depth; depth=$(get_result "$fair" "2" "depth_path")
    local video; video=$(get_result "$fair" "4" "video_path")
    local img2; img2=$(get_result "$fair" "6" "image_path")
    local poem; poem=$(get_result "$fair" "5" "poem")
    local rj="$RUN_ROOT/$fair/results.json"

    log "  [$fair] Importing to playlist: $pname"
    python3 "$REPO_ROOT/bin/_import_playlist.py" \
        "$pname" "$img1" "$img2" "$video" "$depth" "$poem" "$rj" "$fair"
}

# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

log_step "5 World's Fairs — Parallel Pipeline"
log "Run root: $RUN_ROOT"
[[ $DRY_RUN -eq 1 ]] && log "DRY RUN"

# ── Phase 1: FLUX — 5 seed images ────────────────────────────────────────────
log_step "Phase 1: FLUX.1-schnell — 5 seed images"
stop_and_reset "flux"
start_server "flux" "http://localhost:8000/tt-liveness" 30

for FAIR in "${FAIRS[@]}"; do
    submit_image "$FAIR" "1" "${SEED_PROMPT[$FAIR]}" "${SEED[$FAIR]}"
done
log "  Phase 1 complete."

# ── Phase 2: CPU depth maps (parallel, no GPU) ───────────────────────────────
log_step "Phase 2: GLPN depth maps (CPU, parallel)"
for FAIR in "${FAIRS[@]}"; do
    [[ "${USE_DEPTH[$FAIR]}" == "0" ]] && { log "  [$FAIR] depth skipped (fog/exterior)"; continue; }
    img=$(get_result "$FAIR" "1" "image_path")
    [[ -z "$img" || ! -f "$img" ]] && { log "  [$FAIR] depth skipped (no seed image)"; continue; }
    gen_depth "$FAIR" "2" "$img" &
done
wait
log "  Phase 2 complete."

# ── Phase 3: SkyReels I2V — 5 videos (submit all, poll all) ──────────────────
log_step "Phase 3: SkyReels V2 I2V — 5 videos (97 frames each)"
stop_and_reset "skyreels"
start_server "skyreels" "http://localhost:8000/tt-liveness" 90

for FAIR in "${FAIRS[@]}"; do
    img=$(get_result "$FAIR" "1" "image_path")
    [[ -z "$img" || ! -f "$img" ]] && { log "  [$FAIR] video skipped (no seed image)"; continue; }
    VIDEO_PROMPT="${ERA_CONTEXT[$FAIR]}, cinematic slow push-in, photorealistic"
    set_result "$FAIR" "3" "video_prompt" "$VIDEO_PROMPT"
    set_node_label "$FAIR" "3" "video prompt"
    submit_video "$FAIR" "4" "$VIDEO_PROMPT" "$img" "${SEED[$FAIR]}"
    sleep 3
done
log "  All video jobs submitted. Polling..."
for FAIR in "${FAIRS[@]}"; do
    poll_video "$FAIR" "4" &
done
wait
log "  Phase 3 complete."

# ── Phase 4: Llama-3.3-70B — 5 poems ─────────────────────────────────────────
log_step "Phase 4: Llama-3.3-70B — 5 poems"
stop_and_reset "artgen-llama-3.3-70b"
start_server "artgen-llama-3.3-70b" "http://localhost:8002/v1/models" 30
for FAIR in "${FAIRS[@]}"; do
    gen_poem "$FAIR" "5" "${POEM_CONTEXT[$FAIR]}" &
done
wait
log "  Phase 4 complete."

# ── Phase 5: FLUX — 5 poem images ────────────────────────────────────────────
log_step "Phase 5: FLUX.1-schnell — 5 poem images"
stop_and_reset "flux"
start_server "flux" "http://localhost:8000/tt-liveness" 30
for FAIR in "${FAIRS[@]}"; do
    POEM=$(get_result "$FAIR" "5" "poem")
    [[ -z "$POEM" ]] && { log "  [$FAIR] poem image skipped (no poem)"; continue; }
    submit_image "$FAIR" "6" "$POEM" "$(( ${SEED[$FAIR]} + 1 ))"
done
log "  Phase 5 complete."

# ── Phase 6: Import all playlists ────────────────────────────────────────────
log_step "Phase 6: Import artifacts to 5 playlists"
stop_and_reset ""

for FAIR in "${FAIRS[@]}"; do
    import_playlist "$FAIR"
done

log ""
log "✅ All 5 World's Fair pipelines complete!"
log "   Results in: $RUN_ROOT"
for FAIR in "${FAIRS[@]}"; do
    log "   $FAIR → ${PLAYLIST_NAME[$FAIR]}"
done
