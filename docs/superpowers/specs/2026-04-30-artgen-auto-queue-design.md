# Artgen Auto-Generate Queue — Design Spec

**Date:** 2026-04-30
**Status:** Approved

---

## Overview

An "Auto-Generate" mode for the artgen Create tab that runs generations continuously without user intervention. After each generation completes, the system automatically selects a random generator type (from a user-configured subset), randomises that type's parameters, derives a theme via the Inspire pipeline, and starts the next generation. A countdown bar shows time until the next run. The user toggles it on/off; the existing `_generating` boolean is the shared concurrency limit (one job at a time, always).

---

## 1. UI Layout

A collapsible section is appended to the bottom of the Create tab, below the Generate button row, separated by a thin `Gtk.Separator`. It is always visible (not hidden behind a sub-tab).

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Auto-Generate           [Switch OFF]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
(expanded when switch is ON:)

Types   [✓] landscape  [✓] skyline  [✓] verse
        [✓] geometric  [✓] circuit  [✓] constellation
        [✓] ansi       [✓] palette  [✓] freeform

Mood seed  [________________________________]
           placeholder: "e.g. 'industrial decay' — blank = pure chaos"

Delay      [━━━━━●━━━━] 3s   (0–30s, 1s steps)

(while running — countdown row replaces static delay label:)
▓▓▓▓▓▓░░░░░░░░  Inspiring…  / Next in 2s    [■ Stop]
```

The type checkboxes expand/collapse with the switch. "Stop" is a convenience alias for toggling the switch off. At least one type must remain checked — if the user unchecks all, auto-gen turns itself off with a status message.

---

## 2. State

All state is on `ArtgenPanel` (no new files, no DB changes):

```python
_auto_gen: bool = False
_auto_gen_timer_id: int | None = None   # GLib source id for the 100ms heartbeat
_auto_gen_countdown: float = 0.0        # seconds remaining until next fire
_auto_gen_error_streak: int = 0         # consecutive errors; cutoff at 3
```

The type-checkbox and mood-seed widgets are stored as:
```python
_auto_type_checks: dict[str, Gtk.CheckButton]  # gen_name → CheckButton
_auto_seed_entry: Gtk.Entry
_auto_delay_spin: Gtk.SpinButton               # value in seconds, 0–30
_auto_progress: Gtk.ProgressBar
_auto_status_lbl: Gtk.Label                    # "Next in 2s" / "Inspiring…"
```

---

## 3. Logic Flow

### Triggering

`_finish_success` and `_finish_error` (which already run on the GTK main thread via `GLib.idle_add`) call `_auto_maybe_schedule()` at the end of their existing bodies.

```python
def _auto_maybe_schedule(self) -> None:
    if not self._auto_gen:
        return
    checked = [n for n, cb in self._auto_type_checks.items() if cb.get_active()]
    if not checked:
        self._auto_stop("No types selected — auto-generate off")
        return
    self._auto_gen_countdown = float(self._auto_delay_spin.get_value())
    self._auto_gen_timer_id = GLib.timeout_add(100, self._auto_tick)
```

### Heartbeat

`_auto_tick` runs every 100 ms on the main thread:

```python
def _auto_tick(self) -> bool:
    if not self._auto_gen:
        self._auto_gen_timer_id = None
        return GLib.SOURCE_REMOVE
    self._auto_gen_countdown -= 0.1
    frac = max(0.0, self._auto_gen_countdown / self._auto_delay_spin.get_value())
    self._auto_progress.set_fraction(frac)
    secs = int(self._auto_gen_countdown) + 1
    self._auto_status_lbl.set_label(f"Next in {secs}s")
    if self._auto_gen_countdown <= 0:
        self._auto_fire()
        return GLib.SOURCE_REMOVE
    return GLib.SOURCE_CONTINUE
```

### Firing

`_auto_fire` runs on the main thread:

1. Check `_generating` — if True, reschedule for 1s later (the model is still running) and return.
2. Pick a random type from currently-checked types.
3. Call `_auto_apply_random_params(gen_name)` to set widget values for that type.
4. Read the mood seed from `_auto_seed_entry`.
5. Disable the auto UI controls (not the Stop button).
6. Set `_auto_status_lbl` to "Inspiring…".
7. Launch a background thread that:
   a. Calls `prompt_client.generate_prompt("artgen", seed_text=mood_seed_or_random_subject)`.
   b. Falls back to `random.choice(word_banks.SUBJECTS)` if the prompt server is offline.
   c. On completion: `GLib.idle_add(_auto_fire_with_theme, gen_name, theme)`.
8. `_auto_fire_with_theme` (main thread): writes theme into the type's text field, calls `_build_args(gen_name)`, then runs the generation exactly as `_on_generate_clicked` does (set `_generating = True`, disable Generate button, start `_run_generation` thread).

### Stopping

```python
def _auto_stop(self, reason: str = "") -> None:
    self._auto_gen = False
    self._auto_switch.set_active(False)
    if self._auto_gen_timer_id is not None:
        GLib.source_remove(self._auto_gen_timer_id)
        self._auto_gen_timer_id = None
    self._auto_progress.set_fraction(0.0)
    if reason:
        self._set_status(reason)
```

---

## 4. Random Param Generation

`_auto_apply_random_params(gen_name)` sets the GTK widgets for the given type to random values, then `_build_args` reads them as usual. The user sees which values were picked (widgets visibly update). Randomisation tables:

| Type | Randomised params |
|------|-------------------|
| `landscape` | palette: random from `["sunset","blue","purple","red","orange"]`; mountains: random bool; clouds: random bool; glitch: 20% chance True |
| `skyline` | era: random from `["modern","retro","futuristic"]`; density: random from `["low","medium","high"]`; sky: random bool |
| `verse` | form: random from `["haiku","lore","epitaph","couplet"]`; count: random int 1–3; theme: set by Inspire |
| `constellation` | culture: random from `["invented","norse","greek","random"]`; stars: random int 8–20; lore: random bool |
| `geometric` | geo_palette: random from `["teal","mono","ember","forest"]`; complexity: random from `["low","high"]`; style: random from `["mondrian","circuit","recursive","weave"]` |
| `circuit` | circuit_style: random from `["clean","neon","paper"]`; inputs: random 2–4 letters from A–H; gates: random 2–3 from `["and","or","not","xor","nand","nor"]`; depth: random from `[1,2,3]` |
| `ansi` | subject: set by Inspire; width: 80 (fixed); colors: random int 4–8 |
| `palette` | mood: set by Inspire; count: random int 4–6 |
| `freeform` | freeform text: set by Inspire |

For types where the theme/subject is "set by Inspire", the field is written by `_auto_fire_with_theme` after the background Inspire call completes. For other types, `_auto_apply_random_params` sets all fields before the Inspire call begins (so the UI updates immediately and feels responsive).

---

## 5. Theme / Inspire Integration

The mood seed entry drives what Inspire receives as `seed_text`:

- **Empty seed ("pure chaos"):** `seed_text = random.choice(word_banks.SUBJECTS)` — a fresh random subject from the combined word bank, giving fully chaotic output.
- **Non-empty seed:** `seed_text = mood_seed` — the prompt server steers its output toward the user's stated mood/theme, but still varies each run.

The `generate_prompt` call uses `source="artgen"` in both cases. If the prompt server (port 8001) is offline, the fallback is `random.choice(word_banks.SUBJECTS)` directly — no LLM needed, no error shown to the user.

The Inspire result is used as:
- The theme/subject text field for `verse` (theme), `palette` (mood), `ansi` (subject), and `freeform` (freeform text) — written directly into those widgets by `_auto_fire_with_theme`.
- For `landscape`, `skyline`, `geometric`, `circuit`, `constellation`: these types have no free-form theme widget, so the Inspire result is displayed in the status bar ("Inspired: …") for user visibility but does not feed into generation params. Randomised widget values (palettes, eras, gates, etc.) provide sufficient variation for these types.

---

## 6. Error Handling

- **Server offline at start of generation:** `_finish_error` fires, `_auto_gen_error_streak` increments, and `_auto_maybe_schedule()` is called. After **3 consecutive errors**, `_auto_stop("3 errors in a row — auto-generate paused")` is called and a `Gtk.AlertDialog` is shown: "Auto-generate stopped after 3 consecutive failures. Check that the artgen server is running on port 8002."
- **Successful generation:** resets `_auto_gen_error_streak = 0`.
- **All types unchecked:** `_auto_stop("No types selected — auto-generate off")` with no dialog.
- **Inspire call fails:** treated as non-fatal; falls back to word-bank draw silently.

---

## 7. Persistence

The delay value is persisted via `server_config.set("artgen_auto", "delay", value)` so it survives app restarts. All other state (which types are checked, mood seed) is session-only (reasonable defaults on each launch: all types checked, seed empty).

`server_config.py` DEFAULTS gains:
```python
"artgen_auto": {"delay": 3},
```

---

## 8. Files Changed

| File | Change |
|------|--------|
| `app/artgen_panel.py` | Add auto-generate UI section + all logic methods |
| `app/server_config.py` | Add `"artgen_auto"` key to DEFAULTS |

No new files. No schema changes. No new dependencies.

---

## 9. Out of Scope

- Persisting which types are checked across sessions
- Per-type weighting (some types more likely than others)
- Queue *list* visibility (explicit ordered list of upcoming jobs)
- Export / scheduling to a calendar time
