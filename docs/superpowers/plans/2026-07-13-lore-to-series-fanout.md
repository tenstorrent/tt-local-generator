# Lore → a series of stills + a montage movie — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn a generated text "lore" artifact into a **series** — one image per fragment (one FLUX batch), collected into a playlist and stitched into a captioned montage movie — reachable via "🧩 Remix as pipeline" from the Generative Art gallery.

**Architecture:** A **text→pipeline bridge** (artgen gallery → scoped text muse) plus a **fan-out** built as list-valued dataflow + vectorized nodes (no new engine execution model): a split node emits a list, `TTLGTextToImage` generates per-element in one backend session, and a montage node + list-aware playlist consume the list.

**Tech Stack:** Python 3.12 / GTK4 (PyGObject), pytest (+`xvfb-run`), ffmpeg. Reuses `app/pipeline_engine.py`, `app/intent_vocab.py`, `app/spec_remix.py`, `app/recipes.py`, `app/pipeline_studio.py` (`show_muse`), `app/main_window.py` (`_remix_as_pipeline` seam), `app/artgen_panel.py`, `app/media_store.py` (`MediaRecord`).

## Global Constraints

- Pure layers (`split_text`, `artgen_kind`, `recipes`) have **zero GTK imports**; ffmpeg/subprocess behind mockable seams.
- **Never fail hard** — split/kind/montage/fan-out all degrade gracefully; a montage failure must not lose the stills.
- **Board-friendly** — all N image generations happen in ONE `TTLGTextToImage` node execution (one FLUX session, no per-item backend switch). Cap N (`max_items`, default 8) and `log()`/`emit` when a longer lore is truncated.
- **Kind-safe, NO new "list" kind** — list-ness is a runtime handler concern; a list of image paths is still kind `"image"`, a list of fragments still `"text"`. `seed_spec`/`add_step` kind guards are unchanged and remain the backstop.
- **Reuse, don't fork** — the bridge reuses the Task-5 `remix_as_pipeline` path + `show_muse`; recipes build via `seed_spec`; the video preview reuses the v0.22.0 poster-frame path.
- Intent language in ALL copy; model/tool a quiet detail. No regression to prior SP-C phases.
- System `/usr/bin/python3`. GTK tests: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest …`. Two pre-existing failures/skips expected. Everything local on `feat/pipeline-editor` — no push/merge.

### Reference: engine handler pattern (from `pipeline_engine.py`)

```python
@register("TTLGThing")
def _h_thing(nid, inp, ctx):           # ctx: .output_dir (Path), .dry_run (bool), .emit (callable)
    if ctx.dry_run:
        return {"out_key": <deterministic placeholder>}
    ...
    return {"out_key": value}
```
`resolve_inputs` turns `[node_id, key]` wires into the upstream value (a scalar OR a list). `seed_spec(steps, *, seed_artifact=(value, kind))` mints node ids `"1".."N"` in list order, auto-wires each step's `input_key` to the previous step's primary output (kind-checked), and merges each step's `params` on top — **a param whose value is `[node_id, key]` is itself a wire**, so a recipe can express non-linear (DAG) wiring by referencing earlier node ids. `seed_artifact`'s `value` is a path for media kinds and the **literal text** for `kind=="text"` (it is placed verbatim on step 1's `input_key` — no `seed_spec` change needed for text).

---

### Task 1: `TTLGSplitText` — text → list of fragments

**Files:** Create `app/split_text.py`, `tests/test_split_text.py`. Modify `app/pipeline_engine.py` (handler), `app/intent_vocab.py` (intent).

**Interfaces produced:**
```python
# app/split_text.py  (pure, no GTK)
def split_text(text: str, mode: str = "paragraphs", max_items: int = 8) -> "list[str]":
    """Split lore into fragments. mode: "paragraphs" (blank-line groups),
    "lines" (non-blank lines), "numbered" (strip leading "N." / "N)" markers,
    falling back to lines). Trims whitespace, drops empties, caps at max_items
    (>=1). Returns [] for empty/whitespace text (caller handles)."""
```
- Engine: `@register("TTLGSplitText") def _h_split_text(nid, inp, ctx)` → `{"fragments": split_text(inp.get("text",""), inp.get("mode","paragraphs"), int(inp.get("max_items",8)))}`. In `ctx.dry_run`, still call `split_text` on the real input (it's pure/cheap) so dry-run reflects the true fan-out width; if the input text is a placeholder wire that didn't resolve, return `{"fragments": ["fragment 1", "fragment 2"]}`. `emit` a `LOG:` line when the split truncated (`len(raw) > max_items`).
- `intent_vocab.INTENTS["TTLGSplitText"]`: `verb="Break"`, `noun="into fragments"`, `icon="📑"`, `outputs=("fragments",)`, `model_label=None`, `input_key="text"`, `input_kind="text"`, `output_kind="text"`.

- [ ] **Step 1: failing tests** (`tests/test_split_text.py`, pure):
```python
from split_text import split_text
def test_paragraphs(): 
    assert split_text("a\n\nb\n\nc", "paragraphs") == ["a", "b", "c"]
def test_numbered_strips_markers():
    assert split_text("1. red\n2. blue\n3) green", "numbered") == ["red", "blue", "green"]
def test_caps_at_max_items():
    assert len(split_text("\n\n".join(str(i) for i in range(20)), "paragraphs", max_items=8)) == 8
def test_empty_returns_empty():
    assert split_text("   \n  ", "paragraphs") == []
def test_lines_drops_blanks():
    assert split_text("x\n\n y \n", "lines") == ["x", "y"]
```
- [ ] **Step 2: run → fail** (`/usr/bin/python3 -m pytest tests/test_split_text.py -q`).
- [ ] **Step 3: implement** `split_text`, the handler, the intent entry.
- [ ] **Step 4: run → pass**; a dry-run engine test: a 1-node spec `{"1":{"class_type":"TTLGSplitText","inputs":{"text":"a\n\nb\n\nc"}}}` → `run(...,dry_run=True)` gives `results["1"]["fragments"]==["a","b","c"]`.
- [ ] **Step 5: commit** `feat(sp-c): TTLGSplitText — split lore text into a list of fragments`.

---

### Task 2: `TTLGTextToImage` list-aware (vectorized fan-out)

**Files:** Modify `app/pipeline_engine.py` (`_h_text_to_image`). Test: `tests/test_pipeline_engine.py`.

**Behavior:** when the resolved `prompt` input is a **list**, generate one image per element **in this single node execution** (one FLUX session — `_backend_for` already started FLUX before dispatch) and return `{"image_path": [list of paths]}`; a scalar prompt keeps the current single behavior (`{"image_path": path}`). Optional `style_suffix` (str) is appended to every prompt. Per-element output path: `node{nid}_image_{i}.png`. In `ctx.dry_run`, return the list of would-be paths without calling the server. A single element's generation failure is logged and skipped (its slot omitted), the rest proceed.

```python
@register("TTLGTextToImage")
def _h_text_to_image(nid, inp, ctx):
    prompt = inp.get("prompt", "")
    suffix = inp.get("style_suffix", "") or ""
    if isinstance(prompt, list):
        paths = []
        for i, frag in enumerate(prompt):
            out = str(ctx.output_dir / f"node{nid}_image_{i}.png")
            full = f"{frag}{suffix}"
            if ctx.dry_run:
                paths.append(out); continue
            try:
                paths.append(_media_image_request(
                    server=inp.get("server", "http://localhost:8000"), prompt=full,
                    width=inp.get("width",1024), height=inp.get("height",1024),
                    steps=inp.get("steps",4), seed=inp.get("seed",0),
                    negative_prompt=inp.get("negative_prompt"), out_path=out))
            except Exception as e:  # noqa: BLE001 — skip a bad frame, keep the batch
                ctx.emit(f"LOG:  image {i} failed: {e}")
        return {"image_path": paths}
    # scalar (unchanged)
    out = str(ctx.output_dir / f"node{nid}_image.png")
    if ctx.dry_run:
        return {"image_path": out}
    full = f"{prompt}{suffix}"
    return {"image_path": _media_image_request(server=inp.get("server","http://localhost:8000"),
        prompt=full, width=inp.get("width",1024), height=inp.get("height",1024),
        steps=inp.get("steps",4), seed=inp.get("seed",0),
        negative_prompt=inp.get("negative_prompt"), out_path=out)}
```

- [ ] **Step 1: failing tests** — dry-run a spec `SplitText → TextToImage` (TextToImage.prompt wired to split's fragments): assert `results["2"]["image_path"] == ["…/node2_image_0.png","…_1.png","…_2.png"]` for a 3-fragment lore; and a scalar-prompt spec still yields a single `image_path` string; and `style_suffix` is appended (mock `_media_image_request` to capture the prompt).
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** the list-aware handler (verbatim above).
- [ ] **Step 4: run → pass**; full engine test file green.
- [ ] **Step 5: commit** `feat(sp-c): TTLGTextToImage generates a batch when its prompt is a list (fan-out)`.

---

### Task 3: `TTLGMontage` — list of images → one captioned slideshow mp4

**Files:** Modify `app/pipeline_engine.py` (handler + `COMPATIBILITY_MAP` optional entry), `app/intent_vocab.py`. Test: `tests/test_pipeline_engine.py`.

**Behavior:** input `images` (list of paths) + optional `captions` (list of strings) + `seconds_per` (default 2.5) → one ffmpeg slideshow `node{nid}_montage.mp4`. Captions are best-effort (overlaid via ffmpeg `drawtext` per image; if drawtext fails, plain slideshow). **Fail soft:** if ffmpeg is absent or fails, or `images` is empty/not a list, return `{"video_path": None}` (do NOT raise) so the run's stills still stand — and mark the class optional in `COMPATIBILITY_MAP` so a montage failure never aborts the pipeline. The ffmpeg invocation is behind a module-level `_run_ffmpeg(argv) -> bool` seam (mockable).

- Engine: `@register("TTLGMontage") def _h_montage(nid, inp, ctx)`: dry_run → `{"video_path": str(ctx.output_dir/f"node{nid}_montage.mp4")}`. Real: build a concat list (each image, `duration seconds_per`, last image repeated per the concat-demuxer quirk), run `_run_ffmpeg(["-y","-f","concat","-safe","0","-i",<list>, "-vf","scale=1024:-2,format=yuv420p","-r","30", out])`; captions overlaid best-effort; return `{"video_path": out}` on success else `{"video_path": None}`.
- `COMPATIBILITY_MAP["TTLGMontage"] = {"optional": True}` (so a failed montage is skipped, not fatal).
- `intent_vocab.INTENTS["TTLGMontage"]`: `verb="Stitch"`, `noun="a montage"`, `icon="🎞️"`, `outputs=("video_path",)`, `model_label=None`, `input_key="images"`, `input_kind="image"`, `output_kind="video"`.

- [ ] **Step 1: failing tests** — monkeypatch `pipeline_engine._run_ffmpeg` to a fake that records argv + `touch`es the out file and returns True: assert `_h_montage` (via a small spec run, non-dry) returns `{"video_path": "…node{n}_montage.mp4"}`, the fake saw all N image paths, and captions were passed when provided. A fake `_run_ffmpeg` returning False → `{"video_path": None}` and NO exception. Empty `images` → `{"video_path": None}`. Assert `COMPATIBILITY_MAP["TTLGMontage"]["optional"] is True`.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** the handler, `_run_ffmpeg` seam, COMPATIBILITY_MAP entry, intent.
- [ ] **Step 4: run → pass.**
- [ ] **Step 5: commit** `feat(sp-c): TTLGMontage — stitch a captioned slideshow from a list of stills (fail-soft)`.

---

### Task 4: `TTLGAddToPlaylist` list-aware

**Files:** Modify `app/pipeline_engine.py` (`_h_add_to_playlist` / `_add_artifacts_to_playlist`). Test: `tests/test_pipeline_engine.py`.

**Behavior:** the `artifacts` input may now resolve to a **list of paths** (the fan-out image list) OR a single path. Normalize to a flat list before adding (a single string → `[string]`; a list → itself; a list-of-lists → flattened). Everything else unchanged (output `{"playlist_id": …}`).

- [ ] **Step 1: failing tests** — `_add_artifacts_to_playlist` (or the handler via a mocked playlist store) with `artifacts=["/a.png","/b.png","/c.png"]` adds 3; with `artifacts="/a.png"` adds 1; with a nested `[["/a.png","/b.png"]]` adds 2 (flattened). (Mock the underlying playlist add.)
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** the normalization.
- [ ] **Step 4: run → pass.**
- [ ] **Step 5: commit** `feat(sp-c): TTLGAddToPlaylist accepts a list of artifacts (fan-out collect)`.

---

### Task 5: `app/artgen_kind.py` — classify an artgen artifact's pipeline kind

**Files:** Create `app/artgen_kind.py`, `tests/test_artgen_kind.py`.

**Interfaces produced:**
```python
def artgen_seed_kind(file_path: "str | None", generator_type: "str | None" = None) -> "str | None":
    """Map an artgen artifact to a pipeline seed kind by file extension (primary):
    .txt/.md/.py -> "text"; .png/.jpg/.jpeg/.svg/.ans/.webp -> "image";
    .gif -> "gif"; .json or unknown/missing -> None (not seedable as a pipeline)."""
```
(Pure; extension-driven so it works for any `media_type="artgen"` record. `generator_type` is accepted for future refinement but extension wins.)

- [ ] **Step 1: failing tests**: `.txt`→"text", `.py`→"text", `.svg`→"image", `.png`→"image", `.ans`→"image", `.gif`→"gif", `.json`→None, `None`→None, no-extension→None.
- [ ] **Step 2: run → fail** (`tests/test_artgen_kind.py`).
- [ ] **Step 3: implement** `app/artgen_kind.py` (zero GTK imports).
- [ ] **Step 4: run → pass.**
- [ ] **Step 5: commit** `feat(sp-c): artgen_kind — classify an artgen artifact's pipeline seed kind`.

---

### Task 6: `recipes.py` — text-seeded goals (series + singles) + `goals_for("text")`

**Files:** Modify `app/recipes.py`. Test: `tests/test_recipes.py`.

Add these **scoped, text-consuming** curated goals (first step `input_kind=="text"`), surfaced by `goals_for(seed_output_kind="text")` (the existing `goals_for` already filters scoped goals by the first step's `input_kind`, so no `goals_for` change is needed — just the new goals):

```python
Goal("illustrated-series", "An illustrated series", "📽", "video", "scoped",
     (("TTLGSplitText", {"mode": "paragraphs", "max_items": 8}),
      ("TTLGTextToImage", {"style_suffix": ", cinematic, richly detailed, atmospheric"}),
      ("TTLGMontage", {"captions": ["1", "fragments"], "seconds_per": 2.5}),
      ("TTLGAddToPlaylist", {"artifacts": ["2", "image_path"], "playlist_name": "lore series"}))),
Goal("illustrate-it", "An illustration", "🖼", "image", "scoped",
     (("TTLGTextToImage", {"style_suffix": ", cinematic, richly detailed"}),)),
Goal("lore-poster", "A poster", "🖼", "image", "scoped",
     (("TTLGTextToImage", {"style_suffix": ", bold poster art, dramatic composition"}),)),
```

Wiring notes (verified against `seed_spec`): step ids mint `1..4` in order. `TTLGSplitText`(1) gets the seed text on its `text` input (via `seed_artifact`). `TTLGTextToImage`(2) auto-wires `prompt ← [1,"fragments"]` (text→text, kind-safe). `TTLGMontage`(3) auto-wires `images ← [2,"image_path"]` (image→image) and its `captions` param wires to `[1,"fragments"]`. `TTLGAddToPlaylist`(4) has no canonical input (input_key None → no auto-wire); its `artifacts` param wires to `[2,"image_path"]`. This is a DAG (montage + playlist both consume node 2) expressed purely through params — no `seed_spec` change.

- [ ] **Step 1: failing tests** (`tests/test_recipes.py`, pure):
```python
def test_text_scoped_goals_present():
    ids = {g.id for g in recipes.goals_for(seed_output_kind="text")}
    assert {"illustrated-series", "illustrate-it", "lore-poster"} <= ids
    # image-seeded scoped goals are NOT offered for a text seed
    assert "animate-this" not in ids
def test_illustrated_series_builds_a_valid_seeded_spec():
    g = next(g for g in recipes.curated_goals() if g.id == "illustrated-series")
    spec = recipes.build_seed_spec(g, seed_artifact=("frag one\n\nfrag two", "text"))
    assert spec["1"]["class_type"] == "TTLGSplitText"
    assert spec["1"]["inputs"]["text"] == "frag one\n\nfrag two"   # text content seeded, not a path
    assert spec["2"]["inputs"]["prompt"] == ["1", "fragments"]      # auto-wired fan-out
    assert spec["3"]["inputs"]["images"] == ["2", "image_path"]     # montage consumes the batch
    assert spec["4"]["inputs"]["artifacts"] == ["2", "image_path"]  # playlist consumes the batch
```
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** the three goals (verbatim). Confirm each single-goal also materializes (`build_seed_spec` with a text seed).
- [ ] **Step 4: run → pass** (`tests/test_recipes.py`).
- [ ] **Step 5: commit** `feat(sp-c): text-seeded muse goals — illustrated series + illustration + poster`.

---

### Task 7: Generative-art gallery bridge → scoped text muse

**Files:** Modify `app/artgen_panel.py` (add `on_remix_as_pipeline` seam + the "🧩 Remix as pipeline…" affordance on the gallery + detail), `app/main_window.py` (wire `_artgen_panel.on_remix_as_pipeline`; make `_remix_as_pipeline` handle a `MediaRecord`). Test: `tests/test_pipeline_studio.py` or `tests/test_main_window*.py` (xvfb).

- `ArtgenPanel`: add `self.on_remix_as_pipeline: Optional[Callable[[MediaRecord], None]] = None` next to `self.on_remix` (line ~130); wire the inner gallery/detail (`self._gallery`/`self._detail`) the same way `on_remix` is wired (`self._gallery.on_remix_as_pipeline = self._on_remix_as_pipeline_record`), and add a handler `_on_remix_as_pipeline_record(rec)` that calls `self.on_remix_as_pipeline(rec)` when set. Add a "🧩 Remix as pipeline…" button wherever the existing "🔀"/remix affordance is rendered on the artgen cards/detail (tooltip "Turn this into a multi-step pipeline"), calling the new handler. Do NOT disturb the existing `on_remix`/`RemixPopover` path.
- `main_window`: after `self._artgen_panel.on_remix = self._on_remix_card` (line ~7931) add `self._artgen_panel.on_remix_as_pipeline = self._remix_as_pipeline`.
- `MainWindow._remix_as_pipeline(record)` currently handles a `GenerationRecord` (uses `record.media_type`/`media_file_path`/`media_exists`). Extend it to also accept a `MediaRecord` (artgen): detect an artgen record (has `generator_type` / `media_type == "artgen"`) and resolve the seed via `artgen_kind.artgen_seed_kind(record.file_path, record.generator_type)`:
  - kind `"text"` → read `record.file_path` (utf-8, best-effort); `seed_artifact = (content, "text", record.thumbnail_path)` — the muse/`show_muse` passes `(content, "text")` to `build_seed_spec`.
  - kind `"image"`/`"gif"` → `seed_artifact = (record.file_path, kind, record.thumbnail_path)`.
  - unresolved kind / missing file / empty content → open the blank muse (`show_muse()`), never crash.
  Keep the existing `GenerationRecord` branch intact (dispatch on record type).
  NOTE: confirm `PipelineStudio.show_muse` / `MuseView.set_context` already forward `seed_artifact[0]` (value) as `build_seed_spec(..., seed_artifact=(value, kind))` — the muse was built to pass `(path, kind)`; a text value flows through the same seam unchanged. If `set_context` hardcodes treating element 0 as a path anywhere (e.g. only for the thumbnail), ensure the thumbnail uses `seed_artifact[2]` (thumb) and the seed uses `seed_artifact[0]` (value) — the thumbnail for a text seed may be None (show the "text" heading without an image thumb).

- [ ] **Step 1: xvfb tests** — inject a fake `on_remix_as_pipeline`; assert the new artgen gallery/detail button calls it with the `MediaRecord`. For `MainWindow._remix_as_pipeline` with a fake `MediaRecord` (`.txt`, real content on disk): assert it calls `show_muse` with `("<content>", "text", …)` (mock `_pipeline_studio`); with a `.json`/unresolvable artgen record → `show_muse()` blank; with a missing file → blank. Keep the existing `GenerationRecord` tests green.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** the `ArtgenPanel` seam + affordance, the main_window wiring, and the `MediaRecord` branch in `_remix_as_pipeline` (+ any `set_context` value/thumb split needed).
- [ ] **Step 4: run → pass**; full suite (xvfb) — no NEW failures.
- [ ] **Step 5: commit** `feat(sp-c): Remix-as-pipeline from the generative-art gallery (text lore → scoped muse)`.

---

### Task 8: Version + changelog

- [ ] Bump `VERSION` `0.22.0` → `0.23.0` (minor — new user-visible feature).
- [ ] Prepend a `debian/changelog` stanza (match format):
  > Pipeline Studio — lore to a series. Generated text ("lore") in the Generative Art gallery gains "🧩 Remix as pipeline": pick "An illustrated series" and a fan-out pipeline generates one image per lore fragment (a single FLUX batch — no per-item board switching), collects them into a playlist, and stitches a captioned montage movie. New engine nodes TTLGSplitText / TTLGMontage, a list-aware TTLGTextToImage / TTLGAddToPlaylist (fan-out via list-valued dataflow), an artgen kind classifier, and text-seeded muse goals (illustrated series / illustration / poster).
- [ ] **Commit** `chore: release v0.23.0 — lore→series fan-out pipelines`.

---

## Self-Review

**Spec coverage:** text bridge → Tasks 5,7 (+ seed handled by existing `seed_spec`); fan-out engine → Tasks 1–4; text-seeded recipes → Task 6; version → Task 8. Every design section maps to a task. ✓
**Placeholder scan:** real code for the pure/tricky pieces (split_text, list-aware handler, montage seam, artgen_kind, recipe entries) + concrete test cases; fan-out DAG verified expressible via params against `seed_spec`'s actual behavior; board-batching + fail-soft + cap explicit. No TBDs. ✓
**Type consistency:** `split_text`, `artgen_seed_kind`, the `fragments`/`image_path`(list)/`images`/`video_path` outputs, the `(value, kind[, thumb])` seed tuple, and the intent I/O kinds are consistent across tasks; kind-safety reuses `intent_vocab`/`seed_spec` unchanged; new intents declare I/O kinds that make the series recipe wire cleanly (text→text→image→video). ✓
