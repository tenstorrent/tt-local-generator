#!/usr/bin/env bash
# gen-z-image-turbo.sh — One-shot Z-Image-Turbo generation run.
#
# Lifecycle: reset hardware → start z-image-turbo → wait for liveness 200 →
# generate → stop → add record to "New Model Support" playlist → log timing.
#
# Optionally waits for a PID to exit first (e.g. overnight-gen.sh).
#
# Usage:
#   nohup ./bin/gen-z-image-turbo.sh > /tmp/gen-zit.log 2>&1 &
#   ./bin/gen-z-image-turbo.sh --wait-pid 225006   # wait for overnight script first
#   tail -f /tmp/gen-zit.log

REPO="/home/ttuser/code/tt-local-generator"
LOGFILE="/tmp/gen-zit.log"
NEW_MODEL_SUPPORT_PLAYLIST="e46b4782-b991-48bb-b5dd-ada1b0da1b2b"
NEW_MODEL_SUPPORT_LOG="$REPO/docs/new-model-support-log.md"

cd "$REPO"

# ── Parse flags ───────────────────────────────────────────────────────────────

WAIT_PID=""
for arg in "$@"; do
    case "$arg" in
        --wait-pid) shift; WAIT_PID="$1" ;;
        --wait-pid=*) WAIT_PID="${arg#--wait-pid=}" ;;
    esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOGFILE"; }

get_newest_id() {
    /usr/bin/python3 - <<'PYEOF' 2>/dev/null || true
import sys
sys.path.insert(0, '/home/ttuser/code/tt-local-generator/app')
from history_store import HistoryStore
hs = HistoryStore()
recs = hs.all_records()
print(recs[0].id if recs else '')
PYEOF
}

stop_all_and_reset() {
    local running
    running=$(docker ps -q 2>/dev/null)
    if [[ -n "$running" ]]; then
        log "Stopping all running containers before hardware reset…"
        echo "$running" | xargs docker stop 2>&1 | tee -a "$LOGFILE" || true
        sleep 15
    fi
    log "Running tt-smi -r (hardware reset)…"
    tt-smi -r 2>&1 | tee -a "$LOGFILE" || true
    sleep 10
}

wait_liveness_200() {
    local name="$1" max="${2:-7200}"
    log "Waiting for $name /tt-liveness 200 (up to ${max}s, 30s poll)…"
    local elapsed=0
    while [[ $elapsed -lt $max ]]; do
        local code
        code=$(curl -s -o /dev/null -w "%{http_code}" \
               http://localhost:8000/tt-liveness 2>/dev/null || echo "000")
        if [[ "$code" == "200" ]]; then
            log "$name ready ✓ (liveness 200)"
            return 0
        fi
        sleep 30
        elapsed=$((elapsed + 30))
        log "  …${elapsed}s  (liveness: $code)"
    done
    log "ERROR: $name not ready after ${max}s"
    return 1
}

# ── Wait for blocking PID (e.g. overnight-gen.sh) ────────────────────────────

if [[ -n "$WAIT_PID" ]]; then
    log "Waiting for PID $WAIT_PID to finish before starting Z-Image-Turbo…"
    while kill -0 "$WAIT_PID" 2>/dev/null; do
        sleep 30
        log "  …PID $WAIT_PID still running"
    done
    log "PID $WAIT_PID has exited — proceeding"
    # Give the machine a moment to clean up containers/hardware from overnight run
    sleep 30
fi

# ── Main ──────────────────────────────────────────────────────────────────────

log "======================================================"
log "Z-Image-Turbo generation run"
log "======================================================"

GEN_START_EPOCH=$(date +%s)

stop_all_and_reset

BEFORE=$(get_newest_id)

log "Starting z-image-turbo…"
./tt-ctl start z-image-turbo 2>&1 | tee -a "$LOGFILE" || true

WARMUP_START=$(date +%s)

if wait_liveness_200 "z-image-turbo" 7200; then
    WARMUP_END=$(date +%s)
    WARMUP_S=$((WARMUP_END - WARMUP_START))
    log "Warmup complete in ${WARMUP_S}s"

    log "Generating image (type: image)…"
    GEN_T0=$(date +%s)
    ./tt-ctl generate --type image 2>&1 | tee -a "$LOGFILE" || true
    GEN_T1=$(date +%s)
    GEN_S=$((GEN_T1 - GEN_T0))

    AFTER=$(get_newest_id)
    if [[ -n "$AFTER" && "$AFTER" != "$BEFORE" ]]; then
        log "Record captured: $AFTER (generation: ${GEN_S}s, warmup: ${WARMUP_S}s)"

        # Add to "New Model Support" playlist
        /usr/bin/python3 - "$AFTER" "$NEW_MODEL_SUPPORT_PLAYLIST" <<'PYEOF' 2>&1 | tee -a "$LOGFILE"
import sys
sys.path.insert(0, '/home/ttuser/code/tt-local-generator/app')
from playlist_store import PlaylistStore
record_id, playlist_id = sys.argv[1], sys.argv[2]
ps = PlaylistStore()
pl = ps.get(playlist_id)
if pl:
    n = ps.add_records(playlist_id, [record_id])
    print(f"Added {record_id} to playlist '{pl.name}' ({playlist_id}): {n} record(s) added")
else:
    print(f"WARNING: playlist {playlist_id} not found")
PYEOF

        # Append timing to the new-model-support log
        DOCKER_IMAGE="ghcr.io/tenstorrent/tt-media-inference-server:0.18.0-c49bb76"
        TIMESTAMP=$(date '+%Y-%m-%d')
        VIDEO_PATH=$(
            /usr/bin/python3 - "$AFTER" <<'PYEOF' 2>/dev/null || true
import sys
sys.path.insert(0, '/home/ttuser/code/tt-local-generator/app')
from history_store import HistoryStore
hs = HistoryStore()
for r in hs.all_records():
    if r.id == sys.argv[1]:
        print(getattr(r, 'file_path', getattr(r, 'video_path', '')))
        break
PYEOF
        )
        PROMPT_STR=$(
            /usr/bin/python3 - "$AFTER" <<'PYEOF' 2>/dev/null || true
import sys
sys.path.insert(0, '/home/ttuser/code/tt-local-generator/app')
from history_store import HistoryStore
hs = HistoryStore()
for r in hs.all_records():
    if r.id == sys.argv[1]:
        print(r.prompt or '(no prompt)')
        break
PYEOF
        )

        cat >> "$NEW_MODEL_SUPPORT_LOG" <<MDEOF

---

## Z-Image-Turbo (Tongyi-MAI)

| Field | Value |
|---|---|
| Record ID | \`$AFTER\` |
| Date | $TIMESTAMP |
| Hardware | QB2 — P150X4 (4 Blackhole p150 chips, mesh (1,4)) |
| Docker image | \`$DOCKER_IMAGE\` |
| Warmup time | **${WARMUP_S}s** (~$(( WARMUP_S / 60 )) min; first-run TTNN compile ~60–90 min) |
| Generation time | **${GEN_S}s** |
| Inference steps | 9 (hardcoded in runner) |
| Output | JPEG image, 1024×1024 |
| Prompt | *$PROMPT_STR* |
| File path | \`$VIDEO_PATH\` |

**Notes:** First successful generation on v0.17.0 image. Z_IMAGE_TURBO_MODEL_DIR set via
model_spec.py DeviceModelSpec env_vars to \`/home/container_app_user/tt-metal/models/demos/z_image_turbo/tt\`.
MDEOF
        log "Appended timing to $NEW_MODEL_SUPPORT_LOG"
    else
        log "WARNING: no new record after generation"
    fi
else
    log "ERROR: z-image-turbo did not become ready — skipping generation"
fi

log "Stopping z-image-turbo…"
./tt-ctl stop z-image-turbo 2>&1 | tee -a "$LOGFILE" || true

log "======================================================"
log "Done. Full log: $LOGFILE"
log "======================================================"
