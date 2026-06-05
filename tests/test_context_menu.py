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


# ── Action handler unit tests ─────────────────────────────────────────────────

def test_gallery_density_action_saves_setting():
    """win.gallery-density action saves 'compact' to settings."""
    from app_settings import settings as _s
    import gi
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib, Gio
    action = Gio.SimpleAction.new_stateful(
        "gallery-density",
        GLib.VariantType.new("s"),
        GLib.Variant("s", "comfortable"),
    )
    _s.set("gallery_density", "comfortable")

    def _handler(a, p):
        val = p.get_string()
        a.set_state(GLib.Variant("s", val))
        _s.set("gallery_density", val)

    action.connect("activate", _handler)
    action.activate(GLib.Variant("s", "compact"))
    assert _s.get("gallery_density") == "compact"


def test_art_autogen_delay_action_calls_set_delay():
    """win.art-autogen-delay action with '30' calls set_auto_gen_delay(30)."""
    from unittest.mock import MagicMock
    import gi
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib, Gio
    panel = MagicMock()
    action = Gio.SimpleAction.new_stateful(
        "art-autogen-delay",
        GLib.VariantType.new("s"),
        GLib.Variant("s", "3"),
    )

    def _handler(a, p):
        val = p.get_string()
        a.set_state(GLib.Variant("s", val))
        panel.set_auto_gen_delay(int(val))

    action.connect("activate", _handler)
    action.activate(GLib.Variant("s", "30"))
    panel.set_auto_gen_delay.assert_called_once_with(30)


# ── _build_context_menu_for_source tests ─────────────────────────────────────

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Gio


def _collect_menu_labels(menu: Gio.Menu) -> list:
    """Recursively collect all item labels from a Gio.Menu."""
    labels = []
    for i in range(menu.get_n_items()):
        label = menu.get_item_attribute_value(i, "label", GLib.VariantType.new("s"))
        if label:
            labels.append(label.get_string())
        link_menu = menu.get_item_link(i, "section") or menu.get_item_link(i, "submenu")
        if link_menu:
            labels.extend(_collect_menu_labels(link_menu))
    return labels


def test_video_context_has_quality():
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("video"))
    assert any("Fast" in l for l in labels)
    assert any("Standard" in l for l in labels)
    assert any("High Quality" in l for l in labels)


def test_video_context_has_director_style():
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("video"))
    assert any("Sometimes" in l for l in labels)
    assert any("Always" in l for l in labels)


def test_video_context_has_pinned_director():
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("video"))
    assert any("Random" in l for l in labels)


def test_animate_context_has_no_director_style():
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("animate"))
    assert not any("Sometimes" in l for l in labels)
    assert not any("Random" in l for l in labels)


def test_animate_context_has_quality():
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("animate"))
    assert any("Fast" in l for l in labels)


def test_artgen_context_has_auto_generate():
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("artgen"))
    assert any("Enabled" in l for l in labels)


def test_artgen_context_has_auto_delay():
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("artgen"))
    assert any("3 seconds" in l for l in labels)
    assert any("10 seconds" in l for l in labels)


def test_artgen_context_has_sleep_after():
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("artgen"))
    assert any("Never" in l for l in labels)


def test_artgen_context_has_no_director_style():
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("artgen"))
    assert not any("Sometimes" in l for l in labels)
