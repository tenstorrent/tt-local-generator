"""Tests for context-aware menu bar actions and `_build_context_menu_for_source`.

SP-3d-5: the "ArtgenPanel public method tests" section this file used to carry
(toggle_auto_gen/get_auto_gen_delay/set_auto_gen_delay, plus the
win.art-autogen-delay action test) is removed along with `ArtgenPanel` and
the `art-autogen`/`art-autogen-delay` menu actions -- an ACCEPTED, FLAGGED
loss (see CLAUDE.md and .superpowers/sdd/task-5-report.md). The
`_build_context_menu_for_source` tests below are updated to match: the
"artgen" source no longer gets an Auto-generate/Auto Delay section, and no
source gets an Advanced Settings section (ControlPanel-only, also deleted).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


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


def test_video_context_has_no_advanced_settings():
    """SP-3d-5: AdvancedSettingsDialog + its menu section are deleted
    alongside ControlPanel."""
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("video"))
    assert not any("Advanced Settings" in l for l in labels)


def test_animate_context_has_no_director_style():
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("animate"))
    assert not any("Sometimes" in l for l in labels)
    assert not any("Random" in l for l in labels)


def test_animate_context_has_quality():
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("animate"))
    assert any("Fast" in l for l in labels)


def test_artgen_context_has_no_auto_generate():
    """SP-3d-5: the Auto-generate/Auto Delay sections were ArtgenPanel-
    sidebar-only and are removed as an accepted, flagged loss."""
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("artgen"))
    assert not any("Enabled" in l for l in labels)
    assert not any("3 seconds" in l for l in labels)
    assert not any("10 seconds" in l for l in labels)


def test_artgen_context_has_sleep_after():
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("artgen"))
    assert any("Never" in l for l in labels)


def test_artgen_context_has_no_director_style():
    from main_window import _build_context_menu_for_source
    labels = _collect_menu_labels(_build_context_menu_for_source("artgen"))
    assert not any("Sometimes" in l for l in labels)
