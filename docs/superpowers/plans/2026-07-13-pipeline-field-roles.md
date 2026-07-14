# Pipeline Field Roles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the Create surface's field-role vocabulary (✎/✨/⚙ markers, brief→direction→control grouping) and modifier pills into the pipeline node editor (`RemixView`), so a field means the same thing everywhere.

**Architecture:** Deepen the already-stubbed `field_roles.classify_pipeline_field` and add a pure `marker_prefix` formatter; then in `RemixView` step cards classify each `ParamField`, order/mark the rows, tuck deterministic fields under a collapsed "Controls" expander, and give brief text fields a contextual `ModifierPills` (bank chosen by the node's `output_kind`), folding applied pill text into that field's value at edit-collect time. Reuse `ModifierPills` from `create_param_panels`; change no generation or `derive_spec` code.

**Tech Stack:** Python 3, GTK4 via PyGObject, pytest (xvfb for widget tests). System interpreter `/usr/bin/python3`.

## Global Constraints

- **Edit-contract safe:** `RemixView._collect_edits()` still yields only genuinely-changed fields; no text change + no applied pills → field absent from the diff → an untouched run reproduces exactly. Guarded by tests.
- **Palette:** keep Pipeline Studio's forest-teal `ps-*` styling; do NOT introduce the tt-vscode-toolkit variant here. Marker glyphs are unicode in Python label strings, never inside a `b"""..."""` CSS literal (ASCII-only there).
- **Tests:** `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q`. Known pre-existing flake to deselect: `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module` (cffi/cairosvg, passes in isolation).
- **Version discipline:** bump `VERSION` + prepend a `debian/changelog` stanza when landing (final task).
- **Local only:** no push / merge / PR.
- **No generation/derive_spec changes.** Reuse `ModifierPills` from `create_param_panels` (one-directional import; no cycle).

---

### Task 1: Deepen `classify_pipeline_field` + add `marker_prefix`

**Files:**
- Modify: `app/field_roles.py`
- Test: `tests/test_field_roles.py` (extend)

**Interfaces:**
- Produces:
  - `classify_pipeline_field(kind: str, default=None, key: str = "") -> FieldRole` with the deepened rules below.
  - `marker_prefix(marker: str) -> str` — `MARKER_GLYPH[marker] + " "`, or `""` for an unknown marker.
  - `_PIPELINE_BRIEF_KEYS: frozenset[str]` documenting the brief-ish keys.

- [ ] **Step 1: Write failing tests**

```python
# add to tests/test_field_roles.py
def test_pipeline_brief_key_is_brief_words():
    for k in ("prompt", "text", "negative_prompt", "subject", "theme", "caption", "description", "lore"):
        assert fr.classify_pipeline_field("text", "whatever", k) == fr.FieldRole(fr.ROLE_BRIEF, fr.MARK_WORDS)

def test_pipeline_number_is_control_exact():
    assert fr.classify_pipeline_field("number", 20, "steps") == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)

def test_pipeline_bool_is_direction_exact():
    assert fr.classify_pipeline_field("bool", True, "tiled") == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_EXACT)

def test_pipeline_text_random_default_is_interpreted():
    assert fr.classify_pipeline_field("text", "random", "sampler") == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_INTERPRETED)
    assert fr.classify_pipeline_field("choice", None, "scheduler").marker == fr.MARK_INTERPRETED

def test_pipeline_plain_text_is_direction_exact():
    assert fr.classify_pipeline_field("text", "euler", "sampler") == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_EXACT)

def test_pipeline_unknown_kind_is_control_exact():
    assert fr.classify_pipeline_field("weird", "x", "k") == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)

def test_marker_prefix():
    assert fr.marker_prefix(fr.MARK_INTERPRETED) == "✨ "
    assert fr.marker_prefix("nonsense") == ""
```

- [ ] **Step 2: Run to verify fail** — `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_field_roles.py -q` → FAIL.

- [ ] **Step 3: Implement in `app/field_roles.py`**

```python
_PIPELINE_BRIEF_KEYS = frozenset({
    "prompt", "text", "negative_prompt", "subject",
    "theme", "caption", "description", "lore",
})


def classify_pipeline_field(kind: str, default=None, key: str = "") -> FieldRole:
    """Classify one editable pipeline ParamField (kind/value/key)."""
    if key in _PIPELINE_BRIEF_KEYS or kind == "prompt":
        return FieldRole(ROLE_BRIEF, MARK_WORDS)
    if kind == "number":
        return FieldRole(ROLE_CONTROL, MARK_EXACT)
    if kind == "bool":
        return FieldRole(ROLE_DIRECTION, MARK_EXACT)
    if kind in ("text", "choice"):
        if default in _INTERPRETED_VALUES:
            return FieldRole(ROLE_DIRECTION, MARK_INTERPRETED)
        return FieldRole(ROLE_DIRECTION, MARK_EXACT)
    return FieldRole(ROLE_CONTROL, MARK_EXACT)


def marker_prefix(marker: str) -> str:
    """Glyph + trailing space for a marker, or "" for an unknown marker.

    A pure formatter so RoleZonePanel (Create) and RemixView (pipeline)
    decorate field labels identically without importing each other.
    """
    glyph = MARKER_GLYPH.get(marker)
    return f"{glyph} " if glyph else ""
```

(`_INTERPRETED_VALUES` already exists in the module from sub-project 1: `{None, "random", "auto"}`. Replace the old basic `classify_pipeline_field` body with the above.)

- [ ] **Step 4: Run to verify pass** — same command → PASS (existing field_roles tests still green).
- [ ] **Step 5: Commit** — `git add app/field_roles.py tests/test_field_roles.py && git commit -m "feat(pipeline): deepen classify_pipeline_field + marker_prefix formatter"`

---

### Task 2: RemixView — classify, order, mark fields; collapsed Controls expander

**Files:**
- Modify: `app/pipeline_studio.py` (`RemixView._build_step_card`, `_build_field_row`)
- Test: `tests/test_pipeline_studio.py` (extend)

**Interfaces:**
- Consumes: `field_roles.classify_pipeline_field`, `field_roles.marker_prefix`, `field_roles.MARKER_TIP`, `field_roles.ROLE_CONTROL`; `intent_for(class_type)` (already imported).
- Produces: step cards whose field rows are ordered brief → direction → control, each label marker-prefixed with a tooltip, and whose control fields sit inside a `Gtk.Expander` (`self._controls_expanders[node_id]`, not expanded). `_field_widgets`/`_field_meta` still keyed by node_id/key exactly as before (no matter the zone), so `_collect_edits` is unaffected.

- [ ] **Step 1: Write failing tests** (reuse the file's existing `_make_remix_run()` / `_REMIX_SPEC_PATH` harness and xvfb skip guard)

```python
def test_remix_field_labels_carry_role_markers():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    texts = _all_field_label_texts(view)   # helper: walk cards, collect ps-field-key label strings
    assert any(t.startswith("✎ ") for t in texts)   # a brief field (e.g. a prompt)
    assert any(t.startswith(("⚙ ", "✨ ")) for t in texts)

def test_remix_control_fields_live_in_collapsed_expander():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    # a node with at least one number field (e.g. node "2" num_frames) has a Controls expander, collapsed
    exp = view._controls_expanders.get("2")
    assert exp is not None
    assert exp.get_expanded() is False

def test_remix_fields_ordered_brief_then_direction_then_control():
    from pipeline_studio import RemixView
    import field_roles as fr
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    roles = _ordered_field_roles_for_node(view, node_id="1")  # helper: role per field in display order
    order = {fr.ROLE_BRIEF: 0, fr.ROLE_DIRECTION: 1, fr.ROLE_CONTROL: 2}
    ranks = [order[r] for r in roles]
    assert ranks == sorted(ranks)

def test_remix_no_edits_still_emits_empty_dict():
    # invariant unchanged by the reordering/markers
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    received = []
    view.connect("run-remix", lambda _w, sp, edits: received.append(edits))
    view._run_button.emit("clicked")
    assert received == [{}]
```

Add small test helpers near the other RemixView tests: `_all_field_label_texts(view)` walks `view`'s built cards for widgets with CSS class `ps-field-key` and returns their `.get_text()`; `_ordered_field_roles_for_node(view, node_id)` returns the classified role of each field for that node in the order its row/label appears (brief+direction rows in the card body, then the control rows inside that node's expander). If walking the widget tree is awkward, expose a tiny private `_node_field_order(node_id) -> list[str]` on RemixView returning field keys in display order and classify via `field_roles` in the test.

- [ ] **Step 2: Run to verify fail** → FAIL.
- [ ] **Step 3: Implement**
  - In `_build_step_card`, after computing `intent = intent_for(class_type)`: classify each `field` via `field_roles.classify_pipeline_field(field.kind, field.value, field.key)`. Partition into `brief`, `direction`, `control` lists preserving incoming order. Append brief then direction rows to the card body. If any control fields exist, build `exp = Gtk.Expander(label=f"Controls ({len(control)})")`, `exp.set_expanded(False)`, put the control rows in a vertical box as its child, append `exp` to the card, and store `self._controls_expanders[node_id] = exp` (initialize `self._controls_expanders = {}` where the other per-node dicts are initialized). Keep populating `_field_widgets[node_id][key]`/`_field_meta[node_id][key]` for EVERY field regardless of zone.
  - Change `_build_field_row(self, field, role)` to accept the field's `FieldRole`: set the key label text to `field_roles.marker_prefix(role.marker) + field.label` and `key_label.set_tooltip_text(field_roles.MARKER_TIP[role.marker])`. Keep `ps-field-key` styling and the returned `(row, widget)` shape. Update the single call site in `_build_step_card` to pass the classified role.
  - Do not touch `_build_field_widget`, `_collect_edits`, or `_read_widget_value` in this task.
- [ ] **Step 4: Run to verify pass** — `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_studio.py -q` → PASS (all existing RemixView tests still green).
- [ ] **Step 5: Commit** — `git commit -am "feat(pipeline): role-mark + order RemixView fields; collapse control fields"`

---

### Task 3: Contextual ModifierPills on brief text fields + fold into edits

**Files:**
- Modify: `app/pipeline_studio.py` (`RemixView`: `_bank_kind_for_output`, `_build_step_card`, `_collect_edits`)
- Test: `tests/test_pipeline_studio.py` (extend)

**Interfaces:**
- Consumes: `ModifierPills` (`from create_param_panels import ModifierPills`), `intent_for(class_type).output_kind`.
- Produces: `_bank_kind_for_output(output_kind) -> "str | None"`; `self._field_pills[node_id][key] = ModifierPills` for brief text fields on nodes with a bank; `_collect_edits` folds each such field's `applied_text()` into its value.

- [ ] **Step 1: Write failing tests**

```python
def test_bank_kind_for_output_mapping():
    from pipeline_studio import RemixView
    v = RemixView()
    assert v._bank_kind_for_output("image") == "image"
    assert v._bank_kind_for_output("video") == "video"
    assert v._bank_kind_for_output("gif") == "animate"
    assert v._bank_kind_for_output("text") is None
    assert v._bank_kind_for_output("playlist") is None
    assert v._bank_kind_for_output(None) is None

def test_brief_text_field_on_image_node_has_pills():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    # node "1" is a text->image node with a brief "prompt" field
    assert "prompt" in view._field_pills.get("1", {})

def test_brief_field_on_text_node_has_no_pills():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    # a text-output (LLM) node's brief field gets no bank -> no pills entry
    # (assert against whichever node in the fixture has output_kind "text")
    text_node_id = _first_text_output_node(view)     # helper using intent_for on the loaded spec
    assert "prompt" not in view._field_pills.get(text_node_id, {})

def test_applied_pill_folds_into_field_edit_value():
    from pipeline_studio import RemixView
    from chip_config import ChipEntry
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    pills = view._field_pills["1"]["prompt"]
    pills._apply_entry(ChipEntry("golden hour", "golden hour lighting", ""))
    received = []
    view.connect("run-remix", lambda _w, sp, edits: received.append(edits))
    view._run_button.emit("clicked")
    orig = view._field_meta["1"]["prompt"][1]
    assert received[0]["1"]["prompt"] == f"{orig} golden hour lighting"

def test_no_pill_no_text_change_still_empty_edit():
    from pipeline_studio import RemixView
    view = RemixView()
    view.set_run(_make_remix_run(), _REMIX_SPEC_PATH)
    received = []
    view.connect("run-remix", lambda _w, sp, edits: received.append(edits))
    view._run_button.emit("clicked")
    assert received == [{}]
```

If the existing `_make_remix_run()` fixture has no text-output node, extend it (or add a dedicated fixture) so `test_brief_field_on_text_node_has_no_pills` has a real text-output node to assert against — keep existing tests using the original fixture untouched.

- [ ] **Step 2: Run to verify fail** → FAIL.
- [ ] **Step 3: Implement**
  - Add at RemixView module import: `from create_param_panels import ModifierPills`.
  - `_bank_kind_for_output(self, output_kind)`: `return {"image": "image", "video": "video", "gif": "animate"}.get(output_kind)`.
  - Initialize `self._field_pills = {}` alongside the other per-node dicts, and clear it on each `set_run`/re-render where `_field_widgets` is cleared.
  - In `_build_step_card`, when appending a brief field whose `field.kind == "text"` (not number/bool) and `self._bank_kind_for_output(intent.output_kind)` is not None: build `pills = ModifierPills(bank_kind)`, append it to the card directly under that field's row, and store `self._field_pills.setdefault(node_id, {})[field.key] = pills`.
  - In `_collect_edits`, after `new_value = self._read_widget_value(kind, orig_value, widget)`: `pills = self._field_pills.get(node_id, {}).get(key)`; if `pills is not None` and `pills.applied_text()`: `new_value = f"{new_value} {pills.applied_text()}".strip()`. The existing `if new_value != orig_value` guard records it. (So no pills + no edit → unchanged → not in diff.)
- [ ] **Step 4: Run to verify pass** — `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_studio.py -q` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(pipeline): contextual modifier pills on brief fields; fold into edit values"`

---

### Task 4: Version bump, changelog, CLAUDE.md note

**Files:**
- Modify: `VERSION`, `debian/changelog`, `CLAUDE.md`

- [ ] **Step 1:** Set `VERSION` to `0.29.0` (new user-visible feature).
- [ ] **Step 2:** Prepend a `debian/changelog` stanza (0.29.0, noble, urgency=medium) summarizing: pipeline node fields now carry the same ✎/✨/⚙ role markers as Create, are ordered brief→direction→control with deterministic fields under a collapsed "Controls" disclosure, and brief text fields gain contextual modifier pills (bank chosen by the node's output kind) folded into the field value at run time; edit-contract unchanged. Author `Taylor Singletary <tsingletary@tenstorrent.com>`, next timestamp in sequence.
- [ ] **Step 3:** Extend the CLAUDE.md "Create surface" section with a short note that `field_roles` + `ModifierPills` now also drive `RemixView` pipeline fields (`classify_pipeline_field`, `marker_prefix`, `_bank_kind_for_output`, pills folded in `_collect_edits`), and that the edit-diff invariant is preserved.
- [ ] **Step 4:** Full suite — `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module` → all green.
- [ ] **Step 5: Commit** — `git commit -am "chore: release v0.29.0 -- field roles in the pipeline editor"`

---

## Notes for the executor

- The edit-contract invariant (Task 3) is the ballgame: an untouched card must still yield `{}`. `test_no_pill_no_text_change_still_empty_edit` and `test_applied_pill_folds_into_field_edit_value` are the guardrails.
- No changes to generation, the pipeline engine, or `spec_remix.derive_spec`.
- Keep Pipeline Studio's forest-teal `ps-*` styling; any CSS `b"""..."""` stays ASCII (glyphs live in Python label strings via `marker_prefix`).
