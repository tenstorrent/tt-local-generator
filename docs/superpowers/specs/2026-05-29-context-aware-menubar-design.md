# Context-Aware Menu Bar Design

> Branch: `feat/remix` | Date: 2026-05-29 | Status: approved

## Overview

The current menu bar mixes app-level actions, global settings, and tab-specific settings into five menus (File, Generation, Prompt, TT-TV, Playlists, View) with no relationship to which tab is active. The redesign gives the bar a clear structure: three fixed menus anchor the left, one context slot at the right end changes title and contents based on the active source tab.

**Before:** File · Generation · Prompt · TT-TV · Playlists · View

**After:** File · Playlists · View ·· [🎥 Video | 💃 Animate | 🖼️ Image | 🎨 Art]

The `Generation` and `Prompt` menus are eliminated. Their contents are redistributed into the context slot or Preferences. The `TT-TV` menu is eliminated; Configure TT-TV… already exists in Preferences. The `View` menu gains a Gallery Density toggle.

---

## 1. Fixed Menus

### File
Unchanged from today.

```
Open Media Folder
─────────────────
Recover Jobs…
Refresh Remote Library
Download Remote Library…
─────────────────
Preferences…
Quit
```

### Playlists
Unchanged from today. Visible on all tabs — playlists are always accessible regardless of source.

```
Watch All Videos
─────────────────
[By Model section — dynamic]
  Wan2.2 (42)
  Mochi-1 (8)
─────────────────
[Your Playlists section — dynamic]
  Space Adventures (12) ▸
─────────────────
New Playlist…
```

### View
Gains **Gallery Density** (new radio). Detail Panel toggle is enabled only when the active tab has a detail panel (Video/Animate/Image); disabled on Art tab.

```
✓  Detail Panel          (checkmark; disabled/greyed on Art tab)
✓  Status Bar
─────────────────
[Gallery Density]
●  Comfortable
○  Compact
```

`Gallery Density` is a new `win.gallery-density` stateful action (values `"comfortable"` / `"compact"`). Comfortable = current card size. Compact = smaller cards, more per row. The setting is stored in `_settings` as `"gallery_density"`.

---

## 2. Context Slot

The context slot is the rightmost entry in the menu bar, separated from fixed menus by a visual `Gtk.Separator`. Its title and contents change whenever `_set_source()` fires in `ControlPanel`.

### Implementation: `_rebuild_context_menu(source: str)`

Called from `MainWindow._on_source_change()` (line ~7425, already exists — triggered by `_set_source` in `ControlPanel`). Replaces the context submenu in the `Gio.Menu` model by keeping a mutable `Gio.Menu` reference:

```python
self._context_menu_model: Gio.Menu  # holds the context slot's submenu
self._context_menu_item_index: int  # index of the context submenu in menumodel
```

Each call to `_rebuild_context_menu(source)`:
1. Clears `self._context_menu_model`
2. Rebuilds it with source-appropriate sections
3. Updates the submenu title via `menumodel.remove(idx)` + `menumodel.append_submenu(title, self._context_menu_model)`

The `Gio.Menu` model is live — `PopoverMenuBar` reflects changes immediately without rebuilding the widget.

### 🎥 Video context menu

```
[Quality]
●  Fast (10 steps)
○  Standard (30 steps)
○  High Quality (40 steps)
─────────────────
[Sleep After]
●  Never
○  After 10 completions
○  After 20 completions
○  After 50 completions
─────────────────
[Director Style]
○  Never
●  Sometimes (33%)
○  Often (66%)
○  Always
─────────────────
[Pinned Director]
●  Random
○  Kubrick
○  Tarkovsky
○  … (full list)
─────────────────
Advanced Settings…
```

Actions used: `win.quality`, `win.sleep-after`, `win.director-prob`, `win.director-pin`, `win.advanced-settings` — all already registered. No new actions needed.

### 💃 Animate context menu

```
[Quality]
●  Fast (10 steps)
○  Standard (30 steps)
○  High Quality (40 steps)
─────────────────
[Sleep After]
●  Never
○  After 10 completions
○  After 20 completions
○  After 50 completions
─────────────────
Advanced Settings…
```

No Director Style — Animate mode uses a reference video for motion, not text style guidance. Quality and Sleep After apply identically to Animate generations.

### 🖼️ Image context menu

```
[Quality]
●  Fast (10 steps)
○  Standard (30 steps)
○  High Quality (40 steps)
─────────────────
[Sleep After]
●  Never
○  After 10 completions
○  After 20 completions
○  After 50 completions
─────────────────
[Director Style]
○  Never
●  Sometimes (33%)
○  Often (66%)
○  Always
─────────────────
Advanced Settings…
```

Director Style applies (prompt generation is also used for images). Pinned Director omitted — image prompts don't use director names in the same way as video prompts.

### 🎨 Art context menu

```
[Auto-generate]
✓  Enabled               (checkmark toggle — win.art-autogen)
─────────────────
[Auto Delay]
○  3 seconds             (win.art-autogen-delay, target "3")
●  10 seconds            (target "10")
○  30 seconds            (target "30")
─────────────────
[Sleep After]
●  Never
○  After 10 completions
○  After 20 completions
○  After 50 completions
─────────────────
Advanced Settings…
```

Auto-generate and Auto Delay are Art-specific settings currently buried in the artgen panel UI. Surfacing them in the menu makes them accessible without having the panel open.

**Two new actions required:**

`win.art-autogen` — stateful boolean, toggles `ArtgenPanel._auto_gen`. Requires `MainWindow` to call `self._artgen_panel.toggle_auto_gen()` (new method on `ArtgenPanel`).

`win.art-autogen-delay` — stateful string radio (`"3"`, `"10"`, `"30"`), updates `server_config.set("artgen_auto", "delay", int(val))`. Current delay lives in `server_config` under section `"artgen_auto"`, key `"delay"`.

Sleep After uses the existing `win.sleep-after` action — same setting, now accessible from the Art context slot too.

---

## 3. Source → Context Title Mapping

| `_model_source` | Context slot title | Menu label in `Gio.Menu` |
|---|---|---|
| `"video"` | `🎥 Video` | `"🎥 Video"` |
| `"animate"` | `💃 Animate` | `"💃 Animate"` |
| `"image"` | `🖼️ Image` | `"🖼️ Image"` |
| `"artgen"` | `🎨 Art` | `"🎨 Art"` |

---

## 4. Files to Change

| File | Change |
|---|---|
| `app/main_window.py` | `_build_menu_actions()` — add `win.gallery-density`, `win.art-autogen`, `win.art-autogen-delay`; `_build_menu_bar()` — remove Generation/Prompt/TT-TV submenus, add View density, add separator + context slot; add `_rebuild_context_menu(source)` method; wire `_on_source_changed` to call it; add `_on_gallery_density_action`, `_on_art_autogen_action`, `_on_art_autogen_delay_action` handlers |
| `app/artgen_panel.py` | Add `toggle_auto_gen()` public method; add `get_auto_gen_delay() -> int` public method; add `set_auto_gen_delay(seconds: int)` public method |

`_on_source_changed` already fires when the source toggle changes — wire `_rebuild_context_menu` into it.

---

## 5. Removed Symbols

| Symbol | Was in | Now |
|---|---|---|
| `"Generation"` submenu | `_build_menu_bar` | Removed; contents in context slots |
| `"Prompt"` submenu | `_build_menu_bar` | Removed; contents in Video/Image context slots |
| `"TT-TV"` submenu | `_build_menu_bar` | Removed; Configure TT-TV… already in Preferences |
| `win.preferences-tttv` action | `_build_menu_actions` | Kept (still wired from Preferences dialog scroll) |

No action handlers are deleted — `_on_quality_action`, `_on_sleep_after_action`, `_on_director_prob_action`, `_on_director_pin_action` all remain. They are still called; their menu entries just move into context slots.

---

## 6. CSS

Add to the CSS block in `main_window.py`:

```css
/* Context slot — teal accent to distinguish from fixed menus */
menubar > item.context-menu-item > label {
    color: @tt_accent;
    font-weight: 600;
}
```

The context menu item needs a CSS class applied programmatically. Since `Gtk.PopoverMenuBar` builds its items from a `Gio.Menu` model and doesn't expose individual item widgets, apply the class via a `realize` signal on the `PopoverMenuBar` that walks its children and marks the last one:

```python
def _apply_context_menu_css(self, menubar: Gtk.PopoverMenuBar) -> None:
    """Mark the last menubar item (context slot) with context-menu-item CSS class."""
    child = menubar.get_last_child()
    if child:
        child.add_css_class("context-menu-item")
```

Called once after `_build_menu_bar()` returns.

---

## 7. Gallery Density Implementation

`win.gallery-density` stateful string action with values `"comfortable"` (default) and `"compact"`.

Handler `_on_gallery_density_action` saves to `_settings.set("gallery_density", val)` and calls `_apply_gallery_density(val)` which adjusts the `set_size_request` on each `GalleryWidget`'s `_flow`:

- `"comfortable"`: card `set_size_request(_THUMB_W + 20, -1)` (current value, no change)
- `"compact"`: card `set_size_request(160, -1)` (smaller cards, more columns)

Cards regenerate their natural size from `set_size_request`; `FlowBox` reflows automatically. The setting is applied on startup by reading `_settings.get("gallery_density")`.

---

## 8. ArtgenPanel Public Methods

```python
def toggle_auto_gen(self) -> bool:
    """Toggle auto-generate on/off. Returns the new state (True = enabled).

    Mirrors what _on_auto_switch_changed does when the panel's Switch is toggled.
    Also updates the Switch widget state so panel UI stays in sync.
    """
    if self._auto_gen:
        self._auto_stop("menu toggle")
    else:
        self._auto_gen = True
        self._auto_maybe_schedule()
    # Sync the panel's Switch widget if it exists
    if hasattr(self, "_auto_switch"):
        self._auto_switch.set_active(self._auto_gen)
    return self._auto_gen

def get_auto_gen_delay(self) -> int:
    """Return the current auto-generate delay in seconds."""
    from server_config import server_config
    return int(server_config.get("artgen_auto", "delay") or 3)

def set_auto_gen_delay(self, seconds: int) -> None:
    """Set the auto-generate delay. Takes effect on the next countdown cycle."""
    from server_config import server_config
    server_config.set("artgen_auto", "delay", seconds)
```

---

## 9. Test Plan

**`tests/test_menu_actions.py`** (new):
- `win.gallery-density` action with `"compact"` sets `_settings["gallery_density"]` to `"compact"`
- `win.art-autogen` action toggles `_artgen_panel._auto_gen` state
- `win.art-autogen-delay` action with `"10"` calls `artgen_panel.set_auto_gen_delay(10)`
- `_rebuild_context_menu("video")` produces a menu model with Quality, Sleep After, Director Style, Pinned Director, Advanced Settings sections
- `_rebuild_context_menu("animate")` produces Quality, Sleep After, Advanced Settings — no Director Style
- `_rebuild_context_menu("artgen")` produces Auto-generate, Auto Delay, Sleep After, Advanced Settings

**`tests/test_artgen_panel.py`** additions:
- `toggle_auto_gen()` flips `_auto_gen` state and returns the new value
- `get_auto_gen_delay()` returns the configured delay integer
- `set_auto_gen_delay(30)` persists `30` to server_config

---

## 10. Open Questions (deferred)

- Should Gallery Density persist per-tab or globally? Initial answer: globally (one setting for the whole app).
- Should `win.art-autogen` state sync back if the user toggles auto-gen from within the artgen panel UI? Initial answer: yes — the menu action and the panel toggle should stay in sync. ArtgenPanel fires a signal or callback when its state changes; MainWindow updates the action's state.
- Keyboard accelerators for context menu items? Deferred — no existing accelerators to preserve.
