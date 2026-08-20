# Pipeline Field Roles — shared vocabulary in the Remix/Compose editor

**Date:** 2026-07-13
**Branch:** `feat/pipeline-editor` (local; not merged)
**Status:** design approved (self-approved per user instruction)
**Prior art:** sub-project 1 (Create surface redesign, v0.28.1) built the shared
`field_roles` taxonomy and `ModifierPills`; this is sub-project 2 — adopting them
in the pipeline editor so a field means the same thing everywhere.

## Problem

The Create surface groups fields by role (brief / direction / controls) and marks
each with ✎ (your words) / ✨ (the model chooses) / ⚙ (exact setting), and gives
brief text fields a modifier-pill "Add" affordance. The **pipeline node editor**
(`RemixView` in `app/pipeline_studio.py`) still renders every editable input as an
undifferentiated label + widget row — no role signal, no marker, no modifiers. The
user asked that the field-role clarity "goes for configuring pipeline fields too."

## Goals

- Every editable pipeline field carries the same ✎/✨/⚙ marker as Create.
- Step-card fields are ordered brief → direction → control, with the deterministic
  (⚙) fields tucked under a per-card collapsed "Controls" disclosure.
- Brief text fields get the modifier-pill "Add" affordance, with a **contextual**
  bank chosen by the node's output kind.
- Reuse sub-project 1's units (`field_roles`, `ModifierPills`) — no duplication.
- Do not change the pipeline edit/`derive_spec` contract in shape; an untouched
  card still produces an empty edit diff.

## Non-goals

- No change to the pipeline engine, `spec_remix.derive_spec`, or how runs execute.
- No new chip banks authored (reuse the existing image/video/animate banks in
  `config/prompt_chips.yaml`).
- No per-step (vs whole-run) editing rework — RemixView's existing editing surface
  is unchanged in scope; only field presentation + modifiers change.

## Global constraints (bind every task)

- **Edit-contract safe:** `RemixView._collect_edits()` must still yield a diff of
  only genuinely-changed fields; no change to a field's value (and no applied
  pills) → that field is absent from the diff → an untouched run reproduces
  exactly. This is the invariant, guarded by tests.
- **Palette:** Pipeline Studio uses its own forest-teal `ps-*` styling — keep it;
  do NOT introduce the tt-vscode-toolkit variant here (that's the main-app
  surface). Marker glyphs are plain unicode in Python label strings, never inside
  a `b"""..."""` CSS literal (ASCII-only there).
- **System python** `/usr/bin/python3`; tests via
  `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q`.
- **Version discipline:** bump `VERSION` + prepend a `debian/changelog` stanza
  when landing.
- **Local only:** no push / merge / PR.
- **Known flake:** `test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`
  is a pre-existing order-dependent cffi/cairosvg flake (passes in isolation);
  deselect it in full-suite runs, do not attribute it to this work.

## Architecture

Two files change. Everything else is reuse.

### 1. `app/field_roles.py` (MODIFY — deepen + one shared formatter)

- **Deepen `classify_pipeline_field(kind, default=None, key="")`** to classify a
  `spec_remix.ParamField` (callers pass `kind=field.kind`, `default=field.value`,
  `key=field.key`). Rules:
  - `key` in a brief-ish set (`prompt`, `text`, `negative_prompt`, `subject`,
    `theme`, `caption`, `description`, `lore`) OR `kind == "prompt"` → `FieldRole(ROLE_BRIEF, MARK_WORDS)`.
  - `kind == "number"` → `FieldRole(ROLE_CONTROL, MARK_EXACT)`.
  - `kind == "bool"` → `FieldRole(ROLE_DIRECTION, MARK_EXACT)`.
  - `kind in ("text","choice")` with `default` in `{None, "random", "auto"}` →
    `FieldRole(ROLE_DIRECTION, MARK_INTERPRETED)`; otherwise
    `FieldRole(ROLE_DIRECTION, MARK_EXACT)`.
  - Any other/unknown kind → `FieldRole(ROLE_CONTROL, MARK_EXACT)` (safe default).
- **Add `marker_prefix(marker: str) -> str`** — returns `MARKER_GLYPH[marker] + " "`
  (or `""` for an unknown marker). A pure string helper so `RoleZonePanel` and
  `RemixView` decorate labels identically. `field_roles` stays GTK-free.
- A brief-key set constant (`_PIPELINE_BRIEF_KEYS`) documents the brief-ish keys.

### 2. `app/pipeline_studio.py` — `RemixView` step-card fields

New helper `_bank_kind_for_output(output_kind: str) -> "str | None"`: maps a
node's `intent_for(class_type).output_kind` to a chip-bank kind —
`"image"→"image"`, `"video"→"video"`, `"gif"→"animate"`; `"text"`/`"playlist"`/
`None`/unknown → `None` (no bank).

`_build_step_card(index, node_id, class_type, fields)` changes (it already
computes `intent = intent_for(class_type)`):
- Classify each `field` via `field_roles.classify_pipeline_field(field.kind,
  field.value, field.key)`.
- **Order** fields brief → direction → control (stable within each group,
  preserving the incoming order).
- Append brief + direction field rows directly to the card; collect the control
  (⚙) field rows into a `Gtk.Expander` labeled `Controls (N)` set NOT expanded,
  appended after the direction rows. If a node has zero control fields, no
  expander is added.
- Continue storing every field's widget in `_field_widgets[node_id][key]` and
  `_field_meta[node_id][key] = (kind, value)` exactly as today (so `_collect_edits`
  is unchanged in how it finds widgets) — regardless of which zone a field's row
  lands in.

`_build_field_row(field)` changes:
- Prefix the field label with `field_roles.marker_prefix(role.marker)` and set the
  label's tooltip to `field_roles.MARKER_TIP[role.marker]` (compute `role` here or
  pass it in). Keep the existing `ps-field-key` styling.
- For a **brief** field whose `kind` is text (not number/bool) AND whose node has a
  non-None bank kind, build a `ModifierPills(bank_kind)` (imported from
  `create_param_panels`) and place it under the field's Entry. Store it in a new
  `self._field_pills[node_id][key] = pills`.

`_collect_edits()` changes (the only value-path change):
- After computing `new_value = _read_widget_value(kind, orig_value, widget)`, if a
  `ModifierPills` exists for `(node_id, key)` and its `applied_text()` is
  non-empty, set `new_value = (str(new_value) + " " + pills.applied_text()).strip()`.
- The existing `if new_value != orig_value` guard then records the edit. So: no
  text change and no pills → unchanged → not in the diff (invariant holds); pills
  applied → the field's value carries the appended modifier text.

`import`: `from create_param_panels import ModifierPills` (one-directional —
`create_param_panels` does not import `pipeline_studio`, so no cycle). Marker
constants come from `field_roles`.

## Data flow

`editable_params(spec)` → per-node `ParamField`s → `_build_step_card` classifies +
orders + marks + (for brief text fields) attaches `ModifierPills` → user edits /
adds pills → **Run** → `_collect_edits` folds pill text into brief field values →
`run-remix` emits `(spec_path, edits)` → existing `derive_spec` path (unchanged).

## Error handling

- Unknown `class_type`/`output_kind` → `_bank_kind_for_output` returns `None` → no
  pills (graceful; markers still render).
- `classify_pipeline_field` never raises; unknown kind → control/exact.
- A brief text field on a text-output node (LLM) → no bank → no pills, marker only.

## Testing

- `field_roles.classify_pipeline_field`: rule table (brief key → brief/words;
  number → control/exact; bool → direction/exact; text default "random"/None →
  direction/interpreted; plain text → direction/exact; unknown kind →
  control/exact). `marker_prefix` returns glyph+space and "" for unknown.
- `_bank_kind_for_output`: image→image, video→video, gif→animate,
  text/playlist/None→None.
- `RemixView` step card: fields ordered brief→direction→control; each field label
  carries its marker glyph + tooltip; control fields live under a collapsed
  `Controls (N)` expander (and no expander when there are none); a brief text
  field on an image-output node has a `ModifierPills`, a brief field on a
  text-output node does not.
- `_collect_edits` invariant: untouched card → empty diff; a pill applied to a
  brief field → that field's edit value == original text + " " + applied modifier
  text; a number/bool tweak still diffs as before.

## File summary

| File | Change |
|---|---|
| `app/field_roles.py` | deepen `classify_pipeline_field`; add `marker_prefix`; `_PIPELINE_BRIEF_KEYS` |
| `app/pipeline_studio.py` | `RemixView`: classify/order/mark fields, collapsed Controls, contextual `ModifierPills` on brief text fields, fold pill text in `_collect_edits`; `_bank_kind_for_output` |
| `tests/…` | classify_pipeline_field, marker_prefix, bank mapping, step-card ordering/markers/controls/pills, collect_edits invariant |
