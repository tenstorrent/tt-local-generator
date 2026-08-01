#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
#
# smoke_test.sh — real-generation smoke test for tt-local-generator on TT hardware.
#
# Validates the app's generation BACKENDS end-to-end on the Blackhole cards,
# using the same `tt-ctl` paths the GUI (Create surface) shells out to:
#   - prompt-gen  (Qwen3-0.6B, CPU)         -> the "✨ Inspire" path
#   - artgen LLM  (Qwen3-8B, 1 Blackhole)   -> Create's artgen mediums (`tt-ctl artgen`)
#   - image       (FLUX, opt-in --image)    -> a native diffusion medium (`tt-ctl run`)
#
# Design goals (see repo CLAUDE.md + the qb2-card924055 fragility note):
#   * LOW backend-switch CHURN — one clean start -> generate -> stop; single chip
#     for the LLM (never a multi-chip workload) unless you opt into --image.
#   * HEALTH-GATED — aborts before touching a chip if any board isn't heartbeating.
#   * SELF-CLEANING — stops what it started on exit (unless --keep).
#
# Usage:
#   bin/smoke_test.sh                 # health gate + prompt-gen + artgen LLM (default, low risk)
#   bin/smoke_test.sh --image         # also smoke a native FLUX image (heavier, more chips)
#   bin/smoke_test.sh --keep          # leave started servers running afterward
#   bin/smoke_test.sh --timeout 600   # per-server readiness timeout (default 480s)
#   bin/smoke_test.sh --no-gate       # skip the hardware health gate (not recommended)
#
# Exit code 0 = all selected steps PASSED.

set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TTCTL="$REPO/tt-ctl"
OUTDIR="$(mktemp -d /tmp/ttlg-smoke.XXXXXX)"
READY_TIMEOUT=480
DO_IMAGE=0
KEEP=0
GATE=1

while [ $# -gt 0 ]; do
  case "$1" in
    --image)   DO_IMAGE=1 ;;
    --keep)    KEEP=1 ;;
    --no-gate) GATE=0 ;;
    --timeout) shift; READY_TIMEOUT="${1:-480}" ;;
    -h|--help) sed -n '3,30p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
  shift
done

# ── pretty output ──────────────────────────────────────────────────────────
c_g(){ printf '\033[32m%s\033[0m' "$1"; }
c_r(){ printf '\033[31m%s\033[0m' "$1"; }
c_y(){ printf '\033[33m%s\033[0m' "$1"; }
declare -A RESULT
step(){ printf '\n╔══ %s\n' "$1"; }
pass(){ RESULT["$1"]=PASS; printf '║  %s %s\n' "$(c_g '✓ PASS')" "$1"; }
fail(){ RESULT["$1"]=FAIL; printf '║  %s %s — %s\n' "$(c_r '✗ FAIL')" "$1" "${2:-}"; }

STARTED=()   # services we started, to stop on exit
cleanup(){
  if [ "$KEEP" -eq 1 ] || [ "${#STARTED[@]}" -eq 0 ]; then
    [ "$KEEP" -eq 1 ] && echo "(--keep: leaving ${STARTED[*]:-nothing} running)"
    return
  fi
  echo; echo "── cleanup: stopping ${STARTED[*]} ──"
  for s in "${STARTED[@]}"; do timeout 90 "$TTCTL" stop "$s" >/dev/null 2>&1 && echo "  stopped $s"; done
}
trap cleanup EXIT

# ── wait until a managed service reports ready via `tt-ctl servers` ─────────
wait_ready(){ # $1=service key
  local svc="$1" t=0
  printf '║  waiting for %s (≤%ss)' "$svc" "$READY_TIMEOUT"
  while [ "$t" -lt "$READY_TIMEOUT" ]; do
    if timeout 20 "$TTCTL" servers 2>/dev/null | grep -E "●|ready|online" | grep -q "$svc"; then
      printf ' %s\n' "$(c_g ready)"; return 0
    fi
    printf '.'; sleep 8; t=$((t+8))
  done
  printf ' %s\n' "$(c_r timeout)"; return 1
}

# ════════════════════════════════════════════════════════════════════════════
echo "tt-local-generator smoke test  —  logs: $OUTDIR"
echo "repo: $REPO   default-server: ${TTLG_SMOKE_SERVER:-http://localhost:8000}"

# ── STEP 0: hardware health gate ────────────────────────────────────────────
if [ "$GATE" -eq 1 ]; then
  step "0. hardware health (all Blackhole chips heartbeating?)"
  if timeout 60 tt-smi -s > "$OUTDIR/ttsmi.json" 2>/dev/null; then
    if /usr/bin/python3 - "$OUTDIR/ttsmi.json" <<'PY'
import json,sys
t=open(sys.argv[1]).read(); i=t.find("{")
d=json.loads(t[i:]) if i>=0 else {}
devs=d.get("device_info") or d.get("devices") or []
bad=[]
for n,dev in enumerate(devs):
    tel=dev.get("telemetry",{}) or {}
    hb=tel.get("arc0_health") or tel.get("heartbeat")
    temp=tel.get("asic_temperature") or tel.get("asic_temp")
    bus=(dev.get("board_info",{}) or {}).get("bus_id")
    ok = hb not in (None,0,"0")
    print(f"  chip{n} {bus}: heartbeat={hb} temp={temp} {'OK' if ok else 'NO-HEARTBEAT'}")
    if not ok: bad.append(bus)
if not devs: print("  no devices found!"); sys.exit(1)
sys.exit(1 if bad else 0)
PY
    then pass "hw-health"; else fail "hw-health" "a chip is not heartbeating — aborting"; echo; echo "$(c_r 'ABORT: hardware not healthy')"; exit 1; fi
  else
    fail "hw-health" "tt-smi snapshot failed"; exit 1
  fi
fi

# ── STEP 1: start the low-churn servers (artgen LLM on 1 chip + prompt-server) ─
step "1. start artgen LLM (single Blackhole) + prompt-server (CPU)"
if timeout 120 "$TTCTL" start --single-chip > "$OUTDIR/start.log" 2>&1; then
  STARTED+=(artgen-qwen3-8b prompt-server)
  ok1=1; wait_ready prompt-server || ok1=0
  ok2=1; wait_ready artgen-qwen3-8b || wait_ready qwen3-8b || ok2=0
  if [ "$ok1" -eq 1 ] && [ "$ok2" -eq 1 ]; then pass "servers-up"; else fail "servers-up" "one server not ready (see $OUTDIR/start.log)"; fi
else
  fail "servers-up" "tt-ctl start --single-chip failed (see $OUTDIR/start.log)"
fi

# ── STEP 2: prompt-gen smoke (the ✨ Inspire path) ──────────────────────────
step "2. prompt-gen (✨ Inspire path — LLM-guided)"
if timeout 90 "$TTCTL" generate --type image --count 1 > "$OUTDIR/prompt.txt" 2>&1 \
   && [ -s "$OUTDIR/prompt.txt" ] && grep -qi '[a-z]' "$OUTDIR/prompt.txt"; then
  printf '║  prompt: %s\n' "$(head -c 120 "$OUTDIR/prompt.txt" | tr '\n' ' ')"
  pass "prompt-gen"
else
  fail "prompt-gen" "no prompt produced (see $OUTDIR/prompt.txt)"
fi

# ── STEP 3: artgen LLM smoke (Create's artgen mediums use this exact path) ───
step "3. artgen LLM generation (tt-ctl artgen verse — real TT inference)"
ART="$OUTDIR/verse.txt"
if timeout 180 "$TTCTL" artgen verse --theme "tenstorrent forges the future" --output "$ART" > "$OUTDIR/artgen.log" 2>&1 \
   && [ -s "$ART" ]; then
  printf '║  artifact: %s (%s bytes)\n' "$ART" "$(wc -c < "$ART")"
  printf '║  preview: %s\n' "$(head -c 160 "$ART" | tr '\n' ' ')"
  pass "artgen-verse"
else
  fail "artgen-verse" "no artifact produced (see $OUTDIR/artgen.log)"
fi
# a second, structurally-different artgen type (ANSI = 3-pass colorized) if the first worked
if [ "${RESULT[artgen-verse]:-}" = "PASS" ]; then
  step "3b. artgen ANSI (3-pass color grid)"
  ANS="$OUTDIR/art.ans"
  if timeout 480 "$TTCTL" artgen ansi --subject "a blackhole chip" --output "$ANS" > "$OUTDIR/ansi.log" 2>&1 \
     && [ -s "$ANS" ] && grep -q $'\033\[' "$ANS"; then
    printf '║  artifact: %s (%s bytes, has ANSI escapes)\n' "$ANS" "$(wc -c < "$ANS")"
    pass "artgen-ansi"
  else
    fail "artgen-ansi" "no valid .ans produced (see $OUTDIR/ansi.log)"
  fi
fi

# ── STEP 4 (opt-in): native FLUX image ──────────────────────────────────────
if [ "$DO_IMAGE" -eq 1 ]; then
  step "4. native FLUX image (--image; heavier, multi-chip diffusion)"
  echo "║  $(c_y 'note: this starts the diffusion server — more chips + possible first-run compile')"
  if timeout 300 "$TTCTL" start flux > "$OUTDIR/flux_start.log" 2>&1; then
    STARTED+=(flux)
    if wait_ready flux; then
      if timeout 300 "$TTCTL" run --model image --steps 4 "a serene mountain lake at dawn, cinematic" > "$OUTDIR/flux_run.log" 2>&1; then
        pass "image-flux"
      else fail "image-flux" "generation failed (see $OUTDIR/flux_run.log)"; fi
    else fail "image-flux" "flux server not ready"; fi
  else fail "image-flux" "flux start failed (see $OUTDIR/flux_start.log)"; fi
fi

# ── summary ─────────────────────────────────────────────────────────────────
echo; echo "══════════════ SMOKE SUMMARY ══════════════"
FAILED=0
for k in "${!RESULT[@]}"; do :; done
order=(hw-health servers-up prompt-gen artgen-verse artgen-ansi image-flux)
for k in "${order[@]}"; do
  v="${RESULT[$k]:-skip}"
  case "$v" in
    PASS) printf '  %s  %s\n' "$(c_g '✓')" "$k" ;;
    FAIL) printf '  %s  %s\n' "$(c_r '✗')" "$k"; FAILED=1 ;;
    skip) : ;;
  esac
done
echo "logs kept in: $OUTDIR"
[ "$FAILED" -eq 0 ] && { echo "$(c_g 'SMOKE PASSED')"; exit 0; } || { echo "$(c_r 'SMOKE FAILED')"; exit 1; }
