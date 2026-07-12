# SP-C Phase 2b-1: Structural composer (add / remove steps) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the minimal Remix editor (Phase 2a: edit existing params) into a real composer: **add a step** (pick an intent that fits the prior step's output, auto-wired) and **remove a step** (rewire dependents), producing a runnable derived spec — then run it via the existing loop. Free-form "wing it" and the *live MCP-driven* capability list are the next slices (2b-2, 2b-3); this slice uses a **static, contextual** capability list (the known intents, filtered by kind).

**Architecture:** Extend `intent_vocab` with per-intent I/O wiring metadata (input_key/input_kind/output_kind). Extend `spec_remix` with pure `add_step`/`remove_step`/`write_spec` graph operations. Extend the GTK `RemixView` (or a `ComposerView` sibling) with an "add a step" contextual picker + per-step remove, feeding the Phase-2a derive→create→run→done loop.

**Tech Stack:** Python 3.12 / GTK4, pytest (+xvfb). Reuses `app/intent_vocab.py`, `app/spec_remix.py`, `app/pipeline_studio.py` (RemixView/LiveRunView + the wired loop), `app/pipeline_engine.py` (topo_order/_is_wire).

## Global Constraints

- Pure logic (`intent_vocab` additions, `spec_remix` graph ops) has zero GTK imports; unit-tested without a display.
- A derived spec after add/remove MUST remain a valid ComfyUI-API-v1 graph: acyclic, every wire points at an existing node+output, node ids unique. `spec_remix` guarantees this or raises (never writes a broken spec).
- **Auto-wiring rule:** adding intent Y after step X wires Y's `input_key` ← `[X_id, X.primary_output]` ONLY when `Y.input_kind == X.output_kind`; the picker only offers kind-compatible intents (contextual). Non-artifact params (prompt text, steps) keep their spec defaults / are editable via the existing param fields.
- **Remove rule:** removing node N rewires each consumer wire `[N, key]` to N's own upstream source for that artifact if one exists (kind-compatible), else drops that input (consumer falls back to its literal default / becomes a source). Never leave a dangling wire.
- Intent language throughout; model stays a quiet detail; no raw class_type in labels (fallback already safe).
- Reuse the Phase-2a run loop unchanged (derive→create_run→PipelineRunner.start(run_id=…)→LiveRunView→run-done). Base example specs never mutated.
- No regression to Phase-1 (Discover/Open) or Phase-2a (param remix + run).
- Mockups: `.superpowers/brainstorm/988333-1783804257/content/intent-composer.html` (add/remove + flow), `add-step-wingit.html` (the picker — but STATIC contextual list here; dynamic MCP is 2b-2).

## File Structure

- Modify `app/intent_vocab.py` — add `input_key`, `input_kind`, `output_kind` to `Intent` (+ populate for all 12); a helper `compatible_intents(output_kind) -> list[Intent]`.
- Modify `app/spec_remix.py` — `add_step(spec, after_node_id, class_type, params=None) -> dict`, `remove_step(spec, node_id) -> dict`, `write_spec(spec, base_name, dest_dir) -> str` (write a full edited spec dict; `derive_spec` from 2a can delegate to it).
- Modify `app/pipeline_studio.py` — extend `RemixView` with add/remove UI (contextual picker + remove buttons) over a working spec dict; emit the edited spec into the run loop.
- Tests: extend `tests/test_intent_vocab.py`, `tests/test_spec_remix.py`, `tests/test_pipeline_studio.py`.

---

### Task 1: Intent I/O wiring metadata

**Files:** Modify `app/intent_vocab.py`, extend `tests/test_intent_vocab.py`.

**Interfaces produced:** `Intent` gains `input_key: str|None`, `input_kind: str|None`, `output_kind: str|None` (kinds: "image"|"text"|"video"|"gif"|"svg"|None). `compatible_intents(output_kind:str) -> list[Intent]` (intents whose `input_kind == output_kind`, i.e. can consume that artifact). Source nodes (no artifact input, e.g. base TextToImage from scratch) have `input_key=None`.

- [ ] **Step 1: failing tests** — assert the wiring metadata for representative intents: TTLGCaptionImage(input_key="src", input_kind="image", output_kind="text"), TTLGImageToVideo(input_key="image", input_kind="image", output_kind="video"), TTLGTextToImage(input_key="prompt", input_kind="text", output_kind="image"), TTLGRemoveBackground(input_key="src", input_kind="image", output_kind="image"), TTLGGenerateText(input_key="caption", input_kind="text", output_kind="text"); `compatible_intents("image")` includes Caption/RemoveBackground/EstimateDepth/ImageToVideo and excludes TextToImage (text-input). All 12 have the fields set (None allowed where N/A).
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** — add the three fields to `Intent` + populate all 12 (input keys per the 1964 spec: src/image=image-consuming, prompt/caption=text-consuming; output_kind from the primary output) + `compatible_intents`.
- [ ] **Step 4: run → pass.**
- [ ] **Step 5: commit** `feat(sp-c): intent I/O wiring metadata (input/output kinds)`.

---

### Task 2: Structural spec edits (pure)

**Files:** Modify `app/spec_remix.py`, extend `tests/test_spec_remix.py`.

**Interfaces produced:**
- `add_step(spec:dict, after_node_id:str, class_type:str, params:dict|None=None) -> dict` — returns a NEW spec dict with a new node (fresh unique id) whose `intent.input_key` is wired to `[after_node_id, <after node's primary output>]` (using `intent_vocab`), plus any `params` as literal inputs; raises `ValueError` if the new intent's `input_kind` is incompatible with the after-node's `output_kind`. Does not reorder existing nodes; the new node is appended (topo order is by wires, not key order).
- `remove_step(spec:dict, node_id:str) -> dict` — returns a NEW spec dict without `node_id`; for every consumer input wired to `[node_id, k]`, rewire to `node_id`'s own upstream source of the same artifact if present (kind-compatible), else remove that input key. Never leaves a dangling wire.
- `write_spec(spec:dict, base_name:str, dest_dir:str) -> str` — write a full spec dict to `dest_dir/remix_<base_name>_<n>.json` (collision-safe count, no Date/random), return path. `derive_spec` (2a) refactors to: load base → apply param edits → `write_spec`.
- Validate the result (acyclic via `pipeline_engine.topo_order`; all wires resolve) before returning/writing; raise on violation.

- [ ] **Step 1: failing tests** (fixture: the 1964 spec or a small graph) — `add_step` after an image node with `TTLGCaptionImage` wires `src`←`[img,image_path]`, result topo-orders + validates; `add_step` with an incompatible intent (text-input after an image node) raises; `remove_step` on a middle node rewires its consumer to its upstream (assert the consumer's wire now points at the removed node's source) and the result validates with no dangling wires; `write_spec` round-trips + is collision-safe; base spec unchanged.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** (pure; use `intent_vocab` for input_key/kinds + primary output, `pipeline_engine.topo_order`/`_is_wire` for validation).
- [ ] **Step 4: run → pass.**
- [ ] **Step 5: commit** `feat(sp-c): structural spec edits (add/remove step, validated)`.

---

### Task 3: Composer add/remove UI

**Files:** Modify `app/pipeline_studio.py`, extend `tests/test_pipeline_studio.py`.

**Interfaces produced:** `RemixView` (or a `ComposerView`) holds a working spec dict (loaded from the run's spec). Renders steps (intent + editable params from 2a) with, per step, a **Remove** control and an **＋ add a step after this** control that opens a contextual picker = `intent_vocab.compatible_intents(<this step's output_kind>)` (STATIC list, filtered by kind). Choosing an intent calls `spec_remix.add_step` on the working spec and re-renders; Remove calls `remove_step`. "Run this remix →" now emits the fully-edited working spec (write via `spec_remix.write_spec`) into the existing run loop (signal carries the edited spec / its written path). Repeat-safe; data-in via `set_run`.

- [ ] **Step 1: xvfb tests** — after `set_run`, each step shows Remove + add-after; the add picker for an image-output step lists only image-consuming intents (assert TextToImage absent, Caption present); choosing one grows the rendered steps by 1 (the working spec gained a node); Remove shrinks it; "Run" emits a spec whose graph reflects the add/remove (assert via the emitted spec/path loading with the new node). → fail.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** (reuse Phase-1/2a CSS/helpers + the intent-composer mockup; picker as a Gtk.Popover/menu of compatible intents). Keep the param-edit path from 2a working alongside.
- [ ] **Step 4: run → pass; full suite (xvfb) — no NEW failures.**
- [ ] **Step 5: commit** `feat(sp-c): composer add/remove steps (contextual picker)`.

---

### Task 4: Wire into the run loop + version

**Files:** Modify `app/pipeline_studio.py` (loop hookup if the emit shape changed), `VERSION`, `debian/changelog`; extend tests.

- [ ] **Step 1:** test that a structurally-edited remix (add a step) flows through the Phase-2a loop: the derived spec written for the run includes the added node; `create_run`/`PipelineRunner.start(run_id=…)` receive that derived path; single-record integrity preserved. (Mock runner/store.) → fail/adjust.
- [ ] **Step 2:** run → fail.
- [ ] **Step 3:** adapt the `run-remix` handling so a structurally-edited working spec is written via `write_spec` and run (the 2a loop otherwise unchanged — same single-record run_id adoption). Bump `VERSION` (minor) + changelog stanza: "Pipeline Studio Phase 2b-1 — compose pipelines by adding/removing steps (contextual, auto-wired) and running the result."
- [ ] **Step 4:** run → pass; full suite (xvfb) — no NEW failures.
- [ ] **Step 5:** commit `feat(sp-c): run structurally-composed remixes + 0.x bump`.

---

## Self-Review

**Spec coverage (Phase 2b-1 slice):** I/O wiring metadata → Task 1; validated add/remove graph ops → Task 2; contextual add/remove UI → Task 3; run the composed spec via the 2a loop + version → Task 4. Deferred to 2b-2 (dynamic MCP capability list) and 2b-3 (free-form wing-it) — noted, not stubbed-as-if-present. ✓
**Placeholder scan:** pure tasks carry complete interfaces + TDD; GTK task specs the picker/controls + the exact intent_vocab/spec_remix calls + xvfb behaviour tests, mockups as layout ref; capability list explicitly STATIC-contextual here. No TBDs. ✓
**Type consistency:** `Intent.input_key/input_kind/output_kind`, `compatible_intents`, `add_step`/`remove_step`/`write_spec` names consistent; add/remove auto-wiring uses intent_vocab's keys; run loop reuses Phase-2a's `run_id`-adoption single-record path; validation via `pipeline_engine.topo_order`. ✓
