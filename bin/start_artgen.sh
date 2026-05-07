#!/usr/bin/env bash
# start_artgen.sh — Start a text/chat LLM for the artgen feature on port 8002.
#
# Supported models (mid-sized):
#
#   Model                       Device    Docker image
#   --------------------------  --------  -----------------------------------------------
#   Qwen3-8B (default)          p300      qb2_launch-555f240-22be241  (available locally)
#   Llama-3.1-8B-Instruct       p300x2    qb2_launch-555f240-22be241  (available locally)
#   Llama-3.3-70B-Instruct      p300x2    qb2_launch-555f240-22be241  (~/models/ HF cache)
#   Qwen2.5-7B-Instruct         n300      0.12.0-5b5db8a-e771fff  (requires N300 hardware)
#
# The artgen CLI and GUI use port 8002 for the chat/text LLM so it runs alongside
# the diffusion server on port 8000 without conflict.
#
# Usage:
#   ./start_artgen.sh                                   # Qwen3-8B on P300
#   ./start_artgen.sh --model Llama-3.1-8B-Instruct
#   ./start_artgen.sh --model Llama-3.3-70B-Instruct
#   ./start_artgen.sh --model Qwen2.5-7B-Instruct --device n300
#   ./start_artgen.sh --stop                            # stop any container on port 8002
#   ./start_artgen.sh --gui                             # non-interactive (for GUI launcher)
#   ./start_artgen.sh --help                            # show this help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Prefer vendored tt-inference-server; fall back to developer checkout.
if [[ -d "$REPO_ROOT/vendor/tt-inference-server" ]]; then
    REPO_DIR="$REPO_ROOT/vendor/tt-inference-server"
else
    REPO_DIR="$HOME/code/tt-inference-server"
fi

HF_CACHE="$HOME/.cache/huggingface"
SERVICE_PORT=8002
LOG_DIR="$REPO_DIR/workflow_logs/docker_server"
_GHCR="ghcr.io/tenstorrent/tt-inference-server/vllm-tt-metal-src-release-ubuntu-22.04-amd64"
# QB2-targeted release image (locally available on this machine):
_QB2_IMAGE="$_GHCR:qb2_launch-555f240-22be241"

# ── Defaults ──────────────────────────────────────────────────────────────────

MODEL="Qwen3-8B"
DEVICE=""        # empty = derive from model after validation
GUI_MODE=0

# ── Parse flags ───────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            sed -n '2,23p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        --stop)
            # Prefer stopping by image (set after model validation); fall back to port.
            # Since --stop is parsed in the same pass as --model, DOCKER_IMAGE may not
            # be set yet — so we stop by published port which is always unambiguous.
            RUNNING=$(docker ps --filter "publish=$SERVICE_PORT" --format "{{.ID}}" 2>/dev/null)
            if [[ -z "$RUNNING" ]]; then
                echo "No running container found on port $SERVICE_PORT."
                exit 0
            fi
            echo "Stopping container(s) on port $SERVICE_PORT: $RUNNING"
            echo "$RUNNING" | xargs docker stop
            echo "Server stopped."
            exit 0
            ;;
        --gui)
            GUI_MODE=1
            ;;
        --model)
            MODEL="${2:?--model requires an argument}"
            shift
            ;;
        --device)
            DEVICE="${2:?--device requires an argument}"
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Run with --help for usage."
            exit 1
            ;;
    esac
    shift
done

# ── Resolve model → image + default device ───────────────────────────────────
# Images sourced from model_spec.json in tt-inference-server.

case "$MODEL" in
    Qwen3-8B)
        DOCKER_IMAGE="$_QB2_IMAGE"
        DEFAULT_DEVICE="p300"      # P300X2 machine: one P300 (Qwen3-8B has no P300X2 spec)
        DEFAULT_DEVICE_IDS="0,1"   # Pin to first P300 card (chips 0+1); avoid chip 2 if stuck
        ;;
    Llama-3.1-8B-Instruct)
        DOCKER_IMAGE="$_QB2_IMAGE"
        DEFAULT_DEVICE="p300x2"    # Confirmed P300X2 spec in model_spec.json
        DEFAULT_DEVICE_IDS=""
        ;;
    Llama-3.3-70B-Instruct)
        # P300X2 spec: same qb2_launch image (555f240-22be241 commit), ARCH=blackhole, MESH_DEVICE=P300x2
        DOCKER_IMAGE="$_QB2_IMAGE"
        DEFAULT_DEVICE="p300x2"
        DEFAULT_DEVICE_IDS=""
        # Model lives in ~/models/ (HF hub cache layout), not ~/.cache/huggingface
        HF_CACHE="$HOME/models"
        ;;
    Qwen2.5-7B-Instruct)
        DOCKER_IMAGE="$_GHCR:0.12.0-5b5db8a-e771fff"
        DEFAULT_DEVICE="n300"      # N300/N150X4 only; not for P300X2 machines
        DEFAULT_DEVICE_IDS=""
        ;;
    *)
        echo "ERROR: Unknown model '$MODEL'."
        echo "  Supported: Qwen3-8B (default), Llama-3.1-8B-Instruct, Llama-3.3-70B-Instruct, Qwen2.5-7B-Instruct"
        exit 1
        ;;
esac

[[ -z "$DEVICE" ]] && DEVICE="$DEFAULT_DEVICE"
DEVICE_IDS="${DEFAULT_DEVICE_IDS:-}"

# ── Sanity checks ─────────────────────────────────────────────────────────────

if [[ ! -d "$REPO_DIR" ]]; then
    echo "ERROR: tt-inference-server not found at $REPO_DIR"
    echo "Run: ./apply_patches.sh  (which clones vendor/tt-inference-server)"
    exit 1
fi

# ── Check for a container already on this port ────────────────────────────────

EXISTING=$(docker ps --filter "publish=$SERVICE_PORT" --format "{{.ID}}" 2>/dev/null | head -1)
if [[ -n "$EXISTING" ]]; then
    echo "Server already running in container $EXISTING on port $SERVICE_PORT"
    echo ""
    if [[ $GUI_MODE -eq 1 ]]; then
        echo "Server is already up. GUI health check will confirm readiness."
        exit 0
    fi
    LATEST_LOG=$(ls -t "$LOG_DIR"/vllm_*_"${MODEL}"_"${DEVICE}"_server.log 2>/dev/null | head -1 || true)
    if [[ -n "$LATEST_LOG" ]]; then
        echo "Tailing log: $LATEST_LOG"
        echo "(Ctrl-C to stop tailing — server keeps running)"
        echo ""
        exec tail -f "$LATEST_LOG"
    else
        echo "  Logs: docker logs -f $EXISTING"
        echo "  Stop: ./start_artgen.sh --stop"
    fi
    exit 0
fi

# ── Launch ────────────────────────────────────────────────────────────────────
# artgen is a local dev tool — no auth required.  --no-auth skips the
# JWT_SECRET requirement so we don't need it in .env.

echo "Starting $MODEL (artgen chat LLM) on device $DEVICE, port $SERVICE_PORT …"
echo "  Repo:      $REPO_DIR"
echo "  Image:     $DOCKER_IMAGE"
echo "  HF cache:  $HF_CACHE  (bind-mounted read-only)"
echo "  Port:      $SERVICE_PORT"
echo "  Device:    $DEVICE"
echo ""

mkdir -p "$LOG_DIR"
START_TS=$(date +%s)

cd "$REPO_DIR"

DEVICE_ID_ARG=()
[[ -n "$DEVICE_IDS" ]] && DEVICE_ID_ARG=(--device-id "$DEVICE_IDS")

MODEL_SOURCE=huggingface python3 run.py \
    --model "$MODEL" \
    --workflow server \
    --tt-device "$DEVICE" \
    --impl tt-transformers \
    --engine vllm \
    --docker-server \
    --override-docker-image "$DOCKER_IMAGE" \
    --no-auth \
    --service-port "$SERVICE_PORT" \
    --host-hf-cache "$HF_CACHE" \
    "${DEVICE_ID_ARG[@]}" &
WORKFLOW_PID=$!

echo "Workflow PID: $WORKFLOW_PID"
echo "Waiting for server to start …"
echo ""

wait "$WORKFLOW_PID"
WORKFLOW_EXIT=$?

if [[ $WORKFLOW_EXIT -ne 0 ]]; then
    echo "ERROR: Workflow process exited with code $WORKFLOW_EXIT."
    LATEST=$(ls -t "$LOG_DIR"/vllm_*_"${MODEL}"_*.log 2>/dev/null | head -1 || true)
    [[ -n "$LATEST" ]] && { echo "Last log: $LATEST"; echo ""; tail -50 "$LATEST"; }
    exit 1
fi

# Find the log file created by this run (mtime >= start timestamp).
# run.py names vLLM server logs: vllm_{timestamp}_{model}_{device}_server.log
LOG_FILE=$(ls -t "$LOG_DIR"/vllm_*.log 2>/dev/null \
           | while read -r f; do
               mtime=$(stat -c %Y "$f" 2>/dev/null || echo 0)
               [[ $mtime -ge $START_TS ]] && echo "$f" && break
             done || true)

if [[ -z "$LOG_FILE" ]]; then
    echo "WARNING: Could not find a new log file in $LOG_DIR"
    echo "  Check: docker logs -f \$(docker ps -lq)"
    exit 0
fi

echo "Log file: $LOG_FILE"
echo ""
echo "Tip: the server prints 'Application startup complete' when ready."
echo ""

if [[ $GUI_MODE -eq 1 ]]; then
    echo "Server started in Docker. GUI health check (http://localhost:$SERVICE_PORT/health) will detect readiness."
    exit 0
fi

echo "(Ctrl-C to stop tailing — server keeps running in Docker)"
tail -f "$LOG_FILE"
