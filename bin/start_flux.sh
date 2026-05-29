#!/usr/bin/env bash
# start_flux.sh — Start the FLUX.1-dev image generation server on 4× p300c (p300x2).
#
# Hardware note:
#   4× Wormhole p300c PCIe cards = 2 logical p300 boards (L/R dies) = DeviceTypes.P300X2
#   This is distinct from the p150x4 (BH QuietBox) — use --tt-device p300x2.
#
# The Docker image and model are the same tt-media-server used for video,
# but the API is synchronous: POST /v1/images/generations returns a base64 JPEG.
#
# Usage:
#   ./start_flux.sh             # start server and tail its log
#   ./start_flux.sh --schnell   # use FLUX.1-schnell (faster, lower quality)
#   ./start_flux.sh --stop      # stop the running server container
#   ./start_flux.sh --gui       # start without interactive prompts or tail (for GUI use)
#   ./start_flux.sh --help      # show this help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -d "$REPO_ROOT/vendor/tt-inference-server" ]]; then
    REPO_DIR="$REPO_ROOT/vendor/tt-inference-server"
else
    REPO_DIR="$HOME/code/tt-inference-server"
fi
HF_CACHE="$HOME/.cache/huggingface"
DOCKER_IMAGE="ghcr.io/tenstorrent/tt-media-inference-server:0.15.0-25891d3"
MODEL="FLUX.1-dev"
LOG_DIR="$REPO_DIR/workflow_logs/docker_server"

# ── Parse flags ───────────────────────────────────────────────────────────────

GUI_MODE=0
for arg in "$@"; do
    case "$arg" in
        --help|-h)
            sed -n '2,16p' "$0" | sed 's/^# \?//'
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
        --schnell)
            MODEL="FLUX.1-schnell"
            echo "Using FLUX.1-schnell (fewer steps needed, lower quality)."
            ;;
        --gui)
            # GUI mode: skip interactive prompts and the final tail -f.
            # The caller (GUI app) monitors readiness via the /tt-liveness health check.
            GUI_MODE=1
            ;;
    esac
done

LOG_GLOB="media_*_${MODEL}_p300x2_server.log"

# ── Sanity checks ─────────────────────────────────────────────────────────────

if [[ ! -d "$REPO_DIR" ]]; then
    echo "ERROR: tt-inference-server not found at $REPO_DIR"
    exit 1
fi

# Check if FLUX weights are cached.
# HuggingFace CLI stores repos as models--{org}--{name}, preserving dots in the name.
# e.g. black-forest-labs/FLUX.1-dev → models--black-forest-labs--FLUX.1-dev
HF_REPO="black-forest-labs/${MODEL}"
HF_CACHE_DIR="$HF_CACHE/hub/models--black-forest-labs--${MODEL}"
if [[ ! -d "$HF_CACHE_DIR" ]]; then
    echo "WARNING: FLUX weights not found at $HF_CACHE_DIR"
    echo "         Pre-download with:"
    echo "           huggingface-cli download ${HF_REPO}"
    echo "         (~20 GB for FLUX.1-dev, ~7 GB for FLUX.1-schnell)"
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
    --host-hf-cache "$HF_CACHE" &
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
echo "Tip: the server prints 'Application startup complete' when ready (~3–5 min)."
echo ""
echo "API quick test (once ready):"
echo "  curl -s -X POST http://localhost:8000/v1/images/generations \\"
echo "    -H 'Authorization: Bearer \$(grep AUTHORIZATION_TOKEN ~/code/tt-inference-server/.env | cut -d= -f2)' \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"prompt\":\"a red apple\",\"guidance_scale\":3.5}' \\"
echo "    | python3 -c \"import sys,json,base64; d=json.load(sys.stdin); open('/tmp/test.jpg','wb').write(base64.b64decode(d['images'][0]))\""
echo "  xdg-open /tmp/test.jpg"
echo ""

if [[ $GUI_MODE -eq 1 ]]; then
    # In GUI mode the caller monitors readiness via the health check endpoint.
    echo "Server started in Docker. GUI health check will detect when ready."
    exit 0
fi

# Interactive mode: tail the log indefinitely (Ctrl-C to stop; server keeps running in Docker).
echo "(Ctrl-C to stop tailing — server keeps running in Docker)"
tail -f "$LOG_FILE"
