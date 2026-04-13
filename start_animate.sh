#!/usr/bin/env bash
# start_animate.sh — Start the Wan2.2-Animate-14B character animation server on P300x2 (QB2).
#
# Uses the same Docker image as Wan2.2 T2V (0.11.1-bac8b34) which already contains the
# WAN I2V pipeline.  The --dev-mode flag triggers two mechanisms:
#
#   1. tt-media-server bind-mount:
#      tt-inference-server/tt-media-server/ is mounted over the container's server
#      directory, supplying the updated TTWan22AnimateRunner (TT hardware path).
#
#   2. tt_dit hotpatch bind-mount:
#      patches/tt_dit/pipelines/wan/pipeline_wan_animate.py is mounted into the
#      container at ~/tt-metal/models/tt_dit/pipelines/wan/ so that the runner can
#      import WanPipelineAnimate at pipeline-creation time.
#
# The Animate model's motion transfer emerges from the Animate-14B fine-tuned
# weights; the character image is supplied as the I2V conditioning reference frame.
#
# Generation: 81-frame (≈3.4s) 480×832 video from a character image + prompt.
# API endpoint: POST /v1/videos/generations  (note: "videos" plural, same as Mochi)
# Auth: Bearer your-secret-key  (API_KEY env not set → container default)
#
# Usage:
#   ./start_animate.sh             # start server and tail its log
#   ./start_animate.sh --stop      # stop the running server container
#   ./start_animate.sh --gui       # start without interactive prompts or tail
#   ./start_animate.sh --help      # show this help

set -euo pipefail

REPO_DIR="$HOME/code/tt-inference-server"
HF_CACHE="$HOME/.cache/huggingface"
DOCKER_IMAGE="ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34"
MODEL="Wan2.2-Animate-14B-Diffusers"
DEVICE="p300x2"
LOG_DIR="$REPO_DIR/workflow_logs/docker_server"
LOG_GLOB="media_*_${MODEL}_${DEVICE}_server.log"

# ── Parse flags ───────────────────────────────────────────────────────────────

GUI_MODE=0
for arg in "$@"; do
    case "$arg" in
        --help|-h)
            sed -n '2,22p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        --stop)
            RUNNING=$(docker ps --filter "ancestor=$DOCKER_IMAGE" --format "{{.ID}}" 2>/dev/null)
            if [[ -z "$RUNNING" ]]; then
                echo "No running Animate server container found."
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

HF_CACHE_DIR="$HF_CACHE/hub/models--Wan-AI--Wan2.2-Animate-14B-Diffusers"
if [[ ! -d "$HF_CACHE_DIR" ]]; then
    echo "WARNING: Animate-14B weights not found at $HF_CACHE_DIR"
    echo "         Pre-download with:"
    echo "           huggingface-cli download Wan-AI/Wan2.2-Animate-14B-Diffusers"
    echo "         (~30 GB)"
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
    if [[ $GUI_MODE -eq 1 ]]; then
        echo "Server is already up. Use the GUI health indicator to confirm readiness."
        exit 0
    fi
    LATEST_LOG=$(ls -t "$LOG_DIR"/$LOG_GLOB 2>/dev/null | head -1 || true)
    if [[ -n "$LATEST_LOG" ]]; then
        echo "Tailing log: $LATEST_LOG"
        echo "(Ctrl-C to stop tailing — server keeps running)"
        exec tail -f "$LATEST_LOG"
    else
        echo "  Logs: docker logs -f $EXISTING"
        echo "  Stop: docker stop $EXISTING"
    fi
    exit 0
fi

# ── Launch ─────────────────────────────────────────────────────────────────────

echo "Starting Wan2.2-Animate-14B on P300x2 (QB2, 4 chips)…"
echo "  Image:     $DOCKER_IMAGE"
echo "  HF cache:  $HF_CACHE  (bind-mounted read-only)"
echo "  Port:      8000"
echo ""

mkdir -p "$LOG_DIR"

# Record timestamp to identify the log file created by this run.
START_TS=$(date +%s)

cd "$REPO_DIR"

# Read JWT_SECRET from .env so setup_host doesn't prompt for it.
# MODEL_SOURCE=huggingface skips the interactive "How do you want to provide a model?" prompt.
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
    --dev-mode \
    --override-docker-image "$DOCKER_IMAGE" \
    --host-hf-cache "$HF_CACHE" &
WORKFLOW_PID=$!

echo "Workflow PID: $WORKFLOW_PID"
echo "Waiting for log file to appear in $LOG_DIR …"
echo ""

# Wait for run.py to finish launching Docker (exits with 0 once Docker is running).
wait "$WORKFLOW_PID"
WORKFLOW_EXIT=$?

if [[ $WORKFLOW_EXIT -ne 0 ]]; then
    echo "ERROR: Workflow process exited with code $WORKFLOW_EXIT."
    LATEST=$(ls -t "$LOG_DIR"/$LOG_GLOB 2>/dev/null | head -1 || true)
    [[ -n "$LATEST" ]] && { echo "Last log: $LATEST"; echo ""; tail -50 "$LATEST"; }
    exit 1
fi

# ── Find the log file created by this run ─────────────────────────────────────

LOG_FILE=$(ls -t "$LOG_DIR"/$LOG_GLOB 2>/dev/null \
           | while read -r f; do
               mtime=$(stat -c %Y "$f" 2>/dev/null || echo 0)
               [[ $mtime -ge $START_TS ]] && echo "$f" && break
             done)

if [[ -z "$LOG_FILE" ]]; then
    echo "WARNING: Could not find a new log file in $LOG_DIR"
    echo "  Check manually, or run: docker logs -f \$(docker ps -lq)"
    exit 0
fi

# ── Tail the log ──────────────────────────────────────────────────────────────

echo "Log file: $LOG_FILE"
echo ""
echo "Tip: the server prints 'Application startup complete' when ready (~5–10 min)."
echo ""

if [[ $GUI_MODE -eq 1 ]]; then
    echo "Server started in Docker. GUI health check will detect when ready."
    exit 0
fi

echo "(Ctrl-C to stop tailing — server keeps running in Docker)"
tail -f "$LOG_FILE"
