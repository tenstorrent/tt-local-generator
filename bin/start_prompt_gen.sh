#!/usr/bin/env bash
# start_prompt_gen.sh — Start Qwen3 prompt generator on CPU (port 8001).
#
# Runs prompt_server.py, a lightweight transformers-based server that exposes
# an OpenAI-compatible chat API.  Runs entirely on CPU — does not touch the TT
# chips, so it coexists with a video generation server on port 8000.
#
# Model selection (in priority order):
#   --model MODEL_ID          CLI flag overrides everything
#   PROMPT_MODEL env var      e.g. PROMPT_MODEL=Qwen/Qwen3-1.7B ./start_prompt_gen.sh
#   default: Qwen/Qwen3-0.6B  (fast, ~1.2 GB, ~19 tok/s on Ryzen 7 9700X)
#
# Hot-swap a running server (no restart needed):
#   ./start_prompt_gen.sh --swap-model Qwen/Qwen3-1.7B
#   ./start_prompt_gen.sh --swap-model Qwen/Qwen3-0.6B   # swap back
#
# Quick test after starting:
#   curl -s http://localhost:8001/health
#   curl -s http://localhost:8001/v1/chat/completions \
#     -H "Content-Type: application/json" \
#     -d "{\"model\":\"Qwen/Qwen3-0.6B\",\"messages\":[
#           {\"role\":\"system\",\"content\":\"$(cat prompts/prompt_generator.md)\"},
#           {\"role\":\"user\",\"content\":\"video: a fox in a snowy forest at dusk\"}
#         ],\"max_tokens\":150}" | python3 -m json.tool
#
# Usage:
#   ./start_prompt_gen.sh                              # start with default model
#   ./start_prompt_gen.sh --model Qwen/Qwen3-1.7B     # start with 1.7B model
#   ./start_prompt_gen.sh --stop                       # stop the running server
#   ./start_prompt_gen.sh --swap-model Qwen/Qwen3-1.7B # hot-swap model in place
#   ./start_prompt_gen.sh --gui                        # start silently (for GUI use)
#   ./start_prompt_gen.sh --help                       # show this help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Model selection: --model flag > PROMPT_MODEL env > default
MODEL="${PROMPT_MODEL:-Qwen/Qwen3-0.6B}"
PORT=8001
PID_FILE="/tmp/tt_prompt_gen.pid"
LOG_FILE="/tmp/tt_prompt_gen.log"

# ── Parse flags ───────────────────────────────────────────────────────────────

GUI_MODE=0
SWAP_MODEL=""
i=1
while [[ $i -le $# ]]; do
    arg="${!i}"
    case "$arg" in
        --help|-h)
            sed -n '2,30p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        --stop)
            if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
                PID=$(cat "$PID_FILE")
                echo "Stopping prompt generator (PID $PID)…"
                kill "$PID"
                rm -f "$PID_FILE"
                echo "Stopped."
            else
                PIDS=$(lsof -ti:$PORT 2>/dev/null || true)
                if [[ -n "$PIDS" ]]; then
                    echo "Stopping process on port $PORT…"
                    echo "$PIDS" | xargs kill
                    echo "Stopped."
                else
                    echo "No prompt generator running."
                fi
                rm -f "$PID_FILE"
            fi
            exit 0
            ;;
        --gui)
            GUI_MODE=1
            ;;
        --model)
            i=$((i+1))
            MODEL="${!i}"
            ;;
        --model=*)
            MODEL="${arg#--model=}"
            ;;
        --swap-model)
            i=$((i+1))
            SWAP_MODEL="${!i}"
            ;;
        --swap-model=*)
            SWAP_MODEL="${arg#--swap-model=}"
            ;;
    esac
    i=$((i+1))
done

# ── Hot-swap a running server ─────────────────────────────────────────────────

if [[ -n "$SWAP_MODEL" ]]; then
    if ! curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
        echo "ERROR: Prompt server not running on port $PORT."
        echo "  Start it first: $0"
        exit 1
    fi
    CURRENT=$(curl -s "http://127.0.0.1:$PORT/health" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('model','unknown'))" 2>/dev/null || echo "unknown")
    echo "Requesting hot-swap: $CURRENT → $SWAP_MODEL"
    RESULT=$(curl -s -w "\n%{http_code}" -X POST "http://127.0.0.1:$PORT/v1/swap-model" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$SWAP_MODEL\"}")
    HTTP_CODE=$(echo "$RESULT" | tail -1)
    BODY=$(echo "$RESULT" | head -1)
    if [[ "$HTTP_CODE" == "202" ]]; then
        echo "Swap started. Model will be unavailable during load (~30–90s for 1.7B)."
        echo ""
        echo "Monitor progress:"
        echo "  watch -n3 'curl -s http://127.0.0.1:$PORT/health | python3 -m json.tool'"
        echo ""
        echo "Or poll until ready:"
        echo "  until curl -sf http://127.0.0.1:$PORT/health | python3 -c \\"
        echo "    \"import sys,json; d=json.load(sys.stdin); exit(0 if d.get('model_ready') else 1)\"; do sleep 5; done"
    elif [[ "$HTTP_CODE" == "200" ]]; then
        echo "Already loaded: $SWAP_MODEL (no swap needed)."
    elif [[ "$HTTP_CODE" == "409" ]]; then
        echo "Swap already in progress. Check: curl http://127.0.0.1:$PORT/health"
    else
        echo "Swap request failed (HTTP $HTTP_CODE): $BODY"
        exit 1
    fi
    exit 0
fi

# ── Check if already running ──────────────────────────────────────────────────

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Prompt generator already running (PID $(cat "$PID_FILE"), port $PORT)."
    RUNNING_MODEL=$(curl -s "http://127.0.0.1:$PORT/health" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('model','unknown'))" 2>/dev/null || echo "unknown")
    echo "  Model: $RUNNING_MODEL"
    if [[ $GUI_MODE -eq 1 ]]; then
        exit 0
    fi
    echo "  Logs: tail -f $LOG_FILE"
    echo "  Stop: $0 --stop"
    echo "  Swap: $0 --swap-model Qwen/Qwen3-1.7B"
    exec tail -f "$LOG_FILE"
fi

if lsof -ti:$PORT >/dev/null 2>&1; then
    echo "ERROR: Port $PORT is already in use."
    echo "  Check: lsof -i:$PORT"
    exit 1
fi

# ── Check weights (informational only — transformers auto-downloads) ──────────

# Derive the HF cache dir name from the model ID (Org/Name → models--Org--Name)
HF_MODEL_CACHE_NAME="$(echo "$MODEL" | sed 's|/|--|g')"
HF_MODEL_DIR="${HF_HUB_CACHE:-$HOME/.cache/huggingface/hub}/models--$HF_MODEL_CACHE_NAME"
if [[ ! -d "$HF_MODEL_DIR" ]]; then
    echo "NOTE: $MODEL not in HF cache — will download on first start."
    if [[ "$MODEL" == *"1.7B"* ]]; then
        echo "      (~3.4 GB download for 1.7B)"
    fi
fi

# ── Launch ─────────────────────────────────────────────────────────────────────

echo "Starting Qwen3 prompt generator on CPU…"
echo "  Model:  $MODEL"
echo "  Port:   $PORT"
echo "  Log:    $LOG_FILE"
echo ""

# Use the tenstorrent venv python which has torch/transformers; fall back to
# system python3 if the venv is not present.
PYTHON3="${HOME}/.tenstorrent-venv/bin/python3"
[[ -x "$PYTHON3" ]] || PYTHON3="python3"

"$PYTHON3" "$REPO_ROOT/app/prompt_server.py" \
    --model "$MODEL" \
    --port "$PORT" \
    --host "${PROMPT_HOST:-0.0.0.0}" \
    > "$LOG_FILE" 2>&1 &

SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

if [[ $GUI_MODE -eq 1 ]]; then
    # GUI just needs to know it launched — health polling happens in the UI
    echo "Server started (PID $SERVER_PID). GUI health check will detect readiness."
    exit 0
fi

echo "Waiting for server to be ready (model loads in ~10–90s depending on size)…"

for i in $(seq 1 60); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo ""
        echo "ERROR: Server exited early. Last log lines:"
        tail -20 "$LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
    if curl -sf "http://127.0.0.1:$PORT/health" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('model_ready') else 1)" 2>/dev/null; then
        echo ""
        echo "✓ Prompt generator ready at http://127.0.0.1:$PORT"
        echo "  Model: $MODEL"
        echo ""
        echo "Hot-swap to 1.7B:"
        echo "  $0 --swap-model Qwen/Qwen3-1.7B"
        echo ""
        echo "Stop: $0 --stop"
        echo ""
        echo "(Ctrl-C — server keeps running in background)"
        exit 0
    fi
    printf "."
    sleep 3
done

echo ""
echo "Still loading — this can take up to 3 min on first run (model download)."
echo "  Watch: tail -f $LOG_FILE"
echo "  Stop:  $0 --stop"
