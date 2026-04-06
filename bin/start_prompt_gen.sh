#!/usr/bin/env bash
# start_prompt_gen.sh — Start Qwen3-0.6B prompt generator on CPU (port 8001).
#
# Runs prompt_server.py, a lightweight transformers-based server that exposes
# an OpenAI-compatible chat API.  Runs entirely on CPU — does not touch the TT
# chips, so it coexists with a video generation server on port 8000.
#
# The system prompt for cinematic mad-libs prompt generation lives in:
#   prompts/prompt_generator.md
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
#   ./start_prompt_gen.sh          # start server in background, tail log
#   ./start_prompt_gen.sh --stop   # stop the running server
#   ./start_prompt_gen.sh --gui    # start silently (no tail, for GUI use)
#   ./start_prompt_gen.sh --help   # show this help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MODEL="Qwen/Qwen3-0.6B"
PORT=8001
PID_FILE="/tmp/tt_prompt_gen.pid"
LOG_FILE="/tmp/tt_prompt_gen.log"

# ── Parse flags ───────────────────────────────────────────────────────────────

GUI_MODE=0
for arg in "$@"; do
    case "$arg" in
        --help|-h)
            sed -n '2,24p' "$0" | sed 's/^# \?//'
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
    esac
done

# ── Check if already running ──────────────────────────────────────────────────

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Prompt generator already running (PID $(cat "$PID_FILE"), port $PORT)."
    if [[ $GUI_MODE -eq 1 ]]; then
        exit 0
    fi
    echo "  Logs: tail -f $LOG_FILE"
    echo "  Stop: $0 --stop"
    exec tail -f "$LOG_FILE"
fi

if lsof -ti:$PORT >/dev/null 2>&1; then
    echo "ERROR: Port $PORT is already in use."
    echo "  Check: lsof -i:$PORT"
    exit 1
fi

# ── Check weights (informational only — transformers auto-downloads) ──────────

HF_MODEL_DIR="$HOME/.cache/huggingface/hub/models--Qwen--Qwen3-0.6B"
if [[ ! -d "$HF_MODEL_DIR" ]]; then
    echo "NOTE: $MODEL not in HF cache — will download ~1.2 GB on first start."
fi

# ── Launch ─────────────────────────────────────────────────────────────────────

echo "Starting Qwen3-0.6B prompt generator on CPU…"
echo "  Model:  $MODEL"
echo "  Port:   $PORT"
echo "  Log:    $LOG_FILE"
echo ""

python3 "$REPO_ROOT/app/prompt_server.py" \
    --model "$MODEL" \
    --port "$PORT" \
    --host 127.0.0.1 \
    > "$LOG_FILE" 2>&1 &

SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

if [[ $GUI_MODE -eq 1 ]]; then
    # GUI just needs to know it launched — health polling happens in the UI
    echo "Server started (PID $SERVER_PID). GUI health check will detect readiness."
    exit 0
fi

echo "Waiting for server to be ready (model loads in ~10–30s)…"

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
        echo ""
        echo "Test it:"
        printf '  curl -s http://localhost:%d/v1/chat/completions \\\n' "$PORT"
        printf '    -H "Content-Type: application/json" \\\n'
        printf '    -d '"'"'{"model":"%s","messages":[' "$MODEL"
        printf '{"role":"system","content":"You write cinematic video prompts."},'
        printf '{"role":"user","content":"video: a lone wolf at dusk"}],"max_tokens":80}'"'"' \\\n'
        printf '    | python3 -m json.tool\n'
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
echo "Still loading — this can take up to 3 min on first run (model compilation)."
echo "  Watch: tail -f $LOG_FILE"
echo "  Stop:  $0 --stop"
