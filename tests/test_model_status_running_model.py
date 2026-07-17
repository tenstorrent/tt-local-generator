# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""
Tests for app/model_status.py — Task 2 (SP-1 follow-on): track the running
chat model and make artgen/prompt readiness model-specific.

Prior behavior (Task 1 and earlier): any detected chat endpoint marked EVERY
artgen/prompt-capability key READY, since all artgen backends share port 8002
and one detector. That blanket-marking is wrong when only one model is
actually loaded — this file locks down the corrected, model-specific
behavior: only the `server_manager.SERVERS` key that `match_model_id` resolves
the detected id to goes READY off of the sweep; every other artgen/prompt key
stays whatever its own managed health (`health_fn`) says (normally OFF/absent
for the shared-port-8002 family, so it stays OFF).

Drives `_tick()` directly with fake `health_fn`/`detect_fn`/`clock`/
`port_probe` — no threads, no sockets, no real subprocesses (same pattern as
tests/test_model_status.py's `_svc` helper).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import model_status as ms


def _svc(health, detect=(None, None), ports=None, now=100.0):
    ports = ports or {}
    return ms.ModelStatusService(
        health_fn=lambda: dict(health),
        detect_fn=lambda: detect,
        port_probe=lambda key: ports.get(key, False),
        clock=lambda: now,
    )


def test_known_model_id_marks_only_matched_key_ready():
    svc = _svc({}, detect=("http://localhost:8003", "Qwen/Qwen3-8B"))
    svc._tick()
    assert svc.status("artgen-qwen3-8b") == ms.Status.READY
    assert svc.status("artgen-qwen3-32b") == ms.Status.OFF
    assert svc.status("artgen-llama-3.3-70b") == ms.Status.OFF


def test_known_model_id_running_artgen_model_reports_matched_key():
    svc = _svc({}, detect=("http://localhost:8003", "Qwen/Qwen3-8B"))
    svc._tick()
    info = svc.running_artgen_model()
    assert info == ms.ArtgenModelInfo("Qwen/Qwen3-8B", "http://localhost:8003", "artgen-qwen3-8b")


def test_unknown_model_id_marks_no_artgen_key_ready():
    svc = _svc({}, detect=("http://localhost:9001", "qwen3.6-27b"))
    svc._tick()

    import server_manager as sm

    art = [k for k, d in sm.SERVERS.items() if "artgen" in d.capabilities]
    assert art and all(svc.status(k) == ms.Status.OFF for k in art)


def test_unknown_model_id_running_artgen_model_has_none_matched_key():
    svc = _svc({}, detect=("http://localhost:9001", "qwen3.6-27b"))
    svc._tick()
    info = svc.running_artgen_model()
    assert info == ms.ArtgenModelInfo("qwen3.6-27b", "http://localhost:9001", None)


def test_no_endpoint_running_artgen_model_is_none_and_artgen_keys_off():
    svc = _svc({}, detect=(None, None))
    svc._tick()
    assert svc.running_artgen_model() is None

    import server_manager as sm

    art = [k for k, d in sm.SERVERS.items() if "artgen" in d.capabilities]
    assert art and all(svc.status(k) == ms.Status.OFF for k in art)


def test_prompt_server_managed_health_independent_of_sweep():
    # No chat endpoint found by the sweep at all, but prompt-server's own
    # managed health_fn (its dedicated /health ping on port 8001) says it's
    # up -- that must still resolve READY, since prompt-server has its own
    # health check independent of the artgen /v1/models sweep.
    svc = _svc({"prompt-server": True}, detect=(None, None))
    svc._tick()
    assert svc.status("prompt-server") == ms.Status.READY


def test_subscriber_notified_when_running_model_changes_with_no_status_flip():
    # Two different unknown ids across two ticks: both leave every
    # server_manager.SERVERS key at Status.OFF (no per-key Status transition
    # at all), but the running model itself changed from A to B -- the
    # subscriber must still be notified.
    svc = _svc({}, detect=("http://localhost:9001", "unknown-model-a"))
    seen = []
    svc.subscribe(lambda snap: seen.append(snap))
    svc._tick()
    n = len(seen)

    svc._detect_fn = lambda: ("http://localhost:9002", "unknown-model-b")
    svc._tick()
    assert len(seen) == n + 1

    # Sanity: no per-key Status actually flipped between the two ticks.
    info_a = ms.ArtgenModelInfo("unknown-model-a", "http://localhost:9001", None)
    info_b = svc.running_artgen_model()
    assert info_b == ms.ArtgenModelInfo("unknown-model-b", "http://localhost:9002", None)
    assert info_a != info_b
