#!/usr/bin/env bash
# quickstart.sh — Single-command setup for tt-local-generator (CPU-only path).
#
# Gets you to a working state in one shot:
#   1. Python deps (torch, transformers, fastapi, uvicorn, markovify)
#   2. Vendor clone  (vendor/tt-inference-server at pinned SHA)
#   3. Vendor .env   (JWT_SECRET + optional HF_TOKEN)
#   4. Patches       (hotpatches injected into vendor tree)
#   5. GTK4/PyGObject (informational — GUI only)
#   6. Prompt server  (Qwen3-0.6B on CPU, port 8001)
#   7. End-to-end validation (send a test prompt, verify inference works)
#
# "CPU-only" means: prompt server (port 8001) running + GUI launchable.
# Validation proves the model actually responds — not just that the server started.
# Everything else (video, image, artgen LLM) needs TT hardware + Docker.
#
# Usage:
#   ./bin/quickstart.sh                    # full check-and-fix + start server
#   ./bin/quickstart.sh --status           # checks only, no installs
#   ./bin/quickstart.sh --non-interactive  # no prompts; uses $HF_TOKEN env
#   ./bin/quickstart.sh --help

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Colours ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    _GRN="\033[32m" _YLW="\033[33m" _RED="\033[31m" _CYN="\033[36m" _RST="\033[0m" _BLD="\033[1m"
else
    _GRN="" _YLW="" _RED="" _CYN="" _RST="" _BLD=""
fi
ok()   { echo -e "${_GRN}  ✓${_RST} $*"; }
warn() { echo -e "${_YLW}  ⚠${_RST} $*"; }
fail() { echo -e "${_RED}  ✗${_RST} $*"; }
info() { echo -e "    $*"; }
step() { echo -e "\n${_BLD}${_CYN}[$1]${_RST}${_BLD} $2${_RST}"; }
hr()   { echo -e "${_CYN}────────────────────────────────────────────────────${_RST}"; }

# ── Flags ─────────────────────────────────────────────────────────────────────

STATUS_ONLY=0
NON_INTERACTIVE=0
LOG_FILE="/tmp/tt-quickstart.log"

# Auto non-interactive when stdin is not a terminal (ssh, CI, pipe)
[[ -t 0 ]] || NON_INTERACTIVE=1

while [[ ${1:-} != "" ]]; do
    case "$1" in
        --status)           STATUS_ONLY=1 ;;
        --non-interactive)  NON_INTERACTIVE=1 ;;
        --help|-h)
            sed -n '2,20p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)  echo "Unknown flag: $1  (try --help)"; exit 1 ;;
    esac
    shift
done

# ── State tracking ────────────────────────────────────────────────────────────
# Each step appends to one of these arrays for the summary table.
declare -a PASSED=()
declare -a WARNED=()
declare -a FAILED=()

pass()  { PASSED+=("$1"); ok  "$1"; }
warn_s(){ WARNED+=("$1"); warn "$1"; }
fail_s(){ FAILED+=("$1"); fail "$1"; }

# ── Header ────────────────────────────────────────────────────────────────────
echo ""
hr
echo -e "  ${_BLD}TT Local Generator — Quick Start${_RST}"
if [[ $STATUS_ONLY -eq 1 ]]; then
    echo -e "  ${_YLW}Status check only — no changes will be made.${_RST}"
fi
hr

# ── Step 1: Python deps ───────────────────────────────────────────────────────
step 1 "Python dependencies (torch, transformers, fastapi, uvicorn, markovify)"

_PYTHON_DEPS_OK=1
if python3 -c "import torch, transformers, fastapi, uvicorn, markovify" 2>/dev/null; then
    pass "Python deps already installed"
else
    if [[ $STATUS_ONLY -eq 1 ]]; then
        fail_s "Python deps missing"
        info "Fix: pip3 install --break-system-packages torch transformers 'fastapi>=0.100.0' 'uvicorn[standard]>=0.23.0' markovify"
        _PYTHON_DEPS_OK=0
    else
        info "Installing missing packages (pip3 --break-system-packages)…"
        if pip3 install --break-system-packages \
               torch transformers \
               "fastapi>=0.100.0" "uvicorn[standard]>=0.23.0" \
               markovify \
               >> "$LOG_FILE" 2>&1; then
            pass "Python deps installed"
        else
            fail_s "Python deps install failed"
            info "See $LOG_FILE for details."
            info "Try manually: pip3 install --break-system-packages torch transformers fastapi 'uvicorn[standard]' markovify"
            _PYTHON_DEPS_OK=0
        fi
    fi
fi

# ── Step 2: Vendor clone ──────────────────────────────────────────────────────
step 2 "Vendor clone  (vendor/tt-inference-server @ pinned SHA)"

_VENDOR_OK=1
if "$SCRIPT_DIR/setup_vendor.sh" --check >> "$LOG_FILE" 2>&1; then
    pass "vendor/tt-inference-server is at the correct SHA"
else
    if [[ $STATUS_ONLY -eq 1 ]]; then
        fail_s "Vendor clone missing or at wrong SHA"
        info "Fix: ./bin/setup_vendor.sh"
        _VENDOR_OK=0
    else
        info "Cloning vendor/tt-inference-server…"
        _sv_flags=""
        [[ $NON_INTERACTIVE -eq 1 ]] && _sv_flags="--non-interactive" || true
        if "$SCRIPT_DIR/setup_vendor.sh" ${_sv_flags} >> "$LOG_FILE" 2>&1; then
            pass "Vendor cloned successfully"
        else
            fail_s "Vendor clone failed"
            info "See $LOG_FILE"
            info "Manual fix: ./bin/setup_vendor.sh"
            info "Or: git clone https://github.com/tenstorrent/tt-inference-server.git vendor/tt-inference-server"
            info "    then: git -C vendor/tt-inference-server checkout \$(cat vendor/VENDOR_SHA)"
            _VENDOR_OK=0
        fi
    fi
fi

# ── Step 3: Vendor .env ───────────────────────────────────────────────────────
step 3 "Vendor .env  (vendor/tt-inference-server/.env)"

_ENV_FILE="$REPO_ROOT/vendor/tt-inference-server/.env"
if [[ -f "$_ENV_FILE" ]]; then
    # Check JWT_SECRET is not empty/placeholder
    _jwt=$(grep -E '^JWT_SECRET=' "$_ENV_FILE" | cut -d= -f2- | tr -d '[:space:]' || true)
    if [[ -z "$_jwt" || "$_jwt" == "changeme" || "$_jwt" == "your_secret_here" ]]; then
        warn_s ".env exists but JWT_SECRET looks like a placeholder"
        info "Edit $_ENV_FILE and set a real JWT_SECRET value."
    else
        pass ".env present with JWT_SECRET set"
    fi
else
    if [[ $_VENDOR_OK -eq 0 ]]; then
        warn_s ".env not checked — vendor clone failed above"
    else
        fail_s "vendor/.env missing despite vendor clone succeeding"
        info "Run: ./bin/setup_vendor.sh  (it creates the .env)"
    fi
fi

# ── Step 4: Patches ───────────────────────────────────────────────────────────
step 4 "Patches  (hotpatches injected into vendor tree)"

_PATCH_OK=1
if [[ $STATUS_ONLY -eq 1 ]]; then
    # In status mode, check whether the primary patch marker is present.
    # apply_patches.sh step 1 injects "tt_dit_patches_dir" into run_docker_server.py.
    _marker="tt_dit_patches_dir"
    _run_py="$REPO_ROOT/vendor/tt-inference-server/workflows/run_docker_server.py"
    if [[ -f "$_run_py" ]] && grep -q "$_marker" "$_run_py" 2>/dev/null; then
        pass "Patches already applied"
    else
        fail_s "Patches not applied (or vendor not cloned)"
        info "Fix: ./bin/apply_patches.sh"
        _PATCH_OK=0
    fi
else
    if [[ $_VENDOR_OK -eq 0 ]]; then
        warn_s "Skipping patches — vendor clone failed above"
        WARNED+=("Patches skipped")
    else
        info "Applying patches (idempotent — skips already-applied steps)…"
        _patch_log="/tmp/tt-quickstart-patches.log"
        if "$SCRIPT_DIR/apply_patches.sh" > "$_patch_log" 2>&1; then
            pass "Patches applied"
        else
            # apply_patches.sh exits non-zero on hard errors; check for ERROR lines
            if grep -q "^ERROR:" "$_patch_log"; then
                fail_s "Patch step failed"
                info "See $_patch_log for details."
                info "Common cause: vendor tree modified outside of quickstart."
                info "Fix: ./bin/apply_patches.sh  (re-run after inspecting log)"
                _PATCH_OK=0
            else
                # Non-zero exit but no ERROR lines = partial skip (already patched)
                pass "Patches applied (some steps already done)"
            fi
        fi
    fi
fi

# ── Step 5: GTK4 / PyGObject (informational) ─────────────────────────────────
step 5 "GTK4 / PyGObject  (GUI only — prompt server works without this)"

if python3 -c "import gi; gi.require_version('Gtk','4.0'); from gi.repository import Gtk" 2>/dev/null; then
    pass "GTK4 + PyGObject available"
else
    warn_s "GTK4 bindings not found — GUI (./tt-gen) will not open"
    info "Fix: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0"
fi

# ── Step 6: Prompt server (port 8001) ─────────────────────────────────────────
step 6 "Prompt server  (Qwen3-0.6B on CPU, port 8001)"

_health_check() {
    curl -sf "http://127.0.0.1:8001/health" \
         -o /dev/null --max-time 2 2>/dev/null
}

_model_ready() {
    python3 -c "
import urllib.request, json, sys
try:
    r = urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2)
    print(json.loads(r.read()).get('model_ready', False))
except: print(False)
" 2>/dev/null
}

if _health_check; then
    _ready="$(_model_ready)"
    if [[ "$_ready" == "True" ]]; then
        pass "Prompt server already running and model ready"
    else
        warn_s "Prompt server is up but model not yet loaded — still warming up"
        info "Check: curl -s http://localhost:8001/health"
    fi
elif [[ $STATUS_ONLY -eq 1 ]]; then
    fail_s "Prompt server not running"
    info "Start: ./bin/start_prompt_gen.sh"
elif [[ $_PYTHON_DEPS_OK -eq 0 ]]; then
    warn_s "Skipping server start — Python deps not installed"
    WARNED+=("Prompt server not started")
else
    info "Starting prompt server (first run downloads ~1.2 GB model)…"
    "$SCRIPT_DIR/start_prompt_gen.sh" --gui >> "$LOG_FILE" 2>&1 || true

    info "Waiting for model to load (up to 3 min on first run)…"
    _waited=0
    _max=180
    _dot_count=0
    while [[ $_waited -lt $_max ]]; do
        sleep 5
        _waited=$(( _waited + 5 ))
        if _health_check; then
            _ready="$(_model_ready)"
            if [[ "$_ready" == "True" ]]; then
                echo ""  # newline after progress dots
                pass "Prompt server ready  (${_waited}s)"
                break
            fi
        fi
        # Progress dots
        printf "."
        _dot_count=$(( _dot_count + 1 ))
        if [[ $(( _dot_count % 20 )) -eq 0 ]]; then
            printf " %ds\n    " "$_waited"
        fi
    done
    echo ""

    if ! _health_check; then
        fail_s "Prompt server did not start within ${_max}s"
        info "Log: /tmp/tt_prompt_gen.log"
        info "Last lines:"
        tail -5 /tmp/tt_prompt_gen.log 2>/dev/null | sed 's/^/    /'
        info "Retry: ./bin/start_prompt_gen.sh"
        FAILED+=("Prompt server")
    fi
fi

# ── Step 7: End-to-end model validation ──────────────────────────────────────
step 7 "End-to-end validation  (test prompt → Qwen3-0.6B → response)"

_validate_model() {
    # Sends a minimal chat completion request and checks for a non-empty reply.
    # Returns 0 (success) if a non-empty content string comes back.
    python3 - <<'PYEOF'
import urllib.request, json, sys

payload = json.dumps({
    "model": "Qwen3-0.6B",
    "messages": [
        {
            "role": "user",
            "content": (
                "You are a creative prompt generator for AI video models. "
                "Write exactly ONE short text-to-video prompt (max 30 words) "
                "depicting a serene natural scene. Reply with the prompt only, "
                "no labels, no explanation. /no_think"
            ),
        }
    ],
    "max_tokens": 60,
    "temperature": 0.7,
}).encode()

req = urllib.request.Request(
    "http://127.0.0.1:8001/v1/chat/completions",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if content:
        print(content)
        sys.exit(0)
    else:
        print("ERROR: empty response", file=sys.stderr)
        sys.exit(1)
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
}

# Only validate if the server is actually up and model-ready
_can_validate=0
if _health_check; then
    if python3 -c "
import urllib.request, json, sys
try:
    r = urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2)
    ready = json.loads(r.read()).get('model_ready', False)
    sys.exit(0 if ready else 1)
except: sys.exit(1)
" 2>/dev/null; then
        _can_validate=1
    fi
fi

if [[ $STATUS_ONLY -eq 1 ]]; then
    if [[ $_can_validate -eq 1 ]]; then
        info "Server is up — skipping live inference test in --status mode"
        pass "Model ready (validation skipped in status mode)"
    else
        warn_s "Server not ready — cannot validate"
    fi
elif [[ $_can_validate -eq 0 ]]; then
    warn_s "Skipping validation — prompt server not ready"
    WARNED+=("Model validation skipped")
else
    info "Sending test prompt to Qwen3-0.6B…"
    _val_out=$(_validate_model 2>/tmp/tt-quickstart-val.err)
    _val_rc=$?
    if [[ $_val_rc -eq 0 && -n "$_val_out" ]]; then
        pass "Model responded successfully"
        info "Sample output: ${_CYN}${_val_out}${_RST}"
    else
        _val_err=$(cat /tmp/tt-quickstart-val.err 2>/dev/null | head -3)
        fail_s "Model validation failed"
        [[ -n "$_val_err" ]] && info "Error: $_val_err"
        info "The server is running but inference returned an error."
        info "Check: curl -s http://localhost:8001/health"
        FAILED+=("Model validation")
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
hr
echo -e "  ${_BLD}Summary${_RST}"
hr

[[ ${#PASSED[@]}  -gt 0 ]] && for s in "${PASSED[@]}";  do echo -e "${_GRN}  ✓${_RST} $s"; done
[[ ${#WARNED[@]}  -gt 0 ]] && for s in "${WARNED[@]}";  do echo -e "${_YLW}  ⚠${_RST} $s"; done
[[ ${#FAILED[@]}  -gt 0 ]] && for s in "${FAILED[@]}";  do echo -e "${_RED}  ✗${_RST} $s"; done

echo ""
if [[ ${#FAILED[@]} -eq 0 ]]; then
    if [[ $STATUS_ONLY -eq 1 ]]; then
        echo -e "${_GRN}  Everything looks good.${_RST}"
    else
        echo -e "${_GRN}  ${_BLD}Ready!${_RST}  Launch the GUI: ${_CYN}./tt-gen${_RST}"
        echo -e "  Prompt API:   ${_CYN}curl -s http://localhost:8001/health${_RST}"
        echo -e "  Artgen tab:   works in algo/Markov mode (no TT hardware needed)"
        echo ""
        echo -e "  To start video generation you'll also need:"
        echo -e "    • TT hardware (Blackhole card)"
        echo -e "    • Docker CE  (sudo apt install docker-ce)"
        echo -e "    • Model weights  (~118 GB): huggingface-cli download Wan-AI/Wan2.2-T2V-A14B-Diffusers"
        echo -e "    • Then: ./bin/start_wan_qb2.sh"
    fi
else
    echo -e "${_RED}  Blocked on ${#FAILED[@]} step(s).${_RST}  See $LOG_FILE for details."
    echo -e "  Fix the items marked ${_RED}✗${_RST} above, then re-run: ${_CYN}./bin/quickstart.sh${_RST}"
fi
hr
echo ""
