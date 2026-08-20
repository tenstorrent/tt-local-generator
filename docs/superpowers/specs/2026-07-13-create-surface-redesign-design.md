# Create Surface Redesign — role-grouped fields, scoped models, modifier pills

**Date:** 2026-07-13
**Branch:** `feat/pipeline-editor` (local; not merged)
**Status:** design approved (mockup: claude.ai/code/artifact/ada2e596-bce6-40a4-9243-08d2d4f71e42)

## Problem

Four user-reported problems with the new Create surface (`app/create_view.py`),
all facets of one root cause — **the surface doesn't distinguish what a field
does**:

1. **Width overflow "yet again."** The persistent `_model_strip` is a plain
   horizontal `Gtk.Box` (no wrap, no scroll). With ~15 models it runs off the
   right edge of the window.
2. **No scalable model presentation.** ~15 models (growing) in one flat row
   doesn't scale and gives no grouping.
3. **Lost the "fun" of the modifiers.** The old `ControlPanel`'s categorized
   modifier chips (Camera/Shot, Lighting, Motion/Mood, Style — `_VIDEO_CHIPS`
   / `_IMAGE_CHIPS` / `_ANIMATE_CHIPS`, loaded from `config/prompt_chips.yaml`)
   never came over to CreateView.
4. **LLM-field ambiguity.** You can't tell which fields are raw creative input
   the model turns into art, which are choices the model/LLM *interprets*, and
   which are deterministic knobs the model never sees.

The user added: **this field-role clarity must apply to pipeline field
configuration too** — a field should mean the same thing in Create and in
Remix/Compose.

## Goals

- Group Create fields into three role zones so structure carries meaning.
- Mark every field with how its value is used (words / interpreted / exact).
- Retire the overflow-prone model strip; scope models to the active medium,
  with a grouped browse surface for the whole collection.
- Bring the modifier chips back as visible, removable pills.
- Make width overflow impossible by construction.
- Factor the field-role vocabulary into a **shared layer** both CreateView and
  (in a follow-on) pipeline field config consume.

## Non-goals / scope split

This spec covers **sub-project 1 only**: the Create surface redesign plus the
shared `field_roles` layer. Explicitly deferred:

- **Follow-on spec (sub-project 2):** adopt `field_roles` + `ModifierPills` in
  Pipeline node field configuration (Remix/Compose). The shared layer is built
  here so that adoption is small.
- **Deleting the legacy tabs / ControlPanel / ArtgenPanel.** They remain the
  reachable fallback until the hardware smoke test (unchanged from v0.27.x).

## Global constraints (bind every task)

- **Migration-safe.** Generation must not break. `MainWindow._on_generate`'s
  body and the worker classes stay UNCHANGED. The param dict each panel's
  `collect()` produces — the exact keys/values `_on_create_generate` /
  `_on_generate` / the `tt-ctl` artgen dispatch consume — MUST remain
  byte-for-byte compatible; this is the hard invariant, guarded by tests.
- **Palette: tt-vscode-toolkit variant** (`#4FD1C5` on `#0F2A35`). The main app
  is an editor-style surface; do NOT introduce the docs-site forest-teal.
- **`_CSS` bytes literals are ASCII-only.** Marker glyphs (✎ ✨ ⚙) go in Python
  string labels/tooltips, never inside a `b"""..."""` CSS block.
- **System python** `/usr/bin/python3`; tests via
  `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q`.
- **Version discipline:** bump `VERSION` + prepend a `debian/changelog` stanza
  when landing.
- **Local only.** No push / merge / PR — branch reconciliation is the user's
  call.

## Architecture

Five units, each with one responsibility.

### 1. `app/field_roles.py` (NEW — pure, no GTK)

The shared vocabulary. Two orthogonal axes:

- **Role** — which zone the field belongs to:
  `ROLE_BRIEF` · `ROLE_DIRECTION` · `ROLE_CONTROL`.
- **Marker** — how the value is used:
  `MARK_WORDS` (✎ raw text the model renders) ·
  `MARK_INTERPRETED` (✨ the model/LLM decides from this value) ·
  `MARK_EXACT` (⚙ deterministic, the model never reads it).

```python
@dataclass(frozen=True)
class FieldRole:
    role: str      # ROLE_*
    marker: str    # MARK_*

MARKER_GLYPH = {MARK_WORDS: "✎", MARK_INTERPRETED: "✨", MARK_EXACT: "⚙"}
MARKER_TIP   = {MARK_WORDS: "Your words — the model turns this into art.",
                MARK_INTERPRETED: "The model chooses based on this value.",
                MARK_EXACT: "Exact setting — the model never reads it."}

def classify_native(field_key: str) -> FieldRole: ...
def classify_artgen(spec: "_ArgSpec") -> FieldRole: ...
def classify_pipeline_field(descriptor) -> FieldRole: ...   # basic now; sub-project 2 deepens
```

**Native rules (by key):** `prompt` → brief/words; `negative_prompt`/`avoid`
→ brief/words; `steps`/`seed`/`guidance_scale`/`num_inference_steps`/`size`/
`num_frames`/`resolution` → control/exact; `model` handled by CreateView's
scoped dropdown (not a zone field).

**Artgen rules (from `_ArgSpec` — kind/default/dest):**
- dest in {`subject`,`text`,`prompt`,`theme`,`board_name`,`tagline`} → brief/words.
- kind in (`int`,`float`) → control/exact.
- kind == `bool` → direction/exact (a deterministic composition switch).
- kind in (`choice`,`str`): resolved default is `None` or in {`"random"`,`"auto"`}
  → direction/**interpreted** (✨); otherwise direction/exact.

Rules are documented in-module; the point is a single honest source of truth.

### 2. `app/gtk_layout.py` (NEW — extract, don't reinvent)

Move `_MaxWidthBin` and `_wrap_centered` out of `pipeline_studio.py` into a
shared module. `pipeline_studio.py` imports them from here (keep its existing
private names as thin re-exports so its code/tests are untouched). CreateView
uses `_wrap_centered` to clamp its surface to a centered ~760px column.

### 3. `app/create_param_panels.py` (MODIFY)

Separate **what fields exist** (per medium) from **how they're laid out**
(shared), so the same layout serves every medium and — later — pipeline fields.

- **`FieldSpec`** (NEW dataclass): `key, label, kind, default, choices,
  role_override=None, tooltip=""`. A panel emits a list of these.
- **`CreateParamPanel.field_specs() -> list[FieldSpec]`** (NEW): each native
  panel returns its explicit specs; `ArtgenParamPanel` derives specs from its
  existing `_ArgSpec` introspection. Panels keep their current `collect()`
  contract as the compatibility anchor.
- **`RoleZonePanel`** (NEW widget): given `field_specs` + the medium + the
  resolved modifier banks, it renders the three zones:
  - **Your brief** — the shared prompt textarea + an "Avoid" field. (The brief
    text is owned here so it's identical across mediums.)
  - **Direction** — the `ModifierPills` widget (applied pills + grouped "Add"
    chips) followed by each direction field, each prefixed with its marker
    glyph + tooltip; ✨ fields read as "the model chooses".
  - **Controls** — a collapsed `Gtk.Expander` holding the exact/deterministic
    fields in a wrapping grid.
  `RoleZonePanel.collect()` assembles the param dict: brief text with applied
  modifier text appended, plus each field's read value — producing **exactly**
  the dict the medium's old panel produced (invariant #1).
- **`ModifierPills`** (NEW widget): per-medium banks via
  `chip_config.load_chips(kind)` resolved by `medium.kind` (image/video/animate;
  a medium with no matching bank simply shows no modifier chips — graceful).
  Tapping an "Add" chip creates a removable pill; `applied_text()` returns the
  space-joined `ChipEntry.text` of applied pills for `collect()` to append.
  Built to be reused verbatim on pipeline text fields in sub-project 2.

### 4. `app/create_view.py` (MODIFY)

- **Retire `_model_strip`** entirely (delete the persistent horizontal box).
- **Scoped model dropdown** (Idea door): a `Gtk.DropDown` above the panel
  listing only the active medium's models, each with a live-status dot; changing
  medium repopulates it. Feeds `model_id` into `collect()` (same key as today).
- **Model door** (`_set_entry_mode("model")`): replace the flat cards with a
  wrapping grid grouped by type — **Image / Video / Animate / Text** — each card
  a `Gtk.Button` with a status dot. Clicking sets medium+model and switches to
  the Idea door pre-scoped (existing `_on_model_card_clicked` /
  `_server_key_to_medium_id` routing reused). Grouping data comes from
  `server_manager.SERVERS` + `discover_mediums`.
- **Mount `RoleZonePanel`** in `_panel_host` instead of the raw panel.
- **Width discipline:** wrap the whole surface via `gtk_layout._wrap_centered`;
  every multi-item row (medium chips, model groups, modifier chips, controls
  grid) is a wrapping `Gtk.FlowBox`. No unbounded horizontal `Gtk.Box`.

### 5. `config/prompt_chips.yaml` (NO CHANGE required)

Modifier banks are reused by `medium.kind`. Artgen image-like mediums map to
the image bank; mediums with no matching bank show only their interpreted/
direction fields (no modifier chips). Adding a dedicated bank for text mediums
is a YAGNI-deferred nicety, not required here.

## Data flow

1. User picks a medium (chip) or a model (Model door → medium+model).
2. CreateView builds a `RoleZonePanel` from the medium's `field_specs()` and
   `ModifierPills` for `medium.kind`.
3. User writes the brief, taps modifier chips (→ removable pills), sets any
   direction choices, optionally expands Controls.
4. **Create** → `RoleZonePanel.collect()` returns the param dict (brief +
   appended modifier text + fields) → `CreateView.on_create(medium, params)` →
   `MainWindow._on_create_generate` (unchanged routing).

## Error handling

- Missing/empty modifier bank for a `kind` → no modifier chips (no crash).
- A medium with no models → dropdown shows a disabled "no models" row; Model
  door omits empty groups.
- Marker glyphs are plain unicode in labels; the classifier never raises —
  unknown keys default to `control/exact` (safest: shown as an exact setting).

## Testing

- `field_roles`: classify_native + classify_artgen rule table (each role/marker
  path); unknown key → control/exact.
- `RoleZonePanel`: fields land in the correct zone; markers render; **`collect()`
  output equals the legacy panel's dict for image/video/animate/one artgen
  medium** (the migration invariant).
- `ModifierPills`: add creates a pill, remove drops it, `applied_text()` joins
  in order; empty bank → no chips.
- Scoped dropdown: lists only the active medium's models; medium switch
  repopulates.
- Model door: groups Image/Video/Animate/Text; empty groups omitted; click
  routes to medium+model and Idea door.
- Width: assert no direct child of the surface is an unbounded horizontal
  `Gtk.Box` of model cards; surface is wrapped in `_MaxWidthBin`.
- `gtk_layout`: `_MaxWidthBin` caps child width (move existing pipeline_studio
  coverage or add equivalent); pipeline_studio still imports it.

## File summary

| File | Change |
|---|---|
| `app/field_roles.py` | NEW — role/marker taxonomy + classifiers |
| `app/gtk_layout.py` | NEW — `_MaxWidthBin`/`_wrap_centered` extracted from pipeline_studio |
| `app/create_param_panels.py` | `FieldSpec`, `field_specs()`, `RoleZonePanel`, `ModifierPills` |
| `app/create_view.py` | retire `_model_strip`; scoped dropdown; grouped Model door; mount RoleZonePanel; width clamp |
| `app/pipeline_studio.py` | import `_MaxWidthBin`/`_wrap_centered` from `gtk_layout` |
| `tests/…` | field_roles, RoleZonePanel (+collect invariance), ModifierPills, model dropdown/door, width, gtk_layout |
