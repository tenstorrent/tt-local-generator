#!/usr/bin/env bash
# tt-health-check.sh — Pre-flight chip health check for QB2 / Blackhole hardware.
#
# Detects two distinct failure modes that require AC power cycle to recover:
#   1. VRM throttling — chips running at reduced clock/voltage after suspend/resume
#   2. ARC firmware hang — chip's embedded processor is stuck (heartbeat not advancing)
#
# Usage:
#   ./bin/tt-health-check.sh              # check and print report
#   ./bin/tt-health-check.sh --quiet      # exit 0=healthy, 1=degraded, 2=critical (no output on healthy)
#   ./bin/tt-health-check.sh --json       # JSON output for programmatic use

set -euo pipefail

QUIET=0
JSON=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1
[[ "${1:-}" == "--json"  ]] && JSON=1

python3 - "$QUIET" "$JSON" << 'PY'
import sys, json, subprocess, time
from pathlib import Path

quiet = sys.argv[1] == "1"
as_json = sys.argv[2] == "1"

def run_ttsmi():
    r = subprocess.run(["tt-smi", "-s"], capture_output=True, text=True, timeout=10)
    return json.loads(r.stdout)

# ── First snapshot ────────────────────────────────────────────────────────────
try:
    d1 = run_ttsmi()
except Exception as e:
    print(f"ERROR: tt-smi failed: {e}")
    sys.exit(2)

chips1 = d1.get("device_info", [])
if not chips1:
    print("ERROR: no chip data from tt-smi")
    sys.exit(2)

# Brief pause then second snapshot to check ARC heartbeat advancement
time.sleep(2)
try:
    d2 = run_ttsmi()
except Exception:
    d2 = d1
chips2 = d2.get("device_info", [])

# ── Analyse each chip ─────────────────────────────────────────────────────────
issues = []
chip_data = []

for i, (c1, c2) in enumerate(zip(chips1, chips2)):
    t1 = c1.get("smbus_telem", {})
    t2 = c2.get("smbus_telem", {})

    aiclk  = int(t1.get("AICLK",  "0x0"), 16)
    vcore  = int(t1.get("VCORE",  "0x0"), 16)
    temp_raw = int(t1.get("ASIC_TEMPERATURE", "0x0"), 16)
    temp_c = (temp_raw >> 16) & 0xFF
    tdp    = int(t1.get("TDP",    "0x0"), 16)
    hb1    = int(t1.get("TIMER_HEARTBEAT", "0x0"), 16)
    hb2    = int(t2.get("TIMER_HEARTBEAT", "0x0"), 16)
    fan    = int(t1.get("FAN_RPM", "0x0"), 16)

    arc_alive = hb1 != hb2
    throttled = aiclk < 1100  # nominal is 1350 MHz; below 1100 is clearly throttled
    low_vcore = vcore < 750   # nominal is ~836 mV; below 750 suggests VRM issue

    status = "ok"
    if not arc_alive:
        status = "critical"
        issues.append({
            "chip": i, "severity": "critical",
            "issue": "ARC firmware hang",
            "detail": f"heartbeat stuck at {hb1:#x} — AC power cycle required",
        })
    elif throttled:
        status = "degraded"
        issues.append({
            "chip": i, "severity": "degraded",
            "issue": "VRM throttle",
            "detail": f"AICLK={aiclk}MHz (expected ≥1350MHz), Vcore={vcore}mV — AC power cycle likely required",
        })
    elif low_vcore:
        status = "warn"
        issues.append({
            "chip": i, "severity": "warn",
            "issue": "Low Vcore",
            "detail": f"Vcore={vcore}mV (expected ≥800mV) — monitor; AC power cycle if perf is degraded",
        })

    chip_data.append({
        "chip": i, "status": status,
        "aiclk_mhz": aiclk, "vcore_mv": vcore,
        "temp_c": temp_c, "tdp_w": tdp,
        "fan_rpm": fan, "arc_alive": arc_alive,
    })

# ── Vcore asymmetry check ─────────────────────────────────────────────────────
vcores = [c["vcore_mv"] for c in chip_data]
vcore_spread = max(vcores) - min(vcores)
if vcore_spread > 100:
    issues.append({
        "chip": "all", "severity": "degraded",
        "issue": "Vcore asymmetry",
        "detail": f"spread of {vcore_spread}mV across chips (expected <50mV) — suspect partial VRM recovery after suspend",
    })

# ── Overall verdict ───────────────────────────────────────────────────────────
severities = [iss["severity"] for iss in issues]
if "critical" in severities:
    overall = "critical"
    exit_code = 2
elif "degraded" in severities:
    overall = "degraded"
    exit_code = 1
elif "warn" in severities:
    overall = "warn"
    exit_code = 1
else:
    overall = "healthy"
    exit_code = 0

# ── Output ────────────────────────────────────────────────────────────────────
if as_json:
    print(json.dumps({"overall": overall, "chips": chip_data, "issues": issues}, indent=2))
    sys.exit(exit_code)

if quiet and exit_code == 0:
    sys.exit(0)

print(f"\n{'═'*56}")
print(f"  TT Chip Health Check  —  {len(chip_data)} chips")
print(f"{'═'*56}")
print(f"  {'CHIP':<6}  {'AICLK':>8}  {'VCORE':>7}  {'TEMP':>5}  {'TDP':>5}  {'ARC':>5}  STATUS")
print(f"  {'─'*52}")
for c in chip_data:
    arc_str = "alive" if c["arc_alive"] else "HUNG!"
    status_str = {"ok": "✅ ok", "warn": "⚠️  warn", "degraded": "❌ DEGRADED", "critical": "🔴 CRITICAL"}[c["status"]]
    print(f"  {c['chip']:<6}  {c['aiclk_mhz']:>5} MHz  {c['vcore_mv']:>4} mV  {c['temp_c']:>3}°C  {c['tdp_w']:>3}W  {arc_str:>5}  {status_str}")

print(f"{'─'*56}")
if issues:
    print(f"\n  Issues found:\n")
    for iss in issues:
        print(f"  [{iss['severity'].upper()}] Chip {iss['chip']}: {iss['issue']}")
        print(f"    → {iss['detail']}")
    print()
    if overall in ("critical", "degraded"):
        print("  ┌─────────────────────────────────────────────────")
        print("  │  RECOMMENDATION: AC power cycle required")
        print("  │  OS reboot and tt-smi -r are NOT sufficient.")
        print("  │  Suspend/sleep does not reset ARC or VRM state.")
        print("  └─────────────────────────────────────────────────")
else:
    print("\n  All chips healthy. Safe to run inference.\n")

print()
sys.exit(exit_code)
PY
