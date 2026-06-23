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
#   ./bin/quickstart.sh --no-assist        # skip Qwen remediation advice on failure
#   ./bin/quickstart.sh --help
#
# When steps fail and the prompt server is running, Qwen3-0.6B automatically
# provides targeted remediation advice. Pass --no-assist to disable.
#
# Note: -e is intentionally omitted. Steps continue on failure so the user gets
# a full picture in one pass; each step captures its own exit code.

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
ASSIST=1       # auto-assist on failure; --no-assist disables
LOG_FILE="/tmp/tt-quickstart.log"

# Auto non-interactive when stdin is not a terminal (ssh, CI, pipe)
[[ -t 0 ]] || NON_INTERACTIVE=1

while [[ ${1:-} != "" ]]; do
    case "$1" in
        --status)           STATUS_ONLY=1 ;;
        --non-interactive)  NON_INTERACTIVE=1 ;;
        --no-assist)        ASSIST=0 ;;
        --help|-h)
            sed -n '2,24p' "$0" | sed 's/^# \?//'
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

pass()  { PASSED+=("$1"); ok   "$1"; }
warn_s(){ WARNED+=("$1"); warn "$1"; }
fail_s(){ FAILED+=("$1"); fail "$1"; }
# skip_s: records a skipped item in WARNED with a visible warning line
skip_s(){ WARNED+=("$1"); warn "$1"; }

# ── Shared helpers ────────────────────────────────────────────────────────────

# _health_check: returns 0 if the prompt server answers on /health
_health_check() {
    curl -sf "http://127.0.0.1:8001/health" \
         -o /dev/null --max-time 2 2>/dev/null
}

# _model_ready: returns 0 (exit) / 1 (exit) depending on model_ready flag
_model_ready() {
    python3 -c "
import urllib.request, json, sys
try:
    r = urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2)
    sys.exit(0 if json.loads(r.read()).get('model_ready') else 1)
except:
    sys.exit(1)
" 2>/dev/null
}

# _llm_call MODEL MAX_TOKENS TEMP MESSAGE...
# Posts a single-user-message chat completion to the prompt server.
# Prints the response content to stdout. Returns non-zero on error.
# Timeout is deliberately generous (90s) to handle first-token latency.
_llm_call() {
    local model="$1" max_tokens="$2" temp="$3"
    shift 3
    local message="$*"
    python3 - "$model" "$max_tokens" "$temp" "$message" <<'PYEOF'
import sys, json, urllib.request

model, max_tokens, temp, message = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]

payload = json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": message}],
    "max_tokens": max_tokens,
    "temperature": temp,
}).encode()

req = urllib.request.Request(
    "http://127.0.0.1:8001/v1/chat/completions",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read())
    content = data["choices"][0]["message"]["content"].strip()
    if content:
        print(content)
        sys.exit(0)
    sys.exit(1)
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
}

# _llm_call_with_system MODEL MAX_TOKENS TEMP SYSTEM_PROMPT USER_MSG
# Variant that accepts a system prompt (passed as argv to avoid quoting issues).
_llm_call_with_system() {
    local model="$1" max_tokens="$2" temp="$3" sysprompt="$4" usermsg="$5"
    python3 - "$model" "$max_tokens" "$temp" "$sysprompt" "$usermsg" <<'PYEOF'
import sys, json, urllib.request

model, max_tokens, temp = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
sysprompt, usermsg = sys.argv[4], sys.argv[5]

payload = json.dumps({
    "model": model,
    "messages": [
        {"role": "system", "content": sysprompt},
        {"role": "user",   "content": usermsg},
    ],
    "max_tokens": max_tokens,
    "temperature": temp,
}).encode()

req = urllib.request.Request(
    "http://127.0.0.1:8001/v1/chat/completions",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read())
    content = data["choices"][0]["message"]["content"].strip()
    if content:
        print(content)
        sys.exit(0)
    sys.exit(1)
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
}

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
if python3 -c "import torch, transformers, fastapi, uvicorn, markovify, accelerate" 2>/dev/null; then
    pass "Python deps already installed"
else
    if [[ $STATUS_ONLY -eq 1 ]]; then
        fail_s "Python deps missing"
        info "Fix: pip3 install --break-system-packages torch transformers accelerate 'fastapi>=0.100.0' 'uvicorn[standard]>=0.23.0' markovify"
        _PYTHON_DEPS_OK=0
    else
        info "Installing missing packages (pip3 --break-system-packages)…"
        if pip3 install --break-system-packages \
               torch transformers accelerate \
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
    # Check whether the primary patch marker is present.
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
        skip_s "Patches skipped — vendor clone failed above"
    else
        info "Applying patches (idempotent — skips already-applied steps)…"
        _patch_log="/tmp/tt-quickstart-patches.log"
        if "$SCRIPT_DIR/apply_patches.sh" > "$_patch_log" 2>&1; then
            pass "Patches applied"
        else
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

_gtk_install_hint() {
    # Ask Qwen for a tailored one-line install command; fall back to known apt command.
    # Accepts prefixes: sudo, apt, apt-get (all common Qwen response forms).
    local fallback="sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0"
    local hint
    hint=$(_llm_call "Qwen3-0.6B" 40 0.1 \
        "I'm on Ubuntu 24.04 and the GTK4 Python bindings (python3-gi) are missing. Give me the single apt install command to fix it. Reply with only the command — no explanation, no markdown. /no_think" \
        2>/dev/null) || true

    # Sanity-check: response must look like a shell command, not prose
    if [[ "$hint" == sudo\ * || "$hint" == apt\ * || "$hint" == apt-get\ * ]]; then
        echo "$hint"
    else
        echo "$fallback"
    fi
}

if python3 -c "import gi; gi.require_version('Gtk','4.0'); from gi.repository import Gtk" 2>/dev/null; then
    pass "GTK4 + PyGObject available"
else
    warn_s "GTK4 bindings not found — GUI (./tt-gen) will not open"
    _gtk_hint=$(_gtk_install_hint)
    info "Fix: ${_gtk_hint}"
fi

# ── Step 6: Prompt server (port 8001) ─────────────────────────────────────────
step 6 "Prompt server  (Qwen3-0.6B on CPU, port 8001)"

# Track whether step 6 confirmed the server is fully ready so step 7 can skip
# its redundant patience loop.
_SERVER_READY=0

if _health_check; then
    if _model_ready; then
        pass "Prompt server already running and model ready"
        _SERVER_READY=1
    else
        warn_s "Prompt server is up but model not yet loaded — still warming up"
        info "Check: curl -s http://localhost:8001/health"
    fi
elif [[ $STATUS_ONLY -eq 1 ]]; then
    fail_s "Prompt server not running"
    info "Start: ./bin/start_prompt_gen.sh"
elif [[ $_PYTHON_DEPS_OK -eq 0 ]]; then
    skip_s "Prompt server not started — Python deps not installed"
else
    info "Starting prompt server (first run downloads ~1.2 GB model)…"
    "$SCRIPT_DIR/start_prompt_gen.sh" --gui >> "$LOG_FILE" 2>&1 || true

    info "Waiting for model to load (up to 5 min on first run)…"
    _waited=0
    _max=300
    _dot_count=0
    while [[ $_waited -lt $_max ]]; do
        sleep 5
        _waited=$(( _waited + 5 ))
        if _health_check && _model_ready; then
            echo ""  # newline after progress dots
            pass "Prompt server ready  (${_waited}s)"
            _SERVER_READY=1
            break
        fi
        printf "."
        _dot_count=$(( _dot_count + 1 ))
        if [[ $(( _dot_count % 20 )) -eq 0 ]]; then
            printf " %ds\n    " "$_waited"
        fi
    done
    echo ""

    # Server failed if it's not answering health at all after the wait.
    # A server that's up but model_ready=false is a warn, not a fail — it
    # may load in time for step 7's shorter patience loop.
    if ! _health_check; then
        fail_s "Prompt server did not start within ${_max}s"
        info "Log: /tmp/tt_prompt_gen.log"
        info "Last lines:"
        tail -5 /tmp/tt_prompt_gen.log 2>/dev/null | sed 's/^/    /'
        info "Retry: ./bin/start_prompt_gen.sh"
        FAILED+=("Prompt server")
    elif [[ $_SERVER_READY -eq 0 ]]; then
        warn_s "Server is up but model not yet ready after ${_max}s — may still be loading"
    fi
fi

# ── Step 7: End-to-end model validation ──────────────────────────────────────
step 7 "End-to-end validation  (test prompt → Qwen3-0.6B → response)"

# Only validate if the server is up and model_ready.
# If step 6 already confirmed _SERVER_READY, skip the patience loop.
_can_validate=0
if [[ $_SERVER_READY -eq 1 ]]; then
    _can_validate=1
elif _health_check; then
    # Brief patience loop: model may finish loading in the time between steps.
    _wait_ready=0
    while [[ $_wait_ready -lt 30 ]]; do
        if _model_ready; then
            _can_validate=1
            break
        fi
        sleep 2
        _wait_ready=$(( _wait_ready + 2 ))
    done
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
    _val_out=$(_llm_call "Qwen3-0.6B" 60 0.7 \
        "You are a creative prompt generator for AI video models. Write exactly ONE short text-to-video prompt (max 30 words) depicting a serene natural scene. Reply with the prompt only, no labels, no explanation. /no_think" \
        2>/tmp/tt-quickstart-val.err)
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

# ── Qwen remediation assist ───────────────────────────────────────────────────
# If any steps failed and the prompt server is available, ask Qwen3-0.6B to
# interpret the failure context and suggest specific remediation steps.

_ASSIST_SYSTEM_PROMPT='You are a setup assistant for tt-local-generator, a GTK4 Python application
that drives Tenstorrent AI hardware. You help users fix installation problems.

THE SETUP STEPS AND WHAT CAN GO WRONG:

Step 1 — Python deps (torch, transformers, fastapi, uvicorn, markovify)
  Fix: pip3 install --break-system-packages torch transformers \
           '"'"'fastapi>=0.100.0'"'"' '"'"'uvicorn[standard]>=0.23.0'"'"' markovify
  Common failure: externally-managed-environment error → must use
    --break-system-packages (Ubuntu 24.04 blocks pip without it)
  Common failure: torch not found after install → python3 -c '"'"'import torch'"'"'
    to verify; if venv is active, deactivate first.

Step 2 — Vendor clone (vendor/tt-inference-server at pinned SHA)
  Fix: ./bin/setup_vendor.sh
  Common failure: git clone auth error → needs SSH key or HTTPS token for
    github.com/tenstorrent/tt-inference-server (private repo)
  Common failure: wrong SHA → run setup_vendor.sh again to re-checkout
  Common failure: disk full → needs ~2 GB for clone

Step 3 — Vendor .env (vendor/tt-inference-server/.env)
  Fix: edit vendor/tt-inference-server/.env and set JWT_SECRET to any
    random string (e.g. openssl rand -hex 32)
  Common failure: .env missing despite clone → run ./bin/setup_vendor.sh again
  Common failure: JWT_SECRET is placeholder "changeme" → replace it

Step 4 — Patches (hotpatches applied to vendor tree)
  Fix: ./bin/apply_patches.sh
  Common failure: run_docker_server.py not found → vendor clone failed or at
    wrong path; ensure vendor/tt-inference-server exists first
  Common failure: patch already applied (idempotent, not a real error) →
    apply_patches.sh self-guards; re-running is safe
  Common failure: permission denied on vendor files → chmod -R u+w vendor/

Step 5 — GTK4/PyGObject (informational — not blocking)
  Fix: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0
  Note: the GUI (./tt-gen) needs this but the prompt server does not.

Step 6 — Prompt server (Qwen3-0.6B on CPU, port 8001)
  Fix: ./bin/start_prompt_gen.sh
  Common failure: port 8001 already in use → kill existing process:
    lsof -ti:8001 | xargs kill -9
  Common failure: model download fails → set HF_TOKEN env var and retry
  Common failure: out of memory → needs ~2.9 GB RAM free
  Log file: /tmp/tt_prompt_gen.log

Step 7 — Model validation (live inference test)
  Fix: if server is running but validation failed, check /tmp/tt_prompt_gen.log
  Common failure: 503 model still loading → wait 30s and retry
  Common failure: inference hangs → server may be OOM; check free memory

RULES FOR YOUR RESPONSE:
- Be specific and actionable. Give exact commands to run, not vague advice.
- Focus only on the failed steps listed by the user.
- If the log excerpt shows a clear error, explain what caused it.
- Keep your answer under 200 words.
- Format as a short numbered list of actions.
- Do not repeat the problem back to the user — jump straight to fixes.
- Do not mention steps that passed.'

if [[ ${#FAILED[@]} -gt 0 && $ASSIST -eq 1 && $STATUS_ONLY -eq 0 ]]; then
    if _health_check && _model_ready; then
        _failed_list=$(printf '  • %s\n' "${FAILED[@]}")
        _log_tail=$(tail -40 "$LOG_FILE" 2>/dev/null || echo "(log unavailable)")
        _user_msg="These setup steps failed:
${_failed_list}

Last lines from the setup log:
\`\`\`
${_log_tail}
\`\`\`

What went wrong and what should I run to fix it?"

        echo ""
        hr
        echo -e "  ${_BLD}${_CYN}Qwen suggests:${_RST}"
        hr
        _advice=$(_llm_call_with_system "Qwen3-0.6B" 400 0.3 \
            "$_ASSIST_SYSTEM_PROMPT" "$_user_msg" \
            2>/tmp/tt-quickstart-assist.err)
        _rc=$?
        if [[ $_rc -eq 0 && -n "$_advice" ]]; then
            echo "$_advice" | fold -s -w 72 | sed 's/^/  /'
        else
            echo -e "  ${_YLW}(Qwen assist unavailable — could not get a response)${_RST}"
            cat /tmp/tt-quickstart-assist.err 2>/dev/null | head -3 | sed 's/^/  /'
        fi
        hr
        echo ""
    fi
fi
