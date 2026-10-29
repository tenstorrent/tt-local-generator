import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import ready_to_run as rtr
import server_registry as _sr
import tt_backend as _tb


def _status(map_):
    # map_: {key: "ready"|"starting"|"off"}; missing -> "off"
    return lambda k: map_.get(k, "off")


def test_required_server_only_for_real_keys():
    assert rtr.required_server("flux") == "flux"
    assert rtr.required_server("wan2.2") == "wan2.2"
    assert rtr.required_server("animatediff") is None   # synthetic, no server
    assert rtr.required_server("__detected__:foo") is None
    assert rtr.required_server(None) is None
    assert rtr.required_server("") is None


def test_conflict_is_a_running_server_sharing_hardware():
    # flux (image, port 8000) conflicts with wan2.2 (video, port 8000)
    assert rtr.conflicting_server("flux", _status({"wan2.2": "ready"})) == "wan2.2"
    assert rtr.conflicting_server("flux", _status({"wan2.2": "starting"})) == "wan2.2"
    # nothing running -> no conflict
    assert rtr.conflicting_server("flux", _status({})) is None
    # an artgen server does NOT conflict with a media server (different hardware group)
    assert rtr.conflicting_server("flux", _status({"artgen-qwen3-8b": "ready"})) is None
    # artgen conflicts with another artgen
    assert rtr.conflicting_server("artgen-qwen3-8b", _status({"artgen-qwen3-32b": "ready"})) == "artgen-qwen3-32b"
    # a media server does NOT conflict with an artgen target
    assert rtr.conflicting_server("artgen-qwen3-8b", _status({"flux": "ready"})) is None


def test_plan_switch():
    p = rtr.plan_switch("flux", _status({}))
    assert (p.target, p.conflict, p.needs_reset) == ("flux", None, False)
    p = rtr.plan_switch("flux", _status({"wan2.2": "ready"}))
    assert (p.target, p.conflict, p.needs_reset) == ("flux", "wan2.2", True)
    p = rtr.plan_switch("animatediff", _status({"wan2.2": "ready"}))
    assert (p.target, p.conflict, p.needs_reset) == (None, None, False)
    p = rtr.plan_switch(None, _status({}))
    assert (p.target, p.conflict, p.needs_reset) == (None, None, False)


def test_animatediff_needs_no_server():
    plan = rtr.plan_switch("animatediff", lambda k: "off")
    assert plan.target is None          # local, nothing to gate → runs immediately


def test_animate_maps_to_animate_server():
    assert rtr.required_server("animate") == "animate"


# --- registry-aware plan_switch (Task 4) -----------------------------------


def _reg(*models):
    return _sr.build_registry(list(models), Path("/nonexistent/overrides.yaml"))


def _tt(name, engines=("vLLM",), hardware=("p300x2",)):
    return _tb.TtModel(name=name, kind=None, engines=engines, hardware=hardware,
                       installed=False, backends=("inference-server",))


def test_plan_switch_without_registry_unchanged():
    # Same assertions as test_plan_switch; registry=None is the default.
    for kw in ({}, {"registry": None}):
        p = rtr.plan_switch("flux", _status({}), **kw)
        assert (p.target, p.conflict, p.needs_reset, p.unknown) == ("flux", None, False, False)
        p = rtr.plan_switch("flux", _status({"wan2.2": "ready"}), **kw)
        assert (p.target, p.conflict, p.needs_reset) == ("flux", "wan2.2", True)
        p = rtr.plan_switch("animatediff", _status({"wan2.2": "ready"}), **kw)
        assert (p.target, p.conflict, p.needs_reset) == (None, None, False)
        p = rtr.plan_switch("prompt-server", _status({}), **kw)
        assert p.unknown is False


def test_plan_switch_finds_conflict_with_a_tt_entry_on_the_same_group():
    reg = _reg(_tt("Qwen3.8-27B"))
    assert reg["Qwen3.8-27B"].hardware_group == "artgen"
    # tt target conflicts with a running SERVERS artgen model
    p = rtr.plan_switch("Qwen3.8-27B", _status({"artgen-qwen3-32b": "ready"}), registry=reg)
    assert (p.target, p.conflict, p.needs_reset, p.unknown) == (
        "Qwen3.8-27B", "artgen-qwen3-32b", True, False)
    # and a SERVERS target conflicts with a running tt entry
    p = rtr.plan_switch("artgen-qwen3-32b", _status({"Qwen3.8-27B": "starting"}), registry=reg)
    assert p.conflict == "Qwen3.8-27B"
    # a media server is a different group
    p = rtr.plan_switch("Qwen3.8-27B", _status({"flux": "ready"}), registry=reg)
    assert p.conflict is None


def test_plan_switch_missing_hardware_group_returns_no_conflict_and_flags_unknown():
    reg = _reg(_tt("Mystery", engines=("media",)))
    assert reg["Mystery"].hardware_group is None
    p = rtr.plan_switch("Mystery", _status({"flux": "ready", "artgen-qwen3-8b": "ready"}),
                        registry=reg)
    assert (p.target, p.conflict, p.needs_reset, p.unknown) == ("Mystery", None, False, True)
