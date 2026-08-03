# "Video is Video" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Condense the Video / Animate / AnimateDiff medium trio into one "Video" medium whose model is chosen in a benefit-advertising picker, with AnimateDiff as the default when no server is running.

**Architecture:** AnimateDiff and Animate stop being top-level medium chips and become *models inside Video*. A new `benefit_for(key)` / `display_name_for(key)` seam in `server_manager` feeds friendly names + benefit taglines to both picker surfaces (scoped dropdown + Model door). `_native_generate_args`'s video branch routes the `animate` model to `AnimateGenerationWorker`; a reveal-on-demand section surfaces Animate's motion-video / character-image inputs only when that model is picked.

**Tech Stack:** Python 3 (system `/usr/bin/python3`), GTK4/PyGObject, pytest (`xvfb-run --auto-servernum`).

**Spec:** `docs/superpowers/specs/2026-08-03-video-is-video-design.md`

## Global Constraints

- **`collect()` byte-compatibility** — the params dict generation consumes must stay byte-identical for every still-existing path; the picker/reveal additions are decoration, never the value-bearing widget `collect()` reads.
- **GTK single-thread** — all widget work on the main thread; file dialogs use the async `open_finish` pattern; any off-thread result posts via `GLib.idle_add`.
- **`_CSS` in `create_view.py`/`main_window.py` is a `b"""..."""` ASCII-only bytes literal** — every glyph (dots ◌◐●, icons) lives in a Python `str` label, never inside the CSS bytes literal.
- **Palette** — tt-vscode-toolkit variant (`#4FD1C5` / `#0F2A35`), unchanged.
- **Do not touch worker internals** — `GenerationWorker` / `AnimateGenerationWorker` / `AnimateDiffGenerationWorker` and `_on_generate`'s worker-selection body stay as-is; routing changes live in `_native_generate_args` only.
- **Raw labels/keys unchanged** — `ServerDef.label`, `SERVERS` keys, and `ModelStatusService` behavior are untouched; friendly names are picker-only.
- **Local commits only** — do not push. Version discipline: minor bump + `debian/changelog` + CLAUDE.md note in the final task.
- **Tests:** `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q`. Two known env flakes are deselected in full-suite runs: `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`, `tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`.

---

### Task 1: `benefit_for` / `display_name_for` seam in `server_manager`

**Files:**
- Modify: `app/server_manager.py` (ServerDef dataclass ~46-79; SERVERS video-family entries ~106-169; add module tables + helpers after `CAPABILITY_LABELS` ~92)
- Test: `tests/test_server_manager_benefits.py` (new)

**Interfaces:**
- Produces: `ServerDef.benefit: str = ""` (new field, defaulted → every existing literal unaffected); `benefit_for(key: str) -> str`; `display_name_for(key: str) -> str`. Consumed by Task 4 (picker rendering).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server_manager_benefits.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import server_manager as sm


def test_benefit_for_reads_serverdef_field():
    assert "server" in sm.benefit_for("wan2.2").lower()
    assert sm.benefit_for("skyreels")           # non-empty
    assert sm.benefit_for("animate")            # non-empty


def test_benefit_for_synthetic_animatediff_from_fallback_table():
    # animatediff has NO ServerDef — must come from MODEL_BENEFITS
    assert "animatediff" not in sm.SERVERS
    assert "local" in sm.benefit_for("animatediff").lower()


def test_benefit_for_unknown_key_is_empty_string():
    assert sm.benefit_for("no-such-model") == ""


def test_display_name_for_friendly_and_fallback():
    assert sm.display_name_for("wan2.2") == "Wan 2.2"
    assert sm.display_name_for("animatediff") == "AnimateDiff"
    assert sm.display_name_for("animate") == "Animate"
    # unknown key falls back to the raw ServerDef label if present, else the key
    assert sm.display_name_for("flux") == sm.SERVERS["flux"].label
    assert sm.display_name_for("no-such-model") == "no-such-model"


def test_benefit_field_defaults_empty_for_untouched_serverdef():
    assert sm.SERVERS["flux"].benefit == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_server_manager_benefits.py -q`
Expected: FAIL (`AttributeError: module 'server_manager' has no attribute 'benefit_for'` / no `benefit` field).

- [ ] **Step 3: Add the `benefit` field to `ServerDef`**

In the `ServerDef` dataclass (after `model_id: Optional[str] = None`, ~line 79):

```python
    benefit: str = ""  # picker-only "what is this good for" tagline; "" = none
```

- [ ] **Step 4: Populate `benefit` on the video-family SERVERS entries**

Add `benefit=...` to these four `ServerDef(...)` literals (keep every other field exactly as-is):

- `wan2.2` (~106): `benefit="Highest-quality 720p text-to-video. Needs its server running."`
- `mochi` (~114): `benefit="Cinematic text-to-video. Needs its server running."`
- `animate` (~154): `benefit="Bring a character image to life with a motion video."`
- `skyreels` (~162): `benefit="Fast video from a seed image (image-to-video). Blackhole."`

- [ ] **Step 5: Add the fallback tables + helpers after `CAPABILITY_LABELS` (~line 92)**

```python
# Picker-only friendly names + benefit taglines for keys that have no
# ServerDef (the synthetic "animatediff" video model), and friendly display
# overrides for keys whose raw ServerDef.label is an implementation string.
# These are read ONLY by the Create model picker (create_view) — logs, the
# Servers control, and ModelStatusService all key off the raw label/key.
MODEL_BENEFITS: dict = {
    "animatediff": "Runs locally on Blackhole — no server to start. Quick looping animation.",
}
MODEL_DISPLAY_NAMES: dict = {
    "animatediff": "AnimateDiff",
    "wan2.2": "Wan 2.2",
    "mochi": "Mochi",
    "skyreels": "SkyReels",
    "animate": "Animate",
}


def benefit_for(key: str) -> str:
    """Human 'what is this good for' tagline for a model/server key.
    ServerDef.benefit wins; falls back to MODEL_BENEFITS; '' if unknown."""
    sdef = SERVERS.get(key)
    if sdef is not None and sdef.benefit:
        return sdef.benefit
    return MODEL_BENEFITS.get(key, "")


def display_name_for(key: str) -> str:
    """Friendly picker name for a model/server key. MODEL_DISPLAY_NAMES wins;
    falls back to the raw ServerDef.label, then the bare key."""
    if key in MODEL_DISPLAY_NAMES:
        return MODEL_DISPLAY_NAMES[key]
    sdef = SERVERS.get(key)
    return sdef.label if sdef is not None else str(key)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_server_manager_benefits.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/server_manager.py tests/test_server_manager_benefits.py
git commit -m "feat(create): add benefit_for/display_name_for model-picker seam"
```

---

### Task 2: Drop the Animate + AnimateDiff medium chips

**Files:**
- Modify: `app/create_mediums.py` (`_NATIVE_MEDIUMS` ~82-89; `discover_mediums` artgen loop ~201-218)
- Test: `tests/test_create_mediums.py` (add cases; file exists)

**Interfaces:**
- Consumes: nothing new.
- Produces: `default_mediums()` no longer contains an `animate` or `animatediff` Medium; still contains `image`, `video`, and every other artgen kind.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_create_mediums.py
import create_mediums as cm

def test_animate_and_animatediff_are_not_mediums():
    ids = {m.id for m in cm.default_mediums()}
    assert "video" in ids and "image" in ids
    assert "animate" not in ids          # folded into Video as a model
    assert "animatediff" not in ids      # folded into Video as a model
    # other artgen kinds untouched
    assert "verse" in ids and "ansi" in ids and "palette" in ids

def test_discover_mediums_filters_animatediff_name():
    ms = cm.discover_mediums(artgen_names=["verse", "animatediff", "ansi"])
    ids = [m.id for m in ms]
    assert "animatediff" not in ids
    assert ids[:1] == ["image"]          # native still first
    assert "verse" in ids and "ansi" in ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_mediums.py -q`
Expected: FAIL (`animate`/`animatediff` still present).

- [ ] **Step 3: Remove the native `animate` medium**

In `_NATIVE_MEDIUMS` (~82-89), delete the third entry:

```python
_NATIVE_MEDIUMS: tuple[Medium, ...] = (
    Medium(id="image", label="Image", icon="🖼️", kind="image",
           source="native", generator=None),
    Medium(id="video", label="Video", icon="🎥", kind="video",
           source="native", generator=None),
)
```

- [ ] **Step 4: Filter `animatediff` out of the discovered artgen list**

In `discover_mediums`'s `for name in names:` loop (~201), add a skip right after `key = str(name)`:

```python
            key = str(name)
            if key == "animatediff":
                # Folded into the Video medium as a model (see the
                # 'Video is Video' spec) — no longer its own chip.
                continue
```

Leave `_ARTGEN_LABELS_ICONS` / `_ARTGEN_KIND`'s `"animatediff"` rows in place (harmless; still used by the artgen gallery for existing records).

- [ ] **Step 5: Run test to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_mediums.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/create_mediums.py tests/test_create_mediums.py
git commit -m "feat(create): drop Animate + AnimateDiff chips (now Video models)"
```

---

### Task 3: Video model list includes `animate`; make it resolvable + routable

**Files:**
- Modify: `app/create_view.py` (`_scoped_model_keys` video branch ~1770-1771)
- Modify: `app/create_param_panels.py` (`_VIDEO_MODEL_IDS` ~815) and `app/main_window.py` (`_VIDEO_MODEL_IDS` ~1649) — add the `animate` canonical mapping to BOTH copies
- Test: `tests/test_create_view_video_models.py` (new)

**Interfaces:**
- Consumes: `_canonical_model_id_for` (unchanged — resolves via `_VIDEO_MODEL_IDS`).
- Produces: `_scoped_model_keys(video)` → `["animatediff", "wan2.2", "mochi", "skyreels", "animate"]`; `_canonical_model_id_for(video, "animate")` → `"wan2.2-animate-14b"`; `_VIDEO_MODEL_ID_TO_KEY["wan2.2-animate-14b"] == "animate"` (used by Task 7 routing).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_create_view_video_models.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pytest
try:
    import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display", allow_module_level=True)

import create_view as cv
import create_param_panels as cpp
from create_mediums import Medium

VIDEO = Medium(id="video", label="Video", icon="🎥", kind="video", source="native", generator=None)

def test_video_scoped_keys_animatediff_first_and_animate_present(monkeypatch):
    view = cv.CreateView.__new__(cv.CreateView)
    view._active_medium = VIDEO
    view._status_service = None
    keys = view._scoped_model_keys(VIDEO)
    assert keys == ["animatediff", "wan2.2", "mochi", "skyreels", "animate"]

def test_animate_canonical_resolves_for_video():
    assert cv._canonical_model_id_for(VIDEO, "animate") == "wan2.2-animate-14b"

def test_video_model_id_to_key_inverts_animate():
    import main_window as mw
    assert mw._VIDEO_MODEL_ID_TO_KEY["wan2.2-animate-14b"] == "animate"
    assert cpp._VIDEO_MODEL_IDS["animate"] == "wan2.2-animate-14b"
```

Note: the scoped-keys test builds `CreateView` via `__new__` and sets only `_active_medium`/`_status_service` — `_scoped_model_keys` touches nothing else for the video branch. If `servers_for_capability` ordering differs, assert membership + `keys[0] == "animatediff"` and `keys[-1] == "animate"` instead of exact equality.

- [ ] **Step 2: Run test to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_view_video_models.py -q`
Expected: FAIL (`animatediff` appended last, not first; `animate` absent; canonical `None`).

- [ ] **Step 3: Reorder + extend the video scoped keys**

In `_scoped_model_keys` (~1768-1771), replace the `if medium.id == "video":` block:

```python
        cap = "artgen" if medium.source == "artgen" else medium.id
        keys = [sdef.key for sdef in server_manager.servers_for_capability(cap)]
        if medium.id == "video":
            # AnimateDiff (local, no server) leads — always-ready + the
            # index-0 auto-select fallback. Animate (Wan2.2-Animate) is a
            # real server keyed ("animate",), so servers_for_capability("video")
            # won't return it; append it as a Video model by hand.
            keys = ["animatediff"] + keys + ["animate"]
```

- [ ] **Step 4: Add the `animate` canonical mapping to both `_VIDEO_MODEL_IDS` copies**

In `app/create_param_panels.py` `_VIDEO_MODEL_IDS` (~815) add a trailing entry (do NOT add it to `_VIDEO_MODEL_CHOICES` — the panel's own hidden dropdown must not gain a row):

```python
    "animate": "wan2.2-animate-14b",   # Video-model routing only; not a panel choice
```

Apply the identical addition to `app/main_window.py`'s `_VIDEO_MODEL_IDS` (~1649) so `_VIDEO_MODEL_ID_TO_KEY` inverts it.

- [ ] **Step 5: Run test to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_view_video_models.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/create_view.py app/create_param_panels.py app/main_window.py tests/test_create_view_video_models.py
git commit -m "feat(create): Video lists animatediff-first + animate as models"
```

---

### Task 4: Picker advertises friendly names + benefits (dropdown factory + Model door)

**Files:**
- Modify: `app/create_view.py` — dropdown build (`_build_model_row` ~1700-1723 and `_populate_model_dropdown` label build ~1881-1913); Model door grouping (`_CAPABILITY_TO_MODEL_DOOR_GROUP` ~233, `_MODEL_DOOR_GROUP_ORDER` ~244, `_build_model_door` card build ~1400-1470)
- Test: `tests/test_create_view_picker_benefits.py` (new)

**Interfaces:**
- Consumes: `server_manager.display_name_for`, `server_manager.benefit_for` (Task 1); `_model_dropdown_entries` (unchanged 3-tuple shape `(key, canonical, label)`).
- Produces: dropdown rows show friendly name + dimmed benefit; Model door files `animate` under the **Video** group; door cards show benefit subtitles.

**Design note (dropdown factory):** the dropdown is today a plain `Gtk.DropDown` fed `Gtk.StringList.new(labels)`. To show a two-line row, attach a `Gtk.SignalListItemFactory` that binds each row from a parallel `self._model_row_meta` list (built alongside `entries`) holding `(friendly_name, benefit, dot_glyph)`. Keep `_model_dropdown_entries` and `_selected_model_key()` exactly as they are (selection still keys off `entries`). The selected/collapsed display may show the friendly name alone; the benefit line must be visible in the popped-open list.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_create_view_picker_benefits.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pytest
try:
    import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display", allow_module_level=True)

import create_view as cv
from create_mediums import Medium

def test_animate_server_files_under_video_group():
    assert cv._CAPABILITY_TO_MODEL_DOOR_GROUP["animate"] == "Video"
    assert "Animate" not in cv._MODEL_DOOR_GROUP_ORDER

def test_row_meta_carries_friendly_name_and_benefit():
    view = cv.CreateView.__new__(cv.CreateView)
    view._active_medium = Medium(id="video", label="Video", icon="🎥",
                                 kind="video", source="native", generator=None)
    view._status_service = None
    view._model_health = {}
    # build a minimal dropdown widget the populate needs
    view._model_dropdown = Gtk.DropDown()
    view._populate_model_dropdown(view._active_medium)
    meta = view._model_row_meta
    names = [m[0] for m in meta]
    assert "AnimateDiff" in names and "Wan 2.2" in names and "Animate" in names
    # animatediff row carries its benefit
    ad = next(m for m in meta if m[0] == "AnimateDiff")
    assert "local" in ad[1].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_view_picker_benefits.py -q`
Expected: FAIL (`Animate` still in door order; no `_model_row_meta`).

- [ ] **Step 3: Refile the Animate server under Video in the door**

```python
_CAPABILITY_TO_MODEL_DOOR_GROUP: "dict[str, str]" = {
    "image": "Image",
    "video": "Video",
    "animate": "Video",   # Wan2.2-Animate is a Video model now
    "artgen": "Text",
    "prompt": "Text",
}
```
and drop `"Animate"` from `_MODEL_DOOR_GROUP_ORDER`:
```python
_MODEL_DOOR_GROUP_ORDER: "tuple[str, ...]" = ("Image", "Video", "Text")
```

- [ ] **Step 4: Build `_model_row_meta` alongside `entries` in `_populate_model_dropdown`**

Where each label is appended today (the three `labels.append(...)` / `entries.append(...)` sites, ~1882/1895/1912), also append a `(friendly, benefit, dot)` tuple to a new `row_meta` list. Compute:
- `friendly = server_manager.display_name_for(key)` for real/synthetic keys; for the detected sentinel keep `f"{_detected_key_model_id(key)} (detected)"`; for the llm-free artgen self-entry keep `medium.label`.
- `benefit = server_manager.benefit_for(key)` (real/synthetic); `""` for the sentinel/self-entry.
- `dot = self._model_dot_glyph(key, medium=medium)` (same call already used).

Before `self._model_dropdown_entries = entries`, add `self._model_row_meta = row_meta`. Keep the `labels`/`StringList` fallback path (`if not entries:`) but also set `self._model_row_meta = [("No models available", "", "")]` there.

- [ ] **Step 5: Attach a two-line factory to the dropdown**

In `_build_model_row` (~1700), after constructing `self._model_dropdown`, install a `Gtk.SignalListItemFactory` whose `setup` builds a vertical `Gtk.Box` (a name `Gtk.Label` + a dimmed benefit `Gtk.Label` with CSS class `model-row-benefit`), and whose `bind` reads `self._model_row_meta[list_item.get_position()]` to set `f"{dot} {name}"` and the benefit (hide the benefit label when empty). Add a `.model-row-benefit { font-size: 0.85em; opacity: 0.7; }` rule to the `_CSS` bytes literal (ASCII only — no glyphs).

- [ ] **Step 6: Add benefit subtitles to Model-door cards**

In `_build_model_door` card construction (~1400-1470), where each model card's label is set from the server label, use `server_manager.display_name_for(key)` for the title and append a dimmed `benefit_for(key)` subtitle label (skip when empty). Reuse the same `model-row-benefit` CSS class.

- [ ] **Step 7: Run test + regression suites**

Run:
```
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_view_picker_benefits.py tests/test_create_view_model_door.py tests/test_create_view.py -q
```
Expected: PASS (new test green; existing door/model-dropdown tests unbroken — update any that asserted the raw `sdef.label` string or the "Animate" door group to the new friendly-name/group values).

- [ ] **Step 8: Commit**

```bash
git add app/create_view.py tests/test_create_view_picker_benefits.py
git commit -m "feat(create): picker shows friendly names + benefit taglines; Animate under Video"
```

---

### Task 5: Default to AnimateDiff when no server is running

**Files:**
- Modify: `app/create_view.py` (`_autoselect_running_model_index` ~1943-1990)
- Test: `tests/test_create_view_default_model.py` (new)

**Interfaces:**
- Consumes: `_status_service.running_or_starting(cap)`; the video scoped entries (Task 3).
- Produces: with nothing running, the video medium auto-selects the `animatediff` entry; a running video **or** animate server is preferred; same-medium refresh still preserves a manual pick (untouched — this method is only called on fresh populate).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_create_view_default_model.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pytest
try:
    import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display", allow_module_level=True)

import create_view as cv
from create_mediums import Medium

VIDEO = Medium(id="video", label="Video", icon="🎥", kind="video", source="native", generator=None)
ENTRIES = [("animatediff", "animatediff-blackhole", "AnimateDiff"),
           ("wan2.2", "wan2.2-t2v", "Wan 2.2"),
           ("mochi", "mochi-1-preview", "Mochi"),
           ("skyreels", "skyreels-v2-i2v-14b-540p", "SkyReels"),
           ("animate", "wan2.2-animate-14b", "Animate")]

class _Status:
    def __init__(self, mapping): self.mapping = mapping  # cap -> key or None
    def running_or_starting(self, cap): return self.mapping.get(cap)

def _view(status):
    v = cv.CreateView.__new__(cv.CreateView)
    v._status_service = status
    return v

def test_nothing_running_defaults_to_animatediff():
    v = _view(_Status({}))
    assert ENTRIES[v._autoselect_running_model_index(VIDEO, ENTRIES)][0] == "animatediff"

def test_running_video_server_preferred():
    v = _view(_Status({"video": "mochi"}))
    assert ENTRIES[v._autoselect_running_model_index(VIDEO, ENTRIES)][0] == "mochi"

def test_running_animate_server_preferred():
    v = _view(_Status({"animate": "animate"}))
    assert ENTRIES[v._autoselect_running_model_index(VIDEO, ENTRIES)][0] == "animate"

def test_no_status_service_returns_zero():
    v = _view(None)
    assert v._autoselect_running_model_index(VIDEO, ENTRIES) == 0  # index 0 == animatediff
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_view_default_model.py -q`
Expected: FAIL (`test_running_animate_server_preferred` — the video cap lookup doesn't consult `"animate"`).

- [ ] **Step 3: Consult both video + animate caps for the video medium**

In `_autoselect_running_model_index`, after computing `cap` (~1976) and before the `running_key` lookup, special-case video to try both capabilities:

```python
        # 'Video is Video': the video medium's models span two capabilities
        # (video servers + the one animate server). Prefer whichever is
        # actually running; only then fall through to the index-0 default
        # (AnimateDiff — the local no-server model that always works).
        caps = ("video", "animate") if medium.id == "video" else (cap,)
        running_key = None
        for c in caps:
            if c is None:
                continue
            try:
                rk = self._status_service.running_or_starting(c)
            except Exception:
                rk = None
            if rk is not None:
                running_key = rk
                break
```

Replace the existing single `running_key = self._status_service.running_or_starting(cap)` try/except block with the loop above. Keep the artgen `_detected_model_key()` fallback and the `for idx, entry ...` match/return-0 tail unchanged. (With `animatediff` at index 0, "nothing running" → the existing `return 0` yields AnimateDiff — no extra code needed.)

- [ ] **Step 4: Run test to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_view_default_model.py tests/test_create_view.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/create_view.py tests/test_create_view_default_model.py
git commit -m "feat(create): default to AnimateDiff when no video/animate server runs"
```

---

### Task 6: Reveal-on-demand Animate inputs in the Video form

**Files:**
- Modify: `app/create_view.py` — build a reveal section under the model row (in `_build_model_row` or the form assembly); toggle it in `_on_scoped_model_dropdown_changed`/`_sync_panel_model_selection`; fold its values into `_collect_params` (~2319+)
- Reuse: the path-picker + mode-toggle building blocks from `create_param_panels.AnimateParamPanel` (~1461-1685) — extract them into small reusable helpers rather than duplicating
- Test: `tests/test_create_view_animate_reveal.py` (new)

**Interfaces:**
- Consumes: `_selected_model_key()` (Task 3), the async file-dialog pattern already in the surface.
- Produces: when the selected video model is `animate`, `_collect_params()` includes `reference_video_path`, `reference_image_path`, `animate_mode`; for any other model those keys are absent (or empty) so `collect()` is unchanged. The reveal widget is visible only for `animate`.

**Design note:** build a small `_AnimateExtras` GTK widget exposing `.collect() -> {"reference_video_path": str, "reference_image_path": str, "animate_mode": str}` and `.set_visible(bool)`. To avoid drift with the retiring `AnimateParamPanel`, factor the two path-picker rows + the animation/replacement toggle into module helpers in `create_param_panels.py` and build `_AnimateExtras` from them (AnimateParamPanel keeps working for any residual reference, but the Video form uses the shared helpers). Keep it OFF the wrapped `RoleZonePanel` — it is CreateView-owned chrome folded into params exactly like `_prompt_entry`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_create_view_animate_reveal.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pytest
try:
    import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk
    Gtk.Entry()
except Exception:
    pytest.skip("no GTK display", allow_module_level=True)

import create_view as cv

def test_animate_extras_collect_shape():
    ex = cv._AnimateExtras()
    ex.set_paths("/m.mp4", "/c.png")   # test helper to set without a dialog
    ex.set_mode("replacement")
    got = ex.collect()
    assert got == {"reference_video_path": "/m.mp4",
                   "reference_image_path": "/c.png",
                   "animate_mode": "replacement"}

def test_reveal_visible_only_for_animate():
    # visibility helper is pure logic on the selected key
    assert cv._animate_extras_visible_for("animate") is True
    assert cv._animate_extras_visible_for("wan2.2") is False
    assert cv._animate_extras_visible_for("animatediff") is False
    assert cv._animate_extras_visible_for(None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_view_animate_reveal.py -q`
Expected: FAIL (`_AnimateExtras` / `_animate_extras_visible_for` don't exist).

- [ ] **Step 3: Add the pure visibility helper + the `_AnimateExtras` widget**

Module-level in `create_view.py`:

```python
def _animate_extras_visible_for(model_key) -> bool:
    """The Animate-needs section shows only when the Animate model is picked."""
    return model_key == "animate"
```

`_AnimateExtras(Gtk.Box)`: two path-picker rows (Motion video, Character image) + an animation/replacement toggle, built from the shared `create_param_panels` helpers. Expose `collect()` (the dict above), `set_paths(v, c)` / `set_mode(m)` test seams, and start hidden.

- [ ] **Step 4: Mount + toggle the section**

Mount `self._animate_extras = _AnimateExtras()` directly under the model row, `set_visible(False)` initially. In `_on_scoped_model_dropdown_changed` (and once at populate time), call `self._animate_extras.set_visible(_animate_extras_visible_for(self._selected_model_key()))`.

- [ ] **Step 5: Fold values into `_collect_params`**

In `_collect_params` (~2319), after the existing prompt/model overrides, when `_animate_extras_visible_for(self._selected_model_key())` is True, merge `self._animate_extras.collect()` into `params`. **Guard:** only merge for the animate model — for every other model `params` is byte-identical to today (pinned by Step 6's collect-equality test).

- [ ] **Step 6: Add a collect-equality regression test**

```python
# append to tests/test_create_view_animate_reveal.py
def test_collect_params_unchanged_for_non_animate(monkeypatch):
    # A video job with a non-animate model must not gain animate keys.
    view = cv.CreateView.__new__(cv.CreateView)
    view._animate_extras = cv._AnimateExtras()
    view._animate_extras.set_paths("/should-not-leak.mp4", "/x.png")
    monkeypatch.setattr(view, "_selected_model_key", lambda: "wan2.2")
    # minimal params source: fake active panel returning a base dict
    class _P:
        def collect(self): return {"prompt": "p", "model": "wan2.2-t2v"}
        def applied_modifier_text(self): return ""
    view._active_panel = _P()
    view._prompt_entry = None  # exercise the no-prompt-entry branch if present
    params = view._collect_params()
    assert "reference_video_path" not in params
    assert "reference_image_path" not in params
```
Adapt the fake to `_collect_params`'s real dependencies (read the method first; the point is: non-animate model → no animate keys leak).

- [ ] **Step 7: Run tests**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_view_animate_reveal.py tests/test_create_view.py tests/test_role_zone_panel.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/create_view.py app/create_param_panels.py tests/test_create_view_animate_reveal.py
git commit -m "feat(create): reveal Animate motion/character inputs when Animate model picked"
```

---

### Task 7: Route the Animate model to `AnimateGenerationWorker`

**Files:**
- Modify: `app/main_window.py` (`_native_generate_args` video branch ~9197-9263)
- Test: `tests/test_native_generate_args.py` (add cases; file exists — else new)

**Interfaces:**
- Consumes: `params["model"]` (canonical id), `_VIDEO_MODEL_ID_TO_KEY` (Task 3), the reveal-section params (Task 6).
- Produces: `_native_generate_args(video, params)` returns `model_source=="animate"` (+ ref paths + mode) when the chosen model is `animate`; `model_source=="video"` + `video_model_key=="animatediff"` for animatediff; plain `model_source=="video"` otherwise — all without running a worker.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_native_generate_args.py  (add; skip via GTK guard as siblings do)
def _mw():
    import main_window as mw
    from unittest.mock import patch
    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        return mw.MainWindow.__new__(mw.MainWindow)

VIDEO = __import__("create_mediums").Medium(
    id="video", label="Video", icon="🎥", kind="video", source="native", generator=None)

def test_video_animate_model_routes_to_animate_source():
    obj = _mw()
    params = {"prompt": "hi", "model": "wan2.2-animate-14b",
              "reference_video_path": "/m.mp4", "reference_image_path": "/c.png",
              "animate_mode": "replacement"}
    _args, kwargs = obj._native_generate_args(VIDEO, params)
    assert kwargs["model_source"] == "animate"
    assert kwargs["ref_video_path"] == "/m.mp4"
    assert kwargs["ref_char_path"] == "/c.png"
    assert kwargs["animate_mode"] == "replacement"

def test_video_animatediff_model_unchanged():
    obj = _mw()
    _a, kw = obj._native_generate_args(VIDEO, {"prompt": "x", "model": "animatediff-blackhole"})
    assert kw["model_source"] == "video"
    assert kw["video_model_key"] == "animatediff"
    assert kw["animatediff_args"] is not None

def test_video_plain_model_unchanged():
    obj = _mw()
    _a, kw = obj._native_generate_args(VIDEO, {"prompt": "x", "model": "wan2.2-t2v"})
    assert kw["model_source"] == "video"
    assert kw["video_model_key"] == "wan2"
    assert kw["animatediff_args"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_native_generate_args.py -q`
Expected: FAIL (`model_source=="video"` for the animate model — no animate branch yet).

- [ ] **Step 3: Branch the video path on the resolved model key**

In `_native_generate_args`, right after `model_key = _VIDEO_MODEL_ID_TO_KEY.get(params.get("model", ""), "wan2")` (~9198), insert:

```python
        if model_key == "animate":
            # 'Video is Video': the Animate model reuses _on_generate's animate
            # branch (AnimateGenerationWorker). Its two inputs come from the
            # Video form's reveal-on-demand section (see create_view
            # _AnimateExtras), folded into params by _collect_params.
            args = (
                prompt, "",
                int(params.get("num_inference_steps", 20)),
                int(params.get("seed", -1)),
            )
            kwargs = dict(
                seed_image_path="",
                model_source="animate",
                ref_video_path=params.get("reference_video_path", ""),
                ref_char_path=params.get("reference_image_path", ""),
                animate_mode=params.get("animate_mode", "animation"),
            )
            return args, kwargs
```

Leave the SkyReels guard and the rest of the video branch below unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_native_generate_args.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main_window.py tests/test_native_generate_args.py
git commit -m "feat(create): route Video's Animate model to AnimateGenerationWorker"
```

---

### Task 8: Ready-to-Run verify + finalize (version, changelog, CLAUDE.md, full suite)

**Files:**
- Verify only: `app/ready_to_run.py` (`plan_switch`/`required_server`)
- Modify: `VERSION`, `debian/changelog`, `CLAUDE.md`
- Test: `tests/test_ready_to_run.py` (add two assertions)

**Interfaces:** none produced; this task closes the plan.

- [ ] **Step 1: Add ready-to-run assertions**

```python
# add to tests/test_ready_to_run.py
def test_animatediff_needs_no_server():
    import ready_to_run as rr
    plan = rr.plan_switch("animatediff", lambda k: "off")
    assert plan.target is None          # local, nothing to gate → runs immediately

def test_animate_maps_to_animate_server():
    import ready_to_run as rr
    assert rr.required_server("animate") == "animate"
```

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_ready_to_run.py -q`
Expected: PASS. If `required_server("animate")` returns `None`, teach `ready_to_run`'s media-group map that `animate` is a real server key (minimal, spec §9) and re-run.

- [ ] **Step 2: Version bump**

Edit `VERSION` → next minor (e.g. `0.60.4` → `0.61.0`). Prepend a `debian/changelog` stanza describing: Video absorbs Animate + AnimateDiff as models; benefit-advertising picker; AnimateDiff default when no server runs.

- [ ] **Step 3: Update CLAUDE.md**

Add a section under the Create-surface notes: "Video is Video" — the video trio is one medium; AnimateDiff/Animate are Video *models*; `benefit_for`/`display_name_for` seam; AnimateDiff is the no-server default; Animate's inputs reveal on-demand; routing via `_native_generate_args` (animate → `AnimateGenerationWorker`). Note the retired chips and that existing artgen-`animatediff` records still render in the artgen gallery.

- [ ] **Step 4: Full suite**

Run:
```
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q \
  --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module \
  --deselect tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add VERSION debian/changelog CLAUDE.md tests/test_ready_to_run.py
git commit -m "chore: v0.61.0 — Video absorbs Animate + AnimateDiff; benefit picker"
```

## Self-Review notes

- **Spec coverage:** §1 taxonomy → T2; §2 model list → T3; §3 benefit seam → T1; §4 friendly names → T1+T4; §5 picker render → T4; §6 reveal → T6; §7 default → T5; §8 routing → T7; §9 ready-to-run → T8. Covered.
- **Type consistency:** `benefit_for`/`display_name_for` (T1) used verbatim in T4; `_VIDEO_MODEL_IDS["animate"]="wan2.2-animate-14b"` (T3) consumed by T7 via `_VIDEO_MODEL_ID_TO_KEY`; `_AnimateExtras.collect()` keys (`reference_video_path`/`reference_image_path`/`animate_mode`, T6) consumed by T7's `params.get(...)`.
- **Ordering/risk:** T1→T2→T3 (data/plumbing, low risk) → T4 (picker render — read existing door/dropdown tests first, they will need friendly-name/group updates) → T5 (default) → T6 (highest risk: new form chrome + collect fold — the collect-equality test is the guard) → T7 (routing) → T8 (finalize). T4 and T6 are the two to watch.
