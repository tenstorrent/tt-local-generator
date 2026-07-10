#!/usr/bin/env bash
# overnight-gen.sh — Unattended generation run + playlist + suspend
#
# For each model: stop existing containers → tt-smi -r → start fresh →
# wait for /tt-liveness 200 → generate → stop → next model.
#
# Usage:
#   nohup ./bin/overnight-gen.sh > /tmp/overnight.log 2>&1 &
#   tail -f /tmp/overnight.log
#
# Models (in order):
#   wan2.2  — Wan2.2-T2V-A14B video (T2V)
#   flux    — FLUX.1-schnell image
#   motif   — Motif-Image-6B-Preview image
#
# Skipped: z-image-turbo (not in vendor run.py valid choices yet)

REPO="/home/ttuser/code/tt-local-generator"
LOGFILE="/tmp/overnight-gen.log"
PLAYLIST_NAME="2026-07-01"

cd "$REPO"

# ── helpers ───────────────────────────────────────────────────────────────────

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

clear_queue() {
    /usr/bin/python3 - <<'PYEOF' 2>/dev/null || true
import sys
sys.path.insert(0, '/home/ttuser/code/tt-local-generator/app')
from history_store import HistoryStore
HistoryStore().save_queue([])
PYEOF
    log "Queue cleared"
}

# Stop ALL running Docker containers, then reset TT hardware.
# This order is critical: never reset hardware while a container owns the devices.
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

# Wait for /tt-liveness to return exactly 200 (model fully ready).
# In v0.17.0+, /tt-liveness returns 405 while the model is still loading,
# and 200 only once warmup is complete. Connection refused = not yet started.
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
    log "ERROR: $name not ready after ${max}s — skipping"
    return 1
}

# Full lifecycle for one model: reset → start → wait → generate → stop.
run_model() {
    local server_key="$1" gen_type="$2" timeout_s="${3:-7200}"

    log "====== $server_key ($gen_type) ======"

    stop_all_and_reset
    clear_queue

    local BEFORE
    BEFORE=$(get_newest_id)

    log "Starting $server_key…"
    ./tt-ctl start "$server_key" 2>&1 | tee -a "$LOGFILE" || true

    if wait_liveness_200 "$server_key" "$timeout_s"; then
        log "Generating ($gen_type)…"
        ./tt-ctl generate --type "$gen_type" 2>&1 | tee -a "$LOGFILE" || true

        local AFTER
        AFTER=$(get_newest_id)
        if [[ -n "$AFTER" && "$AFTER" != "$BEFORE" ]]; then
            RECORD_IDS+=("$AFTER")
            log "Record captured: $AFTER"
        else
            log "WARNING: no new record after $server_key generation"
        fi
    fi

    log "Stopping $server_key…"
    ./tt-ctl stop "$server_key" 2>&1 | tee -a "$LOGFILE" || true
    log "Waiting 30s for Docker cleanup…"
    sleep 30
}

# ── main ──────────────────────────────────────────────────────────────────────

log "======================================================"
log "Overnight generation run — playlist: $PLAYLIST_NAME"
log "======================================================"

RECORD_IDS=()

# Prompt server: best-effort, algo fallback if offline
log "--- prompt-server ---"
./tt-ctl start prompt-server 2>&1 | tee -a "$LOGFILE" || true
sleep 10

# 2-hour timeout per model (first-run TTNN compilation can take 60-90 min)
run_model "wan2.2" "video" 7200
run_model "flux"   "image" 7200
run_model "motif"  "image" 7200

# ── Playlist ──────────────────────────────────────────────────────────────────
log "====== Playlist ======"
if [[ ${#RECORD_IDS[@]} -gt 0 ]]; then
    printf '%s\n' "${RECORD_IDS[@]}" > /tmp/overnight-gen-ids.txt
    log "Adding ${#RECORD_IDS[@]} record(s) to playlist '$PLAYLIST_NAME'…"
    /usr/bin/python3 - <<PYEOF 2>&1 | tee -a "$LOGFILE"
import sys
sys.path.insert(0, '/home/ttuser/code/tt-local-generator/app')
from playlist_store import PlaylistStore

with open('/tmp/overnight-gen-ids.txt') as f:
    ids = [line.strip() for line in f if line.strip()]

ps  = PlaylistStore()
pl  = ps.get_or_create('$PLAYLIST_NAME')
n   = ps.add_records(pl.id, ids)
print(f"Playlist '{pl.name}' ({pl.id}): {n} record(s) added")
PYEOF
else
    log "No records captured — playlist skipped"
fi

# ── Suspend ───────────────────────────────────────────────────────────────────
log "======================================================"
log "Run complete. Suspending machine in 30s."
log "Full log: $LOGFILE"
log "======================================================"
sleep 30
systemctl suspend
