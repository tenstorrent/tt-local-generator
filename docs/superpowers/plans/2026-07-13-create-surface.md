# Create surface — Implementation Plan (first slice of the loop)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Introduce the Create·Curate·Discover·Remix **loop nav** and a unified **CreateView** where the medium is a chip (not a tab), with three doors (idea/model/inspiration) and per-art-type params — reorganizing the main app per `docs/superpowers/specs/2026-07-13-create-surface-design.md` and the north-star.

**Architecture:** Build CreateView **alongside** the existing UI and migrate incrementally — the app is never broken mid-flight. The loop nav replaces the toolbar's medium tabs; "Create" hosts the new surface, the other three verbs route to today's galleries / Pipeline Studio as-is for now. Generation backends (`GenerationWorker` family, `api_client`, `server_manager`) are untouched — CreateView is a new *surface* over existing machinery.

**Tech Stack:** Python 3.12 / GTK4 (PyGObject), pytest (+`xvfb-run`). Reuses `app/main_window.py` (`ControlPanel`, `_on_generate`, the `source-btn` toggle, `_gallery_stack`, the toolbar, `ArtgenPanel`), `app/worker.py` (worker family), `app/capability_discovery.py`, `app/server_manager.py`, `app/intent_vocab.py`.

## Global Constraints

- **Never break the running app.** Each task leaves generation fully working; the old tabs stay until every medium is covered + verified in CreateView (Task 8 removes them). No task may regress generation behavior.
- GTK single-thread; generation/health off-thread → `GLib.idle_add` (existing patterns). Brand dark forest-teal (already unified). Creative/intent language; humane per-type panels (NOT a flattened generic form). Reuse `GenerationWorker`/`api_client`/`server_manager` — no new generation code.
- Medium set is DISCOVERED (native intents + `artgen.all_names()` + plugins), so new art types appear automatically — no hardcoded medium list.
- System `/usr/bin/python3`; GTK tests via `xvfb-run`. Two pre-existing failures/skips expected. Local on `feat/pipeline-editor` — no push/merge.

### Reference: current create surface (what we reorganize)

- Toolbar tabs: Video / Animate / Image / Generative Art (+ 🧩 Pipelines toggle). Medium today = `model_source` ∈ {"video","image","animate"} via `.source-btn` toggles; Generative Art is a separate `ArtgenPanel` in `_gallery_stack` (child "artgen").
- `ControlPanel` (main_window.py:3876) holds the per-medium input controls. `MainWindow._on_generate(prompt, neg, steps, seed, …)` (≈9946) branches on the source → `ImageGenerationWorker` / `AnimateGenerationWorker` / `AnimateDiffGenerationWorker` / `GenerationWorker` (video).
- `_gallery_stack` (children: video/animate/image/artgen) + `_show_pipelines()` mount PipelineStudio — the precedent for mounting a new top-level view.

---

### Task 1: Loop nav chrome (Create · Curate · Discover · Remix)

**Files:** Modify `app/main_window.py` (toolbar). Test: `tests/test_main_window*.py` (xvfb).

Replace the medium-tab toolbar row with a **loop nav** of four movements. For THIS task the verbs route to *existing* surfaces so nothing breaks:
- **Create** → the current generation UI (the existing source toggle + ControlPanel + galleries), unchanged, just now reached via the Create verb.
- **Discover** → the galleries (+ a link into Pipeline Studio's Discover) — reuse `_gallery_stack` / the existing Pipelines mount.
- **Curate** → the gallery filtered to starred/playlists (reuse existing star/playlist surfaces) — for now, route to the gallery (a later slice refines).
- **Remix** → the Pipeline Studio Muse (`show_muse()`), reusing the existing mount.
- Keep 🧩 Pipelines reachable (Discover/Remix cover it); Watch/TT-TV lives under Discover.

- [ ] **Step 1: xvfb test** — assert the toolbar exposes a nav with the four verbs (a `_loop_nav` with buttons keyed create/discover/curate/remix), Create default-active, and activating each routes to the expected surface (mock the surface-switch calls). Old generation still reachable via Create. → fail.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** the loop nav (reuse the `.source-btn`/attractor button styling → brand). Route handlers call existing show/switch methods. Do NOT yet change ControlPanel internals.
- [ ] **Step 4: run → pass; full suite (xvfb) no NEW failures — generation path untouched.**
- [ ] **Step 5: commit** `feat(ui): loop nav — Create/Curate/Discover/Remix (routes to existing surfaces)`.

---

### Task 2: Medium-chip discovery (pure)

**Files:** Create `app/create_mediums.py`, `tests/test_create_mediums.py`.

**Interfaces:**
```python
@dataclass(frozen=True)
class Medium:
    id: str            # "image" | "video" | "animate" | "verse" | "ansi" | ...
    label: str         # chip text ("Image", "Video", "Verse", ...)
    icon: str          # one emoji
    kind: str          # output kind (image|video|gif|text)
    source: str        # "native" (a GenerationWorker medium) | "artgen" (a generator)
    generator: "str | None"   # artgen generator name, else None
def discover_mediums(*, artgen_names, native=None) -> "list[Medium]": ...
```
Native mediums (image/video/animate) + one Medium per `artgen.all_names()` generator (verse/ansi/landscape/…), deterministic order (native first). `default_mediums()` wraps with the real `artgen.all_names()`. Pure; injected deps.

- [ ] Steps 1-5 (TDD): fake `artgen_names` → assert native + per-generator mediums, order, kinds; `default_mediums()` thin real wrapper. Commit `feat(create): medium discovery`.

---

### Task 3: `CreateView` shell + medium chips + doors + model strip

**Files:** Create `app/create_view.py`, `tests/test_create_view.py`. Modify `main_window.py` (mount under Create).

`CreateView(Gtk.Box)`: the doors row (idea default / model / inspiration), the medium chip row (from `create_mediums.default_mediums`, context-selectable), a host box for the per-medium param panel (Task 4+), the live-model strip (from `server_manager` health, off-thread), and the Create CTA. Injectable seams: `mediums_fn`, `health_fn`, `on_create(medium, params)`, `on_inspiration()`. Emits/【calls】 `on_create` with the chosen medium + collected params. Mount as the Create verb's content (behind Task 1's nav); the OLD source-toggle UI remains available until Task 8.

- [ ] Steps 1-5 (xvfb, injected fakes): chips render from a fake `mediums_fn`; selecting a chip sets the active medium + swaps the param-panel host; the model strip shows fake running/runnable models; "idea"/"model"/"inspiration" doors toggle; CTA calls `on_create` with medium+params; inspiration door calls `on_inspiration`. Commit `feat(create): CreateView shell (doors, chips, model strip)`.

---

### Task 4: `CreateParamPanel` protocol + port the IMAGE medium

**Files:** Modify `app/create_view.py`, `app/main_window.py` (extract image controls). Test: extend `tests/test_create_view.py`.

Define `CreateParamPanel` (`build() -> Gtk.Widget`, `collect() -> dict`). Port the **image** medium's controls (size/steps/seed/style/negative) out of `ControlPanel` into an `ImageParamPanel` reused by BOTH the old ControlPanel and CreateView (single source of the control logic — refactor, don't duplicate). CreateView's CTA for image → the existing `ImageGenerationWorker` path via `on_create`.

- [ ] Steps 1-5: assert selecting Image shows `ImageParamPanel`, `collect()` returns the image params dict the existing worker expects, CTA routes to the (injected) image-generation seam. Old Image tab still works (same panel). Commit `feat(create): CreateParamPanel + image medium`.

---

### Task 5: Port VIDEO + ANIMATE mediums

**Files:** Modify `app/create_view.py`, `app/main_window.py`. Test: extend.

Port video (frames/resolution/model → `GenerationWorker`) and animate (ref video/char, mode → `AnimateGenerationWorker`) into `VideoParamPanel` / `AnimateParamPanel`, reused by old + new. CTA routes to the existing workers.

- [ ] Steps 1-5: each medium shows its own panel, `collect()` matches the worker's params, CTA routes correctly; old tabs unaffected. Commit `feat(create): video + animate mediums`.

---

### Task 6: Port ARTGEN generators as mediums (dissolve the Generative-Art overload)

**Files:** Modify `app/create_view.py`; reuse `ArtgenPanel`'s per-generator controls. Test: extend.

Each artgen generator (verse/ansi/landscape/…) becomes a medium whose param panel is built from the generator's own args (reuse the artgen control-building `ArtgenPanel` already does; do NOT reimplement). CTA routes to the existing artgen generation path. This is where the Generative-Art tab's contents become Create options.

- [ ] Steps 1-5: a sample generator medium renders its real controls; `collect()` → the artgen run params; CTA routes to the (injected) artgen seam. Commit `feat(create): artgen generators as Create mediums`.

---

### Task 7: The three doors wired (idea / model / inspiration)

**Files:** Modify `app/create_view.py`, `main_window.py`. Test: extend.

- **Idea** (default): a prompt entry + medium chips; medium defaults sensibly, overridable.
- **Model**: the model strip becomes selectable model cards (running one-tap; runnable with start cost + board-reset note via `server_manager`); choosing a model **sets the medium from the model's family** and shows its panel.
- **Inspiration**: hands to the existing Muse bridge (`show_muse`) — no reimplementation.

- [ ] Steps 1-5: model-door select sets medium from a fake model→medium map + shows the panel; idea-door default medium; inspiration-door calls the muse seam. Commit `feat(create): three doors (idea/model/inspiration)`.

---

### Task 8: Switch over — remove the old medium tabs

**Files:** Modify `app/main_window.py`. Test: full suite.

Once every medium (image/video/animate/all artgen) is covered + verified in CreateView, make Create the sole generation surface: remove the old `.source-btn` toggle + the medium-tab wiring; `_gallery_stack` becomes Discover's surface. Keep `ControlPanel`'s now-shared param panels. Verify generation for every medium end-to-end (dry/mocked) before deleting the old path.

- [ ] Steps 1-5: assert the old source toggle is gone, Create is the generation surface, every medium still generates (mocked workers), Discover/Curate/Remix nav intact. Full suite green. Commit `feat(create): retire medium tabs — Create is the generation surface`.

---

### Task 9: Version + changelog

- [ ] Bump `VERSION` (minor → 0.26.0) + changelog stanza: the app reorganized around the Create·Curate·Discover·Remix loop; Create unifies all generation (medium is a chip, three doors, per-art-type panels, model-led entry), dissolving the Generative-Art overload. Commit.

---

## Self-Review

**Spec coverage:** loop nav → Task 1; medium-as-property discovery → Task 2; CreateView (doors/chips/model strip) → Task 3; per-type humane panels + all mediums → Tasks 4-6; three doors incl. model-led → Task 7; retire tabs → Task 8; version → Task 9. Every spec section maps. ✓
**Migration safety:** Tasks 1-7 build alongside; the old path works until Task 8 flips it after full coverage — the app is never broken mid-plan. ✓
**Placeholder scan:** each task has a concrete deliverable, injected seams for testability, and reuses existing workers/panels (refactor not rewrite). Real anchors (ControlPanel, _on_generate, source-btn, _gallery_stack, ArtgenPanel). No new generation code. ✓
**Type consistency:** `Medium`, `discover_mediums`, `CreateParamPanel` (build/collect), the `mediums_fn`/`health_fn`/`on_create`/`on_inspiration` seams, and the worker param dicts are consistent across tasks; medium discovery reuses the Muse's capability sources. ✓
