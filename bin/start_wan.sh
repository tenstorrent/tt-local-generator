#!/usr/bin/env bash
# start_wan.sh — Start the Wan2.2-T2V-A14B-Diffusers inference server on P150x4.
#
# Uses the known-working configuration:
#   - Docker image: ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34
#   - Non-dev mode (dev mode breaks device init on this image)
#   - --host-hf-cache mounts the local HuggingFace cache so the 118 GB weights
#     are found immediately inside the container (avoids the 1200s download timeout)
#
# The workflow process writes a log file to:
#   tt-inference-server/workflow_logs/docker_server/media_<timestamp>_Wan2.2-T2V-A14B-Diffusers_p150x4_server.log
# This script waits for that file to appear then tails it live.
#
# Usage:
#   ./start_wan.sh            # start server and tail its log
#   ./start_wan.sh --stop     # stop the running server container
#   ./start_wan.sh --gui      # start without interactive prompts or tail (for GUI use)
#   ./start_wan.sh --help     # show this help

set -euo pipefail

REPO_DIR="$HOME/code/tt-inference-server"
HF_CACHE="$HOME/.cache/huggingface"
DOCKER_IMAGE="ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34"
LOG_DIR="$REPO_DIR/workflow_logs/docker_server"
LOG_GLOB="media_*_Wan2.2-T2V-A14B-Diffusers_p150x4_server.log"

# ── Parse flags ───────────────────────────────────────────────────────────────

GUI_MODE=0
for arg in "$@"; do
    case "$arg" in
        --help|-h)
            sed -n '2,19p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        --stop)
            # Stop the running server container and exit.
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
            # GUI mode: skip interactive prompts and the final tail -f.
            # The caller (GUI app) monitors readiness via the /tt-liveness health check.
            GUI_MODE=1
            ;;
    esac
done

# ── Sanity checks ─────────────────────────────────────────────────────────────

if [[ ! -d "$REPO_DIR" ]]; then
    echo "ERROR: tt-inference-server not found at $REPO_DIR"
    exit 1
fi

if [[ ! -d "$HF_CACHE/hub/models--Wan-AI--Wan2.2-T2V-A14B-Diffusers" ]]; then
    echo "WARNING: HuggingFace cache not found at $HF_CACHE"
    echo "         The model weights will be downloaded inside the container (~118 GB)."
    echo "         This will likely exceed the container's 1200s startup timeout."
    if [[ $GUI_MODE -eq 1 ]]; then
        echo "         Continuing in GUI mode (weights will download inside container)."
    else
        read -rp "Continue anyway? [y/N] " yn
        [[ "${yn,,}" == "y" ]] || exit 1
    fi
fi

# ── Check for running container ───────────────────────────────────────────────

EXISTING=$(docker ps --filter "ancestor=$DOCKER_IMAGE" --format "{{.ID}}" 2>/dev/null | head -1)
if [[ -n "$EXISTING" ]]; then
    echo "Server already running in container $EXISTING"
    echo ""
    if [[ $GUI_MODE -eq 1 ]]; then
        # In GUI mode just report and exit; health check will confirm readiness.
        echo "Server is already up. Use the GUI health indicator to confirm readiness."
        exit 0
    fi
    # Find the most recent log for this container and tail it
    LATEST_LOG=$(ls -t "$LOG_DIR"/$LOG_GLOB 2>/dev/null | head -1)
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

# ── Launch workflow process in background ─────────────────────────────────────

echo "Starting Wan2.2-T2V-A14B-Diffusers on P150x4…"
echo "  Image:     $DOCKER_IMAGE"
echo "  HF cache:  $HF_CACHE  (bind-mounted read-only)"
echo "  Port:      8000"
echo ""

mkdir -p "$LOG_DIR"

# Record the timestamp so we pick the log file created by *this* run, not an old one
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
    --model Wan2.2-T2V-A14B-Diffusers \
    --workflow server \
    --tt-device p150x4 \
    --impl tt-transformers \
    --engine media \
    --docker-server \
    --override-docker-image "$DOCKER_IMAGE" \
    --host-hf-cache "$HF_CACHE" &
WORKFLOW_PID=$!

echo "Workflow PID: $WORKFLOW_PID"
echo "Waiting for log file to appear in $LOG_DIR …"
echo ""

# ── Wait for run.py to finish launching Docker ────────────────────────────────
# run.py exits with code 0 once Docker is running — that's normal, not an error.
# We wait for it, then pick up the log file it created.

wait "$WORKFLOW_PID"
WORKFLOW_EXIT=$?

if [[ $WORKFLOW_EXIT -ne 0 ]]; then
    echo "ERROR: Workflow process exited with code $WORKFLOW_EXIT."
    LATEST=$(ls -t "$LOG_DIR"/$LOG_GLOB 2>/dev/null | head -1 || true)
    [[ -n "$LATEST" ]] && { echo "Last log: $LATEST"; echo ""; tail -50 "$LATEST"; }
    exit 1
fi

# ── Find the log file created by this run ─────────────────────────────────────
# Pick the newest matching file whose mtime is >= when we started.
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
echo "Tip: the server prints 'Application startup complete' when ready (~5 min)."
echo ""

if [[ $GUI_MODE -eq 1 ]]; then
    # In GUI mode the caller monitors readiness via the health check endpoint.
    # Don't tail — just exit so the subprocess terminates cleanly.
    echo "Server started in Docker. GUI health check will detect when ready."
    exit 0
fi

# Interactive mode: tail the log indefinitely (Ctrl-C to stop; server keeps running in Docker).
echo "(Ctrl-C to stop tailing — server keeps running in Docker)"
tail -f "$LOG_FILE"
