# SP-C Phase 1: Discover + Open — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The "feels real" anchor of Pipeline Studio — browse already-run pipelines (Discover) and open one end-to-end (Open), spoken in intent language, grounded in real run artifacts. No editing yet (Phase 2).

**Architecture:** A pure **intent-vocabulary** layer (`class_type ↔ verb+noun intent`) + a pure **run view-model** builder (reads `PipelineStore` records + each run's `output_dir` artifacts) feed two GTK views mounted in a `Gtk.Stack` inside a new "Pipelines" area of the main window. Reuses SP-A's engine outputs and the existing stores; adds no runtime.

**Tech Stack:** Python 3.12 / GTK4 (PyGObject), pytest (+`xvfb-run` for widget tests). System python `/usr/bin/python3`.

## Global Constraints

- Pure logic (`intent_vocab.py`, the view-model builder) has **zero GTK imports** and is unit-tested without a display.
- GTK views obey the threading rule: any filesystem/scan work off the main thread → `GLib.idle_add` for UI updates (CLAUDE.md GTK discipline). Phase-1 reads are light; still never block the main loop on large scans.
- Intent language only in the UI — verb+noun labels; model is a quiet, secondary detail. Source of truth = `intent_vocab.INTENTS`, covering all 12 SP-A `class_type`s (`workflow_compat.COMPATIBILITY_MAP` native keys).
- Real content: Discover/Open render actual run artifacts from a run's `output_dir` (e.g. `node1_image.png`); missing/pending artifacts show an honest placeholder, never a fabricated one.
- Visual identity: dark forest-teal (per the validated mockups + `project-pipeline-ux-philosophy` visual note). No literal effects.
- Phase 1 is read-only browse/learn: "Remix from here" / "Remix" affordances are present but wired to a Phase-2 stub (emit an intent signal / no-op with a "coming next" toast) — do NOT build the editor here.
- Mockup references (layout source of truth): `.superpowers/brainstorm/988333-1783804257/content/discover-gallery.html`, `open-run.html`.

## File Structure

- Create `app/intent_vocab.py` — pure `class_type → Intent` map + helpers.
- Create `app/pipeline_view_model.py` — pure builder: run record → `RunView`/`StepView` dataclasses (title, hero artifact path, ordered steps with intent + artifact path + status).
- Create `app/pipeline_studio.py` — GTK: `PipelineStudio(Gtk.Box)` hosting a `Gtk.Stack` with `DiscoverView` + `OpenView`.
- Modify `app/main_window.py` — mount `PipelineStudio` as a "Pipelines" area (a `Gtk.Stack` child + nav entry), lazily.
- Create `tests/test_intent_vocab.py`, `tests/test_pipeline_view_model.py`, `tests/test_pipeline_studio.py` (xvfb), `tests/fixtures/sp_c_run/…`.

---

### Task 1: Intent-vocabulary layer

**Files:** Create `app/intent_vocab.py`, `tests/test_intent_vocab.py`.

**Interfaces produced:**
- `@dataclass(frozen=True) class Intent: class_type:str; verb:str; noun:str; icon:str; outputs:tuple[str,...]; model_label:str|None`
- `INTENTS: dict[str, Intent]` — one per native class_type.
- `intent_for(class_type:str) -> Intent` (unknown → a generic `Intent(class_type, "Run", class_type, "•", (), None)`).
- `label(class_type) -> str` → e.g. `"Generate an image"`.

- [ ] **Step 1: Write the failing test**
```python
import importlib.util, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import intent_vocab as iv

def test_covers_all_native_class_types():
    from workflow_compat import COMPATIBILITY_MAP
    native = [k for k,v in COMPATIBILITY_MAP.items() if v.get("ttlg")==k]
    for ct in native:
        assert ct in iv.INTENTS, f"missing intent for {ct}"

def test_intent_shape_and_language():
    i = iv.INTENTS["TTLGTextToImage"]
    assert i.verb and i.noun and i.icon
    assert "image" in iv.label("TTLGTextToImage").lower()
    assert "TTLG" not in iv.label("TTLGTextToImage")   # no tool names in the label
    assert "image_path" in i.outputs                    # matches the engine output-key contract

def test_unknown_is_generic_not_crash():
    assert iv.intent_for("TTLGNope").class_type == "TTLGNope"
```

- [ ] **Step 2: Run → fail** (`/usr/bin/python3 -m pytest tests/test_intent_vocab.py -q`).
- [ ] **Step 3: Implement** `app/intent_vocab.py` with the dataclass + `INTENTS` for all 12 native types, each verb/noun/icon/outputs matching the SP-A output-key contract (image_path, video_path, text, caption, fg_path, depth_path, prompt, png_path, image_path, playlist_id, + TTLGArtgenGenerate→artifact_path/text/png_path, TTLGAnimateDiff→gif_path). Labels are verb+noun ("Generate an image", "Film it"/"Animate it", "Write about it", "Describe it", "Cut out the subject", "Read its depth", "Compose a prompt", "Make generative art", "Combine them", "Collect the results"). `model_label` for the tool-bearing ones (e.g. "FLUX", "SkyReels", "Llama"), None for CPU/compose.
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `feat(sp-c): intent vocabulary (class_type → verb+noun)`.

---

### Task 2: Run view-model builder

**Files:** Create `app/pipeline_view_model.py`, `tests/test_pipeline_view_model.py`, `tests/fixtures/sp_c_run/` (a fixture run: a small spec JSON + a couple of artifact files + a matching PipelineStore-style record).

**Interfaces produced:**
- `@dataclass class StepView: node_id:str; intent:Intent; status:str; artifact_path:str|None` (status ∈ done|pending|failed)
- `@dataclass class RunView: run_id:str; title:str; created_at:str; hero_path:str|None; steps:list[StepView]; recipe:list[str]` (recipe = ordered intent labels)
- `build_run_view(record:dict) -> RunView` — loads the run's spec (from `record["spec_path"]`), topo-orders via `pipeline_engine.topo_order`, maps each node to its `Intent`, resolves each step's artifact from `record["output_dir"]` (e.g. `node{id}_image.png`/`_video.mp4`/`_fg.png`/`_depth.png` — existence → done, else pending), picks the hero (first image/video artifact).
- `list_run_views(store, limit=50) -> list[RunView]` — over `store.list_runs()`.

- [ ] **Step 1: Write failing tests** — with the fixture run: `build_run_view` returns steps in topo order, each `StepView.intent` correct, done/pending by artifact existence, `hero_path` = the seed image, `recipe` = verb+noun labels; `list_run_views` maps a fake store's `list_runs()`. (Reuse `pipeline_engine.topo_order` — already tested.)
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** the builder (pure; imports `intent_vocab`, `pipeline_engine.topo_order`, reads files; NO GTK). Artifact-name resolution mirrors the engine handlers' `ctx.output_dir / f"node{nid}_*"` naming.
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `feat(sp-c): run view-model (record → intent steps + artifacts)`.

---

### Task 3: Discover view (GTK)

**Files:** Create `app/pipeline_studio.py` (start it: `DiscoverView(Gtk.Box)` + the `PipelineStudio` shell), add `tests/test_pipeline_studio.py`.

**Interfaces produced:** `DiscoverView(Gtk.Box)` with `set_runs(list[RunView])` (builds a featured hero card from runs[0] + a grid of run cards, each showing hero thumbnail via `main_window._load_pixbuf` (aspect-preserving), title, and the `recipe` as intent chips); emits `self.emit("open-run", run_id)` (custom GObject signal) on a card's Open. `PipelineStudio(Gtk.Box)` holds a `Gtk.Stack` {discover, open} and loads runs via `list_run_views(PipelineStore())` off-thread → `GLib.idle_add(discover.set_runs, …)`.

- [ ] **Step 1:** xvfb construction test — `DiscoverView()` builds; `set_runs([fixture RunView])` populates without error; the featured card exposes the hero + recipe labels; clicking Open emits "open-run" with the run_id (connect a handler, assert called). Follow the layout of the `discover-gallery` mockup (hero + 3-col grid, dark scheme via CSS provider).
- [ ] **Step 2:** Run under `xvfb-run … pytest tests/test_pipeline_studio.py -q` → fail.
- [ ] **Step 3:** Implement `DiscoverView` + `PipelineStudio` shell (CSS provider for the dark forest-teal tokens; reuse `_load_pixbuf`). Data comes only via `set_runs` (no store import in the widget — testable).
- [ ] **Step 4:** Run → pass.
- [ ] **Step 5:** Commit `feat(sp-c): Discover view (browse runs as intent recipes)`.

---

### Task 4: Open view (GTK)

**Files:** extend `app/pipeline_studio.py` (`OpenView(Gtk.Box)`), extend `tests/test_pipeline_studio.py`.

**Interfaces produced:** `OpenView(Gtk.Box)` with `set_run(RunView)` — renders the run title + each `StepView` as a row (intent label, model detail muted, status ✓/⟳/•, artifact thumbnail for done steps via `_load_pixbuf`, honest placeholder for pending), and a per-step "Remix from here →" button + a top "Remix whole pipeline →" that (Phase 1) emit `self.emit("remix-request", node_id_or_empty)` — wired to a "coming in the editor" toast, NOT an editor. Follows the `open-run` mockup.

- [ ] **Step 1:** xvfb test — `set_run(fixture RunView)` renders one row per step in order; done steps expose a thumbnail, pending show the placeholder; "Remix from here" emits "remix-request" with the node id. → fail.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement `OpenView`; `PipelineStudio` switches its `Gtk.Stack` to `open` on `DiscoverView`'s "open-run" (calls `build_run_view(store.get_run(id))` off-thread → `GLib.idle_add(open.set_run, …)`), with a back-to-discover control.
- [ ] **Step 4:** Run → pass.
- [ ] **Step 5:** Commit `feat(sp-c): Open view (one run end-to-end, per-step remix stub)`.

---

### Task 5: Mount in the main window

**Files:** Modify `app/main_window.py`; extend `tests/test_pipeline_studio.py` (or a small `tests/test_main_window_pipelines.py`).

- [ ] **Step 1:** Test that the main window exposes a way to reach `PipelineStudio` (a nav/stack entry named "pipelines") and that activating it lazily constructs a `PipelineStudio` (assert the child exists after activation). Mock `PipelineStore.list_runs` to a fixture so no real disk dependency.
- [ ] **Step 2:** Run (xvfb) → fail.
- [ ] **Step 3:** Add a "Pipelines" entry to the main window's existing navigation (mirror how other views mount on the `Gtk.Stack`; construct `PipelineStudio` lazily on first activation to avoid startup cost). Do not disturb existing tabs/regressions.
- [ ] **Step 4:** Run → pass; run the full suite under xvfb — no NEW failures.
- [ ] **Step 5:** Commit `feat(sp-c): mount Pipeline Studio (Discover+Open) in the main window`.

---

### Task 6: Changelog + version

- [ ] Bump `VERSION` (minor — new user-visible Pipelines browse/open) and prepend a `debian/changelog` stanza: "Pipeline Studio Phase 1 — browse already-run pipelines (Discover) and open one end-to-end (Open), in intent language, grounded in real run artifacts." Commit.

---

## Self-Review

**Spec coverage (Phase 1 of the SP-C design):** intent language → Task 1; real-artifact grounding + recipe → Task 2; Discover (browse real runs) → Task 3; Open (learn end-to-end, per-step remix stub) → Task 4; woven into the main app → Task 5. Phases 2 (Compose/Remix+Run) and 3 (Showcase) are separate plans, per the umbrella spec. ✓
**Placeholder scan:** pure layers (Tasks 1-2) carry complete code; GTK tasks specify widget structure, the exact store/vocab calls, custom signals, and construction/behaviour tests, with the validated mockups as the pixel layout reference — no TBDs. Remix is explicitly a Phase-2 stub (a signal + toast), not omitted-but-implied. ✓
**Type consistency:** `Intent`, `INTENTS`, `intent_for`, `label`, `StepView`, `RunView`, `build_run_view`, `list_run_views`, `DiscoverView.set_runs`, `OpenView.set_run`, signals `open-run`/`remix-request` — names identical across tasks; artifact keys match the SP-A output-key contract. ✓
