# Create Surface Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Regroup the Create surface into three role zones (brief / direction / controls) with per-field markers, scope models to the active medium with a grouped browse door, bring modifier chips back as removable pills, and make width overflow structurally impossible — all without changing generation.

**Architecture:** A new pure `field_roles` module is the shared vocabulary (role + marker per field). A new `gtk_layout` module holds the width-capping container extracted from `pipeline_studio`. `create_param_panels` gains a `FieldSpec` descriptor, a `field_specs()` method per panel, a shared `RoleZonePanel` renderer, and a `ModifierPills` widget. `create_view` mounts `RoleZonePanel`, replaces the overflowing model strip with a scoped dropdown + grouped Model door, and clamps its surface width.

**Tech Stack:** Python 3, GTK4 via PyGObject, pytest (xvfb for widget tests). System interpreter `/usr/bin/python3`.

## Global Constraints

- **Migration-safe:** `MainWindow._on_generate` body and worker classes stay UNCHANGED. Each panel's `collect()` output dict (keys + values feeding `_on_create_generate` / `_on_generate` / the artgen `tt-ctl` dispatch) MUST stay byte-for-byte compatible. This is the hard invariant.
- **Palette:** tt-vscode-toolkit variant `#4FD1C5` on `#0F2A35`; never introduce docs-site forest-teal into the main app.
- **`_CSS` bytes literals are ASCII-only.** Marker glyphs (✎ ✨ ⚙) live only in Python string labels/tooltips, never inside a `b"""..."""` block.
- **Tests:** `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q`. Baseline before this plan: 1265 passed, 1 skipped.
- **Version discipline:** bump `VERSION` + prepend `debian/changelog` when landing (final task).
- **Local only:** no push / merge / PR.
- **Marker semantics (verbatim):** `MARK_WORDS` ✎ = raw text the model renders; `MARK_INTERPRETED` ✨ = the model/LLM decides from this value; `MARK_EXACT` ⚙ = deterministic, the model never reads it. `ROLE_BRIEF` / `ROLE_DIRECTION` / `ROLE_CONTROL` name the three zones.

---

### Task 1: `field_roles` — the shared taxonomy (pure, no GTK)

**Files:**
- Create: `app/field_roles.py`
- Test: `tests/test_field_roles.py`

**Interfaces:**
- Produces:
  - Constants `ROLE_BRIEF="brief"`, `ROLE_DIRECTION="direction"`, `ROLE_CONTROL="control"`; `MARK_WORDS="words"`, `MARK_INTERPRETED="interpreted"`, `MARK_EXACT="exact"`.
  - `@dataclass(frozen=True) FieldRole(role: str, marker: str)`.
  - `MARKER_GLYPH: dict[str,str]` = `{MARK_WORDS:"✎", MARK_INTERPRETED:"✨", MARK_EXACT:"⚙"}` and `MARKER_TIP: dict[str,str]` (the verbatim marker semantics above).
  - `classify_native(field_key: str) -> FieldRole`
  - `classify_artgen(spec) -> FieldRole` where `spec` is a duck-typed object with `.dest: str`, `.kind: str` (`"choice"|"bool"|"int"|"float"|"str"`), `.default` (the resolved default).
  - `classify_pipeline_field(kind: str, default=None, key: str = "") -> FieldRole` (basic now; sub-project 2 deepens).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_field_roles.py
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import field_roles as fr
from dataclasses import dataclass

@dataclass
class _Spec:  # stand-in for create_param_panels._ArgSpec
    dest: str; kind: str; default: object

def test_native_prompt_is_brief_words():
    assert fr.classify_native("prompt") == fr.FieldRole(fr.ROLE_BRIEF, fr.MARK_WORDS)

def test_native_negative_is_brief_words():
    assert fr.classify_native("negative_prompt") == fr.FieldRole(fr.ROLE_BRIEF, fr.MARK_WORDS)

def test_native_numeric_knobs_are_control_exact():
    for k in ("num_inference_steps", "seed", "guidance_scale", "num_frames"):
        assert fr.classify_native(k) == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)

def test_native_unknown_defaults_control_exact():
    assert fr.classify_native("wat").role == fr.ROLE_CONTROL

def test_artgen_subject_is_brief_words():
    assert fr.classify_artgen(_Spec("subject", "str", "a mountain")) == fr.FieldRole(fr.ROLE_BRIEF, fr.MARK_WORDS)

def test_artgen_numeric_is_control_exact():
    assert fr.classify_artgen(_Spec("width", "int", None)) == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)

def test_artgen_bool_is_direction_exact():
    assert fr.classify_artgen(_Spec("mountains", "bool", True)) == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_EXACT)

def test_artgen_random_default_choice_is_interpreted():
    assert fr.classify_artgen(_Spec("palette", "choice", "random")) == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_INTERPRETED)

def test_artgen_none_default_choice_is_interpreted():
    assert fr.classify_artgen(_Spec("ansi_style", "choice", None)).marker == fr.MARK_INTERPRETED

def test_artgen_fixed_choice_is_direction_exact():
    assert fr.classify_artgen(_Spec("colors", "choice", "256")) == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_EXACT)

def test_glyphs_present():
    assert fr.MARKER_GLYPH[fr.MARK_INTERPRETED] == "✨"
```

- [ ] **Step 2: Run to verify fail** — `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_field_roles.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement `app/field_roles.py`**

```python
"""field_roles.py — the shared field-role vocabulary for the Create surface
and (later) pipeline field configuration.

Two orthogonal axes describe every configurable field:
  ROLE_*  — which zone it belongs to (brief / direction / control)
  MARK_*  — how its value is used (words / interpreted / exact)

This module is pure (no GTK) so both create_param_panels and pipeline field
editors can classify fields identically — a field means the same thing
everywhere.
"""
from __future__ import annotations
from dataclasses import dataclass

ROLE_BRIEF = "brief"
ROLE_DIRECTION = "direction"
ROLE_CONTROL = "control"

MARK_WORDS = "words"
MARK_INTERPRETED = "interpreted"
MARK_EXACT = "exact"

MARKER_GLYPH = {MARK_WORDS: "✎", MARK_INTERPRETED: "✨", MARK_EXACT: "⚙"}
MARKER_TIP = {
    MARK_WORDS: "Your words — the model turns this into art.",
    MARK_INTERPRETED: "The model chooses based on this value.",
    MARK_EXACT: "Exact setting — the model never reads it.",
}


@dataclass(frozen=True)
class FieldRole:
    role: str
    marker: str


# Raw creative text the model renders.
_NATIVE_BRIEF = {"prompt", "negative_prompt", "avoid", "theme"}
# Deterministic knobs the model never interprets.
_NATIVE_CONTROL = {
    "num_inference_steps", "steps", "seed", "guidance_scale",
    "num_frames", "size", "resolution",
}
# Values that hand the choice to the model/generator.
_INTERPRETED_VALUES = {None, "random", "auto"}


def classify_native(field_key: str) -> FieldRole:
    if field_key in _NATIVE_BRIEF:
        return FieldRole(ROLE_BRIEF, MARK_WORDS)
    if field_key in _NATIVE_CONTROL:
        return FieldRole(ROLE_CONTROL, MARK_EXACT)
    # Safest default: treat an unknown key as an exact control, so it is never
    # mistaken for creative input.
    return FieldRole(ROLE_CONTROL, MARK_EXACT)


def classify_artgen(spec) -> FieldRole:
    dest = getattr(spec, "dest", "")
    kind = getattr(spec, "kind", "str")
    default = getattr(spec, "default", None)
    if dest in {"subject", "text", "prompt", "theme", "board_name", "tagline"}:
        return FieldRole(ROLE_BRIEF, MARK_WORDS)
    if kind in ("int", "float"):
        return FieldRole(ROLE_CONTROL, MARK_EXACT)
    if kind == "bool":
        return FieldRole(ROLE_DIRECTION, MARK_EXACT)
    # choice / str: does the default hand the decision to the model?
    if default in _INTERPRETED_VALUES:
        return FieldRole(ROLE_DIRECTION, MARK_INTERPRETED)
    return FieldRole(ROLE_DIRECTION, MARK_EXACT)


def classify_pipeline_field(kind: str, default=None, key: str = "") -> FieldRole:
    """Basic pipeline-field classifier (sub-project 2 deepens this)."""
    if key in _NATIVE_BRIEF or kind == "prompt":
        return FieldRole(ROLE_BRIEF, MARK_WORDS)
    if kind in ("int", "float", "number"):
        return FieldRole(ROLE_CONTROL, MARK_EXACT)
    if default in _INTERPRETED_VALUES:
        return FieldRole(ROLE_DIRECTION, MARK_INTERPRETED)
    return FieldRole(ROLE_DIRECTION, MARK_EXACT)
```

- [ ] **Step 4: Run to verify pass** — same command → PASS.
- [ ] **Step 5: Commit** — `git add app/field_roles.py tests/test_field_roles.py && git commit -m "feat(create): shared field_roles taxonomy (role + marker classifiers)"`

---

### Task 2: `gtk_layout` — extract the width-capping container

**Files:**
- Create: `app/gtk_layout.py`
- Modify: `app/pipeline_studio.py` (move `_MaxWidthBin` + `_wrap_centered` out; re-import)
- Test: `tests/test_gtk_layout.py`

**Interfaces:**
- Produces: `class MaxWidthBin(Gtk.Widget)` (the current `_MaxWidthBin`, renamed public) and `def wrap_centered(content: Gtk.Widget, max_width: int = 960) -> Gtk.Widget` (the current `_wrap_centered`).
- `pipeline_studio` keeps `_MaxWidthBin = MaxWidthBin` and `_wrap_centered = wrap_centered` as module-level aliases so its existing code/tests are untouched.

- [ ] **Step 1: Write failing test**

```python
# tests/test_gtk_layout.py
import sys; from pathlib import Path
sys.path.insert(0, "/usr/lib/python3/dist-packages")
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pytest
try:
    import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk
    Gtk.Box()
except Exception:
    pytest.skip("no GTK display", allow_module_level=True)
import gtk_layout

def test_wrap_centered_returns_widget_containing_content():
    content = Gtk.Label(label="x")
    w = gtk_layout.wrap_centered(content, 700)
    assert isinstance(w, Gtk.Widget)

def test_pipeline_studio_still_exposes_aliases():
    import pipeline_studio as ps
    assert ps._MaxWidthBin is gtk_layout.MaxWidthBin
    assert ps._wrap_centered is gtk_layout.wrap_centered
```

- [ ] **Step 2: Run to verify fail** — `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_gtk_layout.py -q` → FAIL.
- [ ] **Step 3: Implement** — Create `app/gtk_layout.py`: move the full `_MaxWidthBin` class body (currently `pipeline_studio.py:428`) into it as `class MaxWidthBin`, and `_wrap_centered` (currently `pipeline_studio.py:500`) as `def wrap_centered` (default `max_width` = the current `_CONTENT_MAX_WIDTH` value, copy that constant over). In `pipeline_studio.py`, delete both definitions, add `from gtk_layout import MaxWidthBin, wrap_centered` and `_MaxWidthBin = MaxWidthBin` / `_wrap_centered = wrap_centered` aliases near the old location. Preserve the `ps-content-column` CSS class usage.
- [ ] **Step 4: Run to verify pass** — `pytest tests/test_gtk_layout.py tests/test_pipeline_studio*.py -q` → PASS (pipeline_studio suite unaffected).
- [ ] **Step 5: Commit** — `git commit -am "refactor(layout): extract MaxWidthBin/wrap_centered to gtk_layout (shared width clamp)"`

---

### Task 3: `ModifierPills` widget

**Files:**
- Modify: `app/create_param_panels.py` (add `ModifierPills`)
- Test: `tests/test_modifier_pills.py`

**Interfaces:**
- Consumes: `chip_config.load_chips(kind)` → `list[ChipCategory(name, chips=[ChipEntry(label, text, tip)])]`.
- Produces: `class ModifierPills(Gtk.Box)` with:
  - `__init__(self, kind: str)` — loads banks for `kind` (`"video"|"image"|"animate"`); an unknown/empty kind yields no chips (no raise).
  - `applied_text(self) -> str` — space-joined `ChipEntry.text` of applied pills, in click order.
  - internal: an "applied" `Gtk.FlowBox` of removable pills + an "add" area of category-grouped `Gtk.FlowBox`es (wrapping — never a plain horizontal Box).

- [ ] **Step 1: Write failing tests** (xvfb header as in Task 2)

```python
import create_param_panels as cpp

def test_pills_start_empty():
    p = cpp.ModifierPills("image")
    assert p.applied_text() == ""

def test_add_then_applied_text(monkeypatch):
    # Force a known bank so the test doesn't depend on prompt_chips.yaml content.
    from chip_config import ChipCategory, ChipEntry
    monkeypatch.setattr(cpp, "load_chips_for_kind",
                        lambda k: [ChipCategory("Lighting", [ChipEntry("golden hour", "golden hour lighting", "")])])
    p = cpp.ModifierPills("image")
    p._apply_entry(ChipEntry("golden hour", "golden hour lighting", ""))
    assert p.applied_text() == "golden hour lighting"

def test_remove_drops_pill():
    from chip_config import ChipEntry
    p = cpp.ModifierPills("image")
    e = ChipEntry("neon", "neon glow", "")
    p._apply_entry(e); p._remove_entry(e)
    assert p.applied_text() == ""

def test_unknown_kind_no_crash():
    assert cpp.ModifierPills("nope").applied_text() == ""
```

- [ ] **Step 2: Run to verify fail** → FAIL.
- [ ] **Step 3: Implement** — Add a module-level `def load_chips_for_kind(kind): from chip_config import load_chips; try: return load_chips(kind) except Exception: return []` (seam the test monkeypatches). Implement `ModifierPills(Gtk.Box, orientation=VERTICAL)`: an applied `Gtk.FlowBox` (`selection_mode=NONE`) holding one removable pill button per applied entry (label + " ✕"; clicking calls `_remove_entry`), and per-category `Gtk.FlowBox` of dashed "+ label" add-buttons (clicking calls `_apply_entry`). Track applied entries in an ordered list `self._applied: list[ChipEntry]`. `_apply_entry` appends + re-renders the applied row; `_remove_entry` removes + re-renders. `applied_text()` returns `" ".join(e.text for e in self._applied)`. All rows are FlowBoxes (wrap). Use CSS classes `create-pill` / `create-addchip` (define later in Task 6's CSS block, ASCII only).
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(create): ModifierPills widget (removable, per-medium, wrapping)"`

---

### Task 4: `FieldSpec` + `field_specs()` on every panel

**Files:**
- Modify: `app/create_param_panels.py`
- Test: `tests/test_field_specs.py`

**Interfaces:**
- Produces:
  - `@dataclass FieldSpec(key: str, label: str, kind: str, default, role: "field_roles.FieldRole", choices: list = None, tooltip: str = "")`.
  - `CreateParamPanel.field_specs(self) -> list[FieldSpec]` (abstract). Each concrete panel implements it. `collect()` stays exactly as-is (compatibility anchor).
  - Native panels build specs with `role=field_roles.classify_native(key)`; `ArtgenParamPanel.field_specs()` maps each `_ArgSpec` to a `FieldSpec` with `role=field_roles.classify_artgen(argspec)`.

- [ ] **Step 1: Write failing tests**

```python
import create_param_panels as cpp
import field_roles as fr

def test_image_panel_field_specs_roles():
    specs = {s.key: s for s in cpp.ImageParamPanel().field_specs()}
    assert specs["num_inference_steps"].role == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)
    assert specs["negative_prompt"].role == fr.FieldRole(fr.ROLE_BRIEF, fr.MARK_WORDS)
    assert specs["model"].kind == "model"   # model handled specially, not a zone field

def test_artgen_panel_field_specs_use_classifier():
    p = cpp.ArtgenParamPanel("landscape")
    roles = {s.key: s.role for s in p.field_specs()}
    # mountains is a bool -> direction/exact
    assert roles["mountains"] == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_EXACT)
    # palette default "random" -> direction/interpreted
    assert roles["palette"].marker == fr.MARK_INTERPRETED

def test_collect_unchanged_image():
    # collect() must still produce the legacy dict shape.
    d = cpp.ImageParamPanel().collect()
    assert set(d) == {"negative_prompt", "num_inference_steps", "seed", "guidance_scale", "model"}
```

- [ ] **Step 2: Run to verify fail** → FAIL.
- [ ] **Step 3: Implement** — Add `FieldSpec`. Add abstract `field_specs()`. For `ImageParamPanel` return specs for `num_inference_steps`, `seed`, `guidance_scale`, `negative_prompt` (each `role=classify_native(key)`) plus a `model` spec with `kind="model"`, `choices=_IMAGE_MODEL_CHOICES`. `VideoParamPanel`/`AnimateParamPanel`: mirror using their own keys (video: `num_inference_steps`, `seed`, `num_frames`, `negative_prompt`, `model`; animate: `num_inference_steps`, `seed`, `animate_mode`→direction, `reference_video_path`/`reference_image_path`→brief-adjacent kind `"path"`; keep `collect()` identical). `ArtgenParamPanel.field_specs()`: iterate the `_ArgSpec` list it already introspects, emit one `FieldSpec(key=spec.dest, label=_humanize_dest(spec.dest), kind=spec.kind, default=spec.default, role=classify_artgen(spec), choices=spec.choices, tooltip=spec.help)`. Do NOT alter any `collect()`.
- [ ] **Step 4: Run to verify pass** → PASS (plus existing `test_create_param_panels.py` still green).
- [ ] **Step 5: Commit** — `git commit -am "feat(create): FieldSpec + field_specs() per panel (roles from field_roles); collect() unchanged"`

---

### Task 5: `RoleZonePanel` — the shared three-zone renderer (migration invariant)

**Files:**
- Modify: `app/create_param_panels.py`
- Test: `tests/test_role_zone_panel.py`

**Interfaces:**
- Consumes: `FieldSpec` (Task 4), `ModifierPills` (Task 3), `field_roles` glyphs (Task 1).
- Produces: `class RoleZonePanel(Gtk.Box)`:
  - `__init__(self, panel: CreateParamPanel, medium)` — calls `panel.build()`, reads `panel.field_specs()`, and lays the panel's already-built field widgets into three zones: a **Your brief** zone header holding the panel's `ROLE_BRIEF` fields (e.g. `negative_prompt` / "Avoid"); a **Direction** zone = a `ModifierPills(medium.kind)` followed by the panel's `ROLE_DIRECTION` fields, each label prefixed with `MARKER_GLYPH[spec.role.marker]` + a `MARKER_TIP` tooltip; and a collapsed **Controls** `Gtk.Expander` holding the `ROLE_CONTROL` fields in a wrapping grid. **The prompt is NOT owned here** — it persists on `CreateView` (survives medium swaps) and sits directly above the mounted RoleZonePanel, so the prompt and the brief zone read as one region.
  - `collect(self) -> dict` — returns `panel.collect()` **verbatim**. RoleZonePanel only re-parents the panel's existing widgets into zone containers; it never rebuilds them, so `panel.collect()` reads its own widgets unchanged and the dict stays byte-for-byte compatible. Modifier text is NOT injected here — CreateView appends it to the prompt (Task 6).
  - `applied_modifier_text(self) -> str` — delegates to the Direction zone's `ModifierPills.applied_text()`. CreateView reads this to build the final prompt.
  - `append_modifier_for_test(self, text: str) -> None` — test hook that applies a synthetic modifier entry (so Task 6 can assert prompt assembly without depending on bank contents).

  Implementation note: because RoleZonePanel re-parents (never rebuilds) the panel's widgets, `_active_panel` in CreateView becomes the `RoleZonePanel`, and `RoleZonePanel.collect()` transparently returns the wrapped panel's dict.

- [ ] **Step 1: Write failing tests**

```python
import create_param_panels as cpp
import field_roles as fr

def _medium(kind="image"):
    from create_mediums import Medium
    return Medium(id="image", label="Image", icon="🖼", kind=kind, source="native")

def test_zones_present_and_controls_collapsed():
    rp = cpp.RoleZonePanel(cpp.ImageParamPanel(), _medium())
    # brief, direction, controls expander all built
    assert rp._brief_zone is not None and rp._direction_zone is not None
    assert rp._controls_expander.get_expanded() is False

def test_collect_matches_legacy_image():
    legacy = cpp.ImageParamPanel(); legacy.build()
    rp = cpp.RoleZonePanel(cpp.ImageParamPanel(), _medium())
    # default (untouched) collect() must equal the legacy panel's default dict
    assert rp.collect() == legacy.collect()

def test_collect_matches_legacy_artgen():
    legacy = cpp.ArtgenParamPanel("landscape"); legacy.build()
    rp = cpp.RoleZonePanel(cpp.ArtgenParamPanel("landscape"), _medium("image"))
    assert rp.collect() == legacy.collect()

def test_interpreted_field_has_spark_glyph():
    rp = cpp.RoleZonePanel(cpp.ArtgenParamPanel("landscape"), _medium())
    labels = rp._direction_label_texts()   # test helper returning the rendered zone labels
    assert any("✨" in t for t in labels)
```

- [ ] **Step 2: Run to verify fail** → FAIL.
- [ ] **Step 3: Implement** `RoleZonePanel` per the interface above. Build zones as `Gtk.Frame`/`Gtk.Box` with header labels ("Your brief", "Direction", "Controls"). Add the `_direction_label_texts()` test helper returning the direction-zone field label strings. Ensure `collect()` returns the wrapped panel's dict verbatim (the panel keeps ownership of its non-brief widgets; only prompt/negative move into the brief zone, and `negative_prompt` is written back so `panel.collect()` still returns it — simplest: RoleZonePanel sets the panel's `_neg_entry` text from the brief-zone avoid field before delegating, OR reads avoid itself and overrides only `negative_prompt` in the returned dict). Keep it ASCII-safe.
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(create): RoleZonePanel three-zone renderer; collect() invariant preserved"`

---

### Task 6: CreateView — mount RoleZonePanel, scoped model dropdown, retire model strip, width clamp

**Files:**
- Modify: `app/create_view.py`
- Test: `tests/test_create_view.py` (extend), `tests/test_create_view_width.py` (new)

**Interfaces:**
- Consumes: `RoleZonePanel`, `gtk_layout.wrap_centered`.
- Produces: CreateView with `_model_strip` REMOVED; a `_model_dropdown` scoped to the active medium's models; `_swap_panel` mounts a `RoleZonePanel`; `_collect_params` assembles `prompt = prompt_entry + " " + active RoleZonePanel.applied_modifier_text()` and merges the RoleZonePanel `collect()` dict (same keys as before). `on_create(medium, params)` contract unchanged.

- [ ] **Step 1: Write failing tests**

```python
def test_no_persistent_model_strip(make_create_view):
    cv = make_create_view()
    assert not hasattr(cv, "_model_strip") or cv._model_strip is None

def test_surface_is_width_clamped(make_create_view):
    import gtk_layout
    cv = make_create_view()
    # some ancestor in the built tree is a MaxWidthBin
    assert cv._is_width_clamped()   # helper: walks children for a MaxWidthBin

def test_scoped_dropdown_lists_only_active_medium_models(make_create_view):
    cv = make_create_view()   # active = image
    keys = cv._scoped_model_keys()
    assert "flux" in keys and "wan2.2" not in keys

def test_collect_params_appends_modifier_text(make_create_view):
    cv = make_create_view()
    cv._prompt_entry.set_text("a castle")
    cv._active_panel.append_modifier_for_test("golden hour lighting")  # test hook
    params = cv._collect_params()
    assert params["prompt"] == "a castle golden hour lighting"

def test_collect_params_dict_keys_unchanged_for_image(make_create_view):
    cv = make_create_view()
    cv._prompt_entry.set_text("x")
    p = cv._collect_params()
    assert set(p) >= {"prompt", "negative_prompt", "num_inference_steps", "seed", "guidance_scale", "model"}
```

- [ ] **Step 2: Run to verify fail** → FAIL.
- [ ] **Step 3: Implement** — In `__init__`, delete the `_model_strip` box + its append + `_refresh_model_strip_async()` call; instead build `_model_dropdown` (a `Gtk.DropDown`) placed above `_panel_host`, populated by `_populate_model_dropdown(medium)` from `discover_mediums`/`server_manager.SERVERS` filtered to the medium's models (status dot via existing health map). `_swap_panel` wraps the chosen panel in `RoleZonePanel(panel, medium)` and mounts that. Wrap the whole surface via `gtk_layout.wrap_centered(...)`. Add `_is_width_clamped`, `_scoped_model_keys` test helpers. Extend `_collect_params` to append `self._active_panel.applied_modifier_text()` to the prompt (only when non-empty) and to read the RoleZonePanel `collect()`. Update the CSS block (`_apply_css`) with `create-pill`, `create-addchip`, zone header, dropdown classes — **ASCII only**, tt-vscode-toolkit palette. Keep `on_create` routing untouched.
- [ ] **Step 4: Run to verify pass** — `pytest tests/test_create_view.py tests/test_create_view_width.py tests/test_main_window_create_generate.py -q` → PASS (generation-routing tests unaffected).
- [ ] **Step 5: Commit** — `git commit -am "feat(create): role-zoned CreateView; scoped model dropdown; model strip retired; width clamped"`

---

### Task 7: CreateView Model door — grouped, wrapping model grid

**Files:**
- Modify: `app/create_view.py`
- Test: `tests/test_create_view.py` (extend)

**Interfaces:**
- Consumes: `server_manager.SERVERS`, `discover_mediums`, `_server_key_to_medium_id`, `_on_model_card_clicked` (all existing).
- Produces: `_build_model_door() -> Gtk.Widget` rendering type groups (Image / Video / Animate / Text) each a header + a wrapping `Gtk.FlowBox` of status-dotted model cards; empty groups omitted. Shown when `_set_entry_mode("model")`.

- [ ] **Step 1: Write failing tests**

```python
def test_model_door_groups_by_type(make_create_view):
    cv = make_create_view()
    groups = cv._model_door_groups()   # dict[str, list[str]] group title -> model keys
    assert set(groups) <= {"Image", "Video", "Animate", "Text"}
    assert "flux" in groups["Image"]
    assert all(v for v in groups.values())   # no empty groups

def test_model_door_card_click_routes_to_medium(make_create_view):
    cv = make_create_view()
    cv._activate_model_card("flux")
    assert cv._active_medium.id == "image"
    assert cv._entry_mode == "idea"
```

- [ ] **Step 2: Run to verify fail** → FAIL.
- [ ] **Step 3: Implement** — Add `_model_door_groups()` classifying each `server_manager.SERVERS` key into Image/Video/Animate/Text (by its medium kind / known text-model keys). `_build_model_door()` renders a `Gtk.Box` (vertical) of group sections, each a header `Gtk.Label` + wrapping `Gtk.FlowBox` of cards; card click → existing `_on_model_card_clicked` (which sets medium + switches to idea). `_activate_model_card` is the test-visible entry to that path. Wire `_set_entry_mode("model")` to show this door in place of the old flat strip cards.
- [ ] **Step 4: Run to verify pass** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(create): grouped, wrapping Model door (Image/Video/Animate/Text)"`

---

### Task 8: Version bump, changelog, CLAUDE.md note

**Files:**
- Modify: `VERSION`, `debian/changelog`, `CLAUDE.md`

- [ ] **Step 1:** Set `VERSION` to `0.28.0` (new user-visible feature).
- [ ] **Step 2:** Prepend a `debian/changelog` stanza (0.28.0, noble, urgency=medium) summarizing: role zones (brief/direction/controls) with ✎/✨/⚙ markers; scoped model dropdown + grouped Model door replacing the overflowing strip; modifier pills back as removable chips; shared `field_roles` layer; width clamped by construction. Author `Taylor Singletary <tsingletary@tenstorrent.com>`, date next in sequence.
- [ ] **Step 3:** Add a short CLAUDE.md subsection under the Create-surface notes describing `field_roles` + `RoleZonePanel` + `ModifierPills` + the migration invariant (collect() dict compatibility).
- [ ] **Step 4:** Full suite — `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q` → all green (no new failures vs. the 1265/1-skip baseline plus this plan's new tests).
- [ ] **Step 5: Commit** — `git commit -am "chore: release v0.28.0 -- role-zoned Create surface"`

---

## Notes for the executor

- **The migration invariant is the whole ballgame.** Tasks 4-6 must not change what any `collect()` returns for a given widget state. Task 5's `test_collect_matches_legacy_*` and Task 6's key-set tests are the guardrails — if they fail, the fix is in the new code, never in loosening the assertion.
- Generation code (`_on_generate`, workers, `tt-ctl` dispatch) is out of bounds.
- The legacy tabs/ControlPanel remain the fallback; do not delete them.
