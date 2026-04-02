# Attractor Mode — Design Spec

**Date:** 2026-04-02
**Status:** Approved

---

## Overview

Attractor Mode is a self-sustaining kiosk loop that plays all existing generated media in a shuffled cycle while simultaneously generating new prompts and queuing new generations. As new videos/images complete they are added to the live pool. Over time the loop self-replenishes: ideally a new item is always ready before the current cycle ends.

The feature opens as a dedicated kiosk window with a narrow live-status sidebar and a crossfading media player occupying the rest of the screen. Generation continues in the background as long as the inference server is accepting work.

---

## Files

| File | Change |
|---|---|
| `attractor.py` | **New** — `AttractorWindow`: kiosk window, A/B crossfade player, generation loop |
| `main_window.py` | Add gallery toolbar button, `_attractor_win` reference, `_on_open_attractor()`, notify attractor on `_on_finished` |

`attractor.py` has **no imports from `main_window.py`** — it receives everything via constructor callbacks.

---

## attractor.py

### `AttractorWindow(Gtk.Window)`

A dedicated non-modal `Gtk.Window` (not a dialog). Opens maximized; `F` toggles true fullscreen; `Escape` closes.

#### Constructor

```python
AttractorWindow(
    records: list[GenerationRecord],      # initial pool (copy from HistoryStore.all_records())
    system_prompt: str,                   # contents of prompts/prompt_generator.md
    model_source: str,                    # "video" | "image" | "animate"
    on_enqueue: Callable,                 # MainWindow._on_enqueue signature
    get_queue_depth: Callable[[], int],   # returns current pending job count
)
```

#### Layout

```
┌─────────────────────────────────────────────────────┐
│  sidebar (140px)  │  media player (rest of width)   │
│                   │                                  │
│  ATTRACTOR MODE   │   [A/B Gtk.Stack crossfade]      │
│  ⬤ server status  │                                  │
│  ⏳ queue: 1      │   current media plays here       │
│  🎬 pool: 23      │                                  │
│                   │   ──────────────────── HUD ───── │
│  Generating…      │   "prompt text…"    pool: 23     │
│  "misty mountain  │                                  │
│   at dawn…"       │                                  │
│                   │                                  │
│  [■ Stop]         │                                  │
└─────────────────────────────────────────────────────┘
```

#### Internal state

```python
_records: list[GenerationRecord]   # live pool; new items appended as generated
_pool_order: list[int]             # shuffled indices into _records; reshuffled when exhausted
_pool_pos: int                     # current position in _pool_order
_avg_image_duration: float         # mean(r.duration_s for videos); default 8.0s if no videos
_gen_stop: threading.Event         # set to stop the generation loop thread
_current_prompt: str               # last generated prompt (shown in sidebar)
```

---

## A/B Crossfade Player

The media player uses a **`Gtk.Stack`** with `CROSSFADE` transition type, 500 ms duration. Two "slot" widgets (`_slot_a`, `_slot_b`) alternate. Each slot is a `Gtk.Overlay` containing:

- `Gtk.Video` (shown when playing video, hidden otherwise)
- `Gtk.Picture` (shown when displaying image, hidden otherwise)

When advancing to the next item:
1. Determine the **inactive slot** (the one not currently showing).
2. Load the next item into the inactive slot (set video file or picture file, hide the other widget).
3. Call `_stack.set_visible_child(inactive_slot)` — triggers the crossfade animation.
4. Schedule the *next* advance: for video, connect `notify::ended` on the new `Gtk.MediaStream`; for images, use `GLib.timeout_add(int(_avg_image_duration * 1000), ...)`.

**`_avg_image_duration`** is recalculated each time `add_record()` is called:
```python
durations = [r.duration_s for r in self._records
             if r.media_type == "video" and r.duration_s > 0]
self._avg_image_duration = statistics.mean(durations) if durations else 8.0
```

---

## Pool Management

#### Shuffle strategy

On open, `_pool_order = list(range(len(_records)))` then `random.shuffle(_pool_order)`. `_pool_pos = 0`.

When `_pool_pos` reaches `len(_pool_order)` (full cycle complete), reshuffle and reset to 0 — but avoid placing the last item of the previous cycle first (prevents immediate repeats across cycle boundaries).

#### `add_record(record: GenerationRecord) -> None`

Called from the main thread (via `GLib.idle_add`) when a new generation completes:
1. Append `record` to `_records`.
2. Append its new index to `_pool_order` at a random position **after** `_pool_pos` (so it appears later in the current cycle, not immediately next).
3. Recalculate `_avg_image_duration`.
4. Update sidebar pool count label.

---

## Generation Loop

Runs on a background daemon thread. Stops when `_gen_stop` is set.

```python
def _generation_loop(self) -> None:
    while not self._gen_stop.wait(0.0):
        depth = self._get_queue_depth()
        if depth >= 2:
            # Back-pressure: server isn't consuming — wait before trying again
            self._gen_stop.wait(30.0)
            continue
        try:
            GLib.idle_add(self._set_sidebar_status, "Generating prompt…")
            prompt = prompt_client.generate_prompt(
                source=self._model_source,
                seed_text="",           # empty → fully random from word banks
                system_prompt=self._system_prompt,
            )
            self._current_prompt = prompt
            GLib.idle_add(self._set_sidebar_prompt, prompt)
            GLib.idle_add(self._set_sidebar_status, "Queued — waiting for server…")
            # Enqueue with default steps/seed/guidance for the current model
            GLib.idle_add(self._enqueue_generation, prompt)
        except Exception as e:
            GLib.idle_add(self._set_sidebar_status, f"Prompt error: {e}")
            self._gen_stop.wait(15.0)   # back off on error
        # Brief pause so we don't spin if enqueue is instant
        self._gen_stop.wait(5.0)
```

`_enqueue_generation(prompt)` calls `on_enqueue` with these defaults (matching control panel defaults when fields are left blank):

```python
on_enqueue(
    prompt=prompt,
    negative_prompt="",
    steps=30,
    seed=-1,              # -1 → server picks random seed
    seed_image_path="",
    model_source=self._model_source,
    guidance_scale=5.0,
    ref_video_path="",
    ref_char_path="",
    animate_mode="animation",
    model_id="",          # "" → server uses whichever model is loaded
)
```

---

## Sidebar

Fixed 140px wide, dark `@tt_bg_darkest` background. Contents (top to bottom):

| Widget | Content |
|---|---|
| Header label | `ATTRACTOR MODE` — teal, bold, small caps |
| Divider | 1px `@tt_border` |
| Status dot + label | `⬤ running` (green) / `⬤ paused` (muted) |
| Queue row | `⏳ queue: N` — updates live |
| Pool row | `🎬 pool: N` — updates when `add_record()` is called |
| Divider | |
| Status label | `Generating…` / `Queued` / `Waiting…` |
| Prompt label | Last generated prompt, italic, wrapping, `@tt_text_muted`, max 5 lines |
| Spacer | fills remaining vertical space |
| Stop button | `■ Stop` — red border, calls `_on_stop()` |

**`_on_stop()`**: sets `_gen_stop`, closes the window. Main app resumes normally.

---

## HUD Overlay

A translucent strip across the bottom of the media player (not the sidebar). Always visible, low-contrast so it doesn't distract:

```
"slow pan across misty mountain ridgeline…"          pool: 23
```

- Left: current item's prompt (truncated to ~80 chars), `@tt_text_muted`, 10px italic
- Right: pool count, `@tt_text_muted`, 10px

---

## CSS additions (in `attractor.py`)

`attractor.py` has no imports from `main_window.py`, so it registers its own `Gtk.CssProvider` using the same `@define-color` variables (which resolve against the application stylesheet already loaded by `main_window.py`).

```css
/* Attractor sidebar */
.attractor-sidebar {
    background-color: @tt_bg_darkest;
    border-right: 1px solid @tt_border;
    padding: 10px 8px;
}
.attractor-header {
    color: @tt_accent;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
}
.attractor-prompt-lbl {
    color: @tt_text_muted;
    font-size: 10px;
    font-style: italic;
}
.attractor-stop-btn {
    background-color: #2D1A1A;
    color: @tt_error;
    border: 1px solid @tt_error;
    border-radius: 4px;
    padding: 5px 8px;
    font-size: 11px;
}
/* HUD overlay */
.attractor-hud {
    background: linear-gradient(transparent, rgba(0,0,0,0.55));
    padding: 20px 12px 8px;
}
.attractor-hud-prompt {
    color: @tt_text_muted;
    font-size: 10px;
    font-style: italic;
}
```

---

## Gallery toolbar button

In `main_window.py`, the gallery section gets a toolbar row above the `Gtk.FlowBox`. A new button triggers Attractor Mode:

```
[ ✦ Attractor ]
```

- CSS class `attractor-launch-btn` (styled like `inspire-btn` — subtle, dark background, teal text)
- Disabled when `len(HistoryStore.all_records()) == 0` (nothing to play)
- On click: calls `MainWindow._on_open_attractor()`

`_on_open_attractor()`:
1. If `self._attractor_win` is not None and still alive, bring it to front.
2. Otherwise: create `AttractorWindow(records, system_prompt, model_source, on_enqueue, get_queue_depth)`, connect `destroy` signal to clear `self._attractor_win`, show maximized.

**`get_queue_depth`** is a lambda: `lambda: len(self._queue)`.

**`_on_finished` change**: after replacing the pending card, if `self._attractor_win` is set, call `GLib.idle_add(self._attractor_win.add_record, record)`.

---

## Keyboard shortcuts (in AttractorWindow)

| Key | Action |
|---|---|
| `Escape` | Close window (stops generation loop) |
| `F` | Toggle fullscreen |
| `Space` | Pause/resume playback (generation loop continues) |

---

## Scope boundaries

**In scope:**
- `attractor.py` — `AttractorWindow`
- Gallery toolbar launch button
- `_on_finished` → `add_record` notification
- CSS for sidebar and HUD

**Out of scope:**
- Prompt history / de-duplication (word banks are varied enough for V1)
- Per-model step/guidance tuning in the attractor UI
- Saving an "attractor session" log
- Multiple simultaneous attractor windows
