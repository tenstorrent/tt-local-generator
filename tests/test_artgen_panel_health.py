"""Tests for the artgen panel health indicator.

The "on" dot must reflect the SAME endpoint discovery the generation router
uses (artgen.detect_artgen_endpoint), not a fixed-port ping. Regression guard
for the bug where a model started on a non-standard port (e.g. a vLLM Llama on
8003) generated fine but showed as offline because the indicator only checked
the configured artgen port (8002).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# System PyGObject lives outside the venv.
_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _run_health_check(panel):
    """Invoke _check_health_bg with GLib.idle_add captured; return (ok, model_id)
    that would be posted to _set_health."""
    import artgen_panel

    captured = {}

    def _fake_idle_add(fn, *args):
        captured["fn"] = fn
        captured["args"] = args
        return 0

    with patch.object(artgen_panel.GLib, "idle_add", _fake_idle_add):
        artgen_panel.ArtgenPanel._check_health_bg(panel)
    return captured["args"]  # (ok, model_id)


def test_indicator_green_when_model_on_nonstandard_port():
    """Model discovered anywhere -> ok=True and its id is reported."""
    import artgen_panel

    panel = artgen_panel.ArtgenPanel.__new__(artgen_panel.ArtgenPanel)
    with (
        patch("artgen.detect_artgen_endpoint",
              return_value=("http://localhost:8003", "meta-llama/Llama-3.3-70B-Instruct")),
        patch("artgen.detect_model", return_value=None),
    ):
        ok, model_id = _run_health_check(panel)

    assert ok is True
    assert model_id == "meta-llama/Llama-3.3-70B-Instruct"
    # Endpoint is cached for the next poll.
    assert panel._last_artgen_base == "http://localhost:8003"


def test_indicator_red_when_nothing_running():
    import artgen_panel

    panel = artgen_panel.ArtgenPanel.__new__(artgen_panel.ArtgenPanel)
    with (
        patch("artgen.detect_artgen_endpoint", return_value=(None, None)),
        patch("artgen.detect_model", return_value=None),
    ):
        ok, model_id = _run_health_check(panel)

    assert ok is False
    assert model_id is None


def test_warm_cache_repings_without_full_sweep():
    """Once found, the poll re-pings only the cached URL (no detect_artgen_endpoint)."""
    import artgen_panel

    panel = artgen_panel.ArtgenPanel.__new__(artgen_panel.ArtgenPanel)
    panel._last_artgen_base = "http://localhost:8003"

    with (
        patch("artgen.detect_model", return_value="meta-llama/Llama-3.3-70B-Instruct") as dm,
        patch("artgen.detect_artgen_endpoint") as dae,
    ):
        ok, model_id = _run_health_check(panel)

    assert ok is True
    assert model_id == "meta-llama/Llama-3.3-70B-Instruct"
    dm.assert_called_once_with("http://localhost:8003", timeout=1.0)
    dae.assert_not_called()  # cached hit -> no port sweep
