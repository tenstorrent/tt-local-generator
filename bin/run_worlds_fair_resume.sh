#!/usr/bin/env bash
# run_worlds_fair_resume.sh — Resume phases 3-6 using existing seed images.
# SkyReels must already be running (model_ready: true on port 8000).
#
# Usage: ./bin/run_worlds_fair_resume.sh <run_root_dir>
#   e.g. ./bin/run_worlds_fair_resume.sh ~/.local/share/tt-local-generator/workflow-runs/20260602_092516_5fairs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_ROOT="${1:-}"

if [[ -z "$RUN_ROOT" || ! -d "$RUN_ROOT" ]]; then
    echo "Usage: $0 <run_root_dir>"
    echo "Example: $0 ~/.local/share/tt-local-generator/workflow-runs/20260602_092516_5fairs"
    exit 1
fi

PYTHON3="${HOME}/.tenstorrent-venv/bin/python3"
[[ ! -f "$PYTHON3" ]] && PYTHON3=/usr/bin/python3

LOG_FILE="${HOME}/.local/share/tt-local-generator/logs/workflow/$(date +%Y%m%d_%H%M%S)_5fairs_resume.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "LOG:$LOG_FILE"

log()      { echo "[$(date '+%H:%M:%S')] $*"; }
log_step() { echo ""; echo "══ $* ══"; }

_RW="$REPO_ROOT/bin/_results_rw.py"
set_result()    { python3 "$_RW" set_result  "$RUN_ROOT/$1/results.json" "$2" "$3" "$4"; }
set_node_label(){ python3 "$_RW" set_label   "$RUN_ROOT/$1/results.json" "$2" "$3"; }
get_result()    { python3 "$_RW" get_result  "$RUN_ROOT/$1/results.json" "$2" "$3"; }

declare -A ERA_CONTEXT POEM_CONTEXT PLAYLIST_NAME SEED
ERA_CONTEXT[1964-ny]="IBM Pavilion, People Wall hydraulic theater, 1964 World's Fair, Kodachrome, cinematic upward motion"
ERA_CONTEXT[1939-ny]="Westinghouse Elektro robot, Hall of Electrical Living, 1939 World's Fair, Kodachrome, crowd pressing glass"
ERA_CONTEXT[1893-chicago]="Nikola Tesla wireless demonstration, Electricity Building, 1893 World's Fair, daguerreotype sepia, cold luminous tube in darkness"
ERA_CONTEXT[1970-osaka]="Pepsi Pavilion fog sculpture, E.A.T. Fujiko Nakaya, Expo 70 Osaka, Fujifilm grain, xenon-lit vapor at twilight"
ERA_CONTEXT[1967-montreal]="Disney Circle-Vision 360° theatre, Bell Telephone Pavilion, Expo 67 Montreal, Kodachrome, bodies leaning into guide rails"

POEM_CONTEXT[1964-ny]="Inside the IBM Pavilion at the 1964 World's Fair, as a bleacher of visitors rises hydraulically into a vast egg-shaped auditorium. Space Age optimism, human wonder at technology, the floor itself becoming the future."
POEM_CONTEXT[1939-ny]="At the Westinghouse Hall of Electrical Living, 1939 World's Fair — Elektro the Moto-Man, a seven-foot aluminum robot, speaks and counts on mechanical fingers while Depression-era Americans press against the glass to see the future."
POEM_CONTEXT[1893-chicago]="In the Electricity Building at the 1893 Chicago World's Fair, Nikola Tesla demonstrates wireless power — a glass vacuum tube blazing with cold blue light held in his bare hand, no wires, no Edison. The crowd's faces are lit from below by something no one has seen before."
POEM_CONTEXT[1970-osaka]="At the Pepsi Pavilion of Expo 70, Osaka — Fujiko Nakaya's fog sculpture, the world's first, dissolves hundreds of visitors into luminous vapor under xenon arc lamps. Technology made atmospheric, bodies made uncertain, the future made of clouds."
POEM_CONTEXT[1967-montreal]="In the Bell Telephone Pavilion at Expo 67, Montreal — Walt Disney's Circle-Vision 360° film puts Niagara Falls directly overhead while visitors grip steel rails, their faces tilted up in vertigo. The last thing Disney made before he died. A Canadian documentary as immersive as any ride."

PLAYLIST_NAME[1964-ny]="World's Fair 1964 NY — IBM People Wall"
PLAYLIST_NAME[1939-ny]="World's Fair 1939 NY — Elektro the Robot"
PLAYLIST_NAME[1893-chicago]="World's Fair 1893 Chicago — Tesla's Light"
PLAYLIST_NAME[1970-osaka]="World's Fair 1970 Osaka — Fog Pavilion"
PLAYLIST_NAME[1967-montreal]="World's Fair 1967 Montreal — Circle-Vision"

SEED[1964-ny]=19640422; SEED[1939-ny]=19390430; SEED[1893-chicago]=18930501
SEED[1970-osaka]=19700315; SEED[1967-montreal]=19670427

FAIRS=(1964-ny 1939-ny 1893-chicago 1970-osaka 1967-montreal)

log_step "Resuming 5-Fair Pipeline — Phases 3-6"
log "Run root: $RUN_ROOT"

# ── Phase 3: Submit all 5 videos ─────────────────────────────────────────────
log_step "Phase 3: SkyReels I2V — submit 5 videos"
for FAIR in "${FAIRS[@]}"; do
    img="$RUN_ROOT/$FAIR/node1_image.png"
    [[ ! -f "$img" ]] && { log "  [$FAIR] ⚠️ no seed image, skipping"; continue; }
    VIDEO_PROMPT="${ERA_CONTEXT[$FAIR]}, cinematic slow push-in, photorealistic"
    set_result "$FAIR" "3" "video_prompt" "$VIDEO_PROMPT"
    set_node_label "$FAIR" "3" "video prompt"
    log "  [$FAIR] Submitting video..."
    JOB=$(python3 "$REPO_ROOT/bin/_submit_video.py" "$VIDEO_PROMPT" "$img" "${SEED[$FAIR]}" 2>/dev/null) || JOB=""
    if [[ -z "$JOB" || "$JOB" == ERROR* ]]; then
        log "  [$FAIR] ❌ submission failed: $JOB"
        echo "FAILED" > "$RUN_ROOT/$FAIR/job_node4.txt"
    else
        log "  [$FAIR] job: $JOB"
        echo "$JOB" > "$RUN_ROOT/$FAIR/job_node4.txt"
    fi
    sleep 3
done

log "  All jobs submitted. Polling sequentially..."
for FAIR in "${FAIRS[@]}"; do
    JOB=$(cat "$RUN_ROOT/$FAIR/job_node4.txt" 2>/dev/null || echo "")
    [[ -z "$JOB" || "$JOB" == "FAILED" ]] && { log "  [$FAIR] ⚠️ video skipped"; continue; }
    out="$RUN_ROOT/$FAIR/node4_video.mp4"
    log "  [$FAIR] Polling job $JOB..."
    STATUS=""
    for i in $(seq 1 240); do
        sleep 30
        STATUS=$(curl -s "http://localhost:8000/v1/videos/generations/$JOB" 2>/dev/null | \
            python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || true)
        [[ "$STATUS" == "completed" ]] && break
        [[ "$STATUS" == "failed" ]] && { log "  [$FAIR] ❌ video generation failed"; break; }
        [[ $((i % 4)) -eq 0 ]] && log "  [$FAIR] ${i}×30s elapsed, status=$STATUS"
    done
    if [[ "$STATUS" == "completed" ]]; then
        curl -sf "http://localhost:8000/v1/videos/generations/$JOB/download" -o "$out"
        log "  [$FAIR] ✅ video: $(du -sh "$out" | cut -f1)"
        set_result "$FAIR" "4" "video_path" "$out"
        set_node_label "$FAIR" "4" "video"
    else
        log "  [$FAIR] ⚠️ video timed out or failed (status: $STATUS)"
    fi
done
log "  Phase 3 complete."

# ── Phase 4: Poems via Llama ─────────────────────────────────────────────────
log_step "Phase 4: Llama-3.3-70B — 5 poems"
log "  Stopping SkyReels, resetting boards, starting artgen..."
docker ps -q | xargs docker kill 2>/dev/null &
disown $! 2>/dev/null || true
sleep 2
tt-smi -r 2>/dev/null | head -1
sleep 10
docker ps -aq | xargs docker rm -f 2>/dev/null || true

cd "$REPO_ROOT" && ./tt-ctl start artgen-llama-3.3-70b 2>&1 | tail -1
log "  Waiting for Llama (up to 30 min)..."
for i in $(seq 1 60); do
    sleep 30
    if curl -sf "http://localhost:8002/v1/models" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('data') else 1)" 2>/dev/null; then
        log "  ✅ Llama ready at $((i*30/60))min $((i*30%60))s"; break
    fi
    [[ $i -eq 60 ]] && { log "  ❌ Llama timed out"; exit 1; }
done

for FAIR in "${FAIRS[@]}"; do
    log "  [$FAIR] Generating poem..."
    TEXT=$(python3 "$REPO_ROOT/bin/_gen_poem.py" "${POEM_CONTEXT[$FAIR]}" 2>/dev/null) || TEXT=""
    if [[ -n "$TEXT" ]]; then
        log "  [$FAIR] ✅ poem: ${TEXT:0:80}..."
        set_result "$FAIR" "5" "poem" "$TEXT"
        set_node_label "$FAIR" "5" "poem"
    else
        log "  [$FAIR] ⚠️ poem empty"
    fi
done
log "  Phase 4 complete."

# ── Phase 5: Poem images via FLUX ────────────────────────────────────────────
log_step "Phase 5: FLUX.1-schnell — 5 poem images"
docker ps -q | xargs docker kill 2>/dev/null &
disown $! 2>/dev/null || true
sleep 2
tt-smi -r 2>/dev/null | head -1
sleep 10
docker ps -aq | xargs docker rm -f 2>/dev/null || true

cd "$REPO_ROOT" && ./tt-ctl start flux 2>&1 | tail -1
log "  Waiting for FLUX (up to 30 min)..."
for i in $(seq 1 60); do
    sleep 30
    if curl -sf "http://localhost:8000/tt-liveness" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('model_ready') else 1)" 2>/dev/null; then
        log "  ✅ FLUX ready at $((i*30/60))min $((i*30%60))s"; break
    fi
    [[ $i -eq 60 ]] && { log "  ❌ FLUX timed out"; exit 1; }
done

for FAIR in "${FAIRS[@]}"; do
    POEM=$(get_result "$FAIR" "5" "poem")
    [[ -z "$POEM" ]] && { log "  [$FAIR] poem image skipped (no poem)"; continue; }
    out="$RUN_ROOT/$FAIR/node6_image.png"
    log "  [$FAIR] Generating poem image..."
    RESULT=$(python3 "$REPO_ROOT/bin/_submit_image.py" "$POEM" "$(( ${SEED[$FAIR]} + 1 ))" "$out" 2>/dev/null) || RESULT=""
    if [[ "$RESULT" == "DONE" ]]; then
        log "  [$FAIR] ✅ poem image: $(du -sh "$out" | cut -f1)"
        set_result "$FAIR" "6" "image_path" "$out"
        set_node_label "$FAIR" "6" "poem image"
    else
        log "  [$FAIR] ⚠️ poem image failed: $RESULT"
    fi
done
log "  Phase 5 complete."

# ── Phase 6: Import playlists ─────────────────────────────────────────────────
log_step "Phase 6: Import artifacts to playlists"
docker ps -q | xargs docker kill 2>/dev/null &
disown $! 2>/dev/null || true
sleep 2
tt-smi -r 2>/dev/null | head -1 || true

for FAIR in "${FAIRS[@]}"; do
    pname="${PLAYLIST_NAME[$FAIR]}"
    img1=$(get_result "$FAIR" "1" "image_path")
    depth=$(get_result "$FAIR" "2" "depth_path")
    video=$(get_result "$FAIR" "4" "video_path")
    img2=$(get_result "$FAIR" "6" "image_path")
    poem=$(get_result "$FAIR" "5" "poem")
    rj="$RUN_ROOT/$FAIR/results.json"
    log "  [$FAIR] Importing: $pname"
    python3 "$REPO_ROOT/bin/_import_playlist.py" \
        "$pname" "$img1" "$img2" "$video" "$depth" "$poem" "$rj" "$FAIR"
done

log ""
log "✅ All 5 World's Fair resume complete!"
