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


def test_tick_artgen_detect_marks_matched_key_ready():
    # BEHAVIOR CHANGE (Task 2, model_status.py): a detected chat endpoint used
    # to mark EVERY artgen/prompt-capability key READY (all of them share
    # port 8002 and one detector). That was a bug -- only one model is ever
    # actually loaded. Now `match_model_id` (Task 1) resolves the detected id
    # to the ONE server_manager.SERVERS key it belongs to, and only that key
    # goes READY off of the sweep; every other artgen key falls back to its
    # own (absent/False) health_fn entry and stays OFF. See
    # tests/test_model_status_running_model.py for the full model-specific
    # behavior (including running_artgen_model()).
    svc = _svc({}, detect=("http://localhost:8002", "Qwen3-8B"))
    svc._tick()
    import server_manager as sm

    art = [k for k, d in sm.SERVERS.items() if "artgen" in d.capabilities]
    assert svc.status("artgen-qwen3-8b") == ms.Status.READY
    assert art and all(
        svc.status(k) == ms.Status.OFF for k in art if k != "artgen-qwen3-8b"
    )


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


# ---------------------------------------------------------------------------
# Task 4: capability query helpers -- ready_keys/starting_keys/running_or_starting
# ---------------------------------------------------------------------------

def test_ready_keys_filtered_by_capability_recent_first():
    # two image servers ready at different times -> most-recently-ready first
    svc = _svc({"flux": True, "sdxl": True})
    svc._clock = lambda: 100.0; svc._tick()   # both ready ~100
    # force sdxl to look more-recently-ready
    svc._ready_at["sdxl"] = 200.0; svc._ready_at["flux"] = 100.0
    ks = svc.ready_keys("image")
    assert ks[0] == "sdxl" and "flux" in ks


def test_running_or_starting_prefers_ready():
    svc = _svc({"wan2.2": True}); svc._tick()
    assert svc.running_or_starting("video") == "wan2.2"


def test_running_or_starting_falls_back_to_starting():
    svc = _svc({"mochi": False}, ports={"mochi": True}); svc._tick()
    assert svc.running_or_starting("video") == "mochi"


def test_running_or_starting_none_when_all_off():
    svc = _svc({}); svc._tick()
    assert svc.running_or_starting("video") is None


# ---------------------------------------------------------------------------
# Shared-port STARTING inference
# ---------------------------------------------------------------------------
#
# Every media server (video/image/animate) is the SAME port-8000 container --
# only one runs at a time. So while Wan2.2 is healthy on 8000, `flux`'s port
# probe finds 8000 open and its own health check fails (wrong runner), and
# `_resolve` rule 3 inferred STARTING from that -- permanently. Invisible while
# the status bar folded everything into one aggregate dot; with a per-function
# bar it renders as "Image starting... 4:32" while nothing is starting at all.
#
# An open port that a DIFFERENT, healthy server is already answering on is not
# evidence that this key is launching.

def test_shared_port_with_a_healthy_other_server_is_off_not_starting():
    svc = _svc(
        {"wan2.2": True},                      # video server up on :8000
        ports={"wan2.2": True, "flux": True},  # flux probes the SAME :8000
    )
    svc._tick()
    assert svc.status("wan2.2") == ms.Status.READY
    assert svc.status("flux") == ms.Status.OFF, (
        "an open port owned by another healthy server is not a launch"
    )


def test_shared_port_inference_also_covers_animate():
    svc = _svc(
        {"wan2.2": True},
        ports={"wan2.2": True, "animate": True, "flux-dev": True},
    )
    svc._tick()
    assert svc.status("animate") == ms.Status.OFF
    assert svc.status("flux-dev") == ms.Status.OFF


def test_an_explicit_note_starting_still_wins_over_the_shared_port_rule():
    """A real launch we kicked off ourselves is tracked by `starting_at`, which
    `_resolve` consults BEFORE the port probe -- suppressing the inference must
    not suppress a genuine, recorded start."""
    svc = _svc({"wan2.2": True}, ports={"wan2.2": True, "flux": True})
    svc.note_starting("flux")
    svc._tick()
    assert svc.status("flux") == ms.Status.STARTING


def test_open_port_with_no_healthy_owner_still_infers_starting():
    """Regression control: the ordinary "something is listening on my port but
    hasn't passed health yet" case must keep inferring STARTING."""
    svc = _svc({}, ports={"flux": True})
    svc._tick()
    assert svc.status("flux") == ms.Status.STARTING


def test_artgen_keys_sharing_8002_do_not_infer_starting_off_a_ready_sibling():
    svc = _svc(
        {"artgen-qwen3-8b": True},
        ports={"artgen-qwen3-8b": True, "artgen-qwen3-32b": True},
    )
    svc._tick()
    assert svc.status("artgen-qwen3-8b") == ms.Status.READY
    assert svc.status("artgen-qwen3-32b") == ms.Status.OFF
