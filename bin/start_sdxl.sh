#!/usr/bin/env bash
# start_sdxl.sh — Start the SDXL image server on P300X2 via the C++ backend.
#
# This is the first test of the cpp_server path (SERVER_MODE=cpp), which is the
# next-generation Tenstorrent inference server.  run_docker_server.py auto-detects
# the SDXL model name and sets SERVER_MODE=cpp automatically — no special flags
# needed here.
#
# Weights:  stabilityai/stable-diffusion-xl-base-1.0 (cached at ~/.cache/huggingface)
# Port:     8000  (same as the media server)
# Endpoint: POST /v1/images/generations  (cpp OpenAI-compatible image API)
# Auth:     Bearer your-secret-key  (cpp default; set OPENAI_API_KEY env to override)
#
# Usage:
#   ./start_sdxl.sh            # start server and tail its log
#   ./start_sdxl.sh --stop     # stop the running server container
#   ./start_sdxl.sh --gui      # start without interactive tail (for GUI use)
#   ./start_sdxl.sh --help     # show this help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -d "$REPO_ROOT/vendor/tt-inference-server" ]]; then
    REPO_DIR="$REPO_ROOT/vendor/tt-inference-server"
else
    REPO_DIR="$HOME/code/tt-inference-server"
fi

HF_CACHE="$HOME/.cache/huggingface"
DOCKER_IMAGE="ghcr.io/tenstorrent/tt-media-inference-server:0.18.0-c49bb76"
MODEL="stable-diffusion-xl-base-1.0"
DEVICE="p300x2"
LOG_DIR="$REPO_DIR/workflow_logs/docker_server"
LOG_GLOB="media_*_${MODEL}_${DEVICE}_server.log"

GUI_MODE=0
for arg in "$@"; do
    case "$arg" in
        --help|-h)
            sed -n '2,18p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        --stop)
            RUNNING=$(docker ps --filter "ancestor=$DOCKER_IMAGE" --format "{{.ID}}" 2>/dev/null)
            if [[ -z "$RUNNING" ]]; then echo "No running server container found."; exit 0; fi
            echo "Stopping container(s): $RUNNING"
            echo "$RUNNING" | xargs docker stop
            echo "Server stopped."
            exit 0
            ;;
        --gui) GUI_MODE=1 ;;
    esac
done

if [[ ! -d "$REPO_DIR" ]]; then
    echo "ERROR: tt-inference-server not found at $REPO_DIR"
    exit 1
fi

if [[ ! -d "$HF_CACHE/hub/models--stabilityai--stable-diffusion-xl-base-1.0" ]]; then
    echo "WARNING: SDXL weights not found at $HF_CACHE/hub/models--stabilityai--stable-diffusion-xl-base-1.0"
    echo "  Download with: huggingface-cli download stabilityai/stable-diffusion-xl-base-1.0"
fi

EXISTING=$(docker ps --filter "ancestor=$DOCKER_IMAGE" --format "{{.ID}}" 2>/dev/null | head -1)
if [[ -n "$EXISTING" ]]; then
    echo "Server already running in container $EXISTING"
    [[ $GUI_MODE -eq 1 ]] && exit 0
    LATEST_LOG=$(ls -t "$LOG_DIR"/$LOG_GLOB 2>/dev/null | head -1)
    [[ -n "$LATEST_LOG" ]] && exec tail -f "$LATEST_LOG"
    exit 0
fi

echo "Starting $MODEL (cpp_server backend) on $DEVICE…"
echo "  Image:     $DOCKER_IMAGE"
echo "  HF cache:  $HF_CACHE"
echo "  Port:      8000"
echo "  Backend:   cpp_server (SERVER_MODE=cpp, auto-set for SDXL)"
echo ""

mkdir -p "$LOG_DIR"
START_TS=$(date +%s)
cd "$REPO_DIR"

JWT_SECRET=$(grep -E '^JWT_SECRET=' "$REPO_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"'"'" || true)
if [[ -z "$JWT_SECRET" ]]; then
    echo "ERROR: JWT_SECRET not found in $REPO_DIR/.env"
    exit 1
fi

MODEL_SOURCE=huggingface JWT_SECRET="$JWT_SECRET" python3 run.py \
    --model "$MODEL" \
    --workflow server \
    --tt-device "$DEVICE" \
    --impl tt-transformers \
    --engine media \
    --docker-server \
    --override-docker-image "$DOCKER_IMAGE" \
    --host-hf-cache "$HF_CACHE" \
    --no-auth &
WORKFLOW_PID=$!

echo "Workflow PID: $WORKFLOW_PID"
echo "Waiting for log file…"

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
    echo "WARNING: Could not find new log file in $LOG_DIR"
    exit 0
fi

# cpp_server does not use /tmp/prometheus_multiproc — no chmod needed.

echo "Log file: $LOG_FILE"
echo ""
echo "Tip: the cpp_server prints 'Server started' when ready (~2-5 min on P300x2)."
echo "     Image endpoint: POST http://localhost:8000/image/generations"
echo ""

[[ $GUI_MODE -eq 1 ]] && { echo "Server started in Docker. GUI health check will detect readiness."; exit 0; }
echo "(Ctrl-C to stop tailing — server keeps running in Docker)"
tail -f "$LOG_FILE"
