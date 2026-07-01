#!/usr/bin/env bash
# start_z_image_turbo.sh — Start the Z-Image-Turbo image generation server on P300X2.
#
# Hardware note:
#   4× Wormhole p300c PCIe cards = 2 logical p300 boards (L/R dies) = DeviceTypes.P300X2
#   Z-Image-Turbo uses a 1×4 device mesh (all four chips in a row).
#
# Status: FUNCTIONAL (validated on P300X2 in tt-inference-server v0.17.0).
#
# The API is synchronous: POST /v1/images/generations returns a base64 JPEG.
# Default steps: 9 (hardcoded in the runner — num_inference_steps is ignored).
#
# Usage:
#   ./start_z_image_turbo.sh          # start server and tail its log
#   ./start_z_image_turbo.sh --stop   # stop the running server container
#   ./start_z_image_turbo.sh --gui    # start without interactive prompts or tail (for GUI use)
#   ./start_z_image_turbo.sh --help   # show this help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -d "$REPO_ROOT/vendor/tt-inference-server" ]]; then
    REPO_DIR="$REPO_ROOT/vendor/tt-inference-server"
else
    REPO_DIR="$HOME/code/tt-inference-server"
fi
HF_CACHE="$HOME/.cache/huggingface"
DOCKER_IMAGE="ghcr.io/tenstorrent/tt-media-inference-server:0.17.0-8c48a10"
MODEL="Z-Image-Turbo"
HF_REPO="Tongyi-MAI/Z-Image-Turbo"
HF_CACHE_DIR="$HF_CACHE/hub/models--Tongyi-MAI--Z-Image-Turbo"
LOG_GLOB="media_*_${MODEL}_p300x2_server.log"
LOG_DIR="$REPO_DIR/workflow_logs/docker_server"

# ── Parse flags ───────────────────────────────────────────────────────────────

GUI_MODE=0
for arg in "$@"; do
    case "$arg" in
        --help|-h)
            sed -n '2,17p' "$0" | sed 's/^# \?//'
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
        --gui)
            GUI_MODE=1
            ;;
    esac
done

# ── Sanity checks ─────────────────────────────────────────────────────────────

if [[ ! -d "$REPO_DIR" ]]; then
    echo "ERROR: tt-inference-server not found at $REPO_DIR"
    exit 1
fi

if [[ ! -d "$HF_CACHE_DIR" ]]; then
    echo "WARNING: Z-Image-Turbo weights not found at $HF_CACHE_DIR"
    echo "         Pre-download with:"
    echo "           huggingface-cli download ${HF_REPO}"
    echo "         (~5–10 GB)"
    if [[ $GUI_MODE -eq 1 ]]; then
        echo "         Continuing in GUI mode (weights will download inside container)."
    else
        read -rp "Continue anyway (weights will download inside container)? [y/N] " yn
        [[ "${yn,,}" == "y" ]] || exit 1
    fi
fi

# ── Check for running container ───────────────────────────────────────────────

EXISTING=$(docker ps --filter "ancestor=$DOCKER_IMAGE" --format "{{.ID}}" 2>/dev/null | head -1)
if [[ -n "$EXISTING" ]]; then
    echo "Server already running in container $EXISTING"
    echo ""
    if [[ $GUI_MODE -eq 1 ]]; then
        echo "Server is already up. Use the GUI health indicator to confirm readiness."
        exit 0
    fi
    LATEST_LOG=$(ls -t "$LOG_DIR"/$LOG_GLOB 2>/dev/null | head -1 || true)
    if [[ -n "$LATEST_LOG" ]]; then
        echo "Tailing log: $LATEST_LOG"
        echo "(Ctrl-C to stop tailing — server keeps running)"
        echo ""
        exec tail -f "$LATEST_LOG"
    else
        echo "  Logs: docker logs -f $EXISTING"
        echo "  Stop: docker stop $EXISTING"
    fi
    exit 0
fi

# ── Launch ────────────────────────────────────────────────────────────────────

echo "Starting ${MODEL} on 4× p300c (p300x2)…"
echo "  Image:     $DOCKER_IMAGE"
echo "  HF cache:  $HF_CACHE  (bind-mounted read-only)"
echo "  Port:      8000"
echo "  API:       POST /v1/images/generations  (synchronous — returns base64 JPEG)"
echo "  Steps:     9  (hardcoded in the runner)"
echo ""

mkdir -p "$LOG_DIR"
START_TS=$(date +%s)

JWT_SECRET=$(grep -E '^JWT_SECRET=' "$REPO_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"'"'" || true)
if [[ -z "$JWT_SECRET" ]]; then
    echo "ERROR: JWT_SECRET not found in $REPO_DIR/.env"
    exit 1
fi

cd "$REPO_DIR"
MODEL_SOURCE=huggingface JWT_SECRET="$JWT_SECRET" python3 run.py \
    --model "$MODEL" \
    --workflow server \
    --tt-device p300x2 \
    --engine media \
    --docker-server \
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
    [[ -n "$LATEST" ]] && { echo "Last log: $LATEST"; echo ""; tail -50 "$LATEST"; }
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
echo "Tip: the server prints 'Application startup complete' when ready (~5–10 min first run)."
echo ""

if [[ $GUI_MODE -eq 1 ]]; then
    echo "Server started in Docker. GUI health check will detect when ready."
    exit 0
fi

echo "(Ctrl-C to stop tailing — server keeps running in Docker)"
tail -f "$LOG_FILE"
