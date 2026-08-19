# Hide pipeline mode behind a flag, keep seeded Remix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide the not-ready pipeline surfaces (browse-runs, blank-canvas compose, DAG editor) behind a single default-off feature flag, while keeping seeded 🔀 Remix fully working — Remix → goal chooser → run (Stage) → Library — with the whole hide reversible by flipping the flag.

**Architecture:** One flag (`app_settings.PIPELINE_MODE_ENABLED`, env `TTLG_PIPELINE_MODE`) gates three doors and the seeded-remix routing. Flag ON = today's UI byte-for-byte. No pipeline code is deleted. Engine, run spec, `collect()`, and the Stage internals are untouched — only *reachability* and the seeded-remix *routing/exit* change.

**Tech Stack:** Python 3, GTK4/PyGObject. System `/usr/bin/python3`. Tests via `xvfb-run --auto-servernum /usr/bin/python3 -m pytest`.

## Global Constraints

- **Reversible by one flag.** Nothing pipeline-related is deleted. `TTLG_PIPELINE_MODE=1` (or flipping the default) restores today's UI unchanged. Every hide is a branch on the flag; the flag-ON path must remain byte-for-byte today's behavior (regression-guarded).
- **Engine/spec/Stage untouched.** No change to `pipeline_engine`, the run spec, `collect()`/`_collect_params()`, or the Stage making-of internals. This plan changes reachability + seeded-remix routing + a small amount of copy only.
- **Palette:** app main scheme teal `#4FD1C5` on `#0F2A35`. Any `_CSS` byte literal stays ASCII-only; glyphs live in Python `str` labels, never in `b"""..."""` CSS.
- **GTK single-threaded.** No new threads — this is nav/routing/copy. (`_launch_run`'s existing `PipelineRunner.start` already owns the only background work.)
- **Fail-soft.** A flag-OFF remix that can't resolve a goal degrades exactly as today (blank Muse / no-op), never crashes.
- **Default OFF.** `PIPELINE_MODE_ENABLED` defaults to `False`. It is a dev/env flag, NOT a persisted user setting and NOT surfaced in Preferences.
- **Version discipline:** minor `VERSION` bump (user-visible: pipeline mode disappears from the shipped UI) — Task 6 only.
- Spec: `docs/superpowers/specs/2026-08-19-remix-without-pipeline-mode-design.md`.

## File Structure / touch map

- `app/app_settings.py` — NEW module constant `PIPELINE_MODE_ENABLED` + env read (Task 1).
- `app/create_view.py` — `_build_doors_row` omits the Inspiration door when `on_inspiration is None` (Task 2).
- `app/pipeline_studio.py` — extract `_launch_run` (Task 3); add `pipeline_mode_enabled`/`on_leave` ctor seams + branch `_on_muse_goal_chosen`/`_on_run_back`/`_on_back_to_discover` + relabel the Muse back button (Task 4).
- `app/main_window.py` — gate `_pipelines_btn`+divider; pass `on_inspiration=None` when OFF; construct `PipelineStudio(pipeline_mode_enabled=…, on_leave=…)`; split `_show_pipelines` → `_ensure_pipeline_studio`; branch `_remix_as_pipeline`; add `_on_pipeline_leave` (Task 5).
- Tests: `tests/test_pipeline_mode_flag.py` (new); updates to `tests/test_create_view.py`, `tests/test_pipeline_studio.py`, `tests/test_main_window_shell_layout.py`, `tests/test_main_window_create_view_mount.py`, and a main_window remix-routing test.
- `VERSION`, `debian/changelog`, `CLAUDE.md` (Task 6).

---

### Task 1: The feature flag

**Files:**
- Modify: `app/app_settings.py`
- Test: `tests/test_pipeline_mode_flag.py`

**Interfaces:**
- Produces: `app_settings.PIPELINE_MODE_ENABLED: bool` — module-level constant, read once at import from env `TTLG_PIPELINE_MODE` (truthy set: `{"1","true","yes","on"}`, case-insensitive, stripped), default `False`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_mode_flag.py
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _reload_with_env(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TTLG_PIPELINE_MODE", raising=False)
    else:
        monkeypatch.setenv("TTLG_PIPELINE_MODE", value)
    import app_settings
    return importlib.reload(app_settings)


def test_pipeline_mode_defaults_off(monkeypatch):
    mod = _reload_with_env(monkeypatch, None)
    assert mod.PIPELINE_MODE_ENABLED is False


def test_pipeline_mode_env_truthy(monkeypatch):
    for v in ("1", "true", "TRUE", "yes", "on", " On "):
        mod = _reload_with_env(monkeypatch, v)
        assert mod.PIPELINE_MODE_ENABLED is True, v


def test_pipeline_mode_env_falsey(monkeypatch):
    for v in ("0", "false", "no", "off", ""):
        mod = _reload_with_env(monkeypatch, v)
        assert mod.PIPELINE_MODE_ENABLED is False, v


def teardown_module(_m):
    # leave app_settings in its default (env-unset) state for other tests
    import os, importlib, app_settings
    os.environ.pop("TTLG_PIPELINE_MODE", None)
    importlib.reload(app_settings)
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_pipeline_mode_flag.py -q`
Expected: FAIL (`AttributeError: module 'app_settings' has no attribute 'PIPELINE_MODE_ENABLED'`).

- [ ] **Step 3: Implement the constant**

In `app/app_settings.py`, add `import os` to the imports block, and after the `SETTINGS_FILE` definition (before `DEFAULTS`) add:

```python
# ── Feature flags (dev/env only — NOT persisted user settings) ────────────────
# Pipeline mode (browse runs / blank-canvas compose / DAG editor) is not ready
# for primetime. OFF by default: the pipeline surfaces are hidden and seeded
# 🔀 Remix routes straight to the run + Library. Set TTLG_PIPELINE_MODE=1 to
# restore the full pipeline UI unchanged. Read once at import.
# See docs/superpowers/specs/2026-08-19-remix-without-pipeline-mode-design.md.
PIPELINE_MODE_ENABLED: bool = (
    os.environ.get("TTLG_PIPELINE_MODE", "").strip().lower()
    in ("1", "true", "yes", "on")
)
```

- [ ] **Step 4: Run to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_pipeline_mode_flag.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/app_settings.py tests/test_pipeline_mode_flag.py
git commit -m "feat(flags): PIPELINE_MODE_ENABLED (env TTLG_PIPELINE_MODE, default off)"
```

---

### Task 2: CreateView omits the Inspiration door when unwired

**Files:**
- Modify: `app/create_view.py` (`_build_doors_row`)
- Test: `tests/test_create_view.py`

**Interfaces:**
- Consumes: `CreateView(..., on_inspiration=None)` (existing ctor param).
- Produces: when `self._on_inspiration is None`, the doors row has ONLY idea + model (no inspiration toggle); `self._doors` has keys `{"idea","model"}`; the model button carries the right-rounded corner class. When `on_inspiration` is a callable, all three doors exist exactly as today.

Rationale: `main_window` (Task 5) passes `on_inspiration=None` when the flag is OFF — the Inspiration door is a blank-canvas compose entry (`_on_loop_nav_remix` → blank Muse), which must not exist when pipeline mode is hidden. `_set_entry_mode("inspiration")` already guards `if ... self._on_inspiration is not None`, so omitting the door is safe. `collect()` is unaffected (a door is navigation, not a value-bearing widget).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_create_view.py (reuse the file's existing CreateView build helpers/fakes)
def test_inspiration_door_omitted_when_unwired():
    view = _make_create_view(on_inspiration=None)   # existing helper; pass None
    assert set(view._doors.keys()) == {"idea", "model"}
    # model becomes the right-most door -> right-rounded corner
    assert view._doors["model"].has_css_class("create-door-btn-right")


def test_inspiration_door_present_when_wired():
    view = _make_create_view(on_inspiration=lambda: None)
    assert set(view._doors.keys()) == {"idea", "model", "inspiration"}
    assert view._doors["inspiration"].has_css_class("create-door-btn-right")
    assert view._doors["model"].has_css_class("create-door-btn-mid")
```

(If `_make_create_view` doesn't already accept `on_inspiration`, extend it to thread the kwarg into the `CreateView(...)` constructor — do not change any other default.)

- [ ] **Step 2: Run to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_view.py -q -k inspiration_door`
Expected: FAIL (`inspiration` key present when unwired / model lacks right class).

- [ ] **Step 3: Implement — gate the door in `_build_doors_row`**

Replace the inspiration-button build + the model button's corner class + the append/`_doors` assembly so the inspiration door is conditional:

```python
        model_btn = Gtk.ToggleButton(label="\U0001f5a5 Start with a model")
        model_btn.add_css_class("create-door-btn")
        model_btn.set_tooltip_text(
            "Start from a running or runnable model — the medium follows the model."
        )

        show_inspiration = self._on_inspiration is not None
        # The right-most door gets the right-rounded corner; that's the
        # inspiration door when present, else the model door.
        model_btn.add_css_class(
            "create-door-btn-mid" if show_inspiration else "create-door-btn-right"
        )
        model_btn.set_group(idea_btn)

        idea_btn.connect("toggled", lambda b: b.get_active() and self._set_entry_mode("idea"))
        model_btn.connect("toggled", lambda b: b.get_active() and self._set_entry_mode("model"))

        row.append(idea_btn)
        row.append(model_btn)
        self._doors = {"idea": idea_btn, "model": model_btn}

        if show_inspiration:
            inspiration_btn = Gtk.ToggleButton(label="\U0001f30c Start with inspiration")
            inspiration_btn.add_css_class("create-door-btn")
            inspiration_btn.add_css_class("create-door-btn-right")
            inspiration_btn.set_tooltip_text("Hand off to the Muse for a creative spark.")
            inspiration_btn.set_group(idea_btn)
            inspiration_btn.connect(
                "toggled", lambda b: b.get_active() and self._set_entry_mode("inspiration")
            )
            row.append(inspiration_btn)
            self._doors["inspiration"] = inspiration_btn

        idea_btn.set_active(True)
        return row
```

(Keep `idea_btn`'s existing build lines above this unchanged. Remove the old unconditional `inspiration_btn` block, the old `model_btn.add_css_class("create-door-btn-mid")`, and the old flat `self._doors = {..., "inspiration": inspiration_btn}` line.)

- [ ] **Step 4: Run to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_view.py -q`
Expected: PASS (new tests + no regressions in the file).

- [ ] **Step 5: Commit**

```bash
git add app/create_view.py tests/test_create_view.py
git commit -m "feat(create): omit the Inspiration door when on_inspiration is unwired"
```

---

### Task 3: Extract `_launch_run` from `_on_run_remix` (pure refactor)

**Files:**
- Modify: `app/pipeline_studio.py` (`PipelineStudio._on_run_remix`)
- Test: `tests/test_pipeline_studio.py`

**Interfaces:**
- Produces: `PipelineStudio._launch_run(self, derived_path: str, edits: dict) -> None` — creates the provisional `PipelineStore` run record, builds the `RunView`, `self.live_run.begin(...)`, shows the `"run"` page, constructs `PipelineRunner`, stores `self._runner`, and `runner.start(...)` — i.e. exactly today's `_on_run_remix` body from `jobs = _default_remix_jobs()` onward.
- `_on_run_remix` keeps computing `derived_path` (`_with_preserved_top_level_metadata(remix_view.current_spec(), spec_path)` → `write_spec`) then calls `self._launch_run(derived_path, edits)`.

This is a behavior-preserving extraction; the equivalence test is the guard the spec calls out as the main risk for the flag-ON path.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_pipeline_studio.py
def test_launch_run_starts_runner_and_shows_run_page(monkeypatch, tmp_path):
    import pipeline_studio as ps
    studio = ps.PipelineStudio()

    started = {}
    class _FakeRunner:
        def __init__(self, *a, **k): pass
        def start(self, derived_path, jobs, **kw):
            started["derived_path"] = derived_path
            started["jobs"] = jobs
            started["overrides"] = kw.get("param_overrides")
            started["run_id"] = kw.get("run_id")
    class _FakeStore:
        def create_run(self, **kw):
            started["create_kw"] = kw
            return "run-xyz"
        def get_run(self, rid):
            return {"run_id": rid, "spec_path": kw_spec, "spec_name": "muse",
                    "jobs": [], "job_states": {}, "param_overrides": {}}
    kw_spec = str(tmp_path / "muse.json")
    monkeypatch.setattr(ps, "PipelineRunner", _FakeRunner)
    monkeypatch.setattr(ps, "PipelineStore", _FakeStore)
    monkeypatch.setattr(ps, "build_run_view", lambda rec: _minimal_run_view())  # existing helper

    studio._launch_run(kw_spec, {"n": 1})

    assert started["derived_path"] == kw_spec
    assert started["overrides"] == {"n": 1}
    assert started["run_id"] == "run-xyz"
    assert studio.stack.get_visible_child_name() == "run"
    assert studio._runner is not None
```

(Use the file's existing minimal-RunView helper; if none exists, build a tiny `RunView` with one step as the other LiveRunView tests in the file do.)

- [ ] **Step 2: Run to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_studio.py -q -k launch_run`
Expected: FAIL (`AttributeError: ... has no attribute '_launch_run'`).

- [ ] **Step 3: Implement the extraction**

Add the method and shrink `_on_run_remix`:

```python
    def _launch_run(self, derived_path: str, edits: dict) -> None:
        """Create the provisional run record, show the Stage, and start the
        runner for an already-written seed/derived spec at *derived_path*
        with *edits* as param overrides. Shared by _on_run_remix (RemixView's
        composed graph) and _on_muse_goal_chosen's flag-OFF straight-to-run
        path (a fresh muse seed, no edits)."""
        jobs = _default_remix_jobs()
        store = PipelineStore()
        run_id = store.create_run(
            spec_path=derived_path,
            spec_name=Path(derived_path).stem,
            jobs=jobs,
            param_overrides=edits,
            pid=0,
            log_file="",
        )
        record = store.get_run(run_id)
        run_view = build_run_view(record)

        self.live_run.begin(run_view)
        self.stack.set_visible_child_name("run")

        runner = PipelineRunner(idle_add=GLib.idle_add)
        self._runner = runner
        runner.start(
            derived_path,
            jobs,
            param_overrides=edits,
            on_node_update=self.live_run.on_node_update,
            on_run_finished=self.live_run.on_finished,
            on_log=self.live_run.on_log,
            run_id=run_id,
        )
```

Then in `_on_run_remix`, replace everything from `jobs = _default_remix_jobs()` through the `runner.start(...)` call with:

```python
        self._launch_run(derived_path, edits)
```

(Keep the `REMIXES_DIR.mkdir(...)`, `final_spec = _with_preserved_top_level_metadata(...)`, and `derived_path = write_spec(...)` lines at the top of `_on_run_remix` unchanged.)

- [ ] **Step 4: Run to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_studio.py -q`
Expected: PASS (new test + all existing pipeline_studio tests, including any that exercise `_on_run_remix`).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline_studio.py tests/test_pipeline_studio.py
git commit -m "refactor(pipeline): extract PipelineStudio._launch_run (behavior-identical)"
```

---

### Task 4: PipelineStudio flag seams — muse→run, and exit→Library

**Files:**
- Modify: `app/pipeline_studio.py` (`PipelineStudio.__init__`, `_on_muse_goal_chosen`, `_on_run_back`, `_on_back_to_discover`, the Muse back-button build)
- Test: `tests/test_pipeline_studio.py`

**Interfaces:**
- Consumes: `Task 3`'s `_launch_run`.
- Produces: `PipelineStudio(..., pipeline_mode_enabled: bool = True, on_leave: "Optional[Callable[[], None]]" = None)`.
  - When `pipeline_mode_enabled is False`: `_on_muse_goal_chosen` writes the seed spec then calls `self._launch_run(derived_path, {})` and shows `"run"` — it does NOT load RemixView or show `"remix"`. The Muse back button reads `"← Back"`.
  - When `on_leave` is set: `_on_run_back` and `_on_back_to_discover` call `on_leave()` instead of switching to the studio `"discover"` page.
  - Defaults (`pipeline_mode_enabled=True`, `on_leave=None`) preserve today's behavior exactly (every existing caller/test unaffected).

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_pipeline_studio.py
def test_flag_off_muse_goal_goes_straight_to_run(monkeypatch, tmp_path):
    import pipeline_studio as ps
    studio = ps.PipelineStudio(pipeline_mode_enabled=False)
    launched = {}
    monkeypatch.setattr(studio, "_launch_run",
                        lambda p, e: launched.update(path=p, edits=e))
    monkeypatch.setattr(ps, "write_spec", lambda spec, base, d: str(tmp_path / "muse.json"))
    monkeypatch.setattr(ps, "REMIXES_DIR", tmp_path)

    studio._on_muse_goal_chosen(studio.muse, {"1": {"class_type": "TTLGArtgenGenerate", "inputs": {}}})

    assert launched["path"] == str(tmp_path / "muse.json")
    assert launched["edits"] == {}
    # never routed through the DAG editor
    assert studio.stack.get_visible_child_name() != "remix"


def test_flag_off_backs_call_on_leave(monkeypatch):
    import pipeline_studio as ps
    left = []
    studio = ps.PipelineStudio(pipeline_mode_enabled=False, on_leave=lambda: left.append(True))
    studio._on_run_back(None)
    studio._on_back_to_discover(None)
    assert left == [True, True]
    # did NOT switch to the studio's discover page
    assert studio.stack.get_visible_child_name() != "discover"


def test_flag_on_muse_goal_uses_dag_editor(monkeypatch, tmp_path):
    import pipeline_studio as ps
    studio = ps.PipelineStudio()   # default: pipeline_mode_enabled=True
    monkeypatch.setattr(ps, "write_spec", lambda spec, base, d: str(tmp_path / "muse.json"))
    monkeypatch.setattr(ps, "REMIXES_DIR", tmp_path)
    seen = {}
    monkeypatch.setattr(studio.remix_view, "load_seed_spec",
                        lambda p, t: seen.update(path=p, title=t))
    studio._on_muse_goal_chosen(studio.muse, {"1": {"class_type": "TTLGArtgenGenerate", "inputs": {}}})
    assert seen["path"] == str(tmp_path / "muse.json")
    assert studio.stack.get_visible_child_name() == "remix"


def test_flag_on_run_back_goes_to_discover():
    import pipeline_studio as ps
    studio = ps.PipelineStudio()   # on_leave=None
    studio._on_run_back(None)
    assert studio.stack.get_visible_child_name() == "discover"
```

- [ ] **Step 2: Run to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_studio.py -q -k "flag_off or flag_on_muse or flag_on_run_back"`
Expected: FAIL (ctor rejects `pipeline_mode_enabled`, no branch behavior).

- [ ] **Step 3: Implement the seams**

3a. `__init__` signature — add the two params and store them:

```python
    def __init__(
        self,
        on_open_run: "Optional[Callable[[str], None]]" = None,
        inspire_fn: "... | None" = None,
        status_service=None,
        on_run_complete: "Optional[Callable[[RunView], None]]" = None,
        pipeline_mode_enabled: bool = True,
        on_leave: "Optional[Callable[[], None]]" = None,
    ) -> None:
```

Near the other seam stores, add:

```python
        # When False (shipping default in main_window), the DAG editor and the
        # studio's own Discover are hidden: a Muse-chosen goal runs immediately
        # and Back leaves the studio via on_leave. True keeps today's full
        # pipeline UI. See spec 2026-08-19-remix-without-pipeline-mode-design.
        self._pipeline_mode_enabled = pipeline_mode_enabled
        # Called (when set) by the Muse/run Back buttons instead of routing to
        # the studio's own Discover — main_window uses it to return to the app
        # Library.
        self._on_leave = on_leave
```

3b. `_on_muse_goal_chosen` — branch after `write_spec`:

```python
        derived_path = write_spec(spec, "muse", str(REMIXES_DIR))

        if not self._pipeline_mode_enabled:
            # Pipeline mode hidden: skip the DAG editor, run the seed as-is.
            self._launch_run(derived_path, {})
            return

        if self._muse_seed_artifact is None:
            title = "a new pipeline"
        else:
            kind = self._muse_seed_artifact[1]
            title = f"your {kind}"
        self.remix_view.load_seed_spec(derived_path, title)
        self.stack.set_visible_child_name("remix")
```

3c. `_on_run_back` — leave via `on_leave` when set:

```python
    def _on_run_back(self, _button) -> None:
        if self._on_leave is not None:
            self._on_leave()
            return
        self.stack.set_visible_child_name("discover")
```

(Keep the existing docstring; append a sentence noting the `on_leave` branch.)

3d. `_on_back_to_discover` — same leave branch at the top:

```python
    def _on_back_to_discover(self, _button) -> None:
        if self._on_leave is not None:
            self._on_leave()
            return
        self.stack.set_visible_child_name("discover")
```

3e. Muse back-button label — when pipeline mode is hidden there is no Discover to go back to. In the muse-page build, replace the fixed label:

```python
        muse_back_btn = Gtk.Button(
            label="← Discover" if self._pipeline_mode_enabled else "← Back"
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_studio.py -q`
Expected: PASS (new branch tests + all existing, incl. Task 3's `_launch_run` test and the Stage/back tests from Slice 1).

- [ ] **Step 5: Commit**

```bash
git add app/pipeline_studio.py tests/test_pipeline_studio.py
git commit -m "feat(pipeline): pipeline_mode_enabled/on_leave seams — muse->run, exit->Library"
```

---

### Task 5: main_window flag wiring

**Files:**
- Modify: `app/main_window.py`
- Test: `tests/test_main_window_shell_layout.py`, `tests/test_main_window_create_view_mount.py`, `tests/test_main_window_pipelines.py` (new routing test)

**Interfaces:**
- Consumes: `app_settings.PIPELINE_MODE_ENABLED` (Task 1); `create_view.CreateView(on_inspiration=…)` (Task 2); `PipelineStudio(pipeline_mode_enabled=…, on_leave=…)` (Task 4).
- Produces:
  - When `PIPELINE_MODE_ENABLED` is False: the `🧩 Pipelines` toggle + its divider are not appended (`self._pipelines_btn` stays `None`); `CreateView` is built with `on_inspiration=None`; `PipelineStudio` is built with `pipeline_mode_enabled=False, on_leave=self._on_pipeline_leave`; `_remix_as_pipeline` enters the studio scoped straight to the Muse (no Discover).
  - `_ensure_pipeline_studio(self)` — the lazy-construct half split out of `_show_pipelines` (constructs `self._pipeline_studio` with all seams incl. the two new ones; adds it to `_gallery_stack` as `"pipelines"`).
  - `_on_pipeline_leave(self)` — returns to the app Library (delegates to `_hide_pipelines`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_main_window_pipelines.py — routing when the flag is OFF
def test_remix_flag_off_enters_muse_not_discover(monkeypatch):
    import main_window as mw
    monkeypatch.setattr(mw.app_settings, "PIPELINE_MODE_ENABLED", False, raising=False)
    obj = _bare_main_window(monkeypatch)   # existing lightweight builder used in this file

    calls = {"discover": 0, "muse": None, "ensured": 0}
    class _FakeStudio:
        def show_discover(self): calls["discover"] += 1
        def show_muse(self, seed_artifact=None): calls["muse"] = seed_artifact
    obj._pipeline_studio = _FakeStudio()
    monkeypatch.setattr(obj, "_ensure_pipeline_studio", lambda: calls.__setitem__("ensured", 1))
    obj._gallery_stack = _FakeStack()   # records set_visible_child_name
    obj._loop_nav = {}

    rec = _fake_generation_record()     # image record with a resolvable path
    obj._remix_as_pipeline(rec)

    assert calls["ensured"] == 1
    assert calls["muse"] is not None            # seeded muse
    assert calls["discover"] == 0               # never lands on studio Discover
    assert obj._gallery_stack.last == "pipelines"


def test_on_pipeline_leave_returns_to_library(monkeypatch):
    import main_window as mw
    obj = _bare_main_window(monkeypatch)
    hidden = []
    monkeypatch.setattr(obj, "_hide_pipelines", lambda: hidden.append(True))
    obj._on_pipeline_leave()
    assert hidden == [True]
```

Update the two brittle source-string tests to be flag-aware:

```python
# tests/test_main_window_shell_layout.py
def test_pipelines_btn_appended_only_when_flag_on():
    # the append is now guarded by the flag, not unconditional
    assert "if app_settings.PIPELINE_MODE_ENABLED" in _SRC
    assert "loop_nav_row.append(self._pipelines_btn)" in _SRC   # still present, now inside the guard

# tests/test_main_window_create_view_mount.py
def test_inspiration_door_wired_only_when_flag_on():
    # on_inspiration is the muse bridge when on, None when off
    assert "on_inspiration=self._on_loop_nav_remix if app_settings.PIPELINE_MODE_ENABLED else None" in _SRC
```

(If `_bare_main_window`/`_FakeStack`/`_fake_generation_record` helpers don't exist in these files, add minimal ones mirroring the file's existing patterns — several tests here already build a bare object and set attributes directly.)

- [ ] **Step 2: Run to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_main_window_pipelines.py tests/test_main_window_shell_layout.py tests/test_main_window_create_view_mount.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement the wiring**

3a. Import: ensure `import app_settings` is present at the top of `main_window.py` (add if missing).

3b. Gate the toggle + divider. In the loop-nav assembly (`loop_nav_row` build), wrap the divider + `_pipelines_btn` build/append in the flag, and initialize the attribute to `None` first:

```python
        self._pipelines_btn = None
        if app_settings.PIPELINE_MODE_ENABLED:
            _loop_div = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
            _loop_div.add_css_class("loop-nav-divider")
            loop_nav_row.append(_loop_div)
            self._pipelines_btn = Gtk.ToggleButton(label="🧩 Pipelines")
            self._pipelines_btn.add_css_class("attractor-launch-btn")
            self._pipelines_btn.set_tooltip_text(...)   # keep existing tooltip text
            self._pipelines_btn.connect("toggled", self._on_pipelines_toggled)
            loop_nav_row.append(self._pipelines_btn)
```

Audit every `_pipelines_btn` reference (`_remix_as_pipeline`, `_uncheck_pipelines_toggle_if_active`, the startup activation dance) — they already `getattr(self, "_pipelines_btn", None)`-guard or must be made to; confirm a `None` toggle is a safe no-op everywhere.

3c. CreateView construction — pass the inspiration seam only when the flag is on:

```python
            on_inspiration=self._on_loop_nav_remix if app_settings.PIPELINE_MODE_ENABLED else None,
```

3d. Split `_show_pipelines` → `_ensure_pipeline_studio` + show. Extract the lazy-construct block into `_ensure_pipeline_studio`, and construct with the two new seams:

```python
    def _ensure_pipeline_studio(self) -> None:
        if self._pipeline_studio is None:
            from pipeline_studio import PipelineStudio
            self._pipeline_studio = PipelineStudio(
                inspire_fn=self._create_inspire_fn,
                status_service=self._status_service,
                on_run_complete=self._register_pipeline_final,
                pipeline_mode_enabled=app_settings.PIPELINE_MODE_ENABLED,
                on_leave=self._on_pipeline_leave,
            )
            self._gallery_stack.add_named(self._pipeline_studio, "pipelines")

    def _show_pipelines(self) -> None:
        # (unchanged behavior; now built on the extracted helper)
        self._ensure_pipeline_studio()
        self._pipeline_studio.show_discover()
        self._gallery_stack.set_visible_child_name("pipelines")
        for _b in getattr(self, "_loop_nav", {}).values():
            if _b.get_active():
                _b.set_active(False)
```

3e. `_on_pipeline_leave`:

```python
    def _on_pipeline_leave(self) -> None:
        """Studio Back (flag OFF) -> return to the app Library. The run keeps
        running in the background; its result is already registered to the
        Library on run-done."""
        self._hide_pipelines()
```

3f. Branch `_remix_as_pipeline`. Keep the seed-resolution block unchanged. Replace the "activate the Pipelines area" tail:

```python
        if app_settings.PIPELINE_MODE_ENABLED:
            pipelines_btn = getattr(self, "_pipelines_btn", None)
            if pipelines_btn is not None and not pipelines_btn.get_active():
                pipelines_btn.set_active(True)   # -> _on_pipelines_toggled -> _show_pipelines
            else:
                self._show_pipelines()
        else:
            # Pipeline mode hidden: enter the studio scoped straight to the
            # Muse goal chooser — never the studio's Discover.
            self._ensure_pipeline_studio()
            self._gallery_stack.set_visible_child_name("pipelines")
            for _b in getattr(self, "_loop_nav", {}).values():
                if _b.get_active():
                    _b.set_active(False)

        self._pipeline_studio.show_muse(seed_artifact=seed_artifact)
```

- [ ] **Step 4: Run to verify it passes**

Run first the targeted files, then the main-window suite:
```bash
xvfb-run --auto-servernum /usr/bin/python3 -m pytest \
  tests/test_main_window_pipelines.py tests/test_main_window_shell_layout.py \
  tests/test_main_window_create_view_mount.py tests/test_main_window_loop_nav.py -q
```
Expected: PASS. Fix any flag-ON regression (the loop-nav/pipelines tests that build their own `_pipelines_btn` should still pass — they exercise the ON path).

- [ ] **Step 5: Commit**

```bash
git add app/main_window.py tests/test_main_window_pipelines.py \
        tests/test_main_window_shell_layout.py tests/test_main_window_create_view_mount.py
git commit -m "feat(shell): hide pipeline mode behind PIPELINE_MODE_ENABLED; seeded Remix -> muse->run->Library"
```

---

### Task 6: Finalize — wording guard, version, changelog, docs, full suite

**Files:**
- Modify: `VERSION`, `debian/changelog`, `CLAUDE.md`
- Test: `tests/test_pipeline_studio.py` (a copy guard)

**Interfaces:** none (finalize).

- [ ] **Step 1: Wording guard test.** The flag-OFF-reachable surfaces are the Muse goal chooser (heading already reads "Make this {kind} into…" / "What do you want to make?") and the Stage. Add a guard that they carry no pipeline-authoring jargon:

```python
# tests/test_pipeline_studio.py
def test_flag_off_reachable_surfaces_have_no_pipeline_jargon():
    import pipeline_studio as ps
    studio = ps.PipelineStudio(pipeline_mode_enabled=False)
    # Muse heading (seeded) reads as "make this into", not pipeline-speak
    studio.muse.set_seed_kind("image")            # or the existing seed setter used by show_muse
    assert "pipeline" not in studio.muse._heading_label.get_label().lower()
    # Muse back button is not "Discover" when there is no studio Discover to reach
    # (built in _build_muse_page; assert via the constructed label)
    assert "discover" not in _muse_back_label(studio).lower()
```

(Use whatever accessor the file already has for the muse heading / seed; if the Muse's seed setter differs, match its real signature. If a private accessor for the back-button label isn't available, assert the label via the widget tree the muse page exposes, or fold this assertion into Task 4's `test_flag_off...` where the label is already reachable — do NOT invent an API.)

- [ ] **Step 2: Run to verify it fails, then holds** (it should already pass given Tasks 2/4 — if it fails, adjust the reachable label, not the test's intent).

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_pipeline_studio.py -q -k jargon`

- [ ] **Step 3: Bump `VERSION`** — minor bump from current (read `VERSION`; e.g. `0.79.0` → `0.80.0`).

- [ ] **Step 4: Prepend a `debian/changelog` stanza** (match the existing trailer format) summarizing: pipeline mode is hidden behind `TTLG_PIPELINE_MODE` (default off) — no Pipelines nav entry, no blank-canvas Inspiration door, no DAG editor; 🔀 Remix now goes goal-chooser → run (Stage) → Library, and leaving returns to the Library; fully reversible by the flag; engine/spec/`collect()` untouched.

- [ ] **Step 5: Update `CLAUDE.md`** — a short "Hiding pipeline mode (vX.Y.0)" note: the `app_settings.PIPELINE_MODE_ENABLED` flag (env `TTLG_PIPELINE_MODE`, default off); the three gated doors (Pipelines toggle, CreateView Inspiration door, DAG editor); the reshaped seeded-remix path (`_remix_as_pipeline` scoped muse → `_on_muse_goal_chosen` → `_launch_run` → Stage → Library) and `on_leave`; that flag-ON restores today's UI and nothing was deleted. Link `[[project_stage_pipeline_direction]]`.

- [ ] **Step 6: Full suite (documented deselects)**

```bash
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q \
  --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module \
  --deselect tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes \
  --deselect tests/test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen
```
Expected: green. Also run once with `TTLG_PIPELINE_MODE=1` set to sanity-check the flag-ON path builds:
```bash
TTLG_PIPELINE_MODE=1 xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_main_window_shell_layout.py tests/test_main_window_pipelines.py -q
```

- [ ] **Step 7: Commit**

```bash
git add VERSION debian/changelog CLAUDE.md tests/test_pipeline_studio.py
git commit -m "chore: finalize hide-pipeline-mode (version, changelog, docs, wording guard)"
```

- [ ] **Step 8: Manual check (user, on the real display — NOT automated).** Default build (flag off): no 🧩 Pipelines in the nav; Create shows only Idea/Model doors; 🔀 Remix on an image → "Make this image into…" chooser → pick a goal → Stage making-of → result appears in the Library; Back from the chooser and from the run both return to the Library, never a studio Discover; the run keeps going if you leave. Then `TTLG_PIPELINE_MODE=1 ./tt-gen` → the full pipeline UI is back unchanged.

---

## Ordering & risk

1 → 2 → 3 → 4 → 5 → 6. Highest risk: **Task 3** (the `_launch_run` extraction must not drift the flag-ON run start — pinned by the equivalence test) and **Task 5** (integration: the `_pipelines_btn = None` guards across all call sites, and the two brittle `_SRC` source-string tests). Tasks 1/2/4/6 are contained. Everything is reversible by the flag, so a flag-ON regression is the main thing reviews should watch.

## Self-Review

**Spec coverage:** flag (§A) → T1; door #1 toggle (§B) → T5; door #2 Inspiration (§C) → T2 (omit) + T5 (pass None); door #3 reshape (§D) → T3 (`_launch_run`) + T4 (muse→run, on_leave, back relabel) + T5 (scoped entry, `_on_pipeline_leave`); wording (§E) → T4 (muse back label) + T6 (guard). Testing/palette/GTK/collect-untouched/reversible → Global Constraints + per-task tests. ✓

**Placeholder scan:** no TBDs; each code step has real code. The two "use the file's existing helper" notes name the exact helper role and instruct matching the real signature rather than inventing one. ✓

**Type/name consistency:** `PIPELINE_MODE_ENABLED` (T1) read in T5; `_launch_run(derived_path, edits)` (T3) called by T4; `pipeline_mode_enabled`/`on_leave` ctor params (T4) passed by T5's `_ensure_pipeline_studio`; `_ensure_pipeline_studio`/`_on_pipeline_leave` (T5) used across T5. ✓
