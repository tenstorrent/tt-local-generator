# In-place Create Result Surfacing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Show a Create job's live pending state and finished artifact **inside the Create view** (a result panel beside the form + a session recents strip), the instant it's done — while still persisting every result to the store/Discover exactly as today.

**Architecture:** A new `CreateResultPanel` widget renders pending/finished/error + a capped recents strip. CreateView becomes a responsive two-pane layout (form | result). `main_window` marks Create-originated jobs and forwards the generation lifecycle (both the native `_on_generate` callback path and the artgen `tt-ctl` path) to the panel, skipping the redundant gallery pending card for those jobs. Generation internals and the store/gallery persistence path are untouched.

**Tech Stack:** Python 3, GTK4/PyGObject, pytest (xvfb).

## Global Constraints

- **Persistence invariant:** a Create result still writes to the store and appears in the Discover gallery. The panel is additional feedback, never a replacement.
- **Migration-safe:** non-Create jobs (attractor / TT-TV / queue) are unaffected — they keep using the gallery pending card and never touch the panel.
- **Width discipline:** the two panes are a wrapping/responsive container (side-by-side wide, stacked narrow); no unbounded horizontal `Gtk.Box`. Surface already sits in `wrap_centered` + a vertical `ScrolledWindow`.
- **GTK threading:** worker/subprocess callbacks reach the panel only via `GLib.idle_add`.
- **Palette:** tt-vscode-toolkit (`#4FD1C5`/`#0F2A35`); `_CSS` bytes literals ASCII-only (glyphs in Python strings).
- **No progressive mid-gen frames** (server doesn't stream) — pending = spinner + elapsed + progress text.
- System python `/usr/bin/python3`. Version bump + changelog on landing. Local only. Deselect the known flake `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module` in full-suite runs.

---

### Task 1: `CreateResultPanel` widget

**Files:** Modify `app/create_view.py`; Test `tests/test_create_result_panel.py`.

**Interfaces — Produces:**
```python
_RECENTS_MAX = 6
class CreateResultPanel(Gtk.Box):           # orientation VERTICAL
    def __init__(self) -> None
    def show_pending(self, prompt: str, medium=None) -> None   # spinner + elapsed + prompt
    def show_progress(self, message: str) -> None              # updates pending status text
    def show_finished(self, record) -> None                    # render artifact + prepend recents
    def show_error(self, message: str) -> None
    def clear(self) -> None
    # test seams:
    @property
    def state(self) -> str                 # "empty"|"pending"|"finished"|"error"
    def recents_count(self) -> int
```
- Renders `record` by kind from its file path/ext: image → `Gtk.Picture.new_for_filename`; video/gif → `Gtk.Video`/poster (reuse the lazy-stream+loop approach used by `GenerationCard`; a static poster is an acceptable v1 if the stream isn't realized); text/`.txt`/`.ans` → a scrollable `Gtk.Label`/`TextView`. A missing/unreadable path → an honest placeholder (never a broken image).
- Recents: a wrapping `Gtk.FlowBox` (`selection_mode=NONE`), newest first, capped at `_RECENTS_MAX` (drop oldest). Clicking a recent re-renders it in the current-result area.
- Pending elapsed timer via `GLib.timeout_add(1000, …)`, cancelled with `GLib.source_remove` when the state changes (mirror `PendingCard`).

- [ ] **Step 1: failing tests** (xvfb header per tests/test_create_view.py)
```python
import create_view as cv
def _rec(tmp_path, kind="image"):
    from history_store import GenerationRecord   # use the real record dataclass
    p = tmp_path / ("a.png" if kind=="image" else "a.txt")
    p.write_bytes(b"\x89PNG\r\n" if kind=="image" else b"hi")
    # construct a minimal valid GenerationRecord for `kind` (fill required fields)
    return GenerationRecord(...)  # see history_store for the exact ctor

def test_starts_empty():
    assert cv.CreateResultPanel().state == "empty"
def test_pending_then_finished(tmp_path):
    p = cv.CreateResultPanel(); p.show_pending("a castle", None)
    assert p.state == "pending"
    p.show_finished(_rec(tmp_path)); assert p.state == "finished"
    assert p.recents_count() == 1
def test_recents_caps_at_max(tmp_path):
    p = cv.CreateResultPanel()
    for _ in range(_RECENTS_MAX + 3): p.show_finished(_rec(tmp_path))
    assert p.recents_count() == cv._RECENTS_MAX
def test_error_state(tmp_path):
    p = cv.CreateResultPanel(); p.show_error("boom"); assert p.state == "error"
```
(Inspect `app/history_store.py` for `GenerationRecord`'s real constructor and fill required fields; adjust the helper accordingly.)
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement `CreateResultPanel` per the interface. Keep all glyphs in Python strings; add CSS classes (`create-result-*`) to `create_view`'s `_apply_css` (ASCII-only).
- [ ] **Step 4:** `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_result_panel.py -q` → PASS.
- [ ] **Step 5:** commit `feat(create): CreateResultPanel — inline pending/finished/error + recents strip`.

---

### Task 2: Two-pane responsive CreateView layout

**Files:** Modify `app/create_view.py`; Test `tests/test_create_view.py` (extend).

**Interfaces — Consumes:** `CreateResultPanel`. **Produces:** CreateView holds `self._result_panel` (a `CreateResultPanel`) beside the form in a responsive wrapping container; `self._is_two_pane()` test seam.

- [ ] **Step 1: failing tests**
```python
def test_create_view_has_result_panel(make_create_view):
    cv = make_create_view()
    import create_view as m
    assert isinstance(cv._result_panel, m.CreateResultPanel)
def test_panes_in_wrapping_container_not_hbox(make_create_view):
    # the form+result live in a FlowBox (wraps) — never a fixed horizontal Box
    cv = make_create_view()
    assert cv._panes_wrap()   # helper: the two-pane container is a Gtk.FlowBox
def test_surface_still_width_clamped(make_create_view):
    assert make_create_view()._is_width_clamped()   # unchanged from prior work
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** wrap the existing form column and a new `CreateResultPanel` as the two children of a `Gtk.FlowBox` (`min_children_per_line=1`, `max_children_per_line=2`, `selection_mode=NONE`, homogeneous off) so they sit side-by-side when wide and stack when narrow; keep the whole thing inside the existing `wrap_centered` clamp. Add `_panes_wrap()`/`_result_panel`. Preserve all existing form behavior and the door/chip/dropdown/CTA tests.
- [ ] **Step 4:** `pytest tests/test_create_view.py tests/test_create_result_panel.py -q` → PASS.
- [ ] **Step 5:** commit `feat(create): two-pane responsive layout (form + live result panel)`.

---

### Task 3: Wire the native generation lifecycle to the panel

**Files:** Modify `app/main_window.py`; Test `tests/test_main_window_create_generate.py` (extend).

**Interfaces — Consumes:** `self._create_view._result_panel`. **Produces:** Create-originated native jobs drive the panel; the gallery pending card is skipped for them; persistence unchanged.

- [ ] **Step 1: failing tests** (extend the existing harness that already tests `_on_create_generate`/`_on_generate` routing)
```python
def test_create_native_job_shows_pending_in_panel(mw):
    # a Create image job calls the result panel's show_pending and marks the job
    mw._create_view = _fake_create_view_with_panel()   # panel records calls
    mw._on_create_generate(_image_medium(), {"prompt":"x", ...})
    assert mw._create_view._result_panel.calls[0][0] == "show_pending"
    assert mw._create_job_active is True
def test_finished_forwards_to_panel_and_still_hits_store(mw):
    # simulate _on_finished for a Create-originated job
    mw._create_job_active = True
    mw._on_finished(_fake_record())
    assert ("show_finished", ) == tuple(c[0] for c in mw._create_view._result_panel.calls[-1:])
    # persistence path still ran (gallery.replace_pending_with / store add) — assert as today
def test_non_create_job_does_not_touch_panel(mw):
    mw._create_job_active = False
    mw._on_finished(_fake_record())
    assert mw._create_view._result_panel.calls == []   # untouched
def test_create_job_skips_gallery_pending_card(mw):
    # _on_generate for a Create-originated job does not add a gallery pending card
    ...
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** add `self._create_job_active = False` init. In `_on_create_generate`, set it True and call `self._create_view._result_panel.show_pending(prompt, medium)` (wrap in try/except so a panel error can't block generation). In `_on_generate`, when `self._create_job_active`, skip `self._gen_gallery.add_pending_card(...)` (still set `_gen_gallery` so the finished record lands in the gallery/store). In `_on_progress`/`_on_finished`/`_on_error`, when `self._create_job_active`, also call the panel's `show_progress`/`show_finished`/`show_error`; clear `_create_job_active` in `_on_finished`/`_on_error`. Persistence (store add, gallery record) stays exactly as today.
- [ ] **Step 4:** `pytest tests/test_main_window_create_generate.py -q` → PASS.
- [ ] **Step 5:** commit `feat(create): native Create jobs surface in the inline result panel`.

---

### Task 4: Wire the artgen (`tt-ctl`) Create path to the panel

**Files:** Modify `app/main_window.py` (`_create_generate_artgen`); Test `tests/test_main_window_create_generate.py` (extend).

**Interfaces:** the artgen path runs a subprocess in a thread and records to the media store (ArtgenPanel pattern). It must also drive the panel: `show_pending` before, `show_finished(record)` on success (using the media-store record it creates), `show_error` on failure — all via `GLib.idle_add`.

- [ ] **Step 1: failing tests**
```python
def test_artgen_create_shows_pending_then_finished(mw, monkeypatch):
    # stub tt-ctl to "succeed" and produce a fake artgen record; assert the
    # panel saw show_pending then show_finished, and the media store got the record
    ...
def test_artgen_create_failure_shows_error_in_panel(mw, monkeypatch):
    ...
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** in `_create_generate_artgen`, call `show_pending` on entry; in the worker thread's completion, `GLib.idle_add(self._create_view._result_panel.show_finished, record)` on success (the record it already builds for the Artgen gallery) and `GLib.idle_add(..._result_panel.show_error, msg)` on failure. Keep the existing media-store write + Artgen-gallery insertion (persistence unchanged). Wrap panel calls so a panel error can't crash the subprocess handler.
- [ ] **Step 4:** `pytest tests/test_main_window_create_generate.py -q` → PASS.
- [ ] **Step 5:** commit `feat(create): artgen Create jobs surface in the inline result panel`.

---

### Task 5: Version, changelog, CLAUDE.md

**Files:** `VERSION`, `debian/changelog`, `CLAUDE.md`.

- [ ] **Step 1:** `VERSION` → `0.31.0` (new user-visible feature).
- [ ] **Step 2:** prepend a `debian/changelog` 0.31.0 stanza: Create now shows results in place — a live pending state beside the form resolving to the finished artifact the instant it's done, plus a session recents strip; results still save to history/Discover; non-Create jobs unaffected.
- [ ] **Step 3:** extend CLAUDE.md's "Create surface" section: `CreateResultPanel`, the two-pane responsive layout, and the `_create_job_active` wiring (native + artgen paths) with the persistence invariant.
- [ ] **Step 4:** full suite green (deselect the known flake).
- [ ] **Step 5:** commit `chore: release v0.31.0 -- in-place Create results`.

---

## Notes for the executor
- Persistence invariant is paramount: every Create result must still reach the store + Discover gallery. The panel is additive.
- Non-Create jobs must be provably untouched (Task 3's `test_non_create_job_does_not_touch_panel`).
- Reuse `GenerationCard`'s video-loop / lazy-stream logic where practical rather than reinventing; a static poster is an acceptable v1 for video.
