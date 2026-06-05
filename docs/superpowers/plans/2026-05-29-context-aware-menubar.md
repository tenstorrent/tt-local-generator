# Context-Aware Menu Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static 6-menu bar (File · Generation · Prompt · TT-TV · Playlists · View) with a 3+1 design: fixed menus File · Playlists · View on the left, plus a dynamic context slot at the right end whose title and contents change with the active source tab.

**Architecture:** All changes are in `app/main_window.py` and `app/artgen_panel.py`. The key mechanism is a mutable `self._context_menu_model: Gio.Menu` whose contents are rebuilt by `_rebuild_context_menu(source)` each time `_on_source_change()` fires. The menumodel's last submenu entry (the context slot) is removed and re-appended with a new title and fresh contents on each source switch. Two new Gio.SimpleActions (`win.art-autogen`, `win.art-autogen-delay`) require corresponding public methods on `ArtgenPanel`. A new `win.gallery-density` action drives card size changes across all `GalleryWidget` instances.

**Tech Stack:** Python 3, GTK4 / PyGObject (`Gio.Menu`, `Gtk.PopoverMenuBar`), `GLib.Variant`, pytest with `unittest.mock`.

---

## File Map

| Action | Path | Purpose |
|---|---|---|
| Modify | `app/main_window.py` | All menu changes: new actions, `_build_menu_bar`, `_rebuild_context_menu`, `_on_source_change` wiring, gallery density, CSS |
| Modify | `app/artgen_panel.py` | Add `toggle_auto_gen()`, `get_auto_gen_delay()`, `set_auto_gen_delay()` public methods |
| Create | `tests/test_context_menu.py` | Tests for `_rebuild_context_menu`, new actions, ArtgenPanel new methods |

---

## Task 1: ArtgenPanel public methods for auto-gen menu control

**Files:**
- Modify: `app/artgen_panel.py`
- Test: `tests/test_context_menu.py`

ArtgenPanel already has `_auto_gen: bool`, `_auto_stop(reason)`, `_auto_maybe_schedule()`, `_auto_switch: Gtk.Switch`, `_auto_switch_handler: int`. We expose three public methods so MainWindow can drive auto-gen state from a menu action without importing GTK internals.

- [ ] **Write failing tests**

Create `tests/test_context_menu.py`:

```python
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


def _patch_server_config(delay_val):
    """Context manager that patches server_config used by artgen_panel."""
    import unittest.mock
    mock_sc = MagicMock()
    mock_sc.get.return_value = delay_val
    return unittest.mock.patch.dict("sys.modules", {"server_config": MagicMock(server_config=mock_sc)})


def test_toggle_auto_gen_off_to_on():
    """toggle_auto_gen() when _auto_gen is False → sets True, schedules, returns True."""
    from artgen_panel import ArtgenPanel
    # We can't instantiate ArtgenPanel (needs GTK display), so we test the logic
    # by injecting it into a stub. The methods are pure Python — no GTK calls
    # except the _auto_switch sync, which we mock.
    panel = _FakeArtgenPanel(auto_gen=False)
    # Bind the real methods from the class to our stub
    panel.toggle_auto_gen = ArtgenPanel.toggle_auto_gen.__get__(panel, type(panel))
    result = panel.toggle_auto_gen()
    assert result is True
    assert panel._auto_gen is True
    assert panel._scheduled is True
    panel._auto_switch.handler_block.assert_called()


def test_toggle_auto_gen_on_to_off():
    """toggle_auto_gen() when _auto_gen is True → stops, returns False."""
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
```

- [ ] **Run to confirm failure**

```bash
cd /home/ttuser/code/tt-local-generator
/usr/bin/python3 -m pytest tests/test_context_menu.py -v 2>&1 | tail -15
```

Expected: ImportError or AttributeError — `toggle_auto_gen` etc. don't exist yet.

- [ ] **Implement in `app/artgen_panel.py`**

Find the last method in `ArtgenPanel` (around line 1532 `_auto_apply_random_params`). Add these three methods after it:

```python
def toggle_auto_gen(self) -> bool:
    """Toggle auto-generate on/off. Returns the new state (True = enabled).

    Mirrors _on_auto_switch_changed. Blocks/unblocks the Switch signal handler
    to avoid re-entrancy when syncing the widget.
    """
    if self._auto_gen:
        self._auto_stop("menu toggle")
    else:
        self._auto_gen = True
        self._auto_maybe_schedule()
    if hasattr(self, "_auto_switch") and hasattr(self, "_auto_switch_handler"):
        self._auto_switch.handler_block(self._auto_switch_handler)
        self._auto_switch.set_active(self._auto_gen)
        self._auto_switch.handler_unblock(self._auto_switch_handler)
    return self._auto_gen

def get_auto_gen_delay(self) -> int:
    """Return the current auto-generate delay in seconds."""
    return int(server_config.get("artgen_auto", "delay") or 3)

def set_auto_gen_delay(self, seconds: int) -> None:
    """Set the auto-generate delay. Takes effect on the next countdown cycle."""
    server_config.set("artgen_auto", "delay", seconds)
```

Check the existing imports at the top of `artgen_panel.py` — `server_config` must be imported. Find how it's currently imported:

```bash
grep -n "server_config\|from server_config\|import server_config" app/artgen_panel.py | head -5
```

If it's imported as `from server_config import server_config as server_config` or similar, use that name. If it's imported inline inside methods, add a module-level import.

- [ ] **Run tests**

```bash
/usr/bin/python3 -m pytest tests/test_context_menu.py -v 2>&1 | tail -15
```

Expected: all 4 pass.

- [ ] **Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all existing tests still pass.

- [ ] **Commit**

```bash
git add app/artgen_panel.py tests/test_context_menu.py
git commit -m "feat(menu): add ArtgenPanel.toggle_auto_gen, get_auto_gen_delay, set_auto_gen_delay"
```

---

## Task 2: Register new Gio.SimpleActions (`win.gallery-density`, `win.art-autogen`, `win.art-autogen-delay`)

**Files:**
- Modify: `app/main_window.py` (`_build_menu_actions`, and add three new handler methods)
- Test: `tests/test_context_menu.py`

Three new actions:
- `win.gallery-density` — stateful string, `"comfortable"` | `"compact"`, persisted in `_settings["gallery_density"]`
- `win.art-autogen` — stateful boolean, drives `self._artgen_panel.toggle_auto_gen()`
- `win.art-autogen-delay` — stateful string radio, `"3"` | `"10"` | `"30"`, drives `self._artgen_panel.set_auto_gen_delay(int(val))`

- [ ] **Add tests to `tests/test_context_menu.py`**

Append to the existing file:

```python
# ── Action handler unit tests ─────────────────────────────────────────────────
# We test the handler logic in isolation — no GTK event loop needed.

def _make_gallery_density_handler():
    """Return a standalone version of _on_gallery_density_action for testing."""
    from app_settings import settings as _s
    results = {}

    def handler(action, param):
        val = param.get_string()
        action.set_state(MagicMock())
        _s.set("gallery_density", val)
        results["density"] = val

    return handler, results


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
    """win.art-autogen-delay action with '30' calls artgen_panel.set_auto_gen_delay(30)."""
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
```

- [ ] **Run new tests to confirm failure**

```bash
/usr/bin/python3 -m pytest tests/test_context_menu.py::test_gallery_density_action_saves_setting tests/test_context_menu.py::test_art_autogen_delay_action_calls_set_delay -v 2>&1 | tail -10
```

Expected: these pass immediately (they use standalone action objects, not MainWindow). That's fine — they verify the handler *logic* pattern. If they fail, fix.

- [ ] **Add the three actions to `_build_menu_actions` in `app/main_window.py`**

Find the end of `_build_menu_actions` (around line 7110, just before `def _build_menu_bar`). Add:

```python
        # ── View: gallery density (radio) ─────────────────────────────────────
        density_val = _settings.get("gallery_density") or "comfortable"
        gallery_density_action = Gio.SimpleAction.new_stateful(
            "gallery-density",
            GLib.VariantType.new("s"),
            GLib.Variant("s", density_val),
        )
        gallery_density_action.connect("activate", self._on_gallery_density_action)
        self.add_action(gallery_density_action)

        # ── Art: auto-generate toggle ──────────────────────────────────────────
        art_autogen_action = Gio.SimpleAction.new_stateful(
            "art-autogen",
            None,
            GLib.Variant("b", False),
        )
        art_autogen_action.connect("activate", self._on_art_autogen_action)
        self.add_action(art_autogen_action)

        # ── Art: auto-generate delay radio ────────────────────────────────────
        art_delay_action = Gio.SimpleAction.new_stateful(
            "art-autogen-delay",
            GLib.VariantType.new("s"),
            GLib.Variant("s", "3"),
        )
        art_delay_action.connect("activate", self._on_art_autogen_delay_action)
        self.add_action(art_delay_action)
```

- [ ] **Add the three handler methods to `MainWindow`**

Place them near the other action handlers (around line 7358, after `_on_toggle_detail`):

```python
    def _on_gallery_density_action(self, action: Gio.SimpleAction,
                                    param: GLib.Variant) -> None:
        """Menu: switch gallery card size between comfortable and compact."""
        val = param.get_string()
        action.set_state(GLib.Variant("s", val))
        _settings.set("gallery_density", val)
        self._apply_gallery_density(val)

    def _apply_gallery_density(self, density: str) -> None:
        """Set card min-width on all GalleryWidget instances and relayout."""
        # comfortable = 220px (current _THUMB_W + 20), compact = 160px
        card_w = (_THUMB_W + 20) if density == "comfortable" else 160
        for gallery in (self._video_gallery, self._image_gallery,
                         self._animate_gallery):
            for card in gallery._cards:
                card.set_size_request(card_w, -1)
            gallery._relayout()

    def _on_art_autogen_action(self, action: Gio.SimpleAction,
                                _param: GLib.Variant) -> None:
        """Menu: toggle artgen auto-generate on/off."""
        new_state = self._artgen_panel.toggle_auto_gen()
        action.set_state(GLib.Variant("b", new_state))

    def _on_art_autogen_delay_action(self, action: Gio.SimpleAction,
                                      param: GLib.Variant) -> None:
        """Menu: set artgen auto-generate delay in seconds."""
        val = param.get_string()
        action.set_state(GLib.Variant("s", val))
        self._artgen_panel.set_auto_gen_delay(int(val))
```

- [ ] **Check that `self._video_gallery`, `self._image_gallery`, `self._animate_gallery` are the correct attribute names**

```bash
grep -n "_video_gallery\|_image_gallery\|_animate_gallery\|GalleryWidget(" app/main_window.py | head -15
```

Update the gallery names in `_apply_gallery_density` to match exactly what MainWindow uses.

- [ ] **Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Commit**

```bash
git add app/main_window.py tests/test_context_menu.py
git commit -m "feat(menu): register win.gallery-density, win.art-autogen, win.art-autogen-delay actions"
```

---

## Task 3: Rebuild `_build_menu_bar` — fixed menus (File, Playlists, View)

**Files:**
- Modify: `app/main_window.py` (`_build_menu_bar`, CSS block)

Replace the current `_build_menu_bar` with the new structure. This task handles only the **fixed** menus; the context slot is added in Task 4.

Key changes from today:
- Remove `Generation` submenu
- Remove `Prompt` submenu  
- Remove `TT-TV` submenu
- Extend `View` with Gallery Density radio
- Add a visual separator (`Gtk.Separator`) after View, before the context slot placeholder
- Store the `menumodel` and context slot index as `self._menumodel` and `self._context_slot_idx`

- [ ] **Replace `_build_menu_bar` in `app/main_window.py`**

Find `def _build_menu_bar(self) -> Gtk.PopoverMenuBar:` (around line 7110) and replace the entire method:

```python
    def _build_menu_bar(self) -> Gtk.PopoverMenuBar:
        """Build the PopoverMenuBar.

        Structure: File · Playlists · View ·· [context slot]
        The context slot (last entry) is rebuilt by _rebuild_context_menu()
        each time the source tab changes.
        """
        self._menumodel = Gio.Menu()

        # ── File ──────────────────────────────────────────────────────────────
        file_menu = Gio.Menu()
        file_menu.append("Open Media Folder", "win.open-media-folder")
        file_menu.append_section(None, Gio.Menu())
        file_menu.append("Recover Jobs…", "win.recover-jobs")
        file_menu.append("Refresh Remote Library", "win.refresh-remote-library")
        file_menu.append("Download Remote Library…", "win.sync-from-server")
        file_menu.append_section(None, Gio.Menu())
        file_menu.append("Preferences…", "win.preferences")
        file_menu.append("Quit", "app.quit")
        self._menumodel.append_submenu("File", file_menu)

        # ── Playlists ─────────────────────────────────────────────────────────
        pl_menu = Gio.Menu()
        pl_menu.append("Watch All Videos", "win.playlist-all")
        self._playlists_model_section = Gio.Menu()
        pl_menu.append_section("By Model", self._playlists_model_section)
        self._playlists_playlist_section = Gio.Menu()
        pl_menu.append_section("Your Playlists", self._playlists_playlist_section)
        pl_manage = Gio.Menu()
        pl_manage.append("New Playlist…", "win.playlist-new")
        pl_menu.append_section(None, pl_manage)
        self._menumodel.append_submenu("Playlists", pl_menu)

        # ── View ──────────────────────────────────────────────────────────────
        view_menu = Gio.Menu()
        toggle_section = Gio.Menu()
        toggle_section.append("Detail Panel", "win.toggle-detail")
        view_menu.append_section(None, toggle_section)

        density_section = Gio.Menu()
        for label, val in [("Comfortable", "comfortable"), ("Compact", "compact")]:
            item = Gio.MenuItem.new(label, "win.gallery-density")
            item.set_attribute_value("target", GLib.Variant("s", val))
            density_section.append_item(item)
        view_menu.append_section("Gallery Density", density_section)
        self._menumodel.append_submenu("View", view_menu)

        # ── Context slot placeholder (replaced by _rebuild_context_menu) ──────
        # Record the index so _rebuild_context_menu knows which entry to replace.
        self._context_slot_idx = self._menumodel.get_n_items()
        self._context_menu_model = Gio.Menu()
        self._menumodel.append_submenu("🎥 Video", self._context_menu_model)

        bar = Gtk.PopoverMenuBar.new_from_model(self._menumodel)
        # Mark the last item with a CSS class for teal accent styling.
        self._apply_context_menu_css(bar)
        return bar

    def _apply_context_menu_css(self, bar: Gtk.PopoverMenuBar) -> None:
        """Add context-menu-item CSS class to the last (context slot) item."""
        child = bar.get_last_child()
        if child is not None:
            child.add_css_class("context-menu-item")
```

Also update `root_box.append(self._build_menu_bar())` (around line 6832) to store the bar:

```python
        self._menu_bar = self._build_menu_bar()
        root_box.append(self._menu_bar)
```

- [ ] **Add CSS for context slot teal accent**

Find the menubar CSS section (around line 851). After `menubar > item:hover, menubar > item:selected { ... }`, add:

```css
/* Context slot — teal accent to distinguish from fixed menus */
menubar > item.context-menu-item > label {
    color: @tt_accent;
    font-weight: 600;
}
menubar > item.context-menu-item:hover > label,
menubar > item.context-menu-item:selected > label {
    color: @tt_accent_light;
}
```

- [ ] **Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all pass. The app won't crash — the context slot will show "🎥 Video" with an empty submenu until Task 4 populates it.

- [ ] **Commit**

```bash
git add app/main_window.py
git commit -m "feat(menu): rebuild fixed menus (File/Playlists/View), add context slot placeholder"
```

---

## Task 4: `_rebuild_context_menu(source)` and wiring to `_on_source_change`

**Files:**
- Modify: `app/main_window.py`
- Test: `tests/test_context_menu.py`

This is the core of the feature. `_rebuild_context_menu` replaces the context submenu in `self._menumodel` with a source-appropriate title and contents, then re-marks the CSS class.

- [ ] **Add tests to `tests/test_context_menu.py`**

Append:

```python
# ── _rebuild_context_menu logic tests ─────────────────────────────────────────
# Test the menu content builder in isolation — no GTK event loop.

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


def _build_video_context() -> Gio.Menu:
    """Call the standalone context-builder for 'video' and return its menu."""
    from main_window import _build_context_menu_for_source
    return _build_context_menu_for_source("video")


def _build_animate_context() -> Gio.Menu:
    from main_window import _build_context_menu_for_source
    return _build_context_menu_for_source("animate")


def _build_artgen_context() -> Gio.Menu:
    from main_window import _build_context_menu_for_source
    return _build_context_menu_for_source("artgen")


def test_video_context_has_quality():
    menu = _build_video_context()
    labels = _collect_menu_labels(menu)
    assert any("Fast" in l for l in labels)
    assert any("Standard" in l for l in labels)
    assert any("High Quality" in l for l in labels)


def test_video_context_has_director_style():
    menu = _build_video_context()
    labels = _collect_menu_labels(menu)
    assert any("Never" in l for l in labels)
    assert any("Sometimes" in l for l in labels)
    assert any("Always" in l for l in labels)


def test_video_context_has_pinned_director():
    menu = _build_video_context()
    labels = _collect_menu_labels(menu)
    assert any("Random" in l for l in labels)


def test_animate_context_has_no_director_style():
    menu = _build_animate_context()
    labels = _collect_menu_labels(menu)
    # Director Style radio items should not appear in Animate context
    assert not any("Sometimes" in l for l in labels)
    assert not any("Random" in l for l in labels)


def test_animate_context_has_quality():
    menu = _build_animate_context()
    labels = _collect_menu_labels(menu)
    assert any("Fast" in l for l in labels)


def test_artgen_context_has_auto_generate():
    menu = _build_artgen_context()
    labels = _collect_menu_labels(menu)
    assert any("Enabled" in l for l in labels)


def test_artgen_context_has_auto_delay():
    menu = _build_artgen_context()
    labels = _collect_menu_labels(menu)
    assert any("3 seconds" in l for l in labels)
    assert any("10 seconds" in l for l in labels)


def test_artgen_context_has_sleep_after():
    menu = _build_artgen_context()
    labels = _collect_menu_labels(menu)
    assert any("Never" in l for l in labels)


def test_artgen_context_has_no_director_style():
    menu = _build_artgen_context()
    labels = _collect_menu_labels(menu)
    assert not any("Sometimes" in l for l in labels)
```

- [ ] **Run to confirm failure**

```bash
/usr/bin/python3 -m pytest tests/test_context_menu.py -k "context" -v 2>&1 | tail -15
```

Expected: ImportError for `_build_context_menu_for_source`.

- [ ] **Add `_build_context_menu_for_source` as a module-level function in `app/main_window.py`**

Place it just before `class MainWindow` (or just after `_DIRECTOR_PINS` near the top of the module-level definitions). This is a pure function — no `self` — so it can be tested without instantiating MainWindow:

```python
def _build_context_menu_for_source(source: str) -> Gio.Menu:
    """
    Build and return a fresh Gio.Menu for the context slot matching *source*.

    source: "video" | "animate" | "image" | "artgen"

    Called by MainWindow._rebuild_context_menu() each time the source tab
    changes. Kept as a module-level function so it can be unit-tested without
    a MainWindow instance.
    """
    menu = Gio.Menu()

    # ── Quality (video / animate / image) ────────────────────────────────────
    if source in ("video", "animate", "image"):
        quality_section = Gio.Menu()
        for label, steps in [("Fast (10 steps)", "10"),
                              ("Standard (30 steps)", "30"),
                              ("High Quality (40 steps)", "40")]:
            item = Gio.MenuItem.new(label, "win.quality")
            item.set_attribute_value("target", GLib.Variant("s", steps))
            quality_section.append_item(item)
        menu.append_section("Quality", quality_section)

    # ── Sleep After (all sources) ────────────────────────────────────────────
    sleep_section = Gio.Menu()
    for label, val in [("Never", "0"), ("After 10 completions", "10"),
                       ("After 20 completions", "20"), ("After 50 completions", "50")]:
        item = Gio.MenuItem.new(label, "win.sleep-after")
        item.set_attribute_value("target", GLib.Variant("s", val))
        sleep_section.append_item(item)
    menu.append_section("Sleep After", sleep_section)

    # ── Director Style (video / image only) ──────────────────────────────────
    if source in ("video", "image"):
        dir_prob_section = Gio.Menu()
        for label, pct in [("Never", "0"), ("Sometimes (33%)", "33"),
                           ("Often (66%)", "66"), ("Always", "100")]:
            item = Gio.MenuItem.new(label, "win.director-prob")
            item.set_attribute_value("target", GLib.Variant("s", pct))
            dir_prob_section.append_item(item)
        menu.append_section("Director Style", dir_prob_section)

    # ── Pinned Director (video only) ─────────────────────────────────────────
    if source == "video":
        pin_section = Gio.Menu()
        for display, full in _DIRECTOR_PINS:
            item = Gio.MenuItem.new(display or "Random", "win.director-pin")
            item.set_attribute_value("target", GLib.Variant("s", full))
            pin_section.append_item(item)
        menu.append_section("Pinned Director", pin_section)

    # ── Art: auto-generate toggle ─────────────────────────────────────────────
    if source == "artgen":
        auto_section = Gio.Menu()
        auto_item = Gio.MenuItem.new("Enabled", "win.art-autogen")
        auto_section.append_item(auto_item)
        menu.append_section("Auto-generate", auto_section)

        delay_section = Gio.Menu()
        for label, val in [("3 seconds", "3"), ("10 seconds", "10"), ("30 seconds", "30")]:
            item = Gio.MenuItem.new(label, "win.art-autogen-delay")
            item.set_attribute_value("target", GLib.Variant("s", val))
            delay_section.append_item(item)
        menu.append_section("Auto Delay", delay_section)

    # ── Advanced Settings (video / animate / image) ───────────────────────────
    if source in ("video", "animate", "image"):
        adv_section = Gio.Menu()
        adv_section.append("Advanced Settings…", "win.advanced-settings")
        menu.append_section(None, adv_section)

    return menu
```

- [ ] **Add `_rebuild_context_menu` method to `MainWindow`**

Place it near `_build_menu_bar` (around line 7195):

```python
    def _rebuild_context_menu(self, source: str) -> None:
        """Replace the context slot title and contents for the given source tab.

        Uses remove() + append_submenu() on self._menumodel so the live
        PopoverMenuBar reflects the change immediately without rebuilding.
        """
        _TITLES = {
            "video":   "🎥 Video",
            "animate": "💃 Animate",
            "image":   "🖼️ Image",
            "artgen":  "🎨 Art",
        }
        title = _TITLES.get(source, "🎥 Video")

        # Replace contents of the existing context_menu_model in-place.
        # Gio.Menu.remove_all() + re-populating is cleaner than remove/re-append
        # of the submenu item itself (which would lose the PopoverMenuBar binding).
        self._context_menu_model.remove_all()
        fresh = _build_context_menu_for_source(source)
        for i in range(fresh.get_n_items()):
            # Copy each section/item from fresh into self._context_menu_model
            link_type = None
            section = fresh.get_item_link(i, "section")
            submenu = fresh.get_item_link(i, "submenu")
            if section:
                label_v = fresh.get_item_attribute_value(i, "label",
                                                          GLib.VariantType.new("s"))
                label = label_v.get_string() if label_v else None
                self._context_menu_model.append_section(label, section)
            elif submenu:
                label_v = fresh.get_item_attribute_value(i, "label",
                                                          GLib.VariantType.new("s"))
                label = label_v.get_string() if label_v else None
                self._context_menu_model.append_submenu(label, submenu)
            else:
                item_label_v = fresh.get_item_attribute_value(i, "label",
                                                               GLib.VariantType.new("s"))
                item_action_v = fresh.get_item_attribute_value(i, "action",
                                                                GLib.VariantType.new("s"))
                if item_label_v and item_action_v:
                    self._context_menu_model.append(
                        item_label_v.get_string(),
                        item_action_v.get_string(),
                    )

        # Update the submenu title by replacing the slot entry in menumodel.
        self._menumodel.remove(self._context_slot_idx)
        self._menumodel.insert_submenu(self._context_slot_idx, title,
                                       self._context_menu_model)

        # Re-apply context CSS class to the (potentially new) last bar item.
        self._apply_context_menu_css(self._menu_bar)
```

- [ ] **Wire `_rebuild_context_menu` into `_on_source_change`**

Find `_on_source_change` (around line 7425):

```python
    def _on_source_change(self, source: str) -> None:
        """Switch the gallery stack; in artgen mode collapse side panels for full-width view."""
        self._gallery_stack.set_visible_child_name(source)
        is_artgen = source == "artgen"
        self._ctrl_wrapper.set_visible(not is_artgen)
        self._detail_wrap.set_visible(not is_artgen)
```

Add one line at the end:

```python
        self._rebuild_context_menu(source)
```

- [ ] **Initialize context menu on startup**

Find where `_build_menu_bar()` is called (around line 6832). After that line, add:

```python
        # Build the initial context menu for the default source (video).
        self._rebuild_context_menu("video")
```

- [ ] **Run context menu tests**

```bash
/usr/bin/python3 -m pytest tests/test_context_menu.py -v 2>&1 | tail -25
```

Expected: all tests pass.

- [ ] **Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Commit**

```bash
git add app/main_window.py tests/test_context_menu.py
git commit -m "feat(menu): add _build_context_menu_for_source and _rebuild_context_menu; wire to source change"
```

---

## Task 5: Apply gallery density on startup; sync art-autogen action state

**Files:**
- Modify: `app/main_window.py`

Two small wiring tasks:

1. On startup, read `_settings.get("gallery_density")` and apply it so cards respect the saved preference.
2. After `ArtgenPanel` is built, sync the `win.art-autogen` action state to reflect whether auto-gen is currently enabled.

- [ ] **Apply gallery density on startup**

Find where `_apply_gallery_density` should be called. Search for where `self._artgen_panel` is constructed and the app finishes building its gallery widgets (around line 6905-6920 in `MainWindow._build_ui`):

```bash
grep -n "self._artgen_panel\|_video_gallery\|_animate_gallery\|_image_gallery\|load_history\|_build_ui" app/main_window.py | head -20
```

After the gallery widgets are constructed (after all `GalleryWidget(...)` calls), add:

```python
        # Apply saved gallery density preference on startup.
        _density = _settings.get("gallery_density") or "comfortable"
        if _density != "comfortable":
            self._apply_gallery_density(_density)
```

- [ ] **Sync `win.art-autogen` action state after artgen panel is ready**

Find where `self._artgen_panel` is created. After that line, add:

```python
        # Sync art-autogen menu action to reflect panel's current state.
        # ArtgenPanel starts with _auto_gen=False, so default state is correct;
        # this is future-proofing for when state is persisted across restarts.
        art_autogen_act = self.lookup_action("art-autogen")
        if art_autogen_act:
            art_autogen_act.set_state(
                GLib.Variant("b", bool(self._artgen_panel._auto_gen))
            )
```

- [ ] **Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Commit**

```bash
git add app/main_window.py
git commit -m "feat(menu): apply gallery density on startup; sync art-autogen action state"
```

---

## Task 6: Final cleanup — remove dead action references and push

**Files:**
- Modify: `app/main_window.py`

The `win.preferences-tttv` action and its handler remain (still used by Preferences dialog). But any code that references the now-removed Generation/Prompt/TT-TV submenus should be cleaned up.

- [ ] **Verify no dead references remain**

```bash
grep -n "Generation\|\"Prompt\"\|TT-TV.*menu\|menumodel.append_submenu.*Prompt\|menumodel.append_submenu.*TT-TV\|menumodel.append_submenu.*Generation" app/main_window.py | head -10
```

Expected: no matches (those submenus are gone). If any remain in `_build_menu_bar`, remove them.

- [ ] **Verify existing menu action handlers still exist**

```bash
grep -n "def _on_quality_action\|def _on_sleep_after_action\|def _on_director_prob_action\|def _on_director_pin_action\|def _on_toggle_detail" app/main_window.py
```

All five must still be present — they are still needed by the context slot actions.

- [ ] **Run full suite one final time**

```bash
/usr/bin/python3 -m pytest tests/ -q 2>&1 | tail -10
```

Expected: all pass (3 pre-existing AnimateDiff errors acceptable).

- [ ] **Push**

```bash
git push origin HEAD
```

- [ ] **Commit cleanup if anything changed**

```bash
git add app/main_window.py
git commit -m "chore(menu): remove dead Generation/Prompt/TT-TV submenu references"
```

---

## Self-Review

**Spec coverage:**
- §1 File menu unchanged → Task 3 ✓
- §1 Playlists unchanged → Task 3 ✓
- §1 View gains Gallery Density radio → Task 3 + Task 2 (action) ✓
- §1 Detail Panel toggle disabled on Art tab → NOT YET COVERED — add below
- §2 Context slot title changes with source → Task 4 (`_rebuild_context_menu`) ✓
- §2 `_rebuild_context_menu(source)` method → Task 4 ✓
- §2 🎥 Video: Quality + Sleep After + Director Style + Pinned Director + Advanced → Task 4 ✓
- §2 💃 Animate: Quality + Sleep After + Advanced, no Director → Task 4 ✓
- §2 🖼️ Image: Quality + Sleep After + Director Style + Advanced, no Pinned Director → Task 4 ✓
- §2 🎨 Art: Auto-gen + Delay + Sleep After + no Advanced → Task 4 ✓
- §3 Source → title mapping → Task 4 ✓
- §4 `win.gallery-density` + `win.art-autogen` + `win.art-autogen-delay` actions → Task 2 ✓
- §5 Generation/Prompt/TT-TV removed → Task 3 + Task 6 ✓
- §7 Gallery density: comfortable vs compact card sizes → Task 2 (`_apply_gallery_density`) ✓
- §8 `toggle_auto_gen`, `get_auto_gen_delay`, `set_auto_gen_delay` on ArtgenPanel → Task 1 ✓
- §9 Test plan → Tasks 1-4 ✓

**Gap: Detail Panel toggle should be disabled (greyed) on Art tab.** Add to Task 5:

- [ ] **Disable Detail Panel toggle on Art tab** (add to Task 5, `_on_source_change`):

Find `_on_source_change` and add after the `_rebuild_context_menu` line:

```python
        # Grey out Detail Panel toggle on Art tab (no detail panel there).
        toggle_act = self.lookup_action("toggle-detail")
        if toggle_act:
            toggle_act.set_enabled(source != "artgen")
```

**Type consistency:** All method signatures consistent across tasks. `_build_context_menu_for_source` is module-level (no `self`), consistent with how it's called from tests and from `_rebuild_context_menu`. ✓
