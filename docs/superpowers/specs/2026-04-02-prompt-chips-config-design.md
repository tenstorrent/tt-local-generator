# Prompt Chips Config Design

**Date:** 2026-04-02  
**Status:** Approved

## Context

Prompt style chips are hardcoded as two Python list constants (`_PROMPT_CHIPS`, `_IMAGE_PROMPT_CHIPS`) inside `main_window.py`. Adding, removing, or tweaking chips requires editing Python source. The lists have no real structure — category groupings exist only as inline comments, invisible in the UI. This makes the chip set hard to curate and hard to extend without touching core code.

---

## Goal

Move chip definitions to `config/prompt_chips.yaml` (versioned with the app). Add a dedicated `chip_config.py` loader module. Update the UI to render chips in named category groups with wrapping layout instead of a single flat scrollable row.

---

## YAML Schema

**File:** `config/prompt_chips.yaml`

A top-level list of category objects. Each category contains a list of chip objects.

```yaml
- name: Camera / Shot
  for: [video, animate]       # which tabs show this category
  chips:
    - label: "🎥 cinematic"
      text: "cinematic shot"
      tip: "Wide-format filmic look"

    - label: "💡 studio"
      text: "studio lighting, soft box"
      tip: "Clean professional lighting"
      for: [image]             # chip-level override replaces category's for:
```

**Field rules:**

| Field | Level | Required | Default |
|-------|-------|----------|---------|
| `name` | category | yes | — |
| `for` | category | no | `[video, image, animate]` |
| `chips` | category | yes | — |
| `label` | chip | yes | — |
| `text` | chip | yes | — |
| `tip` | chip | no | `""` |
| `for` | chip | no | inherits category's `for:` |

**`for:` semantics:** A chip's `for:` field, when present, **replaces** (does not merge with) the category's `for:`. A chip is shown in a tab if the resolved `for:` list contains that tab's key (`"video"`, `"image"`, or `"animate"`).

Valid tab keys: `video`, `image`, `animate`.

---

## `chip_config.py` Module

**File:** `chip_config.py` (repo root, alongside `worker.py`, `api_client.py`)

Public interface:

```python
@dataclass
class ChipEntry:
    label: str      # button label (may include emoji)
    text: str       # text appended to prompt on click
    tip: str        # tooltip (empty string if omitted)

@dataclass
class ChipCategory:
    name: str               # display name shown as group header
    chips: list[ChipEntry]  # chips in this category

def load_chips(tab: str, config_path: Path | None = None) -> list[ChipCategory]:
    """
    Load chip categories for a given tab key ("video", "image", or "animate").
    config_path defaults to <repo_root>/config/prompt_chips.yaml.
    Returns a list of ChipCategory objects whose chips are filtered to `tab`.
    Categories with no matching chips after filtering are omitted.
    Raises FileNotFoundError if the config file is missing.
    Raises ValueError with a descriptive message on schema errors.
    """
```

**Error handling:** If the YAML file is missing or malformed, `load_chips()` raises. `main_window.py` catches this at startup and falls back to an empty list, logging the error to stderr. The app remains functional without chips.

**No caching inside `chip_config.py`** — the caller (`main_window.py`) calls `load_chips()` once at startup and holds the result.

---

## `main_window.py` Changes

### Remove

- `_PROMPT_CHIPS` and `_IMAGE_PROMPT_CHIPS` module-level constants (deleted entirely)

### Add at module level

```python
from chip_config import load_chips, ChipCategory

def _load_chips_safe(tab: str) -> list:
    try:
        return load_chips(tab)
    except Exception as e:
        print(f"Warning: could not load chips for '{tab}': {e}", file=sys.stderr)
        return []

_VIDEO_CHIPS  = _load_chips_safe("video")
_IMAGE_CHIPS  = _load_chips_safe("image")
_ANIMATE_CHIPS = _load_chips_safe("animate")
```

### `_make_chips_box()` rewrite

Replace the current flat `Gtk.Box(HORIZONTAL)` implementation with a vertical grouped layout:

```python
def _make_chips_box(self, source: str) -> Gtk.Box:
    categories = {
        "video":   _VIDEO_CHIPS,
        "image":   _IMAGE_CHIPS,
        "animate": _ANIMATE_CHIPS,
    }.get(source, [])

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    outer.set_margin_start(2)
    outer.set_margin_end(2)
    outer.set_margin_top(2)
    outer.set_margin_bottom(2)

    for cat in categories:
        # Category label
        lbl = Gtk.Label(label=cat.name)
        lbl.set_xalign(0)
        lbl.add_css_class("chips-category-lbl")
        outer.append(lbl)

        # FlowBox of chip buttons
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_row_spacing(3)
        flow.set_column_spacing(4)
        for chip in cat.chips:
            btn = Gtk.Button(label=chip.label)
            btn.set_tooltip_text(chip.tip)
            btn.add_css_class("chip-btn")
            btn.connect("clicked", lambda _b, t=chip.text: self._append_to_prompt(t))
            flow.append(btn)
        outer.append(flow)

    return outer
```

### Scroll container update

The current `_chips_scroll` uses `set_policy(AUTOMATIC, NEVER)` for horizontal-only scrolling. Change to `set_policy(NEVER, AUTOMATIC)` for vertical-only scrolling to accommodate the new multi-row layout.

### New CSS class

Add `.chips-category-lbl` to `_CSS`:

```css
.chips-category-lbl {
    color: @tt_text_muted;
    font-size: 10px;
    margin-top: 4px;
}
```

---

## `config/prompt_chips.yaml`

Migrate all existing chips from the two Python lists into the YAML file, preserving every label/text/tip exactly. Assign `for:` to each category based on the existing split:

- Current `_PROMPT_CHIPS` categories → `for: [video, animate]` (camera, motion chips relevant to both)
- Current `_IMAGE_PROMPT_CHIPS` categories → `for: [image]`
- Shared categories (lighting, quality modifiers that exist in both lists) → `for: [video, image, animate]`, removing per-list duplicates

---

## File Map

| File | Action |
|------|--------|
| `chip_config.py` | Create — loader, `ChipEntry`, `ChipCategory`, `load_chips()` |
| `config/prompt_chips.yaml` | Create — full chip definitions migrated from Python lists |
| `main_window.py` | Remove `_PROMPT_CHIPS`/`_IMAGE_PROMPT_CHIPS`; update `_make_chips_box()`; add `_load_chips_safe()`; add CSS; update scroll policy |
| `tests/test_chip_config.py` | Create — pytest tests for `chip_config.py` (no GTK) |

---

## Testing

`chip_config.py` has zero GTK dependencies and can be fully unit-tested:

- `load_chips("video")` returns categories whose chips all have `"video"` in their resolved `for:`
- Category-level `for:` is inherited by chips that don't specify their own
- Chip-level `for:` overrides category `for:`
- Categories with no matching chips after tab filtering are excluded from results
- Missing YAML file raises `FileNotFoundError`
- Malformed YAML (missing required field) raises `ValueError`
- Empty YAML file returns `[]`

---

## What's Out of Scope

- Hot-reload / file watching (startup-only load)
- Per-user override file in `~/.config/`
- Animate tab chips (the Animate model doesn't have a prompt requirement; adding chips there is trivial once the YAML exists)
- UI to edit chips from within the app
