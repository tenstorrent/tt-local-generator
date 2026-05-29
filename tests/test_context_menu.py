"""Tests for context-aware menu bar: new actions and ArtgenPanel public methods."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


# ── ArtgenPanel public method tests ───────────────────────────────────────────

class _FakeArtgenPanel:
    """Minimal stub matching the fields toggle_auto_gen / get_auto_gen_delay / set_auto_gen_delay need."""
    def __init__(self, auto_gen=False, delay=3):
        self._auto_gen = auto_gen
        self._auto_switch = MagicMock()
        self._auto_switch_handler = 1
        self._auto_stopped_reason = None
        self._scheduled = False

    def _auto_stop(self, reason=""):
        self._auto_gen = False
        self._auto_stopped_reason = reason

    def _auto_maybe_schedule(self):
        self._scheduled = True


def test_toggle_auto_gen_off_to_on():
    """toggle_auto_gen() when _auto_gen is False sets True, schedules, returns True."""
    from artgen_panel import ArtgenPanel
    panel = _FakeArtgenPanel(auto_gen=False)
    panel.toggle_auto_gen = ArtgenPanel.toggle_auto_gen.__get__(panel, type(panel))
    result = panel.toggle_auto_gen()
    assert result is True
    assert panel._auto_gen is True
    assert panel._scheduled is True
    panel._auto_switch.handler_block.assert_called()


def test_toggle_auto_gen_on_to_off():
    """toggle_auto_gen() when _auto_gen is True stops and returns False."""
    from artgen_panel import ArtgenPanel
    panel = _FakeArtgenPanel(auto_gen=True)
    panel.toggle_auto_gen = ArtgenPanel.toggle_auto_gen.__get__(panel, type(panel))
    result = panel.toggle_auto_gen()
    assert result is False
    assert panel._auto_gen is False
    assert panel._auto_stopped_reason == "menu toggle"


def test_get_auto_gen_delay_reads_server_config():
    """get_auto_gen_delay() returns integer from server_config."""
    from artgen_panel import ArtgenPanel
    panel = _FakeArtgenPanel()
    panel.get_auto_gen_delay = ArtgenPanel.get_auto_gen_delay.__get__(panel, type(panel))
    mock_sc = MagicMock()
    mock_sc.get.return_value = "10"
    with patch("artgen_panel.server_config", mock_sc):
        result = panel.get_auto_gen_delay()
    assert result == 10
    mock_sc.get.assert_called_once_with("artgen_auto", "delay")


def test_set_auto_gen_delay_writes_server_config():
    """set_auto_gen_delay(30) calls server_config.set with correct args."""
    from artgen_panel import ArtgenPanel
    panel = _FakeArtgenPanel()
    panel.set_auto_gen_delay = ArtgenPanel.set_auto_gen_delay.__get__(panel, type(panel))
    mock_sc = MagicMock()
    with patch("artgen_panel.server_config", mock_sc):
        panel.set_auto_gen_delay(30)
    mock_sc.set.assert_called_once_with("artgen_auto", "delay", 30)
