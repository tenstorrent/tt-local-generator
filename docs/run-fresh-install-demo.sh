#!/usr/bin/env bash
# run-fresh-install-demo.sh
#
# Wrapper called by demo-fresh-install.tape.
# Manages the qb2-env container lifetime for the VHS recording.
# HF_TOKEN is read from the environment — never printed to stdout.
#
# The tape calls specific functions via:
#   ./docs/run-fresh-install-demo.sh start
#   ./docs/run-fresh-install-demo.sh clone
#   ./docs/run-fresh-install-demo.sh status
#   ./docs/run-fresh-install-demo.sh quickstart
#   ./docs/run-fresh-install-demo.sh health
#   ./docs/run-fresh-install-demo.sh stop

set -euo pipefail

STATE_FILE="/tmp/tt-fresh-install-demo-container"
HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}/hub"
IMAGE="tenstorrent/qb2-env:latest"

_cid() { cat "$STATE_FILE" 2>/dev/null || echo ""; }

_exec() {
    local cid
    cid=$(_cid)
    [[ -z "$cid" ]] && { echo "ERROR: container not running"; exit 1; }
    # Use -it only when stdin is a terminal (VHS recording / interactive use)
    if [[ -t 0 ]]; then
        docker exec -it "$cid" bash -c "$*"
    else
        docker exec -i "$cid" bash -c "$*"
    fi
}

case "${1:-help}" in

  start)
    echo "Starting QB2 container…"
    # Pip wheel cache dir on host (avoids re-downloading large wheels like torch)
    PIP_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/pip"
    mkdir -p "$PIP_CACHE"
    # HF_TOKEN passed via -e but never echoed to terminal
    cid=$(docker run -d --rm \
      -e "HF_TOKEN=${HF_TOKEN:-}" \
      -e "HF_HUB_CACHE=/home/ttuser/.cache/huggingface/hub" \
      -e "PIP_CACHE_DIR=/pip-cache" \
      -v "${HF_CACHE}:/home/ttuser/.cache/huggingface/hub:ro" \
      -v "${PIP_CACHE}:/pip-cache:rw" \
      "$IMAGE" sleep 600)
    echo "$cid" > "$STATE_FILE"
    echo "Container ready."
    ;;

  clone)
    _exec 'mkdir -p /home/ttuser/code && \
      git clone --depth 1 --branch feat/animatediff-v0.9 \
        https://github.com/tenstorrent/tt-local-generator.git \
        /home/ttuser/code/tt-local-generator 2>&1 | tail -5'
    ;;

  status)
    _exec 'cd /home/ttuser/code/tt-local-generator && ./bin/quickstart.sh --status'
    ;;

  quickstart)
    _exec 'cd /home/ttuser/code/tt-local-generator && ./bin/quickstart.sh'
    ;;

  health)
    _exec 'curl -s http://localhost:8001/health | python3 -m json.tool'
    ;;

  stop)
    cid=$(_cid)
    if [[ -n "$cid" ]]; then
        docker stop "$cid" 2>/dev/null || true
        rm -f "$STATE_FILE"
        echo "Container stopped."
    fi
    ;;

  *)
    echo "Usage: $0 {start|clone|status|quickstart|health|stop}"
    exit 1
    ;;
esac
