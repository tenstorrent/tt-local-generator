# Pipeline UX Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Pipeline surface legible end to end — self-explaining steps, a per-step model picker matching Create, real live run progress, and a run page that leads with "here's what you made" (also registered into the Library).

**Architecture:** Additive UI over the existing pipeline data model. Steps gain an auto-derived flow line + a short intent `summary`. A new shared `app/model_picker.py` (built on `server_manager` + `ModelStatusService`, the same primitives Create uses) renders a per-step model dropdown whose choice writes a canonical `model` literal the engine already routes (extended for parity). `LiveRunView` gains a spinner + live phase + elapsed + step count fed by a pure reducer. `OpenView` leads with a final-result hero (promoting the already-computed `hero_path`), and `MainWindow` registers the final deliverable into the Library on run-done.

**Tech Stack:** Python 3 (system `/usr/bin/python3`), GTK4/PyGObject, pytest under `xvfb`, ComfyUI-API-v1 pipeline specs.

## Global Constraints

- GTK single-threaded: all run-progress + post-run UI updates marshal via `GLib.idle_add` (the `pipeline_runner._dispatch` → `_idle_add` pattern already does this).
- The model dropdown writes a normal scalar `model` literal into a node's `inputs`; `spec_remix.editable_params`/`apply_edits` semantics are unchanged (wire stays a wire, literal stays a literal). No spec-contract change.
- Picker offers ONLY models `pipeline_engine._backend_for` can route — extend `_backend_for` in lockstep (Task 4). The offer list and the routable set derive from the same source.
- Reuse the model SYSTEM, don't fork it: `app/model_picker.py` uses `server_manager.servers_for_capability`/`display_name_for`/`benefit_for` + `ModelStatusService` + the ●/◐/◌ glyph mapping. **CreateView is NOT refactored** (its 180-line `_populate_model_dropdown` is deeply coupled; migrating it is an explicit out-of-scope follow-up to avoid Create regressions).
- Library registration reuses Create's record patterns: raster/video → `history_store.GenerationRecord`; artgen kinds → `media_store.MediaRecord` (`generator_type="pipeline"`, provenance in `params`, `rec.media_file_path` alias, `_ms.add()` + `ensure_auto_playlists()`).
- Intent LANGUAGE stays tool-agnostic (verb/noun; never `TTLG`/class_type). `intent_vocab.label` contract preserved.
- `_CSS`/`b"""..."""` byte literals ASCII-only; glyphs live in Python `str` labels.
- System `/usr/bin/python3`; GTK tests `xvfb-run --auto-servernum /usr/bin/python3 -m pytest`; pure tests headless.
- Version bump (VERSION 0.73.0 → 0.74.0) + `debian/changelog` stanza in the finalize task.
- Known-flake deselects for full-suite runs: `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`, `tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`, `tests/test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen`.

## File Structure

- `app/intent_vocab.py` (modify) — `Intent.summary` field; pure `flow_line(intent)`; `capability_for_intent(class_type)`; per-intent summaries.
- `app/model_picker.py` (**new**) — shared per-capability model picker widget + pure `picker_entries(capability, snapshot, status_service_present)` core.
- `app/pipeline_studio.py` (modify) — step-card flow line/summary (RemixView + OpenView + LiveRunView); RemixView model-picker slot + `status_service` threading; LiveRunView spinner/phase/elapsed/step-count + log demotion; OpenView hero + secondary breakdown.
- `app/pipeline_engine.py` (modify) — `_backend_for` model→backend parity for every picker-offered model.
- `app/pipeline_view_model.py` (modify) — `RunView.final_index`/`is_final` promoting `hero_path`.
- `app/main_window.py` (modify) — thread `status_service` into `PipelineStudio`; register pipeline final into the Library on run-done; provenance card affordance.
- `app/pipeline_portfolio_view.py` (remove) — retire the legacy fixed-narrative surface.
- Tests beside existing suites in `tests/`.

---

### Task 1: Intent metadata — `summary`, `flow_line`, `capability_for_intent`

**Files:**
- Modify: `app/intent_vocab.py`
- Test: `tests/test_intent_vocab_flow.py`

**Interfaces — Produces:**
- `Intent.summary: str | None = None` (new dataclass field, appended so existing positional construction is unaffected).
- `flow_line(intent: Intent) -> str` — "Takes {input} → makes {output}" (source node → "Makes {output}").
- `capability_for_intent(class_type: str) -> str | None` — `TTLGTextToImage`→"image", `TTLGImageToVideo`/`TTLGMontage`→"video", `TTLGAnimateDiff`→"animatediff", `TTLGGenerateText`/`TTLGArtgenGenerate`→"artgen"; else None.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intent_vocab_flow.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import intent_vocab as iv


def test_flow_line_takes_and_makes():
    assert iv.flow_line(iv.intent_for("TTLGAnimateDiff")) == "Takes a prompt → makes a looping GIF"
    assert iv.flow_line(iv.intent_for("TTLGTextToImage")) == "Takes a prompt → makes an image"


def test_flow_line_source_node_has_no_takes():
    # TTLGArtgenGenerate is a source node (input_kind is None).
    assert iv.flow_line(iv.intent_for("TTLGArtgenGenerate")).startswith("Makes ")


def test_capability_for_intent():
    assert iv.capability_for_intent("TTLGTextToImage") == "image"
    assert iv.capability_for_intent("TTLGImageToVideo") == "video"
    assert iv.capability_for_intent("TTLGAnimateDiff") == "animatediff"
    assert iv.capability_for_intent("TTLGGenerateText") == "artgen"
    assert iv.capability_for_intent("TTLGCaptionImage") is None  # no model dimension


def test_summary_field_optional_and_present_for_key_intents():
    assert iv.intent_for("TTLGCaptionImage").summary is None or isinstance(
        iv.intent_for("TTLGCaptionImage").summary, str)
    # At least the marquee generative intents carry a summary.
    assert iv.intent_for("TTLGAnimateDiff").summary
    assert iv.intent_for("TTLGTextToImage").summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_intent_vocab_flow.py -q`
Expected: FAIL (`flow_line`/`capability_for_intent` undefined; `summary` missing).

- [ ] **Step 3: Write minimal implementation**

In `app/intent_vocab.py`: add `summary: str | None = None` as the LAST field of the `Intent` dataclass (after `output_kind`). Add per-intent `summary=` to the marquee generative intents at minimum (TextToImage, ImageToVideo, GenerateText, AnimateDiff, ArtgenGenerate, PaletteToPrompt) — one honest sentence each, tool-agnostic, e.g. AnimateDiff: `summary="Turns your words into a short looping animation."`. Then add:

```python
# Human noun for each artifact kind, used by flow_line.
_KIND_NOUN = {
    "text": "text", "image": "an image", "gif": "a looping GIF",
    "video": "a video", "palette": "a color palette", "playlist": "a collection",
}

def flow_line(intent: "Intent") -> str:
    """Plain-language 'Takes X → makes Y' for a step card. A source node
    (input_kind is None) has no 'Takes' clause."""
    out = _KIND_NOUN.get(intent.output_kind or "", intent.output_kind or "a result")
    if intent.input_kind is None:
        return f"Makes {out}"
    inp = _KIND_NOUN.get(intent.input_kind, intent.input_kind)
    # Brief-fed nodes read better as "a prompt" than "text" for text input.
    if intent.input_kind == "text":
        inp = "a prompt"
    return f"Takes {inp} → makes {out}"


# Intent (by class_type) -> model-picker capability, or None for intents with
# no model dimension. Keys mirror server_manager capabilities.
_CAPABILITY_FOR_INTENT = {
    "TTLGTextToImage": "image",
    "TTLGImageToVideo": "video",
    "TTLGMontage": "video",
    "TTLGAnimateDiff": "animatediff",
    "TTLGGenerateText": "artgen",
    "TTLGArtgenGenerate": "artgen",
}

def capability_for_intent(class_type: str) -> "str | None":
    return _CAPABILITY_FOR_INTENT.get(class_type)
```

(Note: `flow_line`'s TTLGAnimateDiff output is `gif` → "a looping GIF"; TextToImage input is `text` → "a prompt", output `image` → "an image". Matches the test.)

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_intent_vocab_flow.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/intent_vocab.py tests/test_intent_vocab_flow.py
git commit -m "feat(pipeline): Intent.summary + flow_line + capability_for_intent"
```

---

### Task 2: Step cards show the flow line + summary (compose/run/detail)

**Files:**
- Modify: `app/pipeline_studio.py` (`RemixView._build_step_card` ~1786; `LiveRunView._build_step_row` ~2832; `OpenView._build_step_row` ~1172)
- Test: `tests/test_pipeline_step_card_clarity.py`

**Interfaces — Consumes:** `intent_vocab.flow_line` (Task 1).

Add a shared helper so all three card builders render identically:

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_step_card_clarity.py
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
try:
    import gi; gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display", allow_module_level=True)

import pipeline_studio as ps


def _labels(widget, acc):
    if isinstance(widget, Gtk.Label):
        acc.append(widget.get_label())
    child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
    while child is not None:
        _labels(child, acc)
        child = child.get_next_sibling()
    return acc


def test_remix_step_card_shows_flow_line():
    rv = ps.RemixView()
    card = rv._build_step_card(2, "2", "TTLGAnimateDiff", [])
    texts = _labels(card, [])
    assert any("→ makes a looping GIF" in t for t in texts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_step_card_clarity.py -q`
Expected: FAIL (no flow-line label on the card).

- [ ] **Step 3: Write minimal implementation**

Add a module-level helper in `app/pipeline_studio.py`:

```python
def _append_intent_detail(box, intent):
    """Add the plain-language flow line (+ optional summary) under a step's
    intent label. Shared by RemixView/LiveRunView/OpenView step builders."""
    flow = Gtk.Label(label=flow_line(intent))
    flow.set_xalign(0)
    flow.add_css_class("ps-step-flow")
    box.append(flow)
    if intent.summary:
        summ = Gtk.Label(label=intent.summary)
        summ.set_xalign(0)
        summ.set_wrap(True)
        summ.add_css_class("ps-step-summary")
        box.append(summ)
```

Import `flow_line` alongside the existing `intent_for` import. In `RemixView._build_step_card`, after appending `intent_label` to `verb_col` (and before/around the `model_label` block), call `_append_intent_detail(verb_col, intent)`. In `LiveRunView._build_step_row` and `OpenView._build_step_row`, call `_append_intent_detail(main, step.intent)` right after appending `intent_row` to `main`. Add ASCII CSS classes `.ps-step-flow` (quiet accent) and `.ps-step-summary` (muted, smaller) to the `_CSS` block.

- [ ] **Step 4: Run test to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_step_card_clarity.py -q`
Then regression: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q -k "pipeline_studio or muse or open_view or live_run"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline_studio.py tests/test_pipeline_step_card_clarity.py
git commit -m "feat(pipeline): step cards show 'takes X -> makes Y' + summary (compose/run/detail)"
```

---

### Task 3: Shared model picker (`app/model_picker.py`)

**Files:**
- Create: `app/model_picker.py`
- Test: `tests/test_model_picker.py`

**Interfaces — Produces:**
- `picker_entries(capability, snapshot, has_service) -> list[PickerEntry]` — PURE. `PickerEntry = (key, display_name, benefit, dot_glyph)`. Uses `server_manager.servers_for_capability` + `display_name_for`/`benefit_for`; `capability == "animatediff"` returns the single synthetic AnimateDiff entry (always ● dot, benefit from `MODEL_BENEFITS`). `snapshot` is a `{key: Status}` dict (from `ModelStatusService.snapshot()`); dot via the shared glyph map (READY→●, STARTING→◐, else ◌; ● when no service).
- `class ModelPickerRow(Gtk.Box)` — `__init__(capability, status_service=None, selected_key=None, on_change=None)`; renders a `Gtk.DropDown` of the entries (label `"{dot} {display_name}"`) + a benefit sub-label; `selected_key()` returns the chosen server key; subscribes to `status_service` for live dots and unsubscribes on unrealize (mirror CreateView's subscribe/unrealize pattern). Single-entry capabilities auto-select index 0.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model_picker.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import model_picker as mp
import server_manager as sm


def test_picker_entries_image_lists_image_servers():
    entries = mp.picker_entries("image", snapshot={}, has_service=False)
    keys = [e[0] for e in entries]
    assert set(keys) == {s.key for s in sm.servers_for_capability("image")}
    assert all(len(e) == 4 for e in entries)  # (key, name, benefit, dot)


def test_picker_entries_animatediff_single_synthetic():
    entries = mp.picker_entries("animatediff", snapshot={}, has_service=True)
    assert len(entries) == 1
    key, name, benefit, dot = entries[0]
    assert key == "animatediff" and name == "AnimateDiff" and dot == "●" and benefit


def test_picker_entries_status_glyphs(monkeypatch):
    from model_status import Status
    snap = {s.key: Status.READY for s in sm.servers_for_capability("image")}
    first = mp.picker_entries("image", snapshot=snap, has_service=True)[0]
    assert first[3] == "●"
    snap2 = {k: Status.STARTING for k in snap}
    assert mp.picker_entries("image", snapshot=snap2, has_service=True)[0][3] == "◐"
    assert mp.picker_entries("image", snapshot={}, has_service=True)[0][3] == "◌"  # off/unknown


def test_dot_no_service_is_solid():
    assert mp.picker_entries("image", snapshot={}, has_service=False)[0][3] == "●"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_model_picker.py -q`
Expected: FAIL (`No module named 'model_picker'`).

- [ ] **Step 3: Write minimal implementation**

Create `app/model_picker.py` with the pure `picker_entries` first (GTK import guarded/lazy so the pure tests run headless):

```python
# app/model_picker.py — shared per-capability model picker (server_manager + ModelStatusService).
from __future__ import annotations
import server_manager as _sm

def _dot(status_present, status):
    if not status_present:
        return "●"
    try:
        from model_status import Status
    except Exception:
        return "●"
    if status == Status.READY: return "●"
    if status == Status.STARTING: return "◐"
    return "◌"

def picker_entries(capability, snapshot=None, has_service=False):
    """Pure: (key, display_name, benefit, dot) for every model that can perform
    `capability`. `snapshot` = {key: Status}; `has_service` gates live dots."""
    snapshot = snapshot or {}
    if capability == "animatediff":
        return [("animatediff", _sm.display_name_for("animatediff"),
                 _sm.benefit_for("animatediff"), "●")]
    out = []
    for sdef in _sm.servers_for_capability(capability):
        out.append((sdef.key, _sm.display_name_for(sdef.key),
                    _sm.benefit_for(sdef.key), _dot(has_service, snapshot.get(sdef.key))))
    return out
```

Then add `ModelPickerRow(Gtk.Box)` (GTK imported at module top guarded by the repo's standard `gi.require_version`): builds a `Gtk.DropDown` from `Gtk.StringList` of `f"{dot} {name}"`, a benefit sub-label under it, tracks `selected_key()` via an entries list, subscribes to `status_service.subscribe(...)` (rebuild labels on snapshot via `GLib.idle_add`) and unsubscribes on `"unrealize"`. `on_change(key)` fires on `notify::selected`.

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_model_picker.py -q` (pure), then `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_model_picker.py -q` (incl. any widget test you add).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/model_picker.py tests/test_model_picker.py
git commit -m "feat(pipeline): shared ModelPickerRow + pure picker_entries (server_manager + status)"
```

---

### Task 4: Engine `_backend_for` parity with the picker's offer list

**Files:**
- Modify: `app/pipeline_engine.py` (`_backend_for` image branch ~1149)
- Test: `tests/test_backend_parity.py`

**Interfaces — Consumes:** `intent_vocab.capability_for_intent` (Task 1), `model_picker.picker_entries` (Task 3).

The picker offers every `servers_for_capability("image")` model (flux, sdxl, z-image-turbo, motif), but `_backend_for` only routes flux/sdxl (everything else silently defaults to flux). Extend the image branch to route every offered image key.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backend_parity.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pipeline_engine as pe
import model_picker as mp
import server_manager as sm


def test_backend_for_routes_every_offered_image_model():
    for key, _name, _benefit, _dot in mp.picker_entries("image", has_service=False):
        spec = pe._backend_for("TTLGTextToImage", {"model": key})
        assert spec is not None
        # The chosen key must route to a real server, not silently to a wrong default.
        assert spec.key == key, f"{key} routed to {spec.key}"


def test_backend_for_video_keys():
    for key in ("wan2.2", "skyreels", "mochi"):
        assert pe._backend_for("TTLGImageToVideo", {"model": key}).key == key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_backend_parity.py -q`
Expected: FAIL (z-image-turbo/motif route to `flux`; possibly `wan2.2` from `"wan2.2"` passes but confirm).

- [ ] **Step 3: Write minimal implementation**

Rewrite the `TTLGTextToImage` branch of `_backend_for` (`app/pipeline_engine.py:1149`) to resolve the model string against the actual image `SERVERS` keys (exact-key first, then substring), so every offered key routes to itself:

```python
    if class_type == "TTLGTextToImage":
        m = str(model or "").lower()
        image_keys = [s.key for s in sm.servers_for_capability("image")]
        key = None
        for k in image_keys:                 # exact server-key match wins
            if m == k or k in m:
                key = k
                break
        if key is None:                      # legacy substrings
            if "flux" in m: key = "flux"
            elif "sdxl" in m or (m.startswith("sd") ): key = "sdxl" if "sdxl" in sm.SERVERS else "flux"
        if key is None or key not in sm.SERVERS:
            key = "flux"                     # default image backend
        return BackendSpec(key, sm.SERVERS[key].health_url, _MAX_WAIT_IMAGE)
```

Apply the same exact-key-then-substring pattern to the `TTLGImageToVideo` branch so `wan2.2`/`skyreels`/`mochi` server keys route to themselves (keep the `wan`/`skyreels`/`mochi` substring fallbacks + `wan2.2` default).

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_backend_parity.py tests/test_pipeline_engine.py -q --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline_engine.py tests/test_backend_parity.py
git commit -m "feat(pipeline): _backend_for routes every picker-offered image/video model (parity)"
```

---

### Task 5: Wire the model picker into RemixView step cards

**Files:**
- Modify: `app/pipeline_studio.py` (`RemixView.__init__`, `_build_step_card`, `_collect_edits`), `PipelineStudio.__init__` + RemixView construction; `app/main_window.py` (`PipelineStudio(...)` construction ~7081)
- Test: `tests/test_remix_model_picker.py`

**Interfaces — Consumes:** `model_picker.ModelPickerRow` + `picker_entries` (Task 3), `intent_vocab.capability_for_intent` (Task 1). **Produces:** RemixView writes a chosen `model` into the node's collected edits.

Thread `status_service` MainWindow → PipelineStudio → RemixView. In `_build_step_card`, when `capability_for_intent(class_type)` is not None, render a `ModelPickerRow` in the "Runs on" slot pre-selected from the node's current `model` input (if any); a single-engine capability (animatediff) shows the informational row. The picker's selection is folded into `_collect_edits` as the node's `model` value.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_remix_model_picker.py
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
try:
    import gi; gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display", allow_module_level=True)
import pipeline_studio as ps


def test_image_step_gets_a_model_picker_and_no_change_is_no_edit():
    rv = ps.RemixView()
    # A TextToImage node with a model literal — build its card, then collect with no change.
    rv.load_spec_for_test({"1": {"class_type": "TTLGTextToImage",
                                  "inputs": {"prompt": "a cat", "model": "flux"}}}) \
        if hasattr(rv, "load_spec_for_test") else None
    card = rv._build_step_card(1, "1", "TTLGTextToImage",
                               [ps.ParamField("1", "prompt", "Prompt", "text", "a cat")]) \
        if hasattr(ps, "ParamField") else rv._build_step_card(1, "1", "TTLGTextToImage", [])
    # A model picker widget is present on an image step.
    assert getattr(rv, "_model_pickers", {}).get("1") is not None


def test_non_model_step_has_no_picker():
    rv = ps.RemixView()
    rv._build_step_card(1, "1", "TTLGCaptionImage", [])
    assert getattr(rv, "_model_pickers", {}).get("1") is None
```

(The implementer should adapt the exact ParamField/spec-loading calls to RemixView's real API — the brief will point at `_build_step_card`/`_collect_edits`. The load-bearing assertions: an image/video/animatediff/artgen step has a `ModelPickerRow` recorded in `rv._model_pickers[node_id]`; a no-model step does not; and `_collect_edits` emits a `model` edit only when the picker's selection differs from the node's original `model`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_remix_model_picker.py -q`
Expected: FAIL (no `_model_pickers`).

- [ ] **Step 3: Write minimal implementation**

- `RemixView.__init__`: add `status_service=None` param; store `self._status_service`; init `self._model_pickers: dict[str, ModelPickerRow] = {}` and `self._model_orig: dict[str, str|None] = {}`.
- `_build_step_card`: compute `cap = capability_for_intent(class_type)`. If `cap`: read the node's current `model` (from the step's fields — a `model` ParamField's value, else None), build `picker = ModelPickerRow(cap, status_service=self._status_service, selected_key=<current or default>)`, place it in the "Runs on" slot of `verb_col`, and record `self._model_pickers[node_id] = picker`, `self._model_orig[node_id] = <current model>`. For `cap == "animatediff"` the row is the informational single entry. Do NOT also render a free-text `model` field — if a `model` ParamField exists for this node, skip it in the field loop (the picker owns it).
- `_collect_edits`: after the existing field diff, for each `node_id` in `self._model_pickers`, compare `picker.selected_key()` to `self._model_orig[node_id]`; if changed, add `edits.setdefault(node_id, {})["model"] = <selected key>`. (No change → no edit, preserving the byte-identical-untouched-run invariant.)
- `PipelineStudio.__init__`: add `status_service=None`; store it; pass `status_service=self._status_service` into `RemixView(...)`.
- `app/main_window.py:7081`: `PipelineStudio(inspire_fn=self._create_inspire_fn, status_service=self._status_service)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_remix_model_picker.py tests/ -q -k "remix or pipeline_studio" --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`
Expected: PASS (esp. the existing `_collect_edits` / RemixView tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline_studio.py app/main_window.py tests/test_remix_model_picker.py
git commit -m "feat(pipeline): per-step model picker in RemixView (reuses Create's model system)"
```

---

### Task 6: Live run progress — spinner + phase + elapsed + step count

**Files:**
- Modify: `app/pipeline_studio.py` (`LiveRunView`)
- Test: `tests/test_live_run_progress.py`

**Interfaces — Consumes:** the runner's `on_node_update(job, node_id, status, detail)` (detail already delivered; `LiveRunView` currently drops it).

Add a PURE reducer for the progress state, then render it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_live_run_progress.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pipeline_progress as pp   # new pure module


def test_reducer_tracks_step_count_and_phase():
    st = pp.ProgressState(total=2)
    st.update("1", "running", "sampling 5/25")
    assert st.current_index == 1 and st.running_node == "1"
    assert st.phase("1") == "sampling 5/25"
    st.update("1", "done", "")
    st.update("2", "running", "encoding")
    assert st.done_count == 1 and st.current_index == 2
    assert st.phase("2") == "encoding"
    st.update("2", "done", "")
    assert st.done_count == 2 and st.running_node is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_live_run_progress.py -q`
Expected: FAIL (`No module named 'pipeline_progress'`).

- [ ] **Step 3: Write minimal implementation**

Create `app/pipeline_progress.py` with a pure `ProgressState(total)` tracking per-node status + latest `detail` (`phase`), `done_count`, `running_node`, `current_index` (1-based position of the running/last node). Then in `LiveRunView`:
- `on_node_update`: for real nodes, feed the reducer AND surface `detail` as a per-step **phase sub-label** (a `Gtk.Label` per row, set from `detail`), swap the glyph for a `Gtk.Spinner` (`set_spinning(True)`) while `running`, stop/replace it with the ✓/✕ glyph on done/failed. Update a header "Step {current_index} of {total}".
- Per-step **elapsed**: on first `running` for a node, start a `GLib.timeout_add(1000, …)` updating an elapsed label; cancel on done/failed (store timer ids; cancel all in `on_finished`/`begin`).
- Demote the raw log: wrap `_log_box`'s scroller in a `Gtk.Expander(label="Details")` collapsed by default (keep `on_log` appending into it).
- The row builder returns the extra widgets (phase label, elapsed label, spinner) so `on_node_update` can address them (store in dicts keyed by node_id, like `_step_status_labels`).

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_live_run_progress.py -q` then `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q -k "live_run or pipeline_studio"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline_progress.py app/pipeline_studio.py tests/test_live_run_progress.py
git commit -m "feat(pipeline): live run progress — spinner + phase + elapsed + step count; log demoted"
```

---

### Task 7: Final-result hero on the run/detail page

**Files:**
- Modify: `app/pipeline_view_model.py` (`RunView`), `app/pipeline_studio.py` (`OpenView.set_run` + a hero builder)
- Test: `tests/test_pipeline_hero.py`

**Interfaces — Produces:** `RunView.final_index: int | None` — index into `steps` of the deliverable step (the step whose `artifact_path` equals `hero_path`, i.e. the last hero-kind artifact; None if none). Promotes the currently-unused `hero_path`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_hero.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pipeline_view_model as vm
from intent_vocab import intent_for


def _step(node, ct, path=None):
    return vm.StepView(node_id=node, intent=intent_for(ct), status="done",
                       artifact_path=path, artifact_paths=((path,) if path else ()))


def test_final_index_points_at_hero_artifact():
    steps = [_step("1", "TTLGPaletteToPrompt", None),
             _step("2", "TTLGAnimateDiff", "/out/node2_artifact.gif")]
    rv = vm.RunView(run_id="r", title="t", recipe=[], hero_path="/out/node2_artifact.gif",
                    steps=steps)
    # add the attribute if the constructor doesn't take it yet — the impl adds it.
    assert vm.final_index_for(rv) == 1   # 0-based index of the deliverable step


def test_final_index_none_when_no_hero():
    steps = [_step("1", "TTLGGenerateText", None)]
    rv = vm.RunView(run_id="r", title="t", recipe=[], hero_path=None, steps=steps)
    assert vm.final_index_for(rv) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_pipeline_hero.py -q`
Expected: FAIL (`final_index_for` undefined).

- [ ] **Step 3: Write minimal implementation**

- `pipeline_view_model.py`: add a pure `final_index_for(run) -> int | None` returning the index of the step whose `artifact_path == run.hero_path` (or None). (Keeps `RunView` construction unchanged; the hero is derived, not a new required field.)
- `OpenView`: add `_build_hero(step)` — a large preview of `step.artifact_path` (reuse `_build_thumb_frame` at `PREVIEW_W×PREVIEW_H` or larger) titled "Here's what you made", with action buttons ⛶ Fullscreen / ⤓ Save / ↪ In Library / 🔀 Remix (wire ⛶ + 🔀 to existing handlers; ↪/⤓ can call existing reveal/export helpers or be stubbed to the showcase/export path already present). In `set_run`, if `final_index_for(run)` is not None, prepend the hero for that step and render the remaining steps under a `Gtk.Expander(label="How it was made")` (collapsed) — tag each row's kind (artifact/text/none) via a small label using the existing `has_content` split. Text-only pipelines (hero None but a final text step) show that text as the hero.

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_pipeline_hero.py -q` then `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q -k "open_view or pipeline_view_model or pipeline_studio"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline_view_model.py app/pipeline_studio.py tests/test_pipeline_hero.py
git commit -m "feat(pipeline): run page leads with a final-result hero + 'how it was made' breakdown"
```

---

### Task 8: Register the final deliverable into the Library + retire legacy portfolio

**Files:**
- Modify: `app/pipeline_studio.py` (emit/callback on run-done with the final artifact), `app/main_window.py` (register + refresh gallery)
- Remove: `app/pipeline_portfolio_view.py` (+ its test, if any)
- Test: `tests/test_pipeline_library_registration.py`

**Interfaces — Consumes:** `pipeline_view_model.final_index_for` (Task 7). **Produces:** a `MainWindow._register_pipeline_final(run_view)` that adds one Library record (by kind) with pipeline provenance.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_library_registration.py
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import main_window as mw
from unittest.mock import MagicMock
import pipeline_view_model as vm
from intent_vocab import intent_for


def _run_with_final(tmp_path, ext, ct):
    art = tmp_path / f"final{ext}"; art.write_bytes(b"x")
    step = vm.StepView(node_id="2", intent=intent_for(ct), status="done",
                       artifact_path=str(art), artifact_paths=(str(art),))
    return vm.RunView(run_id="run-1", title="t", recipe=[],
                      hero_path=str(art), steps=[step])


def test_gif_final_registers_media_record(tmp_path, monkeypatch):
    obj = mw.MainWindow.__new__(mw.MainWindow)
    added = {}
    monkeypatch.setattr(mw, "media_store", MagicMock())
    # bind the real method
    obj._register_pipeline_final = mw.MainWindow._register_pipeline_final.__get__(obj)
    obj._artgen_gallery = MagicMock()
    obj._store = MagicMock()
    obj._gallery_for_type = MagicMock(return_value=MagicMock())
    rv = _run_with_final(tmp_path, ".gif", "TTLGAnimateDiff")

    obj._register_pipeline_final(rv)

    # A gif final is an artgen MediaRecord with pipeline provenance.
    assert mw.media_store.media_store.add.called or True  # impl detail; see below
```

(The implementer refines the assertions to the real store seam — the load-bearing requirements: a `.gif`/artgen-kind final → one `media_store.MediaRecord` with `generator_type="pipeline"` and `params` containing the run id; a `.png`/`.mp4` final → the native `history_store.GenerationRecord`/gallery path; a missing file → NO record and no crash; the owning gallery's `refresh`/`replace_pending_with` is invoked exactly once.)

- [ ] **Step 2: Run test to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_library_registration.py -q`
Expected: FAIL (`_register_pipeline_final` undefined).

- [ ] **Step 3: Write minimal implementation**

- `MainWindow._register_pipeline_final(run_view)`: resolve the final step via `final_index_for`; if none or its `artifact_path` missing on disk → return (fail-soft). Classify by extension: raster (`.png/.jpg/.jpeg/.webp`) or `.mp4` → build a `history_store.GenerationRecord` and route through the existing gallery add path (mirror `_on_finished`'s `gallery.replace_pending_with` / the store add the native flow uses); artgen kinds (`.gif/.svg/.ans/.json/.py/.md`) → build a `media_store.MediaRecord(generator_type="pipeline", params=json.dumps({"_pipeline_run_id": run_view.run_id, "recipe": run_view.recipe}), …)` with the `rec.media_file_path` alias, `_ms.add()` + `ensure_auto_playlists()`, then refresh `self._artgen_gallery`. Wrap the whole thing so a failure never breaks the run-done view.
- Hook it up: `PipelineStudio` gains an `on_run_complete` callback (invoked from the run-done flow with the `RunView`); `MainWindow` passes `on_run_complete=self._register_pipeline_final` when constructing `PipelineStudio`. (Register once per run — guard on run id.)
- Provenance affordance: the Library card for a pipeline final links back to its run (open the OpenView for `_pipeline_run_id`) — wire via the existing `on_open_run` path; if that's more than a couple lines, note it as a follow-up in the report rather than overbuild.
- Delete `app/pipeline_portfolio_view.py` and any import/test of it (grep first; it's unwired from the finished-run flow).

- [ ] **Step 4: Run test to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_library_registration.py -q` then `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q -k "pipeline or main_window"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline_studio.py app/main_window.py tests/test_pipeline_library_registration.py
git rm app/pipeline_portfolio_view.py
git commit -m "feat(pipeline): register final deliverable into the Library on run-done; retire legacy portfolio view"
```

---

### Task 9: Finalize — full suite, version, docs

**Files:**
- Modify: `VERSION`, `debian/changelog`, `CLAUDE.md`

- [ ] **Step 1: Full suite**

Run:
```bash
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q \
  --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module \
  --deselect tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes \
  --deselect tests/test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen
```
Expected: green.

- [ ] **Step 2: Version + changelog**

- `VERSION`: `0.73.0` → `0.74.0`.
- Prepend a `debian/changelog` `0.74.0` stanza: self-explaining pipeline steps (flow line + summary); per-step model picker reusing Create's model system (`model_picker.py`, server_manager + ModelStatusService); `_backend_for` routing parity; live run progress (spinner + phase + elapsed + step count, log demoted); final-result hero on the run page; pipeline finals registered into the Library; legacy portfolio view retired.

- [ ] **Step 3: CLAUDE.md**

Add a "Pipeline UX overhaul (v0.74.0)" section: the four fixes + key seams — `intent_vocab.flow_line`/`summary`/`capability_for_intent`; `app/model_picker.py` (shared, and the note that CreateView keeps its own picker for now); `_backend_for` parity requirement; `app/pipeline_progress.py` reducer + LiveRunView spinner/phase/elapsed; `pipeline_view_model.final_index_for` + OpenView hero; `MainWindow._register_pipeline_final` provenance registration; `pipeline_portfolio_view.py` removed.

- [ ] **Step 4: Commit**

```bash
git add VERSION debian/changelog CLAUDE.md
git commit -m "chore(pipeline): VERSION 0.74.0 + changelog + CLAUDE.md for the pipeline UX overhaul"
```

---

## Self-Review

**Spec coverage:**
- Self-explaining steps (#1) → Tasks 1-2. ✓
- Per-step model picker reusing Create's system (#2) → Tasks 3 (shared widget on the same primitives), 5 (wired into RemixView), 4 (engine parity so no dead choices). ✓ — deviation logged: a NEW shared `model_picker.py` rather than refactoring CreateView (rationale: avoid Create regressions; CreateView migration is an explicit follow-up).
- Live progress (#3) → Task 6. ✓
- Final-result hero (#4) → Task 7; Library registration → Task 8; retire legacy portfolio → Task 8. ✓
- Constraints (collect-invariant, GTK threading, ASCII CSS, version) → Tasks 5/6/8/9. ✓

**Placeholder scan:** Tasks 5 and 8 intentionally hand the implementer latitude on the exact RemixView field-API / store seam (with the load-bearing assertions stated) because those touch large existing methods; every other step has concrete code. No "TBD"/vague-error-handling.

**Type consistency:** `capability_for_intent` (Task 1) feeds `picker_entries(capability,…)` (Task 3), `_backend_for` parity (Task 4), and RemixView wiring (Task 5) with the same capability strings ("image"/"video"/"animatediff"/"artgen"). `PickerEntry` 4-tuple `(key, name, benefit, dot)` consistent across Tasks 3-5. `final_index_for(run)` (Task 7) consumed by Task 8. `RunView`/`StepView` fields match the extraction.

**Ordering & risk:** 1→2 (clarity), 3→4→5 (model picker; 4 before 5 so the picker never offers a dead choice), 6 (progress), 7→8 (hero then Library), 9 (finalize). Highest risk: Task 5 (RemixView integration + collect-invariant) and Task 8 (store registration) — both have explicit invariants and fail-soft requirements. Task 3's `ModelPickerRow` widget and Task 6's GTK wiring are the other integration points; both have pure cores unit-tested first.
