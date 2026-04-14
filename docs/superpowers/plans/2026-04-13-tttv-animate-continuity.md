# TT-TV Animate Continuity Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable TT-TV to auto-generate animate clips in a continuous chain where each clip's character reference is the last frame of the previous clip, using random bundled motion clips and random prompts.

**Architecture:** Four sequential changes — worker extracts last frame and stores its path in `extra_meta`, attractor accepts a `get_animate_inputs` callback and uses it in `_enqueue_generation`, MainWindow implements the callback using BundledClipScanner + history store, and requirements.txt documents ffmpeg as a system dep. The temporary `auto_generate=False` guard is replaced by the real implementation.

**Tech Stack:** Python 3.10+, ffmpeg (system), subprocess, pathlib, unittest.mock

---

## File Map

| File | Change |
|---|---|
| `app/worker.py` | Add `_extract_last_frame()` to `AnimateGenerationWorker`; call it in `run_with_callbacks`; update `_write_prompt_sidecar` |
| `app/attractor.py` | Add `get_animate_inputs` param to `AttractorWindow.__init__`; update `_enqueue_generation` |
| `app/main_window.py` | Add `_get_animate_inputs()` method; update `_on_open_attractor` |
| `requirements.txt` | Add ffmpeg comment |
| `tests/test_worker_animate.py` | Add last-frame extraction tests |
| `tests/test_attractor_animate.py` | New file — attractor animate branch tests |
| `tests/test_main_window_animate_inputs.py` | New file — `_get_animate_inputs` logic tests |

---

## Task 1: Worker — `_extract_last_frame` + sidecar

**Files:**
- Modify: `app/worker.py` (AnimateGenerationWorker, around line 424–474)
- Test: `tests/test_worker_animate.py`

### Background

`AnimateGenerationWorker._extract_thumbnail` (line 457) uses ffmpeg to grab the first frame:
```python
def _extract_thumbnail(self, video_path: str, thumbnail_path: str) -> None:
    Path(thumbnail_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-vframes", "1", "-q:v", "2", "-update", "1", thumbnail_path],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
```

`run_with_callbacks` calls it at step 5 (line 425), then `_write_prompt_sidecar` (line 428), then persists the record (line 431–432).

The `last_frame_path` is derived from `thumbnail_path` by inserting `_last` before `.jpg`:
```python
from pathlib import Path
last_frame_path = str(
    Path(record.thumbnail_path).with_stem(Path(record.thumbnail_path).stem + "_last")
)
# e.g. thumbnails/20260413_170522_abcd1234.jpg → thumbnails/20260413_170522_abcd1234_last.jpg
```

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_worker_animate.py`:

```python
from unittest.mock import patch, MagicMock, call
from pathlib import Path


def test_extract_last_frame_sets_extra_meta_on_success():
    """When ffmpeg succeeds, extra_meta['last_frame_path'] is set on the record."""
    client = MagicMock()
    client.submit_animate.return_value = "job-last0001"
    client.poll_status.return_value = ("completed", None, {})
    client.download.return_value = None

    store = MagicMock()
    finished_records = []

    worker = _make_worker(client, store)

    # Make the last-frame file appear to exist so Path.exists() check would pass
    with (
        patch.object(worker, "_extract_thumbnail"),
        patch.object(worker, "_write_prompt_sidecar"),
        patch("worker.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        worker.run_with_callbacks(
            on_progress=lambda msg: None,
            on_finished=lambda rec: finished_records.append(rec),
            on_error=lambda msg: None,
        )

    assert len(finished_records) == 1
    rec = finished_records[0]
    assert "last_frame_path" in rec.extra_meta
    assert rec.extra_meta["last_frame_path"].endswith("_last.jpg")


def test_extract_last_frame_skips_extra_meta_on_ffmpeg_failure():
    """When ffmpeg raises FileNotFoundError, extra_meta is not set."""
    client = MagicMock()
    client.submit_animate.return_value = "job-last0002"
    client.poll_status.return_value = ("completed", None, {})
    client.download.return_value = None

    store = MagicMock()
    finished_records = []

    worker = _make_worker(client, store)

    with (
        patch.object(worker, "_extract_thumbnail"),
        patch.object(worker, "_write_prompt_sidecar"),
        patch("worker.subprocess.run", side_effect=FileNotFoundError),
    ):
        worker.run_with_callbacks(
            on_progress=lambda msg: None,
            on_finished=lambda rec: finished_records.append(rec),
            on_error=lambda msg: None,
        )

    assert len(finished_records) == 1
    assert "last_frame_path" not in finished_records[0].extra_meta


def test_extract_last_frame_uses_sseof_flag():
    """ffmpeg is called with -sseof to seek from end, not -ss 0."""
    client = MagicMock()
    client.submit_animate.return_value = "job-last0003"
    client.poll_status.return_value = ("completed", None, {})
    client.download.return_value = None

    store = MagicMock()
    worker = _make_worker(client, store)

    captured_calls = []

    with (
        patch.object(worker, "_extract_thumbnail"),
        patch.object(worker, "_write_prompt_sidecar"),
        patch("worker.subprocess.run") as mock_run,
    ):
        mock_run.side_effect = lambda args, **kw: captured_calls.append(args) or MagicMock(returncode=0)
        worker.run_with_callbacks(
            on_progress=lambda msg: None,
            on_finished=lambda rec: None,
            on_error=lambda msg: None,
        )

    # At least one call must include -sseof
    assert any("-sseof" in args for args in captured_calls), \
        f"Expected -sseof in ffmpeg call, got: {captured_calls}"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /home/ttuser/code/tt-local-generator
python -m pytest tests/test_worker_animate.py::test_extract_last_frame_sets_extra_meta_on_success tests/test_worker_animate.py::test_extract_last_frame_skips_extra_meta_on_ffmpeg_failure tests/test_worker_animate.py::test_extract_last_frame_uses_sseof_flag -v 2>&1 | tail -20
```

Expected: 3 FAILs (AttributeError or method missing).

- [ ] **Step 3: Add `_extract_last_frame` to `AnimateGenerationWorker`**

In `app/worker.py`, add this method after `_extract_thumbnail` (after line 474):

```python
def _extract_last_frame(self, video_path: str, last_frame_path: str) -> bool:
    """Extract the last frame of the video as a JPEG via ffmpeg.

    Uses -sseof -0.1 to seek 0.1 s from the end.  Returns True on
    success, False on any ffmpeg error.  The caller decides whether
    to record the path.
    """
    Path(last_frame_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-sseof", "-0.1",
                "-i", video_path,
                "-vframes", "1",
                "-q:v", "2",
                "-update", "1",
                last_frame_path,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
```

- [ ] **Step 4: Call it in `run_with_callbacks` and update `_write_prompt_sidecar`**

In `run_with_callbacks`, replace the thumbnail step (line 424–425) with:

```python
        # ── 5. Thumbnail + last frame ──────────────────────────────────────────
        self._extract_thumbnail(record.video_path, record.thumbnail_path)

        last_frame_path = str(
            Path(record.thumbnail_path).with_stem(
                Path(record.thumbnail_path).stem + "_last"
            )
        )
        if self._extract_last_frame(record.video_path, last_frame_path):
            record.extra_meta["last_frame_path"] = last_frame_path
```

In `_write_prompt_sidecar`, add one line after `f"reference_image: {self._ref_image}"`:

```python
        lines = [
            f"mode: animate:{self._animate_mode}",
            f"prompt: {record.prompt}",
            f"reference_video: {self._ref_video}",
            f"reference_image: {self._ref_image}",
            f"last_frame: {record.extra_meta.get('last_frame_path', '')}",
            f"steps: {record.num_inference_steps}",
            f"seed: {record.seed}",
            f"generated: {record.created_at}",
            f"duration_s: {record.duration_s}",
            f"sec_per_step: {sec_per_step}",
        ]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_worker_animate.py -v 2>&1 | tail -20
```

Expected: all tests PASS (new 3 + existing 2 = 5 total).

- [ ] **Step 6: Commit**

```bash
git add app/worker.py tests/test_worker_animate.py
git commit -m "feat: extract last frame in AnimateGenerationWorker; store in extra_meta"
```

---

## Task 2: Attractor — `get_animate_inputs` param + animate branch in `_enqueue_generation`

**Files:**
- Modify: `app/attractor.py` (lines 409–426 for `__init__`; line 1226 for `_enqueue_generation`)
- Test: `tests/test_attractor_animate.py` (new file)

### Background

`AttractorWindow.__init__` signature ends at line 409–410:
```python
        get_playlists: "Callable[[], list]" = lambda: [],  # for channel switcher dropdown
    ) -> None:
```

And stores it at line 426:
```python
        self._get_playlists: Callable = get_playlists
```

`_enqueue_generation` at line 1226 currently calls `self._on_enqueue` with
`ref_video_path=""` and `ref_char_path=""` hardcoded.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_attractor_animate.py`:

```python
"""
Unit tests for AttractorWindow animate auto-generation branch.
No GTK display required — we only test the non-GTK _enqueue_generation logic
by subclassing and overriding the GTK parts.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _make_attractor(model_source="video", get_animate_inputs=None):
    """Build a minimal AttractorWindow with all GTK bypassed."""
    import attractor as att

    with patch("attractor.Gtk.ApplicationWindow.__init__", return_value=None), \
         patch.object(att.AttractorWindow, "_build", return_value=None), \
         patch.object(att.AttractorWindow, "maximize", return_value=None), \
         patch.object(att.AttractorWindow, "get_display", return_value=MagicMock()):
        win = att.AttractorWindow.__new__(att.AttractorWindow)
        win._alive = True
        win._model_source = model_source
        win._on_enqueue = MagicMock()
        win._get_animate_inputs = get_animate_inputs
    return win


def test_enqueue_generation_animate_calls_get_inputs():
    """When model_source=='animate', _enqueue_generation calls get_animate_inputs."""
    inputs_called = []

    def fake_inputs():
        inputs_called.append(True)
        return ("/tmp/motion.mp4", "/tmp/char.jpg")

    win = _make_attractor(model_source="animate", get_animate_inputs=fake_inputs)
    win._enqueue_generation("a prompt")

    assert inputs_called, "get_animate_inputs was not called"
    win._on_enqueue.assert_called_once()
    call_kwargs = win._on_enqueue.call_args[1]
    assert call_kwargs["ref_video_path"] == "/tmp/motion.mp4"
    assert call_kwargs["ref_char_path"] == "/tmp/char.jpg"
    assert call_kwargs["model_source"] == "animate"


def test_enqueue_generation_animate_skips_when_no_callback():
    """When get_animate_inputs is None, animate generation is skipped silently."""
    win = _make_attractor(model_source="animate", get_animate_inputs=None)
    win._enqueue_generation("a prompt")
    win._on_enqueue.assert_not_called()


def test_enqueue_generation_animate_skips_when_inputs_empty():
    """When get_animate_inputs returns ('', ''), no job is enqueued."""
    win = _make_attractor(model_source="animate", get_animate_inputs=lambda: ("", ""))
    win._enqueue_generation("a prompt")
    win._on_enqueue.assert_not_called()


def test_enqueue_generation_animate_skips_when_ref_video_empty():
    """Missing ref_video alone is enough to skip enqueueing."""
    win = _make_attractor(
        model_source="animate",
        get_animate_inputs=lambda: ("", "/tmp/char.jpg"),
    )
    win._enqueue_generation("a prompt")
    win._on_enqueue.assert_not_called()


def test_enqueue_generation_video_mode_unchanged():
    """For model_source=='video', _enqueue_generation works as before (no animate inputs)."""
    win = _make_attractor(model_source="video", get_animate_inputs=None)
    win._enqueue_generation("a video prompt")
    win._on_enqueue.assert_called_once()
    call_kwargs = win._on_enqueue.call_args[1]
    assert call_kwargs["ref_video_path"] == ""
    assert call_kwargs["ref_char_path"] == ""
    assert call_kwargs["model_source"] == "video"
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_attractor_animate.py -v 2>&1 | tail -20
```

Expected: FAILs — `_get_animate_inputs` attribute missing / wrong behaviour.

- [ ] **Step 3: Add `get_animate_inputs` param to `AttractorWindow.__init__`**

In `app/attractor.py`, change the end of `__init__`'s signature (around line 409) from:

```python
        get_playlists: "Callable[[], list]" = lambda: [],  # for channel switcher dropdown
    ) -> None:
```

to:

```python
        get_playlists: "Callable[[], list]" = lambda: [],  # for channel switcher dropdown
        get_animate_inputs: "Callable[[], tuple[str, str]] | None" = None,  # animate TT-TV inputs
    ) -> None:
```

And add the storage line after `self._get_playlists` (around line 426):

```python
        self._get_playlists: Callable = get_playlists
        self._get_animate_inputs = get_animate_inputs
```

- [ ] **Step 4: Update `_enqueue_generation`**

Replace the full `_enqueue_generation` method (line 1226) with:

```python
    def _enqueue_generation(self, prompt: str) -> None:
        """
        Called on the main thread via GLib.idle_add.
        Forwards a generation request to MainWindow via the on_enqueue callback.
        Uses the model defaults from the spec (steps=20, seed=-1, guidance=5.0).

        For animate mode, calls get_animate_inputs() to get (ref_video, ref_char).
        Skips silently if the callback is None or returns empty strings.
        """
        if not self._alive:
            return
        ref_video, ref_char = "", ""
        if self._model_source == "animate":
            if self._get_animate_inputs is None:
                return   # not wired — skip silently
            ref_video, ref_char = self._get_animate_inputs()
            if not ref_video or not ref_char:
                return   # no inputs available yet — skip this cycle
        self._on_enqueue(
            prompt=prompt,
            neg="",
            steps=20,
            seed=-1,
            seed_image_path="",
            model_source=self._model_source,
            guidance_scale=5.0,
            ref_video_path=ref_video,
            ref_char_path=ref_char,
            animate_mode="animation",
            model_id="",
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_attractor_animate.py tests/test_attractor.py -v 2>&1 | tail -20
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/attractor.py tests/test_attractor_animate.py
git commit -m "feat: attractor accepts get_animate_inputs callback; animate branch in _enqueue_generation"
```

---

## Task 3: MainWindow — `_get_animate_inputs` + wire into `_on_open_attractor`

**Files:**
- Modify: `app/main_window.py`
- Test: `tests/test_main_window_animate_inputs.py` (new file)

### Background

`BundledClipScanner` (in `app/animate_picker.py`) has a `scan()` method:
```python
scanner = BundledClipScanner("/path/to/motion_clips")
data = scanner.scan()
# Returns: {"locomotion": [{"name": "walk_forward", "mp4": "/abs/path.mp4", "thumb": "..."}, ...], ...}
```

To get all clips as a flat list of mp4 path strings:
```python
import random
from animate_picker import BundledClipScanner
from app_settings import settings as _settings

clips_dir = _settings.get("motion_clips_dir")
all_clips = [
    clip["mp4"]
    for clips in BundledClipScanner(clips_dir).scan().values()
    for clip in clips
]
ref_video = random.choice(all_clips) if all_clips else ""
```

`_settings.get("motion_clips_dir")` returns the configured path (defaults to
`app/assets/motion_clips` relative to the install prefix).

For `ref_char`, `self._store.all_records()` returns all `GenerationRecord` objects
newest-first. `r.media_type` is `"animate"` for animate records, `"image"` for FLUX.
`r.extra_meta` is a dict. `r.thumbnail_path` and `r.image_path` are string paths.

`_on_open_attractor` in `main_window.py` is around line 6837. The temporary
animate guard to remove is at lines 6868–6873:
```python
        # Animate mode auto-generation is not yet implemented...
        current_source = self._controls.get_model_source()
        if current_source == "animate":
            auto_generate = False
```
After this change, `current_source` is still needed for the new
`get_animate_inputs` kwarg — keep the assignment, just remove the guard.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_main_window_animate_inputs.py`:

```python
"""
Unit tests for MainWindow._get_animate_inputs().
Tests the pure logic only — no GTK, no real BundledClipScanner I/O.
"""
import sys
import random
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _make_record(media_type="animate", thumb="", image_path="", extra_meta=None):
    r = MagicMock()
    r.media_type = media_type
    r.thumbnail_path = thumb
    r.image_path = image_path
    r.extra_meta = extra_meta if extra_meta is not None else {}
    return r


def _make_mw_with_store(records, clips=None):
    """
    Build a minimal fake MainWindow with _store and _controls mocked.
    Binds _get_animate_inputs from the real MainWindow class without GTK init.
    """
    import main_window as mw

    obj = object.__new__(mw.MainWindow)
    store = MagicMock()
    store.all_records.return_value = records
    obj._store = store
    obj._controls = MagicMock()
    # Patch BundledClipScanner.scan() to return controlled clip data
    obj._clips_for_test = clips or []
    return obj


def _bind_method(obj):
    """Bind _get_animate_inputs to obj so self works."""
    import main_window as mw
    import types
    return types.MethodType(mw.MainWindow._get_animate_inputs, obj)


def test_returns_last_frame_path_when_available(tmp_path):
    """Uses extra_meta['last_frame_path'] when it exists and the file is on disk."""
    last_frame = tmp_path / "frame_last.jpg"
    last_frame.write_bytes(b"jpeg")

    rec = _make_record(
        media_type="animate",
        thumb=str(tmp_path / "frame.jpg"),
        extra_meta={"last_frame_path": str(last_frame)},
    )
    obj = _make_mw_with_store([rec], clips=["/clips/walk.mp4"])
    fn = _bind_method(obj)

    with patch("main_window.BundledClipScanner") as MockScanner, \
         patch("main_window._settings") as mock_settings:
        mock_settings.get.return_value = "/clips"
        MockScanner.return_value.scan.return_value = {
            "locomotion": [{"mp4": "/clips/walk.mp4", "name": "walk", "thumb": ""}]
        }
        ref_video, ref_char = fn()

    assert ref_video == "/clips/walk.mp4"
    assert ref_char == str(last_frame)


def test_falls_back_to_thumbnail_when_last_frame_missing(tmp_path):
    """Falls back to thumbnail_path when last_frame_path file does not exist."""
    thumb = tmp_path / "frame.jpg"
    thumb.write_bytes(b"jpeg")

    rec = _make_record(
        media_type="animate",
        thumb=str(thumb),
        extra_meta={"last_frame_path": "/nonexistent/frame_last.jpg"},
    )
    obj = _make_mw_with_store([rec], clips=["/clips/walk.mp4"])
    fn = _bind_method(obj)

    with patch("main_window.BundledClipScanner") as MockScanner, \
         patch("main_window._settings") as mock_settings:
        mock_settings.get.return_value = "/clips"
        MockScanner.return_value.scan.return_value = {
            "locomotion": [{"mp4": "/clips/walk.mp4", "name": "walk", "thumb": ""}]
        }
        ref_video, ref_char = fn()

    assert ref_char == str(thumb)


def test_falls_back_to_flux_image_when_no_animate_records(tmp_path):
    """When no animate records exist, uses a FLUX image record's image_path."""
    img = tmp_path / "flux.jpg"
    img.write_bytes(b"jpeg")

    rec = _make_record(media_type="image", image_path=str(img))
    obj = _make_mw_with_store([rec], clips=["/clips/walk.mp4"])
    fn = _bind_method(obj)

    with patch("main_window.BundledClipScanner") as MockScanner, \
         patch("main_window._settings") as mock_settings:
        mock_settings.get.return_value = "/clips"
        MockScanner.return_value.scan.return_value = {
            "locomotion": [{"mp4": "/clips/walk.mp4", "name": "walk", "thumb": ""}]
        }
        ref_video, ref_char = fn()

    assert ref_char == str(img)


def test_returns_empty_strings_when_no_media(tmp_path):
    """Returns ('', '') when store is empty — attractor will skip the cycle."""
    obj = _make_mw_with_store([], clips=["/clips/walk.mp4"])
    fn = _bind_method(obj)

    with patch("main_window.BundledClipScanner") as MockScanner, \
         patch("main_window._settings") as mock_settings:
        mock_settings.get.return_value = "/clips"
        MockScanner.return_value.scan.return_value = {
            "locomotion": [{"mp4": "/clips/walk.mp4", "name": "walk", "thumb": ""}]
        }
        ref_video, ref_char = fn()

    assert ref_char == ""


def test_returns_empty_strings_when_no_clips():
    """Returns ('', '') when BundledClipScanner finds no clips."""
    rec = _make_record(media_type="animate", thumb="/some/thumb.jpg",
                       extra_meta={"last_frame_path": "/some/last.jpg"})
    obj = _make_mw_with_store([rec])
    fn = _bind_method(obj)

    with patch("main_window.BundledClipScanner") as MockScanner, \
         patch("main_window.Path") as MockPath, \
         patch("main_window._settings") as mock_settings:
        mock_settings.get.return_value = "/clips"
        MockScanner.return_value.scan.return_value = {}  # no clips
        ref_video, ref_char = fn()

    assert ref_video == ""
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_main_window_animate_inputs.py -v 2>&1 | tail -20
```

Expected: FAILs — `_get_animate_inputs` method doesn't exist yet.

- [ ] **Step 3: Add `_get_animate_inputs` to `MainWindow`**

Find the `_on_generate` or `_on_attractor_generate` method group (around line 6960) and add `_get_animate_inputs` just before `_on_open_attractor` (line 6837), as a new method on MainWindow:

```python
    def _get_animate_inputs(self) -> "tuple[str, str]":
        """
        Pick (ref_video_path, ref_char_path) for TT-TV animate auto-generation.

        ref_video: random bundled motion clip from motion_clips_dir.
        ref_char:  last frame of most recent animate record (extra_meta['last_frame_path'])
                   → fallback: thumbnail of most recent animate record
                   → fallback: image_path of most recent FLUX image record
                   → fallback: "" (attractor skips the cycle)

        Returns ("", "") if no valid inputs can be found.
        """
        import random as _random
        from animate_picker import BundledClipScanner

        # ── ref_video: random bundled clip ─────────────────────────────────────
        clips_dir = _settings.get("motion_clips_dir")
        all_clips = [
            clip["mp4"]
            for clips in BundledClipScanner(clips_dir).scan().values()
            for clip in clips
            if clip.get("mp4")
        ]
        if not all_clips:
            return "", ""
        ref_video = _random.choice(all_clips)

        # ── ref_char: last frame chain, then fallbacks ─────────────────────────
        all_records = self._store.all_records()

        # Priority 1: last frame of most recent animate record
        for r in all_records:
            if r.media_type != "animate":
                continue
            lfp = r.extra_meta.get("last_frame_path", "")
            if lfp and Path(lfp).exists():
                return ref_video, lfp
            # Priority 2: thumbnail of most recent animate record
            if r.thumbnail_path and Path(r.thumbnail_path).exists():
                return ref_video, r.thumbnail_path
            break  # only check the most recent animate record

        # Priority 3: most recent FLUX image
        for r in all_records:
            if r.media_type == "image" and r.image_path and Path(r.image_path).exists():
                return ref_video, r.image_path

        return ref_video, ""
```

- [ ] **Step 4: Update `_on_open_attractor` — remove guard, add kwarg**

In `_on_open_attractor` (around line 6868), replace the temporary animate guard:

```python
        # Animate mode auto-generation is not yet implemented (needs randomised
        # ref_video + ref_char inputs).  Disable silently so TT-TV still works
        # as a viewer but won't flood the queue with empty-input animate jobs.
        current_source = self._controls.get_model_source()
        if current_source == "animate":
            auto_generate = False
```

with just:

```python
        current_source = self._controls.get_model_source()
```

Then add `get_animate_inputs` to the `AttractorWindow(...)` constructor call (after `get_playlists=...`):

```python
                get_playlists=lambda: (
                    __import__("playlist_store").playlist_store.all()
                ),
                get_animate_inputs=(
                    self._get_animate_inputs if current_source == "animate" else None
                ),
```

- [ ] **Step 5: Run all tests**

```bash
python -m pytest tests/test_main_window_animate_inputs.py tests/test_attractor_animate.py tests/test_worker_animate.py -v 2>&1 | tail -30
```

Expected: all PASS.

- [ ] **Step 6: Smoke-check imports**

```bash
cd /home/ttuser/code/tt-local-generator
python -c "import sys; sys.path.insert(0,'app'); import main_window; import attractor; import worker; print('OK')"
```

Expected: `OK` with no errors.

- [ ] **Step 7: Commit**

```bash
git add app/main_window.py tests/test_main_window_animate_inputs.py
git commit -m "feat: MainWindow._get_animate_inputs — last-frame continuity chain for TT-TV animate"
```

---

## Task 4: requirements.txt — document ffmpeg system dep

**Files:**
- Modify: `requirements.txt`

ffmpeg is already declared in `debian/tt-local-generator/DEBIAN/control` Depends.
This task adds a visible comment for developers running from source.

- [ ] **Step 1: Add the comment to `requirements.txt`**

Replace the current GTK comment block:

```
# GTK4 bindings — must be installed via system package manager, not pip:
#   sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0
#
# Run with system python3 (which has access to python3-gi):
#   /usr/bin/python3 main.py
```

with:

```
# GTK4 bindings — must be installed via system package manager, not pip:
#   sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0
#
# Video processing — must be installed via system package manager, not pip:
#   sudo apt install ffmpeg
#   (also declared in debian/tt-local-generator/DEBIAN/control Depends)
#
# Run with system python3 (which has access to python3-gi):
#   /usr/bin/python3 main.py
```

- [ ] **Step 2: Verify the file looks right**

```bash
cat /home/ttuser/code/tt-local-generator/requirements.txt
```

Expected: ffmpeg comment visible between the GTK note and the run note.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "docs: document ffmpeg as required system package in requirements.txt"
```

---

## Final check

- [ ] **Run the full test suite**

```bash
cd /home/ttuser/code/tt-local-generator
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all existing tests pass; new tests pass.

- [ ] **Verify no regressions in import**

```bash
python -c "import sys; sys.path.insert(0,'app'); import main_window; import attractor; import worker; print('OK')"
```
