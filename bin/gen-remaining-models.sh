#!/usr/bin/env bash
# gen-remaining-models.sh — Sequential generation run for FLUX, Motif, and Z-Image-Turbo.
#
# Rolls through each model not yet successfully tested, generates one image per
# model, adds the record to the "New Model Support" playlist, and logs timing to
# docs/new-model-support-log.md.  Continues to the next model even if one fails.
#
# Patches applied before this run:
#   - TTFlux1Runner.get_pipeline_device_params() restored to self.settings.trace_region_size
#     (our 50 MB hardcode exceeded the device trace buffer; ~33 MB default fits).
#   - TTMotifImage6BPreviewRunner.create_pipeline() now passes (2,2) mesh params
#     explicitly to bypass the missing default_config entry in pipeline_motif.py.
#   - constants.py adds MODEL_RUNNER_TO_MODEL_NAMES_MAP alias for 0.9.0 image.
#
# Usage:
#   nohup ./bin/gen-remaining-models.sh > /tmp/gen-remaining.log 2>&1 &
#   tail -f /tmp/gen-remaining.log

REPO="/home/ttuser/code/tt-local-generator"
LOGFILE="/tmp/gen-remaining.log"
NEW_MODEL_SUPPORT_PLAYLIST="e46b4782-b991-48bb-b5dd-ada1b0da1b2b"
NEW_MODEL_SUPPORT_LOG="$REPO/docs/new-model-support-log.md"

cd "$REPO"

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
        log "Stopping all running containers…"
        echo "$running" | xargs docker stop 2>&1 | tee -a "$LOGFILE" || true
        sleep 15
    fi
    log "Running tt-smi -r (hardware reset)…"
    tt-smi -r 2>&1 | tee -a "$LOGFILE" || true
    sleep 10
}

wait_liveness_200() {
    local name="$1" max="${2:-14400}"
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
        [[ $((elapsed % 300)) -eq 0 ]] && log "  …${elapsed}s (liveness: $code)"
    done
    log "ERROR: $name not ready after ${max}s"
    return 1
}

add_to_playlist() {
    local record_id="$1" playlist_id="$2"
    /usr/bin/python3 - "$record_id" "$playlist_id" <<'PYEOF' 2>&1 | tee -a "$LOGFILE"
import sys
sys.path.insert(0, '/home/ttuser/code/tt-local-generator/app')
from playlist_store import PlaylistStore
record_id, playlist_id = sys.argv[1], sys.argv[2]
ps = PlaylistStore()
pl = ps.get(playlist_id)
if pl:
    n = ps.add_records(playlist_id, [record_id])
    print(f"Added {record_id} to playlist '{pl.name}': {n} record(s) added")
else:
    print(f"WARNING: playlist {playlist_id} not found")
PYEOF
}

get_record_info() {
    local record_id="$1"
    /usr/bin/python3 - "$record_id" <<'PYEOF' 2>/dev/null || true
import sys, json
sys.path.insert(0, '/home/ttuser/code/tt-local-generator/app')
from history_store import HistoryStore
hs = HistoryStore()
for r in hs.all_records():
    if r.id == sys.argv[1]:
        print(json.dumps({
            "file_path": getattr(r, 'file_path', getattr(r, 'video_path', '')),
            "prompt": r.prompt or '(no prompt)',
        }))
        break
PYEOF
}

append_log_entry() {
    local model_name="$1" hardware="$2" docker_image="$3"
    local warmup_s="$4" gen_s="$5" record_id="$6" file_path="$7" prompt="$8"
    local ts
    ts=$(date '+%Y-%m-%d')
    cat >> "$NEW_MODEL_SUPPORT_LOG" <<MDEOF

---

## ${model_name}

| Field | Value |
|---|---|
| Record ID | \`${record_id}\` |
| Date | ${ts} |
| Hardware | ${hardware} |
| Docker image | \`${docker_image}\` |
| Warmup time | **${warmup_s}s** (~$((warmup_s / 60)) min) |
| Generation time | **${gen_s}s** |
| Output | JPEG image |
| Prompt | *${prompt}* |
| File path | \`${file_path}\` |
MDEOF
    log "Appended timing to $NEW_MODEL_SUPPORT_LOG"
}

run_model() {
    local key="$1" name="$2" hardware="$3" docker_image="$4" start_cmd="$5"
    local max_wait="${6:-14400}"

    log "======================================================"
    log "Running: $name"
    log "======================================================"

    stop_all_and_reset

    local before
    before=$(get_newest_id)

    log "Starting $key server…"
    eval "$start_cmd" 2>&1 | tee -a "$LOGFILE" || true

    local warmup_start warmup_end warmup_s
    warmup_start=$(date +%s)

    if ! wait_liveness_200 "$name" "$max_wait"; then
        log "SKIPPING $name — server did not become ready"
        return 1
    fi

    warmup_end=$(date +%s)
    warmup_s=$((warmup_end - warmup_start))
    log "Warmup: ${warmup_s}s"

    log "Generating image…"
    local gen_t0 gen_t1 gen_s
    gen_t0=$(date +%s)
    ./tt-ctl generate --type image 2>&1 | tee -a "$LOGFILE" || true
    gen_t1=$(date +%s)
    gen_s=$((gen_t1 - gen_t0))
    log "Generation API call: ${gen_s}s"

    local after
    after=$(get_newest_id)
    if [[ -z "$after" || "$after" == "$before" ]]; then
        log "WARNING: no new record found after generation for $name"
        log "Stopping $key…"
        ./tt-ctl stop "$key" 2>&1 | tee -a "$LOGFILE" || true
        return 1
    fi

    log "Record captured: $after (warmup: ${warmup_s}s, gen: ${gen_s}s)"

    add_to_playlist "$after" "$NEW_MODEL_SUPPORT_PLAYLIST"

    local info file_path prompt
    info=$(get_record_info "$after")
    file_path=$(echo "$info" | /usr/bin/python3 -c "import sys,json; d=json.load(sys.stdin); print(d['file_path'])" 2>/dev/null || true)
    prompt=$(echo "$info" | /usr/bin/python3 -c "import sys,json; d=json.load(sys.stdin); print(d['prompt'])" 2>/dev/null || true)

    append_log_entry "$name" "$hardware" "$docker_image" \
        "$warmup_s" "$gen_s" "$after" "${file_path:-unknown}" "${prompt:-(unknown)}"

    log "Stopping $key…"
    ./tt-ctl stop "$key" 2>&1 | tee -a "$LOGFILE" || true
    return 0
}

# ── Main ──────────────────────────────────────────────────────────────────────

log "======================================================"
log "gen-remaining-models.sh — FLUX → Motif → Z-Image-Turbo"
log "======================================================"

FLUX_OK=0
MOTIF_OK=0
ZIT_OK=0

# ── 1. FLUX.1-schnell ─────────────────────────────────────────────────────────
# First run: TTNN kernel compilation ~90-120 min, then trace setup.
# trace_region_size now uses self.settings.trace_region_size (~33 MB) — no override.
if run_model \
    "flux" \
    "FLUX.1-schnell (Tenstorrent)" \
    "QB2 — P300X2 (4× Wormhole p300c, mesh (2,2))" \
    "ghcr.io/tenstorrent/tt-media-inference-server:0.17.0-8c48a10" \
    "./tt-ctl start flux" \
    18000; then   # 5-hour budget for first-run TTNN compilation
    FLUX_OK=1
    log "FLUX SUCCESS"
else
    log "FLUX FAILED — continuing to Motif"
fi

# ── 2. Motif-Image-6B-Preview ─────────────────────────────────────────────────
# Uses 0.9.0-c180ef7 image. Fixes: constants.py alias + (2,2) mesh params in
# dit_runners.py. Previous failure was ImportError + KeyError — both patched.
if run_model \
    "motif" \
    "Motif-Image-6B-Preview (Motif Technologies)" \
    "QB2 — P300X2 (4× Wormhole p300c, mesh (2,2))" \
    "ghcr.io/tenstorrent/tt-media-inference-server:0.9.0-c180ef7" \
    "./tt-ctl start motif" \
    18000; then   # 5-hour budget for first run
    MOTIF_OK=1
    log "MOTIF SUCCESS"
else
    log "MOTIF FAILED — continuing to Z-Image-Turbo"
fi

# ── 3. Z-Image-Turbo ──────────────────────────────────────────────────────────
# Uses 0.17.0-8c48a10 image. P150X4 (QB2 4× BH p150 chips, mesh (1,4)).
# Z_IMAGE_TURBO_MODEL_DIR set via model_spec.py DeviceModelSpec env_vars.
if run_model \
    "z-image-turbo" \
    "Z-Image-Turbo (Tongyi-MAI)" \
    "QB2 — P150X4 (4× Blackhole p150, mesh (1,4))" \
    "ghcr.io/tenstorrent/tt-media-inference-server:0.17.0-8c48a10" \
    "./tt-ctl start z-image-turbo" \
    18000; then   # 5-hour budget for first run
    ZIT_OK=1
    log "Z-IMAGE-TURBO SUCCESS"
else
    log "Z-IMAGE-TURBO FAILED"
fi

# ── Summary ───────────────────────────────────────────────────────────────────

log ""
log "======================================================"
log "SUMMARY"
log "======================================================"
log "FLUX.1-schnell:       $([ $FLUX_OK  -eq 1 ] && echo 'SUCCESS ✓' || echo 'FAILED ✗')"
log "Motif-Image-6B:       $([ $MOTIF_OK -eq 1 ] && echo 'SUCCESS ✓' || echo 'FAILED ✗')"
log "Z-Image-Turbo:        $([ $ZIT_OK   -eq 1 ] && echo 'SUCCESS ✓' || echo 'FAILED ✗')"
log ""
log "Full log: $LOGFILE"
log "Timing:   $NEW_MODEL_SUPPORT_LOG"
