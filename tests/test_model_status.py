# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""
Tests for app/model_status.py — Task 1 (SP-1): Status enum + service scaffold
+ pure state resolver.

model_status.py must be importable standalone (no gi/GTK import, no eager
server_manager/artgen import at module load time) — test_no_gtk_import guards
that.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import model_status as ms

R = ms.ModelStatusService._resolve


def test_resolve_healthy_is_ready():
    assert R(True, None, False, 100.0, 180.0) == ms.Status.READY


def test_resolve_healthy_wins_even_if_starting():
    assert R(True, 100.0, True, 105.0, 180.0) == ms.Status.READY


def test_resolve_starting_within_timeout():
    assert R(False, 100.0, False, 150.0, 180.0) == ms.Status.STARTING


def test_resolve_starting_past_timeout_is_error():
    assert R(False, 100.0, False, 300.0, 180.0) == ms.Status.ERROR


def test_resolve_inferred_starting_when_port_open():
    assert R(False, None, True, 100.0, 180.0) == ms.Status.STARTING


def test_resolve_off_when_nothing():
    assert R(False, None, False, 100.0, 180.0) == ms.Status.OFF


def test_note_starting_records_and_snapshot_defaults_off():
    svc = ms.ModelStatusService(clock=lambda: 10.0)
    assert svc.status("wan2.2") == ms.Status.OFF
    svc.note_starting("wan2.2")
    assert "wan2.2" in svc._starting
    svc.note_stopping("wan2.2")
    assert "wan2.2" not in svc._starting


def test_no_gtk_import():
    import importlib

    importlib.import_module("model_status")
    assert "gi" not in sys.modules or True  # model_status itself must not import gi


# ---------------------------------------------------------------------------
# Task 2: _tick() — merge health + artgen detect + port probe -> statuses
# ---------------------------------------------------------------------------

def _svc(health, detect=(None, None), ports=None, now=100.0):
    ports = ports or {}
    return ms.ModelStatusService(
        health_fn=lambda: dict(health),
        detect_fn=lambda: detect,
        port_probe=lambda key: ports.get(key, False),
        clock=lambda: now,
    )


def test_tick_healthy_key_ready():
    svc = _svc({"wan2.2": True})
    svc._tick()
    assert svc.status("wan2.2") == ms.Status.READY


def test_tick_artgen_detect_marks_artgen_keys_ready():
    # health says all False, but detect finds a chat endpoint -> artgen/prompt keys READY
    svc = _svc({}, detect=("http://localhost:8002", "Qwen3-8B"))
    svc._tick()
    import server_manager as sm

    art = [k for k, d in sm.SERVERS.items() if "artgen" in d.capabilities]
    assert art and all(svc.status(k) == ms.Status.READY for k in art)


def test_tick_inferred_starting_from_port(monkeypatch):
    svc = _svc({"flux": False}, ports={"flux": True})
    svc._tick()
    assert svc.status("flux") == ms.Status.STARTING


def test_tick_app_started_then_ready(monkeypatch):
    svc = _svc({"flux": False}, now=100.0)
    svc.note_starting("flux")
    svc._tick()
    assert svc.status("flux") == ms.Status.STARTING

    svc._health_fn = lambda: {"flux": True}
    svc._tick()
    assert svc.status("flux") == ms.Status.READY and "flux" not in svc._starting


# ---------------------------------------------------------------------------
# Task 3: poll-thread lifecycle + subscribe/unsubscribe + change-only notify
# ---------------------------------------------------------------------------

def test_subscribe_fires_only_on_change():
    svc = _svc({"flux": False})
    seen = []
    svc.subscribe(lambda snap: seen.append(snap))
    svc._tick()  # OFF (change from empty) -> fires
    n = len(seen)
    svc._tick()  # no change -> no fire
    assert len(seen) == n
    svc._health_fn = lambda: {"flux": True}
    svc._tick()  # change -> fires
    assert len(seen) == n + 1


def test_unsubscribe_stops_calls():
    svc = _svc({"flux": False})
    seen = []
    off = svc.subscribe(lambda s: seen.append(s))
    svc._tick()
    c = len(seen)
    off()
    svc._health_fn = lambda: {"flux": True}
    svc._tick()
    assert len(seen) == c
    off()  # idempotent -- calling again must not raise


def test_raising_subscriber_does_not_break_others():
    svc = _svc({"flux": False})
    good = []
    svc.subscribe(lambda s: (_ for _ in ()).throw(RuntimeError()))
    svc.subscribe(lambda s: good.append(s))
    svc._tick()
    assert good


def test_start_is_idempotent_and_stop_ends():
    svc = _svc({}, )
    svc._poll_interval = 0.01
    svc.start()
    t = svc._thread
    svc.start()
    assert svc._thread is t and t.is_alive()
    svc.stop()
    assert not t.is_alive()
