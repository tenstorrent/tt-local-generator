# Remix UI Design

> Branch: `feat/remix` | Date: 2026-05-26 | Status: approved

## Overview

Every entry point that takes an existing artifact and starts a new generation
from it — the hover `💃 Animate` button, the artgen `Use as seed` action, the
detail panel `↺ Iterate` button, drag-to-seed-well — is consolidated into a
single `🔀 Remix` surface. The Remix popover is identical whether opened from
a card hover bar or from the detail panel.

This spec covers the UI consolidation and the `ffmpeg` utility plugin that
enables silent format conversions (frame extraction, GIF/MP4 conversion) the
remix engine needs.

The underlying plugin system architecture (MCP manifests, `accepts_remix_from`,
`can_remix_to`) is specified in `2026-05-27-plugin-system-design.md`. This spec
builds directly on top of it.

---

## 1. Hover Bar Consolidation

### Before

`GenerationCard` hover bar shows:
- `💃 Animate` button (only if `animate_cb` is not None)
- `☆` star toggle

### After

`GenerationCard` hover bar shows:
- `🔀 Remix` button — always present on every card type (video, image, artgen)
- `☆` star toggle

**Removed:**
- `_animate_cb` parameter from `GenerationCard.__init__`
- `animate_btn` construction and `.hover-action-btn-animate` CSS class
- `_on_animate_card_action()` method in `MainWindow`
- `animate_action_cb` parameter in `MainWindow._build_gallery_panel()`
- `on_use_as_seed` callback on `ArtgenPanel` and `ArtgenGallery`
- `_on_artgen_use_as_seed()` method in `MainWindow`

`↺ Iterate` buttons on cards and in the detail panel are **also removed** —
their function is absorbed into the Remix popover as "Remix as → same type"
(same prompt, new seed). The `iterate_cb` / `populate_prompts` plumbing stays
as the internal write target but is no longer exposed as a named button.

---

## 2. Remix Popover

A `Gtk.Popover` anchored to whichever `🔀 Remix` button was clicked. The same
widget class is used from both the hover bar and the detail panel.

### Layout (top to bottom)

```
┌──────────────────────────────────────────┐
│  [48×27 thumb]  "a red car in the rain"  │  ← source identity
│                 video · 2026-05-26        │
├──────────────────────────────────────────┤
│  CARRY INTO REMIX                         │  ← only shown when >1 ingredient
│  ☑  Prompt text                          │
│     "a red car in the rain"              │
│  ☑  Frame thumbnail  (→ I2V seed image)  │
├──────────────────────────────────────────┤
│  HINT PREVIEW                             │  ← live, updates on toggle
│  │ a red car in the rain [+ seed frame]  │
├──────────────────────────────────────────┤
│  REMIX AS                                 │
│  [ 🎬 Video (I2V) ]  [ 💃 Animate ]     │
│  [ ✨ New variation ]                    │
└──────────────────────────────────────────┘
```

**Source identity row:** 48×27 thumbnail (or type icon if no thumbnail),
clipped prompt/title text (one line, ellipsize end), type + date sub-label.

**Ingredient section:** only shown when the source has more than one ingredient
that is relevant to the current target. Each ingredient is a `Gtk.CheckButton`
with a label and a sub-label showing the extracted value (hex colors, truncated
prose, etc.). Toggling re-computes the hint preview immediately.

**Hint preview:** a read-only label showing the combined text that will be
injected into the target prompt field. Styled with a left border in the accent
color. Not editable in the popover — the user edits in the prompt field after
switching.

**Target buttons:** one `Gtk.Button` per valid remix target, derived at render
time from `remix_targets_for(source_type)` (plugin manifest query). Target
buttons are arranged in a wrapping flow. Clicking a target:
1. Closes the popover
2. Performs any silent format conversions (see §3)
3. Calls `_dispatch_remix(context)` on `MainWindow`

### "New variation" target

Always present regardless of plugin manifests. Remixes into the same type with
the same prompt but a fresh random seed. Equivalent to the old `↺ Iterate`.
Does not require ingredient selection.

### Popover sizing

Minimum width 240 px. Maximum width 340 px. Height is natural (GTK auto-sizes).
Positioned with `set_position(Gtk.PositionType.BOTTOM)` and
`set_has_arrow(True)`.

---

## 3. Ingredient Model and Silent Resolution

Each artifact type exposes a set of *ingredients* — independently usable
pieces that can travel into a remix.

### Ingredient table

| Artifact type | Ingredient A | Ingredient B | Ingredient C |
|---|---|---|---|
| `palette` | Hex colors | Lore prose | Mood prompt |
| `verse` / `haiku` | Full text | Theme prompt | — |
| `landscape` / `skyline` SVG | Thumbnail image | Vibe phrase | Source prompt |
| `video` / `gif` | Full video file | Frame thumbnail | Prompt text |
| `image` | Image file | Prompt text | — |
| `ansi` | Rendered thumbnail | Source prompt | — |
| `geometric` / `circuit` / `constellation` | Thumbnail image | Source prompt | — |

### Target-aware ingredient filtering

The ingredient checkboxes shown in the popover are **filtered to the selected
target**, not displayed as a flat list of everything. When the user clicks a
target button, the popover re-renders showing only the ingredients that target
can consume.

For targets where only one ingredient is useful, no checkboxes are shown — the
popover shows a plain description of what will happen ("The prompt text will be
used as the video prompt").

### Silent resolution rules

Technical format conversions are performed automatically, without UI, before
`_dispatch_remix` is called:

| Situation | Resolution |
|---|---|
| Source is video; target needs a still image (e.g. SkyReels I2V seed) | Extract first frame silently via `ffmpeg extract_frame` |
| Source is video; target needs a full video (Wan2.2-Animate motion ref) | Use file directly, no conversion |
| Source is SVG; target needs a raster image | Render via librsvg to PNG at 512×288 |
| Source is palette JSON; target needs text hint | Serialize as `palette: #hex1 #hex2 … lore-text` |
| Source has prompt text; target only accepts text | Use prompt, no binary ingredient |
| Any conversion fails | Fall back gracefully: use text hint only; log warning |

The rule is: **never block the user or show a dialog for a technical mismatch**.
The app picks the most useful automatic conversion. If even that fails, it
proceeds with whatever text context is available.

---

## 4. `RemixPopover` Widget

**File:** `app/remix_popover.py` (new file, no GTK dependency in tests)

```python
class RemixPopover(Gtk.Popover):
    """
    Remix popover widget. Anchored to a trigger button (hover bar or detail panel).

    Usage:
        pop = RemixPopover(record, on_remix=self._dispatch_remix)
        pop.set_parent(trigger_btn)
        pop.popup()
    """
    def __init__(self, record: "GenerationRecord | MediaRecord",
                 on_remix: "Callable[[RemixContext], None]"):
        ...
```

`on_remix` receives a `RemixContext` with all ingredients resolved and
conversions already completed (by the time `on_remix` fires, the hint string
is ready to inject and any seed image path points to a real file).

**`_build_ingredients(target_key)`** — called when a target button is focused
or hovered. Returns a list of `IngredientSpec` (label, value, default_on).
Updates the ingredient checkboxes and hint preview without closing the popover.

**`_resolve_ingredients(target_key, on_done)`** — runs in a background thread
(`threading.Thread`, daemon=True). Performs silent conversions by calling the
ffmpeg plugin tool functions directly (not via MCP at this layer — the plugin's
Python module is imported directly for in-process speed). While running, the
clicked target button shows a spinner label ("⟳ preparing…") and is
desensitized. `on_done(ctx: RemixContext)` is called via `GLib.idle_add` when
complete, which closes the popover and calls `_dispatch_remix`.

---

## 5. `_dispatch_remix(context: RemixContext)` in `MainWindow`

Replaces `_on_animate_card_action`, `_on_artgen_use_as_seed`, and the
`iterate_cb` routing.

```python
def _dispatch_remix(self, ctx: RemixContext) -> None:
    target = ctx.target_type
    if target == "animate":
        self._controls.switch_to_source("animate")
        self._controls.populate_prompts(ctx.hint, "", ctx.seed_image_path)
        if ctx.ref_video_path:
            self._controls._ref_video_path = ctx.ref_video_path
    elif target in ("video", "wan2", "mochi", "skyreels", "animatediff"):
        self._controls.switch_to_source("video")
        self._controls.populate_prompts(ctx.hint, "", ctx.seed_image_path)
    elif target == "image":
        self._controls.switch_to_source("image")
        self._controls.populate_prompts(ctx.hint, "", ctx.seed_image_path)
    elif target == "same":
        # "New variation" — same source, fresh seed
        self._controls.populate_prompts(ctx.hint, ctx.negative_hint)
    else:
        # artgen target: switch to art tab, set generator type, pre-fill theme
        self._controls._src_art_btn.set_active(True)
        self._artgen_panel.set_generator(target)
        self._artgen_panel.set_theme(ctx.hint)
    self._flash_status(f"Remix ready — {ctx.target_label} ✓")
```

`RemixContext` is extended with `seed_image_path`, `ref_video_path`,
`target_label`, and `negative_hint` fields alongside the existing
`source_record`, `source_type`, `target_type`, `hint`.

---

## 6. `ffmpeg` Utility Plugin

**Directory:** `plugins/ffmpeg/`

A utility plugin — not a content generator, so `x-ttlg.tab` is absent and it
does not appear in the Art tab generator picker. It is loaded by the plugin
system and exposed both as MCP tools and as importable Python functions for
in-process use by the remix engine.

### `plugins/ffmpeg/mcp.json` (excerpt)

```json
{
  "x-ttlg": {
    "output_ext": null,
    "media_type": null,
    "tab": null,
    "utility": true
  },
  "tools": [
    {
      "name": "ffmpeg_extract_frame",
      "description": "Extract a single frame from a video file as a JPEG or PNG image",
      "inputSchema": {
        "type": "object",
        "required": ["input_path", "output_path"],
        "properties": {
          "input_path":  {"type": "string", "description": "Path to input video file"},
          "output_path": {"type": "string", "description": "Path for the output image"},
          "timestamp":   {"type": "number", "description": "Time in seconds (default: 0)"},
          "format":      {"type": "string", "enum": ["jpg", "png"], "default": "jpg"}
        }
      }
    },
    {
      "name": "ffmpeg_get_metadata",
      "description": "Return duration, dimensions, fps, codec, and file size of a video or audio file",
      "inputSchema": {
        "type": "object",
        "required": ["input_path"],
        "properties": {
          "input_path": {"type": "string"}
        }
      }
    },
    {
      "name": "ffmpeg_convert_to_gif",
      "description": "Convert a video to an optimised animated GIF using a two-pass palette",
      "inputSchema": {
        "type": "object",
        "required": ["input_path", "output_path"],
        "properties": {
          "input_path":  {"type": "string"},
          "output_path": {"type": "string"},
          "fps":         {"type": "integer", "default": 12},
          "width":       {"type": "integer", "default": 480, "description": "Output width in px; height scales proportionally"}
        }
      }
    },
    {
      "name": "ffmpeg_convert_to_mp4",
      "description": "Re-encode a GIF or video file as an H.264 MP4 suitable for web playback",
      "inputSchema": {
        "type": "object",
        "required": ["input_path", "output_path"],
        "properties": {
          "input_path":  {"type": "string"},
          "output_path": {"type": "string"}
        }
      }
    },
    {
      "name": "ffmpeg_resize",
      "description": "Resize a video or image to the given dimensions, preserving aspect ratio",
      "inputSchema": {
        "type": "object",
        "required": ["input_path", "output_path", "width"],
        "properties": {
          "input_path":  {"type": "string"},
          "output_path": {"type": "string"},
          "width":       {"type": "integer"},
          "height":      {"type": "integer", "description": "If omitted, scales proportionally"}
        }
      }
    }
  ]
}
```

### `plugins/ffmpeg/plugin.py`

Implements each tool as a plain Python function (no `ArtGenerator` subclass —
utility plugins use a `UtilityPlugin` base class defined in `plugin_loader.py`).
All subprocess calls pass `stdin=subprocess.DEVNULL` and `check=True`. Raises
`subprocess.CalledProcessError` on non-zero exit; callers in the remix engine
catch this and apply the fallback rules from §3.

### Dual-use

- **In-process (remix engine):** `from plugins.ffmpeg.plugin import extract_frame`
  called synchronously in `RemixPopover._resolve_ingredients()`.
- **Via MCP (Claude Code / external tools):** exposed through `app/mcp_server.py`
  on port 8003 alongside all generator plugins. Available immediately after
  `tt-ctl mcp-config` is run.

---

## 7. Detail Panel Remix Row

The existing `↺ Iterate` button in the detail panel `GenerationCard` and
`DetailPanel` action rows is replaced with `🔀 Remix`. Clicking it opens the
same `RemixPopover` anchored to the button, with the same behaviour as from
the hover bar.

The GIF↔MP4 conversion buttons already in the detail panel (`→ MP4`, `→ GIF`)
are kept as-is — they are export utilities, not remix actions. They may
eventually migrate to the ffmpeg plugin's MCP surface, but that is out of scope
here.

---

## 8. Removals Summary

| Symbol | File | Replaced by |
|---|---|---|
| `animate_action_cb` param | `MainWindow._build_gallery_panel()` | `RemixPopover` via `GenerationCard` |
| `_on_animate_card_action()` | `main_window.py` | `_dispatch_remix()` |
| `ArtgenPanel.on_use_as_seed` | `artgen_panel.py` | `RemixPopover` → `_dispatch_remix()` |
| `ArtgenGallery.on_use_as_seed` | `artgen_gallery.py` | `RemixPopover` → `_dispatch_remix()` |
| `_on_artgen_use_as_seed()` | `main_window.py` | `_dispatch_remix()` |
| `_animate_cb` field | `GenerationCard` | removed |
| `animate_btn` widget | `GenerationCard` hover bar | `🔀 Remix` button |
| `.hover-action-btn-animate` CSS | `main_window.py` | removed |
| `↺ Iterate` button | `GenerationCard`, `DetailPanel` | `🔀 Remix` → "New variation" |
| `iterate_cb` param threading | `DetailPanel`, `DetailViewWindow` | `RemixPopover.on_remix` |

`populate_prompts()` on `ControlPanel` is **kept** — it is the write target
that `_dispatch_remix` calls internally. It does not need a public caller
outside `MainWindow`.

---

## 9. Test Plan

**`tests/test_remix_popover.py`**
- `RemixPopover` constructed with a video record shows correct source identity
- `remix_targets_for("palette")` returns expected plugin list (mocked registry)
- Ingredient toggles update hint preview string correctly
- Clicking a target calls `on_remix` with correctly populated `RemixContext`
- "New variation" target produces `target_type == "same"`

**`tests/test_remix_dispatch.py`**
- `_dispatch_remix` with `target_type="animate"` calls `switch_to_source("animate")` and `populate_prompts`
- `_dispatch_remix` with `target_type="video"` calls `switch_to_source("video")`
- `_dispatch_remix` with artgen target calls `set_generator` and `set_theme` on artgen panel

**`tests/test_ffmpeg_plugin.py`**
- `extract_frame` calls ffmpeg with correct arguments (mocked subprocess)
- `get_metadata` parses ffprobe JSON output correctly
- `convert_to_gif` performs two-pass palette generation (two subprocess calls)
- `convert_to_mp4` calls ffmpeg with correct H.264 flags
- CalledProcessError propagates correctly

**`tests/test_remix_resolution.py`**
- Video source + I2V target → `extract_frame` called, result path in context
- Video source + animate target → full video path in context, no extraction
- SVG source + image target → librsvg render called
- Any source + text-only target → hint string only, no binary path

---

## Open Questions (deferred)

- Should `🔀 Remix` appear on `PendingCard` (in-progress job)? Initial answer:
  no — wait until the artifact exists.
- Remix lineage in history: `remix_source_id` field on `GenerationRecord` and
  `MediaRecord` — deferred to a follow-on; not needed for the initial UI.
- Same-type artgen remix (verse → verse, palette → palette) — deferred; add by
  putting the generator's own type in its `accepts_remix_from` list when ready.
- Popover keyboard navigation — Tab between ingredient checkboxes and target
  buttons; Enter activates focused target. Deferred post-launch.
