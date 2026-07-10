"""Tests for Servers-popover artgen status reconciliation.

The artgen rows all share the fixed health port (8002), so status_all() lights
all of them when anything is on 8002 and none when a model is started on another
port. MainWindow._reconcile_artgen_statuses() overrides them from the router's
discovery so only the row matching the actually-loaded model lights, wherever it
is served.
"""
import sys
from pathlib import Path

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _artgen_keys():
    import server_manager as sm
    return [k for k, s in sm.SERVERS.items() if "artgen" in (s.capabilities or ())]


def test_only_matching_row_lights_for_external_model():
    """A Llama-3.3-70B discovered on a non-standard port lights only its row."""
    import main_window as mw

    # status_all() would have lit every artgen row (all ping 8002); start there.
    statuses = {k: True for k in _artgen_keys()}
    statuses["prompt-server"] = True  # non-artgen must be untouched

    mw.ControlPanel._reconcile_artgen_statuses(
        statuses, "http://localhost:8003", "meta-llama/Llama-3.3-70B-Instruct"
    )

    assert statuses["artgen-llama-3.3-70b"] is True
    assert statuses["artgen-qwen3-8b"] is False
    assert statuses["artgen-qwen2.5-7b"] is False
    assert statuses["prompt-server"] is True  # untouched


def test_all_artgen_off_when_nothing_discovered():
    import main_window as mw

    statuses = {k: True for k in _artgen_keys()}
    mw.ControlPanel._reconcile_artgen_statuses(statuses, None, None)

    assert all(statuses[k] is False for k in _artgen_keys())


def test_matches_on_model_arg_not_just_label():
    """DeepSeek's label differs from its --model arg; match must use the arg."""
    import main_window as mw

    statuses = {k: False for k in _artgen_keys()}
    mw.ControlPanel._reconcile_artgen_statuses(
        statuses, "http://localhost:8003",
        "deepseek-ai/DeepSeek-R1-Distill-Llama-70B",
    )

    assert statuses["artgen-deepseek-r1-70b"] is True
    assert statuses["artgen-llama-3.3-70b"] is False


def test_unknown_model_lights_no_row():
    """A model the app doesn't manage lights no artgen row (generation still
    works via the router; this popover just has no row for it)."""
    import main_window as mw

    statuses = {k: True for k in _artgen_keys()}
    mw.ControlPanel._reconcile_artgen_statuses(
        statuses, "http://localhost:9000", "some-org/Mystery-Model-42B"
    )

    assert all(statuses[k] is False for k in _artgen_keys())
