# SP-C Phase 2a: Remix + Run loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Thread the whole create loop minimally — from **Open**, *Remix* one step's plain-language params ("change one thing safely"), **Run** the derived pipeline live through the existing runner→engine, and land on a finished state that links back into Discover/Open. The free-form composer (add/remove steps, wing-it, dynamic MCP capabilities) is the *next* slice (Phase 2b); Showcase is Phase 3.

**Architecture:** A pure **spec-remix** helper derives a modified ComfyUI-API-v1 spec file from param edits over a base spec. A minimal **RemixView** (GTK) collects edits from a `RunView`; a **LiveRunView** (GTK) renders the `run-watch` mockup driven by the existing `PipelineRunner`'s `NODE:`/`LOG:` callbacks. `PipelineStudio` wires Open→Remix→(derive spec + `PipelineStore.create_run` + `PipelineRunner.start`)→LiveRun→done.

**Tech Stack:** Python 3.12 / GTK4, pytest (+`xvfb-run`). Reuses `app/pipeline_runner.py`, `app/pipeline_store.py`, `app/pipeline_engine.py` (load_spec/topo_order), `app/intent_vocab.py`, `app/pipeline_view_model.py`, `app/pipeline_studio.py` (Phase 1).

## Global Constraints

- Pure logic (`spec_remix.py`) has zero GTK imports; unit-tested without a display.
- GTK views obey threading: the run subprocess + parsing live in `PipelineRunner` (already off-thread via its `idle_add`); views only mutate widgets on the main thread. `PipelineStudio` passes `GLib.idle_add` to `PipelineRunner(idle_add=…)`.
- Intent language throughout (reuse `intent_vocab`); model stays a quiet detail; no raw class_type in labels (Phase-1 `intent_for` fallback already safe).
- Remix is MINIMAL in 2a: edit existing steps' **plain-language scalar params** (text/prompt/number/choice) only — NO add/remove/reorder steps, NO wing-it. Those are Phase 2b (leave a clear extension point, not a stub that pretends to do more).
- Derived specs are written to a remixes dir (`~/.local/share/tt-local-generator/remixes/`), never mutating the base example spec.
- The engine runs a spec FILE; there is no override arg — so 2a derives a real modified spec file and runs THAT via `PipelineRunner.start(derived_spec_path, …)`.
- Do not regress Phase-1 Discover/Open or the main-window mount.
- Mockup references: `.superpowers/brainstorm/988333-1783804257/content/intent-composer.html` (edit surface), `run-watch.html` (live run).

## File Structure

- Create `app/spec_remix.py` — pure: `editable_params(spec) -> dict[node_id, list[ParamField]]`, `derive_spec(base_spec_path, edits, dest_dir) -> str` (writes + returns path).
- Modify `app/pipeline_studio.py` — add `RemixView(Gtk.Box)` + `LiveRunView(Gtk.Box)`; extend `PipelineStudio` with the remix→run→done wiring (new stack pages `remix`, `run`).
- Modify `app/main_window.py` — nothing new expected (PipelineStudio is already mounted); confirm the new pages work within it.
- Create `tests/test_spec_remix.py`, extend `tests/test_pipeline_studio.py`, `tests/fixtures/` (a small editable spec).

---

### Task 1: Spec-remix helper (pure)

**Files:** Create `app/spec_remix.py`, `tests/test_spec_remix.py`.

**Interfaces produced:**
- `@dataclass ParamField: node_id:str; key:str; label:str; kind:str; value` (kind ∈ text|number|choice|bool; `label` is plain-language, e.g. "Prompt", "Steps"; wired inputs are EXCLUDED — you can't edit a value that comes from another node).
- `editable_params(spec:dict) -> dict[str, list[ParamField]]` — per node, the non-wire scalar inputs, labelled. Skips inputs whose value is a wire (`[id,key]`) and structural/`_`-prefixed keys.
- `derive_spec(base_spec_path:str, edits:dict[node_id, dict[key,value]], dest_dir:str) -> str` — load base spec, apply edits (only to existing non-wire inputs; ignore unknown node/key), write to `dest_dir/remix_<basename>_<n>.json` (n avoids collision without Date/random — use a count of existing files), return the path. Never mutate the base file.

- [ ] **Step 1: Write failing tests** (fixture: a 3-node spec with text+number inputs and one wired input): `editable_params` returns the scalar inputs (prompt:text, steps:number) and EXCLUDES the wired input; `derive_spec` applies an edited prompt + steps, writes a new file (base file unchanged, verified by re-reading), and the derived spec loads via `pipeline_engine.load_spec` with the new values; unknown node/key in edits is ignored, not fatal.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `app/spec_remix.py` (pure; may import `pipeline_engine._is_wire`/`load_spec`).
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `feat(sp-c): spec-remix helper (editable params + derive modified spec)`.

---

### Task 2: RemixView (GTK) — minimal edit surface

**Files:** Modify `app/pipeline_studio.py`, extend `tests/test_pipeline_studio.py`.

**Interfaces produced:** `RemixView(Gtk.Box)` with `set_run(run:RunView, spec_path:str)` — renders each step (intent label, model quiet) with editable widgets for its `editable_params` (Gtk.Entry for text, SpinButton for number, DropDown for choice, Switch for bool), pre-filled with current values; a "Run this remix →" button emits custom signal `run-remix` (str spec_path, object edits-dict). Focus (per the "change one thing" idea): make it obvious you can tweak just one field and run. Uses `spec_remix.editable_params`. Data only via `set_run` (no store import in the widget).

- [ ] **Step 1:** xvfb test — `set_run(fixture RunView, fixture spec_path)` builds one editable field per editable param, pre-filled; changing a field and clicking Run emits `run-remix` with an edits dict carrying the changed value (real signal). No-edit run emits an empty-ish edits dict (runs the base unchanged). → fail.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement `RemixView` (reuse Phase-1 CSS/`_build_thumb_frame`; intent-composer mockup for layout). Widget kind per `ParamField.kind`.
- [ ] **Step 4:** Run → pass.
- [ ] **Step 5:** Commit `feat(sp-c): RemixView (edit a run's params, minimal)`.

---

### Task 3: LiveRunView (GTK) — watch it run

**Files:** Modify `app/pipeline_studio.py`, extend `tests/test_pipeline_studio.py`.

**Interfaces produced:** `LiveRunView(Gtk.Box)` with `begin(run:RunView)` (render steps as pending, intent-labelled, per `run-watch` mockup) and `on_node_update(job, node_id, status, detail)` / `on_log(line)` / `on_finished(...)` handler methods that update a step row's status glyph (✓/⟳/•/✕) + append to a live log tail. Surfaces the `__health__`/`__chips__` and board-switch `LOG:` lines as first-class rows (per the mockup's transparency). Emits `run-done` (str run_id) when finished. These handlers are what `PipelineStudio` passes to `PipelineRunner`.

- [ ] **Step 1:** xvfb test — `begin(fixture RunView)` shows all steps pending; calling `on_node_update(job,"1","done","")` flips step 1 to ✓; `on_log("LOG:  resetting boards (flux → skyreels)")` adds a switch row; `on_finished` emits `run-done`. (Drive the handlers directly — no real subprocess.) → fail.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement `LiveRunView` (map `NODE:` node_id→its step row by node_id; the `run-watch` mockup for layout; live log tail). Match the signal-string formats `PipelineRunner` emits.
- [ ] **Step 4:** Run → pass.
- [ ] **Step 5:** Commit `feat(sp-c): LiveRunView (live NODE/LOG progress)`.

---

### Task 4: Wire the loop (Open → Remix → Run → done)

**Files:** Modify `app/pipeline_studio.py`, extend `tests/test_pipeline_studio.py`.

- [ ] **Step 1:** Test (xvfb, `PipelineRunner` + `PipelineStore` mocked): OpenView's `remix-request` → `PipelineStudio` shows `remix` page with `RemixView.set_run`; `RemixView`'s `run-remix` → `PipelineStudio` calls `spec_remix.derive_spec` (writing to a tmp remixes dir), `PipelineStore.create_run(derived_path, …)`, and `PipelineRunner(idle_add=…).start(derived_path, …, on_node_update=live.on_node_update, on_log=live.on_log, on_finished=…)`, and switches to the `run` page; assert the mocked runner received the DERIVED spec path and the LiveRunView handlers as callbacks. `run-done` → return to Open for that run (now showing the fresh artifacts). → fail.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement the wiring in `PipelineStudio` (new `remix`/`run` stack pages; a remixes dir under the app data dir; pass `GLib.idle_add` to `PipelineRunner`). On `run-done`, rebuild the Open view from the new run record so the user sees real results. Back controls throughout.
- [ ] **Step 4:** Run → pass; full suite (xvfb) — no NEW failures.
- [ ] **Step 5:** Commit `feat(sp-c): wire Remix → Run → done loop`.

---

### Task 5: Changelog + version

- [ ] Bump `VERSION` (minor) + changelog stanza: "Pipeline Studio Phase 2a — Remix a run's parameters and Run the derived pipeline live (intent-labelled progress + board-switch transparency), closing the browse→remix→run loop." Commit.

---

## Self-Review

**Spec coverage (Phase 2a slice of SP-C):** derive-modified-spec-from-edits → Task 1; minimal Remix edit surface → Task 2; live Run view over the real runner → Task 3; the Open→Remix→Run→done loop wired → Task 4. Phase 2b (free-form composer + wing-it + dynamic capabilities) and Phase 3 (Showcase generator) remain separate plans. ✓
**Placeholder scan:** Task 1 pure w/ complete code; GTK tasks specify widget APIs, custom signals, the exact runner/store/spec_remix calls, and xvfb behaviour tests, with the validated mockups as layout reference; remix scope is explicitly minimal (edit existing scalar params) with add/remove deferred to 2b — no half-built pretense. No TBDs. ✓
**Type consistency:** `ParamField`, `editable_params`, `derive_spec`, `RemixView.set_run`, `run-remix`(spec_path, edits), `LiveRunView.begin/on_node_update/on_log/on_finished`, `run-done` — names consistent across tasks; `PipelineRunner.start`/`PipelineStore.create_run` signatures match the real modules; NODE:/LOG: formats match `pipeline_runner`. ✓
