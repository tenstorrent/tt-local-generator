import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import ready_to_run as rtr


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
