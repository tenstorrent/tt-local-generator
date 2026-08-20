# SP-C Phase 3: In-app showcase generator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Productize the showcase capstone — a standard, offered-on-completion action that turns a finished run into a self-contained, shareable HTML page: the results (hero + gallery, real artifacts embedded) **and** the pipeline recipe behind them. This is the in-app version of the pattern already proven by the live published 1964 artifact.

**Architecture:** A pure-ish **showcase generator** builds self-contained HTML from a `RunView` (embedding each artifact as a data URI via an injected encoder), styled in the dark forest-teal identity (per the validated `showcase-finale-v2` mockup — quiet/refined, NO literal effects). The composer's run-done / Open views gain a "Build showcase" affordance that generates the file (off-thread) and reveals it.

**Tech Stack:** Python 3.12 / GTK4, pytest (+xvfb), PIL (downscale). Reuses `app/pipeline_view_model.py` (`RunView`/`StepView`), `app/intent_vocab.py` (recipe labels), `app/pipeline_studio.py`. Design source: the proven scratchpad builder (dark editorial hero+gallery+recipe+footer) + the `showcase-finale-v2.html` mockup.

## Global Constraints

- The generator core has **no GTK imports**; the asset-encoding (PIL/base64/file-read) is behind an injected `encode_asset(path, kind) -> data_uri|None` so unit tests run without PIL/large data/real files.
- **Self-contained output:** the emitted HTML references NO external hosts — every image/video/text artifact is inlined (data URI / inline text). Downscale images (hero ≤~1000px, gallery ≤~680px) to keep size sane; video embedded as `<video>` data URI; missing/absent artifacts shown as an honest placeholder, never fabricated.
- **Honesty:** the page reflects the real run — pending/failed steps shown as such (not faked); footer states "made with tt-local-generator on Tenstorrent hardware".
- **Identity:** dark forest-teal, quiet/refined delight (composition + type, NO literal fireworks/animation) per `project-showcase-finale` memory + the mockup.
- **The pipeline recipe is always shown** (the "how it was made" + one-click-remix hook — Phase 3 shows the recipe; a live "remix this" deep-link is a nice-to-have, not required).
- Reuse `RunView` (from `build_run_view`) — do NOT re-derive artifacts; the view-model already resolves them.
- No regression to Phase-1/2a/2b. GTK work off-thread → `GLib.idle_add`.
- Mockup: `.superpowers/brainstorm/988333-1783804257/content/showcase-finale-v2.html`.

## File Structure

- Create `app/showcase.py` — pure-ish: `build_showcase_html(run_view, *, encode_asset) -> str`; `write_showcase(run_view, dest_dir, *, encode_asset=default_encode_asset) -> str` (writes `<dest>/showcase_<run>_<n>.html`, returns path); `default_encode_asset(path, kind)` (PIL downscale + base64 for image, base64 for video, read text) — the only part touching PIL/disk.
- Modify `app/pipeline_studio.py` — a "Build showcase" affordance on `LiveRunView` finish (run-done) and/or `OpenView`; generates off-thread, then reveals the path (a label/"open externally"), via a `showcase_fn` seam.
- Tests: `tests/test_showcase.py`, extend `tests/test_pipeline_studio.py`, fixtures.

---

### Task 1: Showcase generator (pure-ish, injected encoder)

**Files:** Create `app/showcase.py`, `tests/test_showcase.py`, fixtures.

**Interfaces produced:**
- `build_showcase_html(run_view:RunView, *, encode_asset) -> str`: returns a full self-contained HTML string. Structure (from the mockup): eyebrow + `run_view.title` + a hero (first image/video artifact via `encode_asset`) + a "what it made" gallery (each `StepView` with an artifact → its embedded thumbnail + intent label + quiet model; pending/failed → honest placeholder) + a "The pipeline behind it" recipe (`run_view.recipe` intent labels joined with →) + an honest footer. Dark forest-teal inline `<style>`. `encode_asset(path, kind)` returns a data URI (or None → placeholder); kind derived from the step intent's `output_kind` (image|video|text). NO external refs.
- `write_showcase(run_view, dest_dir, *, encode_asset=default_encode_asset) -> str`: `mkdir` dest, write `showcase_<slug(title)>_<n>.html` (collision-safe count, no Date/random), return path.
- `default_encode_asset(path, kind) -> str|None`: image → PIL downscale (hero/gallery sizes) + base64 JPEG/PNG data URI; video → base64 mp4 data URI; text → read file → (returned specially or inlined by the builder); missing/unreadable → None. Only this fn imports PIL / reads disk.

- [ ] **Step 1: failing tests** (fixture `RunView` with a done image step (real tiny PNG path), a done text step, and a pending step; a FAKE `encode_asset` returning `"data:image/png;base64,AAAA"` for known paths / None for the pending): `build_showcase_html` returns HTML containing the title, the recipe labels, the fake data URI (embedded, no `http`/external `src`), the pending step as a placeholder (not a data URI), and the "tt-local-generator" footer; assert NO `src="http` / no external host anywhere. `write_showcase` writes a file (collision-safe) and returns its path. (Pure — fake encoder, no PIL.)
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** `app/showcase.py` (builder + `write_showcase` + `default_encode_asset`; lift the dark editorial template from the proven scratchpad builder `/tmp/.../scratchpad/build_showcase.py` + the `showcase-finale-v2` mockup).
- [ ] **Step 4: run → pass** (`/usr/bin/python3 -m pytest tests/test_showcase.py -q`); full suite (xvfb) — no NEW failures.
- [ ] **Step 5: commit** `feat(sp-c): showcase generator (self-contained HTML from a run)`.

---

### Task 2: "Build showcase" capstone in the app

**Files:** Modify `app/pipeline_studio.py`, extend `tests/test_pipeline_studio.py`.

- [ ] **Step 1: xvfb tests** — via a `showcase_fn` seam (default = `lambda run_view: showcase.write_showcase(run_view, SHOWCASES_DIR)`): `OpenView` (and the run-done state) shows a "Build showcase" control; clicking it calls `showcase_fn(run_view)` (inject a fake returning a tmp path) and reveals the resulting path (a label / an "open externally" affordance); a fake raising → gentle message, no crash. Assert the fake was called with the run's RunView and the path is surfaced. → fail.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** — add the control to `OpenView` (and/or the LiveRunView finished state); run `showcase_fn` OFF the main thread (generation embeds/encodes → may be slow) → `GLib.idle_add` to reveal the path; a small "Open" that launches the file via the platform opener (reuse whatever the app already uses to open files externally). Gentle failure message.
- [ ] **Step 4: run → pass; full suite (xvfb) — no NEW failures.**
- [ ] **Step 5: commit** `feat(sp-c): offer 'Build showcase' on a completed run`.

---

### Task 3: Changelog + version

- [ ] Bump `VERSION` (minor) + changelog stanza: "Pipeline Studio Phase 3 — build a shareable showcase from any completed run: a self-contained page with the results (real artifacts embedded) and the pipeline recipe behind them, in the app's dark identity. Completes the create arc (Discover → Open → Remix/Compose → Run → Showcase)." Commit.

---

## Self-Review

**Spec coverage (Phase 3):** self-contained showcase generator from a RunView (embedded assets + recipe, honest, dark) → Task 1; the offered-on-completion "Build showcase" capstone → Task 2; version → Task 3. Completes SP-C's five modes. ✓
**Placeholder scan:** Task 1 pure-ish with an injected encoder + TDD asserting self-containment (no external refs) + honest placeholders; GTK task specs the `showcase_fn` seam, off-thread generation, reveal/open, gentle failure, xvfb tests; design lifted from the PROVEN scratchpad builder + mockup (real, not invented). No TBDs. ✓
**Type consistency:** `build_showcase_html`, `write_showcase`, `default_encode_asset`, the `encode_asset`/`showcase_fn` seams, and `RunView`/`StepView`/`recipe` usage all consistent with the view-model + prior phases; threading follows the RemixView/PipelineRunner GLib pattern. ✓
