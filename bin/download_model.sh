#!/bin/bash
# bin/download_model.sh — HuggingFace model downloader for tt-local-generator
#
# Used by postinst scripts of the tt-model-* packages and exposed as
# /usr/bin/tt-local-gen-download-model for manual re-runs.
#
# Usage:
#   download_model.sh --repo <org/model> [--token <hf_token>]
#                     [--skip-if-exists] [--check-only]
#
# Options:
#   --repo  <org/model>   HuggingFace repo ID (required)
#   --token <hf_token>    HF access token (optional; also searched in env/files)
#   --skip-if-exists      Exit 0 silently if model dir already present in HF cache
#   --check-only          Exit 0 if present, exit 1 if not — no download attempted
#
# Token search order (when --token is not supplied):
#   1. $HF_TOKEN env var
#   2. $HUGGING_FACE_HUB_TOKEN env var
#   3. ${HF_HOME:-~/.cache/huggingface}/token  (written by huggingface-cli login)
#   4. ~/.huggingface/token                    (legacy location)
#   5. /etc/tt-generator/hf_token             (admin-staged / CI path)
#
# huggingface-cli discovery order:
#   1. ~/.tenstorrent-venv/bin/huggingface-cli (tt-installer venv)
#   2. system huggingface-cli on $PATH
#   3. pip-install huggingface_hub[cli] into system Python3 and retry

set -euo pipefail

# ── Argument parsing ───────────────────────────────────────────────────────────
REPO=""
TOKEN=""
SKIP_IF_EXISTS=false
CHECK_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)
            REPO="$2"; shift 2 ;;
        --token)
            TOKEN="$2"; shift 2 ;;
        --skip-if-exists)
            SKIP_IF_EXISTS=true; shift ;;
        --check-only)
            CHECK_ONLY=true; shift ;;
        --help|-h)
            sed -n '2,/^set -/p' "$0" | grep '^#' | sed 's/^# \?//'
            exit 0 ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            echo "Usage: $0 --repo <org/model> [--token <tok>] [--skip-if-exists] [--check-only]" >&2
            exit 1 ;;
    esac
done

if [[ -z "$REPO" ]]; then
    echo "ERROR: --repo is required." >&2
    exit 1
fi

# ── Resolve HF cache root ──────────────────────────────────────────────────────
# Honour $HF_HOME if set; otherwise fall back to ~/.cache/huggingface.
HF_CACHE="${HF_HOME:-${HOME:-/root}/.cache/huggingface}"

# Convert "org/model" → "models--org--model" (HF cache convention).
_cache_slug() {
    echo "models--$(echo "$1" | tr '/' '--')"
}

MODEL_CACHE_DIR="$HF_CACHE/hub/$(_cache_slug "$REPO")"

# ── --check-only / --skip-if-exists fast path ─────────────────────────────────
if [[ -d "$MODEL_CACHE_DIR" ]]; then
    if $CHECK_ONLY || $SKIP_IF_EXISTS; then
        echo "Model already present: $MODEL_CACHE_DIR"
        exit 0
    fi
else
    if $CHECK_ONLY; then
        echo "Model not found: $MODEL_CACHE_DIR"
        exit 1
    fi
fi

# ── Token discovery ────────────────────────────────────────────────────────────
# Only search if --token was not supplied on the command line.
if [[ -z "$TOKEN" ]]; then
    if [[ -n "${HF_TOKEN:-}" ]]; then
        TOKEN="$HF_TOKEN"
        echo "Using token from \$HF_TOKEN"
    elif [[ -n "${HUGGING_FACE_HUB_TOKEN:-}" ]]; then
        TOKEN="$HUGGING_FACE_HUB_TOKEN"
        echo "Using token from \$HUGGING_FACE_HUB_TOKEN"
    else
        # Check well-known token file locations.
        for _tok_file in \
            "${HF_HOME:-${HOME:-/root}/.cache/huggingface}/token" \
            "${HOME:-/root}/.huggingface/token" \
            "/etc/tt-generator/hf_token"
        do
            if [[ -f "$_tok_file" ]]; then
                TOKEN="$(cat "$_tok_file")"
                echo "Using token from $_tok_file"
                break
            fi
        done
    fi
fi

# ── huggingface-cli discovery ──────────────────────────────────────────────────
_find_hf_cli() {
    # 1. tt-installer venv (most up-to-date on QB2 hosts)
    local _venv_cli="${HOME:-/root}/.tenstorrent-venv/bin/huggingface-cli"
    if [[ -x "$_venv_cli" ]]; then
        echo "$_venv_cli"
        return 0
    fi

    # 2. System huggingface-cli
    if command -v huggingface-cli &>/dev/null; then
        echo "$(command -v huggingface-cli)"
        return 0
    fi

    return 1
}

HF_CLI=""
if HF_CLI="$(_find_hf_cli)"; then
    echo "Found huggingface-cli: $HF_CLI"
else
    echo "huggingface-cli not found. Installing huggingface_hub[cli] via pip…"
    /usr/bin/python3 -m pip install --quiet --break-system-packages \
        "huggingface_hub[cli]>=0.24"
    # Retry discovery after install.
    if HF_CLI="$(_find_hf_cli)"; then
        echo "Found huggingface-cli after install: $HF_CLI"
    else
        echo "ERROR: huggingface-cli still not found after pip install." >&2
        echo "Install manually: pip install 'huggingface_hub[cli]'" >&2
        exit 1
    fi
fi

# ── Download ───────────────────────────────────────────────────────────────────
echo ""
echo "Downloading model: $REPO"
echo "Cache target:      $MODEL_CACHE_DIR"
[[ -n "$TOKEN" ]] && echo "Auth:              HF token provided"
echo ""

# Build the download command; add --token only when we have one.
DOWNLOAD_CMD=("$HF_CLI" download "$REPO")
if [[ -n "$TOKEN" ]]; then
    DOWNLOAD_CMD+=(--token "$TOKEN")
fi

# Run the download. Exit status is propagated to the caller.
"${DOWNLOAD_CMD[@]}"

echo ""
echo "Download complete: $REPO"
echo "Cache:             $MODEL_CACHE_DIR"
