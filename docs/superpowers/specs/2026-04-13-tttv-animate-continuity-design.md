# TT-TV Animate Auto-Generation: Last-Frame Continuity Chain

## Goal

Enable TT-TV to run continuously in Animate mode, chaining clips together so
each new generation uses the last frame of the previous clip as its character
reference — producing a slow visual drift across random cinematic prompts.

## Architecture

Four touch-points, each with one clear responsibility:

- **`worker.py`** — extracts last frame alongside thumbnail at job completion
- **`attractor.py`** — accepts an input-provider callback; uses it when model is animate
- **`main_window.py`** — implements the input provider; lifts the auto_generate=False guard
- **`requirements.txt`** — documents ffmpeg as a required system package

## Data Model

`GenerationRecord.extra_meta` (already a free-form dict, already serialised to
`history.json`) gains one key written by `AnimateGenerationWorker`:

```python
record.extra_meta["last_frame_path"] = "~/.local/share/tt-video-gen/thumbnails/20260413_170522_abcd1234_last.jpg"
```

Stored in `THUMBNAILS_DIR` alongside the existing thumbnail, named
`{ts}_{job_id[:8]}_last.jpg`. Persists across sessions. No schema migration
needed — `extra_meta` is already loaded with `r.get("extra_meta", {})`.

## Worker Change (`worker.py`)

`AnimateGenerationWorker` gains `_extract_last_frame(video_path, last_frame_path)`.
Identical infrastructure to `_extract_thumbnail` but uses `-sseof -0.1` to seek
0.1 s from the end of the video instead of the first frame.

Execution order in `run_with_callbacks`:
```
Step 5a: _extract_thumbnail  → thumbnails/{ts}_{id}.jpg        (first frame, unchanged)
Step 5b: _extract_last_frame → thumbnails/{ts}_{id}_last.jpg   (last frame, new)
Step 6:  _write_prompt_sidecar
Step 7:  store.append + on_finished
```

`_write_prompt_sidecar` should also write `last_frame: {last_frame_path}` when
the key is present in `extra_meta`, for debuggability.

On ffmpeg failure, `last_frame_path` is not written into `extra_meta` — the
attractor falls back gracefully to the thumbnail.

The `last_frame_path` is computed from the record's `thumbnail_path` by
appending `_last` before the extension:
```python
last_frame_path = str(Path(record.thumbnail_path).with_stem(
    Path(record.thumbnail_path).stem + "_last"
))
```

## Attractor Change (`attractor.py`)

`AttractorWindow.__init__` gains one optional parameter:

```python
get_animate_inputs: "Callable[[], tuple[str, str]] | None" = None
# Returns (ref_video_path, ref_char_path) at generation time.
# Called on the main thread from _enqueue_generation.
# Return ("", "") to skip this generation cycle.
```

Stored as `self._get_animate_inputs = get_animate_inputs`.

`_enqueue_generation` grows an animate branch:

```python
def _enqueue_generation(self, prompt: str) -> None:
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

When `model_source != "animate"`, the existing code path is completely unchanged.

## MainWindow Changes (`main_window.py`)

### `_get_animate_inputs()` method

```python
def _get_animate_inputs(self) -> tuple[str, str]:
    """
    Pick (ref_video_path, ref_char_path) for the next TT-TV animate job.

    ref_video: random bundled motion clip
    ref_char:  last frame of most recent animate record (extra_meta)
               → fallback: thumbnail of most recent animate record
               → fallback: image_path of most recent FLUX image record
               → fallback: ("", "") — attractor skips the cycle
    """
```

**ref_video selection:**
```python
from animate_picker import BundledClipScanner
clips = BundledClipScanner().clips()   # already in the codebase
ref_video = str(random.choice(clips).path) if clips else ""
```

**ref_char selection (priority order):**
1. Most recent animate record where `Path(extra_meta["last_frame_path"]).exists()`
2. Most recent animate record's `thumbnail_path` (first frame fallback)
3. Most recent image record's `image_path` (FLUX portrait fallback)
4. `""` → attractor skips cycle

"Most recent" = `next(r for r in store.all_records() if r.media_type == "animate")`,
since `all_records()` returns newest-first.

### `_on_open_attractor` wiring

Pass the new callback when `current_source == "animate"`:

```python
get_animate_inputs=(
    self._get_animate_inputs if current_source == "animate" else None
),
```

Remove the `if current_source == "animate": auto_generate = False` guard added
as a temporary safety measure — this feature replaces it. The guard comment
should be removed entirely so `auto_generate` is set only by playlist/model
filter logic.

## requirements.txt

Add a comment block documenting ffmpeg as a required system package (it is
already in the debian `Depends` field; this makes it visible to developers
running from source):

```
# System packages (not pip) — required at runtime:
#   sudo apt install ffmpeg
#   (also declared in debian/tt-local-generator/DEBIAN/control Depends)
```

## Error Handling

| Scenario | Behaviour |
|---|---|
| ffmpeg unavailable | `_extract_last_frame` silently no-ops; `last_frame_path` absent from `extra_meta` |
| Last frame file deleted | Attractor falls back to thumbnail |
| No animate records in history | Falls back to FLUX image or skips cycle |
| No FLUX images either | Returns `("", "")` → attractor skips cycle, logs at DEBUG |
| `BundledClipScanner` returns empty | Returns `("", "")` → attractor skips cycle |

## Testing

- Unit test `_extract_last_frame`: mock ffmpeg subprocess; assert `extra_meta["last_frame_path"]` set on success, absent on failure.
- Unit test `_get_animate_inputs`: mock store with (a) animate records with/without last_frame_path, (b) image-only records, (c) empty store.
- Unit test `_enqueue_generation` animate branch: assert skips when `get_animate_inputs` returns `("", "")`.
