#!/usr/bin/env bash
# start_skyreels_i2v.sh — Start the SkyReels-V2-I2V-14B-540P inference server on Blackhole.
#
# Target hardware: Tenstorrent Blackhole
#   P150X4  — 4× P150 cards, (1, 4) mesh  (recommended: pure 4-way TP)
#   P300X2  — 2× P300 cards, (2, 2) mesh  (QB2 machine)
#
# Model: SkyReels-V2-I2V-14B-540P (image-to-video, 14B params, raw WAN 2.1 format)
#   Input:  text prompt + conditioning image  (base64 in API)
#   Output: 540P video (960×544 native), default 97 frames (≈4 s @ 24fps)
#
# Uses:
#   - Docker image: ghcr.io/tenstorrent/tt-media-inference-server:0.15.0-25891d3
#   - --dev-mode: required to bind-mount the tt_dit hotpatch (pipeline_skyreels_i2v.py)
#   - --host-hf-cache: mounts host HF cache (weights in HF cache, not a diffusers checkpoint)
#
# Prerequisites:
#   1. Apply patches first: cd tt-local-generator && bin/apply_patches.sh
#   2. SkyReels I2V weights downloaded to HF cache:
#        huggingface-cli download Skywork/SkyReels-V2-I2V-14B-540P --local-dir-use-symlinks False
#      (~58 GB download, 14 sharded safetensors files)
#   3. WAN 2.2 A14B diffusers checkpoint in HF cache (used to load VAE/T5 architecture):
#        huggingface-cli download Wan-AI/Wan2.2-T2V-A14B-Diffusers --local-dir-use-symlinks False
#      (~50 GB download, already present if you ran start_wan.sh)
#   4. tt-inference-server .env with JWT_SECRET
#
# Notes:
#   - Weight loading takes 30-60 min (14B params × 4 chips over PCIe to Blackhole).
#   - The server timeout is set to 5400s (90 min) in ModelConfigs for this runner.
#   - The server prints "Application startup complete" when ready (~45-60 min first run).
#   - Image conditioning via VAE channel concatenation (36ch); CLIP cross-attn not used.
#
# Usage:
#   ./start_skyreels_i2v.sh [--device p150x4|p300x2] [--stop] [--restart] [--gui] [--help]
#
#   --stop      Stop any running server container and exit.
#   --restart   Stop any running container, then start fresh (combines --stop + start).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Prefer vendored tt-inference-server if present.
if [[ -d "$REPO_ROOT/vendor/tt-inference-server" ]]; then
    REPO_DIR="$REPO_ROOT/vendor/tt-inference-server"
else
    REPO_DIR="$HOME/code/tt-inference-server"
fi

HF_CACHE="$HOME/.cache/huggingface"
DOCKER_IMAGE="ghcr.io/tenstorrent/tt-media-inference-server:0.15.0-25891d3"
LOG_DIR="$REPO_DIR/workflow_logs/docker_server"

# ── Parse flags ───────────────────────────────────────────────────────────────

DEVICE="p300x2"
GUI_MODE=0
RESTART_MODE=0
_PREV_ARG=""

for arg in "$@"; do
    case "$arg" in
        --help|-h)
            sed -n '2,38p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        --stop)
            RUNNING=$(docker ps --filter "ancestor=$DOCKER_IMAGE" --format "{{.ID}}" 2>/dev/null)
            if [[ -z "$RUNNING" ]]; then
                echo "No running server container found."
                exit 0
            fi
            echo "Stopping container(s): $RUNNING"
            echo "$RUNNING" | xargs docker stop
            echo "Server stopped."
            exit 0
            ;;
        --restart)
            RESTART_MODE=1
            ;;
        --gui)
            GUI_MODE=1
            ;;
        --device)
            ;;  # next arg will be the device value
        p150x4|p300x2)
            if [[ "$_PREV_ARG" == "--device" ]]; then
                DEVICE="$arg"
            else
                DEVICE="$arg"
            fi
            ;;
    esac
    _PREV_ARG="$arg"
done

LOG_GLOB="media_*_SkyReels-V2-I2V-14B-540P_${DEVICE}_server.log"

# ── Sanity checks ─────────────────────────────────────────────────────────────

if [[ ! -d "$REPO_DIR" ]]; then
    echo "ERROR: tt-inference-server not found at $REPO_DIR"
    echo "Run: bin/apply_patches.sh"
    exit 1
fi

if [[ ! -d "$HF_CACHE/hub/models--Skywork--SkyReels-V2-I2V-14B-540P" ]]; then
    echo "WARNING: SkyReels I2V weights not found in HF cache at $HF_CACHE"
    echo "         Run: huggingface-cli download Skywork/SkyReels-V2-I2V-14B-540P \\"
    echo "                  --local-dir-use-symlinks False"
    echo "         (~58 GB download — needed before first run)"
    if [[ $GUI_MODE -eq 1 ]]; then
        echo "         Continuing in GUI mode (weights will download inside container — slow)."
    else
        read -rp "Continue anyway? [y/N] " yn
        [[ "${yn,,}" == "y" ]] || exit 1
    fi
fi

# The I2V pipeline loads VAE and T5 architecture from the WAN 2.2 A14B diffusers
# checkpoint (already downloaded if you used start_wan.sh).
if [[ ! -d "$HF_CACHE/hub/models--Wan-AI--Wan2.2-T2V-A14B-Diffusers" ]]; then
    echo "WARNING: WAN 2.2 A14B diffusers checkpoint not found in HF cache."
    echo "         The I2V pipeline uses it to load VAE and T5 encoder architectures."
    echo "         Run: huggingface-cli download Wan-AI/Wan2.2-T2V-A14B-Diffusers \\"
    echo "                  --local-dir-use-symlinks False"
    echo "         (~50 GB download)"
    if [[ $GUI_MODE -ne 1 ]]; then
        read -rp "Continue anyway? [y/N] " yn
        [[ "${yn,,}" == "y" ]] || exit 1
    fi
fi

# Verify patches have been applied
if [[ ! -f "$REPO_DIR/patches/tt_dit/pipelines/skyreels_v2/pipeline_skyreels_i2v.py" ]]; then
    echo "ERROR: SkyReels I2V tt_dit hotpatch not found."
    echo "       Run: bin/apply_patches.sh"
    exit 1
fi

# ── Check for running container ───────────────────────────────────────────────

EXISTING=$(docker ps --filter "ancestor=$DOCKER_IMAGE" --format "{{.ID}}" 2>/dev/null | head -1)
if [[ -n "$EXISTING" ]]; then
    if [[ $RESTART_MODE -eq 1 ]]; then
        echo "Stopping existing container $EXISTING for restart …"
        docker stop "$EXISTING"
        echo "Stopped. Starting fresh …"
        echo ""
    elif [[ $GUI_MODE -eq 1 ]]; then
        echo "Server already running in container $EXISTING"
        echo "Server is already up."
        exit 0
    else
        echo "Server already running in container $EXISTING"
        LATEST_LOG=$(ls -t "$LOG_DIR"/$LOG_GLOB 2>/dev/null | head -1 || true)
        if [[ -n "$LATEST_LOG" ]]; then
            echo "Tailing log: $LATEST_LOG"
            echo "(Ctrl-C to stop tailing — server keeps running)"
            exec tail -f "$LATEST_LOG"
        else
            echo "  Logs: docker logs -f $EXISTING"
            echo "  Stop: $0 --stop"
        fi
        exit 0
    fi
fi

# ── Launch ────────────────────────────────────────────────────────────────────

echo "Starting SkyReels-V2-I2V-14B-540P on $DEVICE …"
echo "  Repo:      $REPO_DIR"
echo "  Image:     $DOCKER_IMAGE"
echo "  HF cache:  $HF_CACHE  (bind-mounted read-only)"
echo "  Port:      8000"
echo "  Note:      Weight loading takes 30-60 min — server not ready until complete."
echo ""

mkdir -p "$LOG_DIR"
START_TS=$(date +%s)
cd "$REPO_DIR"

JWT_SECRET=$(grep -E '^JWT_SECRET=' "$REPO_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"'"'" || true)
if [[ -z "$JWT_SECRET" ]]; then
    echo "ERROR: JWT_SECRET not found in $REPO_DIR/.env"
    exit 1
fi

# --dev-mode is required: it activates the tt_dit hotpatch bind-mount mechanism
# that injects pipeline_skyreels_i2v.py into the container at startup.
MODEL_SOURCE=huggingface JWT_SECRET="$JWT_SECRET" python3 run.py \
    --model SkyReels-V2-I2V-14B-540P \
    --workflow server \
    --tt-device "$DEVICE" \
    --impl tt-transformers \
    --engine media \
    --docker-server \
    --dev-mode \
    --override-docker-image "$DOCKER_IMAGE" \
    --host-hf-cache "$HF_CACHE" \
    --no-auth &
WORKFLOW_PID=$!

echo "Workflow PID: $WORKFLOW_PID"
echo "Waiting for log file to appear in $LOG_DIR …"
echo ""

wait "$WORKFLOW_PID"
WORKFLOW_EXIT=$?

if [[ $WORKFLOW_EXIT -ne 0 ]]; then
    echo "ERROR: Workflow process exited with code $WORKFLOW_EXIT."
    LATEST=$(ls -t "$LOG_DIR"/$LOG_GLOB 2>/dev/null | head -1 || true)
    [[ -n "$LATEST" ]] && { echo "Last log: $LATEST"; tail -50 "$LATEST"; }
    exit 1
fi

LOG_FILE=$(ls -t "$LOG_DIR"/$LOG_GLOB 2>/dev/null \
           | while read -r f; do
               mtime=$(stat -c %Y "$f" 2>/dev/null || echo 0)
               [[ $mtime -ge $START_TS ]] && echo "$f" && break
             done)

if [[ -z "$LOG_FILE" ]]; then
    echo "WARNING: Could not find a new log file in $LOG_DIR"
    echo "  Check manually: docker logs -f \$(docker ps -lq)"
    exit 0
fi

echo "Log file: $LOG_FILE"
echo ""
echo "Tip: the server prints 'Application startup complete' when ready."
echo "     Weight loading takes 30-60 min on first start."
echo ""

if [[ $GUI_MODE -eq 1 ]]; then
    echo "Server started in Docker. GUI health check will detect when ready."
    exit 0
fi

echo "(Ctrl-C to stop tailing — server keeps running in Docker)"
tail -f "$LOG_FILE"
