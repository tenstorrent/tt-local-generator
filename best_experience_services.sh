#!/usr/bin/env bash
# best_experience_services.sh — Start or stop the full tt-local-generator service stack.
#
# "Best experience" means both services running together:
#   - Wan2.2-T2V-A14B-Diffusers inference server (port 8000, TT chips)
#   - Qwen3-0.6B prompt generator (port 8001, CPU)
#
# Usage:
#   ./best_experience_services.sh start   # start both services
#   ./best_experience_services.sh stop    # stop both services
#   ./best_experience_services.sh status  # show running state of each
#   ./best_experience_services.sh --help  # show this help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    sed -n '2,10p' "$0" | sed 's/^# \?//'
}

# ── Status helpers ────────────────────────────────────────────────────────────

wan_running() {
    docker ps --filter "ancestor=ghcr.io/tenstorrent/tt-media-inference-server:0.11.1-bac8b34" \
        --format "{{.ID}}" 2>/dev/null | grep -q .
}

prompt_gen_running() {
    local pid_file="/tmp/tt_prompt_gen.pid"
    [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

# ── Commands ──────────────────────────────────────────────────────────────────

cmd_status() {
    echo "Service status:"
    if wan_running; then
        echo "  ● Wan2.2 inference server  — running (port 8000)"
    else
        echo "  ○ Wan2.2 inference server  — stopped"
    fi

    if prompt_gen_running; then
        echo "  ● Prompt generator         — running (port 8001)"
    else
        echo "  ○ Prompt generator         — stopped"
    fi
}

cmd_start() {
    echo "Starting tt-local-generator services…"
    echo ""

    # ── Wan2.2 inference server ────────────────────────────────────────────
    if wan_running; then
        echo "  ● Wan2.2 already running — skipping"
    else
        echo "  Starting Wan2.2 inference server (--gui, background)…"
        "$SCRIPT_DIR/start_wan.sh" --gui
        echo "  ✓ Wan2.2 server started — loading model in background (~5 min)"
        echo "    Watch: tail -f ~/code/tt-inference-server/workflow_logs/docker_server/media_*.log | grep -v 'tt-liveness'"
    fi

    echo ""

    # ── Prompt generator ──────────────────────────────────────────────────
    if prompt_gen_running; then
        echo "  ● Prompt generator already running — skipping"
    else
        echo "  Starting Qwen3-0.6B prompt generator (background)…"
        "$SCRIPT_DIR/start_prompt_gen.sh" --gui
        echo "  ✓ Prompt generator started — will be ready in ~10–30s"
        echo "    Watch: tail -f /tmp/tt_prompt_gen.log"
    fi

    echo ""
    echo "Both services started. The Wan2.2 server takes ~5 min to finish loading."
    echo "Run './best_experience_services.sh status' to check state, or open the app:"
    echo "  /usr/bin/python3 $SCRIPT_DIR/main.py"
}

cmd_stop() {
    echo "Stopping tt-local-generator services…"
    echo ""

    # ── Wan2.2 inference server ────────────────────────────────────────────
    if wan_running; then
        echo "  Stopping Wan2.2 inference server…"
        "$SCRIPT_DIR/start_wan.sh" --stop
    else
        echo "  ○ Wan2.2 not running — skipping"
    fi

    echo ""

    # ── Prompt generator ──────────────────────────────────────────────────
    if prompt_gen_running; then
        echo "  Stopping prompt generator…"
        "$SCRIPT_DIR/start_prompt_gen.sh" --stop
    else
        echo "  ○ Prompt generator not running — skipping"
    fi

    echo ""
    echo "All services stopped."
}

# ── Main ──────────────────────────────────────────────────────────────────────

case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    --help|-h) usage ;;
    *)
        echo "Usage: $0 {start|stop|status}"
        echo "Run '$0 --help' for details."
        exit 1
        ;;
esac
