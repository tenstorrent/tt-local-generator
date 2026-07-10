# Concurrent artgen jobs (design)

Date: 2026-07-08
Status: approved (design), pending implementation plan

## Summary

The artgen panel currently runs generations strictly one at a time: a single
`_generating: bool` gates a serial `_gen_queue`. Artgen backends (vLLM / any
OpenAI-compatible chat server) batch concurrent requests efficiently, so the
serial model — inherited from the single-GPU diffusion path — wastes throughput.

This change lets up to **3** artgen generations run concurrently, for both the
manual `✦ Generate` queue and auto-gen / endless (TT-TV) mode, with an aggregate
progress display. Scope is `app/artgen_panel.py` only.

## Decisions (locked during brainstorming)

- **Concurrency cap:** fixed `_MAX_CONCURRENT_ARTGEN = 3` (module constant, no setting).
- **Backend gating:** none — the cap applies to every backend. The prompt-gen
  fallback (Qwen3-0.6B, CPU on 8001) simply serializes internally; it won't break.
- **Scope:** manual queue **and** auto-gen / endless mode.
- **Progress UI:** aggregate — one shared status line + one ticker showing the
  count (`Generating N job(s)… Xs`); results drop into the Gallery as they finish.
  No per-job cards or per-job timers.

## Non-goals

- No per-backend concurrency tuning, no user setting for the cap.
- No per-job progress widgets / pending cards.
- No change to how results are rendered or stored (each finish prepends to the
  Gallery exactly as today).
- No change outside `app/artgen_panel.py`.

## Current behavior (what changes)

- `self._generating: bool` (`:126`) gates everything; `self._gen_queue: deque` (`:127`).
- `_on_generate_clicked` (`:849`) appends `(gen_name, args)` and calls `_drain_queue`.
- `_drain_queue` (`:860`): if `_generating`, only updates the button label; else pops
  one job, sets `_generating = True`, spawns `_run_generation` in a thread.
- `_run_generation` (`:1032`): background thread — detects endpoint, builds `call_fn`,
  calls `gen.generate_artifact`, saves a `MediaRecord`, then `GLib.idle_add(_finish_*)`.
  It also does `GLib.idle_add(self._begin_llm_timer, t0)` for the display ticker, and
  keeps a **local** `t0` used for the record's `generation_seconds`.
- `_begin_llm_timer`/`_tick_llm_timer`/`_cancel_llm_timer` (`:1375-1392`): a single
  shared ticker via `_llm_t0` + `_llm_timer_id`.
- `_finish_success` (`:1396`) / `_finish_error` (`:1422`): set `_generating = False`,
  update the Gallery / status, call `_drain_queue`, then auto-gen scheduling guarded by
  `if not self._generating`.
- Auto-gen: `_auto_fire_with_theme` (`:1711`) sets `_generating = True` and spawns
  `_run_generation`; `_auto_maybe_schedule` schedules the next fire guarded on
  `_generating`.

## Design

### Concurrency-safety invariant

All shared state (`_active_count`, `_gen_queue`, ticker fields, button label) is
read and written **only on the GTK main thread**: at launch points (`_drain_queue`,
`_auto_fire_with_theme`) and at finish points (`_finish_success`/`_finish_error`,
which run via `GLib.idle_add`). Worker threads (`_run_generation`) touch only their
own locals and post back via `idle_add`. Therefore **no locks are needed**. This
invariant must be preserved by every change below.

### State model

- Add module constant `_MAX_CONCURRENT_ARTGEN = 3`.
- Replace `self._generating: bool` with `self._active_count: int` (starts 0).
- Keep `self._gen_queue: deque` unchanged.
- Every current read of `_generating` is remapped:
  - "is anything running?" → `self._active_count > 0`
  - "can I start another?" → `self._active_count < _MAX_CONCURRENT_ARTGEN`

### Launch / drain

`_drain_queue` becomes a loop:

```
while self._active_count < _MAX_CONCURRENT_ARTGEN and self._gen_queue:
    gen_name, args = self._gen_queue.popleft()
    self._active_count += 1
    self._ensure_ticker()                     # start the shared ticker if not running
    threading.Thread(target=self._run_generation, args=(gen_name, args), daemon=True).start()
self._update_gen_button()                     # label reflects active + queued
```

`_run_generation` is unchanged except: **remove** its
`GLib.idle_add(self._begin_llm_timer, t0)` call (the ticker is now count-managed at
launch). It keeps its local `t0` for the record's `generation_seconds`.

### Finish

Both `_finish_success` and `_finish_error`:

1. `self._active_count -= 1` (never below 0).
2. success: prepend the record to the Gallery + watch list, switch to the Gallery
   tab, scroll to top (exactly as today). error: leave the view, record the error.
3. `self._drain_queue()` — pulls the next queued job into the freed slot.
4. If `self._active_count == 0`: stop the ticker; set status `Done (Xs)` (success) or
   `Error: …` (error). Else: keep the ticker; status stays `Generating N job(s)… Xs`.
5. Auto-gen: replace `if not self._generating` with
   `if self._active_count < _MAX_CONCURRENT_ARTGEN` so endless mode keeps up to 3 in
   flight. Preserve the error-streak counter and the 3-consecutive-failure auto-stop.

### Shared ticker (aggregate)

Rework the single ticker to be count-managed:

- `_ensure_ticker()`: if no ticker running, record the start time and start a
  `GLib.timeout_add(500, _tick)`; idempotent (no-op if already running).
- `_tick()`: set status `Generating {self._active_count} job(s)… {elapsed}s`; returns
  `SOURCE_CONTINUE`.
- `_stop_ticker()`: remove the timeout source if present; returns the elapsed seconds.

The ticker's elapsed time is display-only (wall-clock since the first of the current
active batch started). Per-job accuracy for the stored record is unaffected — that
still comes from `_run_generation`'s local `t0`.

### Button label

Extract a pure helper `_gen_button_label(active: int, queued: int) -> str`:

- `active == 0` → `"✦ Generate"`
- `active > 0`, `queued == 0` → `f"Generating… ({active} running)"`
- `active > 0`, `queued > 0` → `f"Generating… ({active} running, +{queued})"`

`_update_gen_button()` calls `self._gen_btn.set_label(_gen_button_label(self._active_count, len(self._gen_queue)))`.

### Auto-gen

- `_auto_fire_with_theme`: replace `self._generating = True` with
  `self._active_count += 1` + `self._ensure_ticker()` before spawning `_run_generation`;
  keep writing the inspired theme into the generator's widget first.
- `_auto_maybe_schedule`: guard becomes `if self._active_count < _MAX_CONCURRENT_ARTGEN`.
  The inter-fire delay timer is unchanged, so auto-gen still paces fires by the
  configured delay; concurrency only means a new fire may start while up to 2 prior
  jobs are still finishing.
- The manual-vs-auto interplay in `_finish_success` (`don't auto-schedule if a manual
  item just started`) is preserved via the same `_active_count < MAX` check.

## Error handling

- A worker thread that raises still posts `_finish_error` via `idle_add`; the count is
  decremented there, so a failing job never leaks a slot.
- `_active_count` is clamped at 0 on decrement (defensive; a double-finish must not
  drive it negative).
- Auto-gen's 3-consecutive-error stop is preserved and counts errors across concurrent
  jobs the same way it does today.

## Testing

`tests/test_artgen_concurrency.py`. Construct the panel via `ArtgenPanel.__new__`
(no GTK init) and set only the attributes under test; patch `threading.Thread` (record
launches without running them) and `GLib` (`idle_add`/`timeout_add`/`source_remove`) so
the main-thread logic runs synchronously — mirrors the existing panel-test pattern.

- **Cap enforcement:** queue 5 jobs, drain → `_active_count == 3`, `len(_gen_queue) == 2`,
  3 threads launched.
- **Slot refill:** simulate a finish → `_active_count` drops to 2, drain launches exactly
  one more (→ 3 active, 1 queued); repeat until the queue drains and count returns to 0.
- **Button label:** `_gen_button_label` for (0,0)/(3,0)/(3,2)/(1,0) → exact strings.
- **Ticker lifecycle:** `_ensure_ticker` is idempotent (one `timeout_add` for a batch);
  `_stop_ticker` only fires on the →0 transition.
- **Auto-gen guard:** `_auto_maybe_schedule` schedules while `_active_count < 3` and does
  not once at 3; error-streak stop still triggers at 3 consecutive errors.
- **No-negative:** two finishes without an intervening launch never drive `_active_count`
  below 0.

## Version

Fold in with the already-landed 600s timeout + codeart website examples as a single
minor bump `0.10.0 → 0.11.0`; update `VERSION`, prepend a `debian/changelog` stanza,
and update PR #20's title/body to reflect the 0.11.0 scope.
