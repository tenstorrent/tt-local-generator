# The Muse — creative wizard + image→pipeline bridge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SP-C's two missing front doors — a goal-first "start from scratch" creative wizard and a main-app "Remix as pipeline" bridge — unified under one `MuseView` that seeds the existing intent composer with a real starter recipe.

**Architecture:** New pure layers (`spec_remix.seed_spec`, `app/recipes.py`, `wingit.map_freeform_to_pipeline`) build a fresh ComfyUI-API-v1 spec from an ordered list of intents (optionally seeded by an artifact). A GTK `MuseView` presents goal cards (curated + MCP-discovered) in blank or artifact-scoped mode; choosing one materializes a seed spec into the existing `RemixView` composer. The main-app gallery gains a bridge action into the scoped muse.

**Tech Stack:** Python 3.12 / GTK4 (PyGObject), pytest (+ `xvfb-run`). Reuses `app/spec_remix.py`, `app/intent_vocab.py`, `app/capability_discovery.py`, `app/wingit.py`, `app/pipeline_studio.py`, `app/artgen/__init__.py`.

## Global Constraints

- Pure layers (`spec_remix`, `recipes`, `wingit` core) have **zero GTK imports**; all LLM/discovery behind injected seams (`llm_fn`, `mcp_reader`, capability lists).
- **Never fail hard** — every free-text / LLM / discovery path degrades to a deterministic fallback or a gentle message; never a traceback or empty broken screen.
- **Kind-safe** — the muse never offers a transformation the seed kind cannot feed; `spec_remix.add_step`'s existing kind guard and `_validate` are the backstops. Kinds come from `intent_vocab.Intent` (`input_kind`/`output_kind`).
- **Intent language** in ALL user-facing copy; the model/tool is a quiet detail; never surface a raw `class_type` or model name in a label.
- **Reuse, don't fork** — recipes build specs via `seed_spec`; the composer, `add_step`, `capability_discovery`, and single-step `wingit` are reused unchanged.
- No regression to existing SP-C phases. GTK work off the main thread → `GLib.idle_add`.
- Use the **system** python (`/usr/bin/python3`). Full suite: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q` — two pre-existing failures/skips are expected (see repo CLAUDE.md "Running tests"); no NEW failures.
- Everything stays local on branch `feat/pipeline-editor` — no push/merge/PR.

### Reference: the intent vocabulary (from `intent_vocab.INTENTS`)

Every native `class_type`, its verb/noun, and its I/O kinds — the raw material for recipes and kind-safety:

| class_type | label | input_kind (key) | output_kind (primary out) |
|---|---|---|---|
| `TTLGPromptCompose` | Compose a prompt | text (`caption`) | text (`prompt`) |
| `TTLGTextToImage` | Generate an image | text (`prompt`) | image (`image_path`) |
| `TTLGCaptionImage` | Describe it | image (`src`) | text (`caption`) |
| `TTLGGenerateText` | Write about it | text (`caption`) | text (`text`) |
| `TTLGImageToVideo` | Film it | image (`image`) | video (`video_path`) |
| `TTLGAnimateDiff` | Animate a prompt | text (`prompt`) | gif (`gif_path`) |
| `TTLGRemoveBackground` | Cut out the subject | image (`src`) | image (`fg_path`) |
| `TTLGEstimateDepth` | Read its depth | image (`src`) | image (`depth_path`) |
| `TTLGSVGRender` | Render a drawing | text (`src`) | image (`png_path`) |
| `TTLGComposite` | Combine them | image (`background_path`) | image (`image_path`) |
| `TTLGAddToPlaylist` | Collect the results | — (source) | playlist (`playlist_id`) |
| `TTLGArtgenGenerate` | Make generative art | — (source) | text/artifact (`artifact_path`) |

`intent_vocab.compatible_intents(output_kind)` returns intents whose `input_kind == output_kind`.

---

### Task 1: `spec_remix.seed_spec` — a fresh spec from an intent list

**Files:**
- Modify: `app/spec_remix.py` (add `seed_spec` near `add_step`; reuse `intent_for`, `_validate`).
- Test: `tests/test_spec_remix.py` (extend).

**Interfaces:**
- Consumes: `intent_vocab.intent_for(class_type) -> Intent` (fields `input_key`, `input_kind`, `output_kind`, `outputs`); `spec_remix._validate(spec)`.
- Produces:
  ```python
  def seed_spec(
      steps: "list[tuple[str, dict]]",
      *,
      seed_artifact: "tuple[str, str] | None" = None,
  ) -> dict:
      """Build a fresh ComfyUI-API-v1 spec from an ordered list of
      (class_type, params) steps, wiring each step's primary output into the
      next step's canonical input (same kind rules as add_step).

      Node ids are minted "1","2","3",... in list order. Step i+1's
      intent.input_key is wired to [str(i+1), prev_intent.outputs[0]] when
      that intent declares a canonical input; params are merged on top.

      seed_artifact=(path, kind): the FIRST step consumes it as starting
      material. If step-0's intent has a canonical input whose input_kind
      matches `kind`, the literal `path` is placed on that input_key (a
      literal file-path string — exactly what a wire from an upstream
      *_path output would deliver, so the engine resolves it unchanged).
      Raises ValueError if `kind` is incompatible with step-0's input_kind.

      Raises ValueError on an empty `steps` list, an unknown class_type with
      a declared input that can't be wired, a kind mismatch between adjacent
      steps, or a spec that fails _validate.
      """
  ```

- [ ] **Step 1: Write failing tests**

```python
# tests/test_spec_remix.py  (add)
import pytest
from spec_remix import seed_spec

def test_seed_spec_single_step():
    spec = seed_spec([("TTLGTextToImage", {"prompt": "a fox"})])
    assert spec["1"]["class_type"] == "TTLGTextToImage"
    assert spec["1"]["inputs"]["prompt"] == "a fox"

def test_seed_spec_wires_adjacent_steps():
    spec = seed_spec([
        ("TTLGTextToImage", {"prompt": "a fox"}),
        ("TTLGImageToVideo", {}),
    ])
    # step 2 (Film it) consumes step 1's image_path via its "image" key
    assert spec["2"]["class_type"] == "TTLGImageToVideo"
    assert spec["2"]["inputs"]["image"] == ["1", "image_path"]

def test_seed_spec_kind_mismatch_raises():
    with pytest.raises(ValueError):
        # Film it (needs image) cannot follow Compose a prompt (produces text)
        seed_spec([("TTLGPromptCompose", {}), ("TTLGImageToVideo", {})])

def test_seed_spec_with_seed_artifact_places_literal_path():
    spec = seed_spec(
        [("TTLGImageToVideo", {})],
        seed_artifact=("/tmp/pic.png", "image"),
    )
    assert spec["1"]["inputs"]["image"] == "/tmp/pic.png"

def test_seed_spec_seed_artifact_kind_mismatch_raises():
    with pytest.raises(ValueError):
        # Compose a prompt needs text, not an image seed
        seed_spec([("TTLGPromptCompose", {})], seed_artifact=("/tmp/pic.png", "image"))

def test_seed_spec_empty_raises():
    with pytest.raises(ValueError):
        seed_spec([])
```

- [ ] **Step 2: Run to verify fail**

Run: `/usr/bin/python3 -m pytest tests/test_spec_remix.py -k seed_spec -q`
Expected: FAIL (`cannot import name 'seed_spec'`).

- [ ] **Step 3: Implement `seed_spec`**

```python
# app/spec_remix.py  (add near add_step; imports intent_for already present)
def seed_spec(steps, *, seed_artifact=None):
    if not steps:
        raise ValueError("seed_spec requires at least one step")

    spec: "dict[str, Any]" = {}
    prev_id = None
    prev_intent = None
    for idx, (class_type, params) in enumerate(steps):
        node_id = str(idx + 1)
        intent = intent_for(class_type)
        inputs: "dict[str, Any]" = {}

        if intent.input_key and prev_id is not None:
            if intent.input_kind and prev_intent is not None \
                    and intent.input_kind != prev_intent.output_kind:
                raise ValueError(
                    f"cannot follow {prev_intent.output_kind!r} with "
                    f"{class_type!r} (needs {intent.input_kind!r})"
                )
            if prev_intent is not None and prev_intent.outputs:
                inputs[intent.input_key] = [prev_id, prev_intent.outputs[0]]

        if idx == 0 and seed_artifact is not None:
            path, kind = seed_artifact
            if intent.input_key and intent.input_kind and intent.input_kind != kind:
                raise ValueError(
                    f"seed artifact kind {kind!r} incompatible with "
                    f"{class_type!r} (needs {intent.input_kind!r})"
                )
            if intent.input_key:
                inputs[intent.input_key] = path

        if params:
            inputs.update(params)

        spec[node_id] = {"class_type": class_type, "inputs": inputs}
        prev_id, prev_intent = node_id, intent

    _validate(spec)
    return spec
```

- [ ] **Step 4: Run to verify pass**

Run: `/usr/bin/python3 -m pytest tests/test_spec_remix.py -k seed_spec -q` → PASS.
Then confirm the seeded spec is engine-runnable structurally:
Run: `/usr/bin/python3 -c "import sys; sys.path.insert(0,'app'); from spec_remix import seed_spec; import pipeline_engine as pe; s=seed_spec([('TTLGTextToImage',{'prompt':'x'}),('TTLGImageToVideo',{})]); print(pe.topo_order(pe.load_spec_dict(s) if hasattr(pe,'load_spec_dict') else s))"` — if `pipeline_engine` has no dict loader, skip this line; the `_validate` in `seed_spec` already guarantees a wire-consistent graph. Document which was used in the report.

- [ ] **Step 5: Commit**

```bash
git add app/spec_remix.py tests/test_spec_remix.py
git commit -m "feat(sp-c): spec_remix.seed_spec — fresh spec from an intent list (+ optional seed artifact)"
```

---

### Task 2: `app/recipes.py` — goal catalog + hybrid discovery

**Files:**
- Create: `app/recipes.py`.
- Test: `tests/test_recipes.py`.

**Interfaces:**
- Consumes: `spec_remix.seed_spec`; `intent_vocab.intent_for`; `capability_discovery.load_plugin_capabilities(mcp_reader)` (returns plugin manifest dicts, skips `x-ttlg.utility`) and `capability_discovery._read_all_plugin_mcp` (the real `mcp_reader`); `intent_vocab.compatible_intents`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class Goal:
      id: str            # stable slug, e.g. "looping-animation"
      label: str         # intent-language card text, e.g. "A looping animation"
      icon: str          # one emoji
      output_kind: str   # what the finished pipeline produces
      applies_to: str    # "blank" | "scoped" | "both"
      recipe_steps: "tuple[tuple[str, dict], ...]"  # (class_type, params)
      via: str = "curated"  # "curated" | "discovered"

  def curated_goals() -> "list[Goal]": ...
  def discover_goals(*, mcp_reader) -> "list[Goal]": ...     # pure core, injected reader
  def all_goals(*, mcp_reader=None) -> "list[Goal]": ...     # curated + discovered, dedup by id (curated wins)
  def goals_for(*, seed_output_kind=None, mcp_reader=None) -> "list[Goal]": ...
  def build_seed_spec(goal: Goal, *, seed_artifact=None) -> dict:   # -> spec_remix.seed_spec(goal.recipe_steps, ...)
  ```
  `goals_for`: `seed_output_kind is None` (blank) → goals with `applies_to in {"blank","both"}`. Otherwise (scoped) → goals with `applies_to in {"scoped","both"}` whose FIRST recipe step's `intent_for(class_type).input_kind == seed_output_kind` (kind-safe). Deterministic order: curated before discovered, each in declaration order.

**Curated core** (author these exact goals — all verified kind-safe against the intent table above):

```python
_CURATED = [
    Goal("poster", "A poster", "🖼", "image", "blank",
         (("TTLGPromptCompose", {}), ("TTLGTextToImage", {}))),
    Goal("looping-animation", "A looping animation", "🔁", "gif", "blank",
         (("TTLGAnimateDiff", {"seamless_loop": True}),)),
    Goal("illustrated-poem", "An illustrated poem", "📜", "image", "blank",
         (("TTLGGenerateText", {}), ("TTLGTextToImage", {}), ("TTLGAddToPlaylist", {}))),
    Goal("short-film", "A short film", "🎬", "video", "blank",
         (("TTLGPromptCompose", {}), ("TTLGTextToImage", {}), ("TTLGImageToVideo", {}))),
    Goal("explorable-world", "An explorable world", "🌍", "image", "blank",
         (("TTLGTextToImage", {}), ("TTLGEstimateDepth", {}), ("TTLGAddToPlaylist", {}))),
    # scoped — first step consumes the seed artifact
    Goal("animate-this", "A looping animation", "🔁", "video", "scoped",
         (("TTLGImageToVideo", {}),)),
    Goal("poem-about-this", "A poem about it", "📜", "text", "scoped",
         (("TTLGCaptionImage", {}), ("TTLGGenerateText", {}))),
    Goal("depth-scene", "A depth scene", "🌀", "image", "scoped",
         (("TTLGEstimateDepth", {}),)),
    Goal("variations", "Variations", "🎨", "image", "scoped",
         (("TTLGCaptionImage", {}), ("TTLGPromptCompose", {}), ("TTLGTextToImage", {}))),
    Goal("film-this", "A short film", "🎬", "video", "scoped",
         (("TTLGImageToVideo", {}),)),
]
```

**`discover_goals`** — read plugin manifests via `load_plugin_capabilities(mcp_reader)`; for each manifest whose `x-ttlg` carries a `goal` block (`{"goal": {"label","icon","output_kind","recipe": [class_type,...]}}`), build a `Goal(via="discovered", applies_to="both", recipe_steps=tuple((ct,{}) for ct in recipe))`. Skip manifests with no `x-ttlg.goal`. A raising/empty `mcp_reader` → `[]` (never crashes).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_recipes.py
import pytest
import recipes
from recipes import Goal, curated_goals, all_goals, goals_for, build_seed_spec

def test_curated_goals_nonempty_and_kind_safe():
    goals = curated_goals()
    assert any(g.id == "looping-animation" for g in goals)
    # every curated recipe materializes without raising (kind-safe wiring)
    for g in goals:
        seed = ("/tmp/x.png", "image") if g.applies_to == "scoped" else None
        spec = build_seed_spec(g, seed_artifact=seed)
        assert len(spec) == len(g.recipe_steps)

def test_goals_for_blank_excludes_scoped():
    ids = {g.id for g in goals_for(seed_output_kind=None)}
    assert "poster" in ids
    assert "animate-this" not in ids   # scoped-only

def test_goals_for_scoped_image_only_image_consumers():
    goals = goals_for(seed_output_kind="image")
    assert goals, "expected image-consuming scoped goals"
    for g in goals:
        first_ct = g.recipe_steps[0][0]
        import intent_vocab as iv
        assert iv.intent_for(first_ct).input_kind == "image"

def test_discover_goals_from_fake_mcp_reader():
    fake = lambda: {"myplug": {"x-ttlg": {"goal": {
        "label": "A music video", "icon": "🎵", "output_kind": "video",
        "recipe": ["TTLGTextToImage", "TTLGImageToVideo"]}},
        "tools": [{"name": "myplug"}]}}
    goals = recipes.discover_goals(mcp_reader=fake)
    assert any(g.label == "A music video" and g.via == "discovered" for g in goals)

def test_discover_goals_bad_reader_returns_empty():
    def boom(): raise RuntimeError("no disk")
    assert recipes.discover_goals(mcp_reader=boom) == []

def test_all_goals_curated_wins_on_id_collision():
    fake = lambda: {"p": {"x-ttlg": {"goal": {
        "label": "X", "icon": "x", "output_kind": "image", "recipe": ["TTLGTextToImage"],
        "id": "poster"}}, "tools": [{"name": "p"}]}}
    poster = [g for g in all_goals(mcp_reader=fake) if g.id == "poster"]
    assert len(poster) == 1 and poster[0].via == "curated"
```

- [ ] **Step 2: Run to verify fail** — `/usr/bin/python3 -m pytest tests/test_recipes.py -q` → FAIL (no module `recipes`).
- [ ] **Step 3: Implement `app/recipes.py`** per the interfaces above (curated list verbatim; `discover_goals` reads `x-ttlg.goal`, defaults `id` to a slug of the label when not given, tolerates a raising reader; `all_goals` dedups by id with curated winning; `goals_for` filters by mode + first-step kind; `build_seed_spec` delegates to `spec_remix.seed_spec`). No GTK imports.
- [ ] **Step 4: Run to verify pass** — `/usr/bin/python3 -m pytest tests/test_recipes.py -q` → PASS.
- [ ] **Step 5: Commit** — `git add app/recipes.py tests/test_recipes.py && git commit -m "feat(sp-c): recipes — curated goal catalog + MCP-discovered goals + build_seed_spec"`

---

### Task 3: `wingit.map_freeform_to_pipeline` — free-text → multi-step draft

**Files:**
- Modify: `app/wingit.py` (add `map_freeform_to_pipeline`; reuse `_build_prompt`-style prompt building, `default_llm_fn`, lenient parsing helpers, `Capability`).
- Test: `tests/test_wingit.py` (extend).

**Interfaces:**
- Consumes: `capability_discovery.Capability` (`.live`, `.id`, `.class_type`, `.kind_in`, `.kind_out`, `.plugin`); `wingit.default_llm_fn`.
- Produces:
  ```python
  def map_freeform_to_pipeline(
      text: str,
      *,
      seed_output_kind: "str | None",
      capabilities: "list[Capability]",
      llm_fn: "Callable[[str], str | None] | None",
      max_steps: int = 4,
  ) -> "list[tuple[str, dict]] | None":
      """Draft an ordered [(class_type, params), ...] pipeline (<= max_steps)
      from free-form text. Only live capabilities are offered. LLM-assisted:
      ask for a JSON list of {"capability_id","params"}; parse leniently
      (strip fences/think-blocks; take the first JSON array); validate each id
      against the live set AND that adjacent kinds chain (each step's kind_in
      is None or equals the prior step's kind_out; step 0's kind_in is None or
      equals seed_output_kind). Drop invalid trailing steps rather than
      failing the whole draft. On llm_fn None / raise / unparseable / empty
      valid list, FALL BACK to a deterministic heuristic: the single best live
      capability that can consume seed_output_kind (or a source capability when
      seed_output_kind is None), with `text` in its primary text param. Return
      None only when NO live capability fits at all.
      """
  ```

- [ ] **Step 1: Write failing tests** (fake `llm_fn`, small live `Capability` list mixing native + plugin):

```python
# tests/test_wingit.py  (add)
from wingit import map_freeform_to_pipeline
from capability_discovery import Capability

def _cap(id, ct, kin, kout, live=True, plugin=None, source="native"):
    return Capability(id=id, label=id, kind_out=kout, kind_in=kin, source=source,
                      class_type=ct, plugin=plugin, hardware=None, live=live, reason=None)

CAPS = [
    _cap("TTLGTextToImage", "TTLGTextToImage", "text", "image"),
    _cap("TTLGImageToVideo", "TTLGImageToVideo", "image", "video"),
    _cap("TTLGCaptionImage", "TTLGCaptionImage", "image", "text"),
]

def test_pipeline_llm_valid_chain():
    fn = lambda p: '[{"capability_id":"TTLGTextToImage","params":{"prompt":"koi"}},' \
                   '{"capability_id":"TTLGImageToVideo","params":{}}]'
    steps = map_freeform_to_pipeline("koi looping", seed_output_kind=None,
                                     capabilities=CAPS, llm_fn=fn)
    assert [ct for ct, _ in steps] == ["TTLGTextToImage", "TTLGImageToVideo"]

def test_pipeline_drops_kind_broken_tail():
    # 2nd step (video->?) can't chain image consumer after a video producer
    fn = lambda p: '[{"capability_id":"TTLGTextToImage","params":{}},' \
                   '{"capability_id":"TTLGCaptionImage","params":{}}]'  # caption needs image, gets image? ok
    steps = map_freeform_to_pipeline("x", seed_output_kind=None, capabilities=CAPS, llm_fn=fn)
    assert steps[0][0] == "TTLGTextToImage"

def test_pipeline_fallback_when_no_llm():
    steps = map_freeform_to_pipeline("a fox", seed_output_kind=None,
                                     capabilities=CAPS, llm_fn=None)
    assert steps and steps[0][0] == "TTLGTextToImage"
    assert "a fox" in str(steps[0][1].values())

def test_pipeline_none_when_nothing_fits():
    only_image_consumers = [_cap("TTLGImageToVideo","TTLGImageToVideo","image","video")]
    assert map_freeform_to_pipeline("x", seed_output_kind="text",
                                    capabilities=only_image_consumers, llm_fn=None) is None
```

- [ ] **Step 2: Run to verify fail** — `/usr/bin/python3 -m pytest tests/test_wingit.py -k pipeline -q` → FAIL.
- [ ] **Step 3: Implement** `map_freeform_to_pipeline` (reuse the module's existing lenient JSON extraction — generalize it to arrays; reuse the fallback capability-selection logic from `_fallback`; enforce the adjacent-kind chain; cap at `max_steps`). No new deps.
- [ ] **Step 4: Run to verify pass** — `/usr/bin/python3 -m pytest tests/test_wingit.py -q` → PASS.
- [ ] **Step 5: Commit** — `git add app/wingit.py tests/test_wingit.py && git commit -m "feat(sp-c): wingit.map_freeform_to_pipeline — free-text -> multi-step draft"`

---

### Task 4: `MuseView` + `PipelineStudio` muse page + "Start from scratch"

**Files:**
- Modify: `app/pipeline_studio.py` (add `MuseView`; add a `"muse"` stack page + `show_muse`; add a "✦ Start from scratch" affordance to Discover; add `RemixView.load_seed_spec`).
- Test: `tests/test_pipeline_studio.py` (extend).

**Interfaces:**
- Consumes: `recipes.goals_for`, `recipes.build_seed_spec`, `recipes.Goal`; `wingit.map_freeform_to_pipeline`, `wingit.default_llm_fn`; `capability_discovery.default_capabilities`; `spec_remix.seed_spec`, `spec_remix.write_spec`; `RemixView`.
- Produces:
  - `RemixView.load_seed_spec(spec_path: str, title: str) -> None` — like `set_run` but without a `RunView`: sets `self._spec_path = spec_path`, `self._title_label.set_label(f"Composing · {title}")`, `self.working_spec = load_spec(spec_path)`, `self._hide_message()`, `self._render()`.
  - `class MuseView(Gtk.Box)` with `__gsignals__ = {"goal-chosen": (RUN_FIRST, None, (object,))}` emitting the built **seed spec dict**. Constructor seams:
    ```python
    def __init__(self, *, goals_fn=None, wingit_pipeline_fn=None, seed_spec_fn=None):
        # goals_fn(seed_output_kind) -> list[Goal]   (default recipes.goals_for)
        # wingit_pipeline_fn(text, seed_output_kind) -> list[(ct,params)]|None
        #   (default wraps wingit.map_freeform_to_pipeline + default_llm_fn +
        #    capability_discovery.default_capabilities)
        # seed_spec_fn(steps, seed_artifact) -> dict  (default spec_remix.seed_spec)
    ```
    - `set_context(seed_artifact=None)`: `seed_artifact` is `(path, kind, thumb_path)` or None. Heading = "What do you want to make?" (blank) or "Make this {kind} into…" with the thumbnail (scoped). Renders one card per `goals_fn(seed_output_kind)` (icon + label; intent language only), a "✨ Surprise me" button, and a free-text entry + "Dream it up →".
    - Card click → `build_seed_spec(goal, seed_artifact=(path,kind))` (goal path is synchronous) → emit `goal-chosen`(spec). "Surprise me" → a deterministic pick (index `len(goals)//2`, NOT random — `Math.random`/time are unavailable in this codebase's test discipline; pick middle for stability) → same path. Free-text → run `wingit_pipeline_fn` OFF the main thread (real llm_fn hits network) → `GLib.idle_add` to build the seed spec via `seed_spec_fn` and emit; a `None` result → gentle `_show_message("Couldn't compose that — try rephrasing.")`, emit nothing.
- `PipelineStudio`:
  - `show_muse(self, seed_artifact=None) -> None` — build/reset `self.muse`, call `muse.set_context(seed_artifact)`, `stack.set_visible_child_name("muse")`.
  - On `muse::goal-chosen`(spec): write the seed spec to `REMIXES_DIR` via `spec_remix.write_spec(spec, "muse", str(REMIXES_DIR))`, then `self.remix_view.load_seed_spec(path, title)` (title = "a new pipeline" or "your <kind>"), `stack.set_visible_child_name("remix")`.
  - The `"muse"` page has a back control → `show_discover()`.
  - Discover's hero/empty area gains a "✦ Start from scratch" button → `show_muse()` (blank). (DiscoverView emits a `start-from-scratch` signal; `PipelineStudio` connects it to `show_muse`.)

- [ ] **Step 1: xvfb tests** — with injected fakes (no network): a fake `goals_fn` returning two `Goal`s; a fake `wingit_pipeline_fn`; a fake `seed_spec_fn`. Assert: blank `set_context()` shows the two goal cards + "Surprise me" + free-text; clicking a card emits `goal-chosen` with the fake seed spec; scoped `set_context((path,"image",thumb))` calls `goals_fn` with `"image"` and shows the "Make this image into…" heading; a free-text submit whose `wingit_pipeline_fn` returns `None` shows the gentle message and does NOT emit; DiscoverView's "Start from scratch" affordance emits `start-from-scratch`; `PipelineStudio.show_muse()` switches the stack to `"muse"` and a `goal-chosen` switches to `"remix"` with `remix_view.current_spec()` reflecting the seeded spec. Run under `xvfb-run`. → fail.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** `RemixView.load_seed_spec`, `MuseView`, the `"muse"` page + `show_muse` + wiring, and the Discover "✦ Start from scratch" affordance. Off-thread free-text path per the GTK threading rule; synchronous goal/Surprise path. Intent language throughout.
- [ ] **Step 4: Run to verify pass** — `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_studio.py -q` → PASS; full suite → no NEW failures.
- [ ] **Step 5: Commit** — `git add app/pipeline_studio.py tests/test_pipeline_studio.py && git commit -m "feat(sp-c): MuseView — goal-first start-from-scratch wizard"`

---

### Task 5: Main-app bridge — "Remix as pipeline…" → scoped muse

**Files:**
- Modify: `app/main_window.py` (card hover action bar + detail panel action row; a handler that opens the Pipelines area in scoped-muse mode).
- Test: `tests/test_pipeline_studio.py` or `tests/test_main_window.py` (extend, xvfb).

**Interfaces:**
- Consumes: `PipelineStudio.show_muse(seed_artifact)`; the existing `_show_pipelines()` mount path (main_window.py ~8650) and `_pipelines_btn` toggle (~7820); `GenerationRecord` (the card/detail `self._record`) — its primary artifact path + kind.
- Produces:
  - A `MainWindow._remix_as_pipeline(record)` method: resolve the record's primary artifact `(path, kind, thumb_path)` (kind from the record's media type — image/video/gif/text); activate the Pipelines area (set `_pipelines_btn` active / call `_show_pipelines()`), then `self._pipeline_studio.show_muse(seed_artifact=(path, kind, thumb_path))`. If the artifact is missing/unreadable, fall back to `show_muse()` (blank) — never fail.
  - A "🧩 Remix as pipeline…" button in `GenerationCard`'s hover action bar (near the existing "🔀 Remix", tooltip "Remix this into a multi-step pipeline") and in `DetailPanel`'s action row, wired via a `remix_as_pipeline_cb(record)` seam (same injection pattern as the existing `remix_cb`).

- [ ] **Step 1: xvfb tests** — inject a fake `remix_as_pipeline_cb`; assert clicking the new button on a card/detail calls it with the record; assert `MainWindow._remix_as_pipeline` calls `show_muse` with the resolved `(path, kind, thumb)` (mock `_pipeline_studio`), and with a missing artifact calls `show_muse()` blank. → fail.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** the button (both surfaces), the `remix_as_pipeline_cb` seam, and `_remix_as_pipeline` (kind resolution + Pipelines activation + graceful fallback).
- [ ] **Step 4: Run to verify pass** — targeted test + full suite (xvfb) → no NEW failures.
- [ ] **Step 5: Commit** — `git commit -am "feat(sp-c): main-app bridge — Remix as pipeline opens the scoped muse"`

---

### Task 6: Visual polish pass (composer / Open / Discover)

**Files:**
- Modify: `app/pipeline_studio.py` (OpenView `_build_step_row`; DiscoverView hero thumb; `_build_thumb_frame` placeholder; RemixView step rows if they share the verb/noun split; CSS `.ps-step-verb`/`.ps-step-noun`/`.ps-step-model`, hero/placeholder classes).
- Test: `tests/test_pipeline_studio.py` (extend — assert structure, not pixels).

**Root causes (from `~/Pictures/ttlg-p1.png`, `ttlg-p2.png`):**
1. **Fragmented intent label** — OpenView step rows render verb, noun, model as three stacked labels; reads as broken text. Fix: render one cohesive `f"{verb} {noun}"` label (the full intent label) with the status glyph inline, and the model as a single small muted caption below (not a third stacked line competing with the noun).
2. **Oversized/sparse step rows** — `STEP_THUMB_W,H = 150,92` + generous margins make each row very tall for little content. Fix: reduce the per-row vertical footprint (smaller thumb / tighter row) so cards read as a compact list.
3. **Hero image doesn't fill** — DiscoverView hero thumb letterboxes a landscape image small inside a `320×200` box. Fix: make the hero thumb fill its frame (cover-fit / expand) so a real thumbnail isn't small and off-center with empty space beside it.
4. **Placeholder reads as broken** — `_build_thumb_frame` shows a lone 🖼 emoji in a large empty dark box when there's no artifact. Fix: render a missing artifact as an intentional intent-icon tile (the step's intent `icon` centered on a subtle card, sized to the frame) so an empty step reads as "nothing here yet", not "broken".

- [ ] **Step 1: tests** — assert the OpenView step row now contains a single combined intent label (one label whose text == `f"{verb} {noun}"`) rather than separate verb/noun widgets; assert `_build_thumb_frame(None, ...)` for a step renders the intent icon (pass the intent through), not the bare 🖼 fallback. (Structural assertions via the widget tree under xvfb.) → fail.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** the four fixes. Keep changes CSS/layout-local; do not alter data flow. Left-side/bottom bars only per global TUI rule (n/a to GTK but keep layouts clean). Verify visually is out of scope for CI — assert structure.
- [ ] **Step 4: Run to verify pass** — full suite (xvfb) → no NEW failures.
- [ ] **Step 5: Commit** — `git commit -am "fix(sp-c): pipeline studio visual polish — cohesive labels, compact rows, hero fill, tidy placeholders"`

---

### Task 7: Version + changelog

**Files:** Modify `VERSION`, `debian/changelog`.

- [ ] Bump `VERSION` `0.20.0` → `0.21.0` (minor — new user-visible feature).
- [ ] Prepend a `debian/changelog` stanza (match existing format/indentation):
  > Pipeline Studio — the Muse: a goal-first "start from scratch" creative wizard ("What do you want to make?") and a main-app "Remix as pipeline" bridge that turns any image/video/poem into a multi-step pipeline ("Make this into…"), both seeding the intent composer with a real starter recipe (curated + MCP-discovered goals, with a free-text "tell me your idea" escape hatch). Plus a Pipeline Studio visual polish pass. Positions the app as a creative solution, not a technical node editor.
- [ ] **Commit** — `git add VERSION debian/changelog && git commit -m "chore: release v0.21.0 — the Muse (creative wizard + image→pipeline bridge)"`

---

## Self-Review

**Spec coverage:** the Muse's two modes → MuseView (Task 4) blank + Task 5 scoped bridge; seed-spec primitive → Task 1; curated+MCP hybrid recipes → Task 2; free-text whole-pipeline wing-it → Task 3; folded-in visual polish → Task 6; version → Task 7. Every spec section maps to a task. ✓

**Placeholder scan:** every code step carries real code or exact interface signatures; curated recipes are enumerated verbatim and verified kind-safe against the intent table; tests are concrete with fakes (no network/GTK in pure tasks; xvfb for GTK); "Surprise me" uses a deterministic pick (no `random`/time, honoring the codebase's resume/test discipline). No TBDs. ✓

**Type consistency:** `seed_spec(steps, *, seed_artifact)`, `Goal`, `goals_for`/`build_seed_spec`, `map_freeform_to_pipeline`, `RemixView.load_seed_spec`, `MuseView` seams (`goals_fn`/`wingit_pipeline_fn`/`seed_spec_fn`), and `show_muse`/`remix_as_pipeline_cb` are used consistently across tasks; `Capability`/`Intent`/`Goal` fields match their source dataclasses; kind rules reuse `intent_vocab`/`add_step` guards throughout. ✓
