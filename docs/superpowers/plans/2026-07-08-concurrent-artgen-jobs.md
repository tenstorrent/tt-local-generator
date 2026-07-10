# Concurrent artgen jobs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let up to 3 artgen generations run concurrently (manual queue + auto-gen), replacing the serial `_generating` gate with an active-job counter and an aggregate progress display.

**Architecture:** Single-file change to `app/artgen_panel.py`. Replace `self._generating: bool` with `self._active_count: int` and a module cap `_MAX_CONCURRENT_ARTGEN = 3`. `_drain_queue` launches jobs up to the cap; `_finish_*` decrement and re-drain; a single count-managed ticker shows `Generating N job(s)… Xs`. All state mutates only on the GTK main thread (launch points + `GLib.idle_add` finishes), so no locks.

**Tech Stack:** Python 3.12 (system `/usr/bin/python3`), PyGObject/GTK4, pytest. Tests mock `threading.Thread` + `GLib` and construct the panel via `__new__` (no display).

## Global Constraints

- `app/artgen_panel.py` is the ONLY source file changed by Task 1. Tests go in `tests/test_artgen_concurrency.py`.
- System python for tests: `/usr/bin/python3 -m pytest …`. These tests do not create real GTK widgets (all widgets are MagicMocks), so no xvfb is needed.
- Concurrency cap is exactly `_MAX_CONCURRENT_ARTGEN = 3` (module-level constant).
- Cap applies to ALL backends (no per-backend gating).
- Invariant to preserve: all of `_active_count`, `_gen_queue`, ticker fields, and the button label are read/written only on the GTK main thread. Worker threads only touch locals + `GLib.idle_add`. No locks.
- Preserve existing behaviors: per-job record timing (`generation_seconds`) from `_run_generation`'s local `t0`; auto-gen inter-fire delay pacing; auto-gen 3-consecutive-error auto-stop.
- Version: Task 2 bumps `0.10.0 → 0.11.0` (minor) and the changelog stanza covers the already-landed 600s timeout + codeart website examples AND this concurrency feature.

---

## File Structure

- Modify: `app/artgen_panel.py` — state model, `_drain_queue`, ticker methods, button helpers, `_run_generation` (drop one line), `_finish_success`, `_finish_error`, and 4 auto-gen `_generating` sites.
- Create: `tests/test_artgen_concurrency.py` — unit tests driving main-thread methods with mocked threads/GLib.
- Modify (Task 2): `VERSION`, `debian/changelog`.

---

### Task 1: Concurrency core + auto-gen wiring + tests

**Files:**
- Modify: `app/artgen_panel.py`
- Test: `tests/test_artgen_concurrency.py`

**Interfaces produced (names later code/tests rely on):**
- Module const `_MAX_CONCURRENT_ARTGEN = 3`
- `self._active_count: int` (replaces `self._generating: bool`)
- `CodeArt... n/a` — `ArtgenPanel._gen_button_label(active: int, queued: int) -> str` (staticmethod)
- `ArtgenPanel._update_gen_button(self) -> None`
- `ArtgenPanel._ensure_ticker(self) -> None`, `_tick_llm_timer(self) -> bool`, `_stop_ticker(self) -> int | None`
  (these replace `_begin_llm_timer`/`_cancel_llm_timer`; `_tick_llm_timer` is reused/renamed)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_artgen_concurrency.py`:

```python
"""Concurrency for the artgen panel: cap-3 active jobs, queue refill, aggregate
button/ticker, auto-gen guard. Drives main-thread methods with threads + GLib
mocked; the panel is built via __new__ so no GTK display is needed."""
import sys
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import artgen_panel


def _panel(active=0, queue_items=()):
    """A panel stub with only the attributes the concurrency methods touch."""
    p = artgen_panel.ArtgenPanel.__new__(artgen_panel.ArtgenPanel)
    p._active_count = active
    p._gen_queue = deque(queue_items)
    p._gen_btn = MagicMock()
    p._status_lbl = MagicMock()
    p._llm_timer_id = None
    p._llm_t0 = 0.0
    p._auto_gen = False
    p._auto_gen_error_streak = 0
    # gallery/view attrs touched by _finish_success (not used by error/drain tests)
    p._gallery = MagicMock()
    p._watch = MagicMock(); p._watch._records = []
    p._sub_stack = MagicMock()
    p._gallery_tab_btn = MagicMock()
    p._last_out_path = None
    return p


def test_button_label_variants():
    f = artgen_panel.ArtgenPanel._gen_button_label
    assert f(0, 0) == "✦ Generate"
    assert f(3, 0) == "Generating… (3 running)"
    assert f(3, 2) == "Generating… (3 running, +2)"
    assert f(1, 0) == "Generating… (1 running)"


def test_drain_launches_up_to_cap():
    p = _panel(active=0, queue_items=[("verse", object()) for _ in range(5)])
    with patch.object(artgen_panel, "threading") as th:
        p._drain_queue()
        assert th.Thread.call_count == 3          # cap
    assert p._active_count == 3
    assert len(p._gen_queue) == 2                 # remainder queued


def test_drain_respects_existing_active():
    p = _panel(active=2, queue_items=[("verse", object()) for _ in range(5)])
    with patch.object(artgen_panel, "threading") as th:
        p._drain_queue()
        assert th.Thread.call_count == 1          # only 1 free slot
    assert p._active_count == 3
    assert len(p._gen_queue) == 4


def test_finish_error_decrements_and_refills():
    p = _panel(active=3, queue_items=[("verse", object())])
    with patch.object(artgen_panel, "threading") as th, \
         patch.object(artgen_panel, "GLib") as glib:
        p._finish_error("boom")
        # one slot freed by the finish, immediately refilled from the queue
        assert th.Thread.call_count == 1
    assert p._active_count == 3
    assert len(p._gen_queue) == 0


def test_finish_error_never_negative():
    p = _panel(active=0)
    with patch.object(artgen_panel, "GLib"):
        p._finish_error("x")
        p._finish_error("x")
    assert p._active_count == 0


def test_ensure_ticker_idempotent():
    p = _panel(active=1)
    with patch.object(artgen_panel, "GLib") as glib:
        glib.timeout_add.return_value = 42
        p._ensure_ticker()
        p._ensure_ticker()
        assert glib.timeout_add.call_count == 1   # second call is a no-op
    assert p._llm_timer_id == 42


def test_auto_fire_waits_when_at_cap():
    p = _panel(active=3)
    p._auto_gen = True
    p._auto_status_lbl = MagicMock()
    with patch.object(artgen_panel, "threading") as th, \
         patch.object(artgen_panel, "GLib") as glib:
        p._auto_fire()
        th.Thread.assert_not_called()             # no inspire thread while full
    p._auto_status_lbl.set_label.assert_called_with("Waiting for generation…")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_artgen_concurrency.py -q`
Expected: FAIL — `_gen_button_label`/`_active_count`/`_ensure_ticker` don't exist yet (AttributeError), `_drain_queue` still serial.

- [ ] **Step 3: Add the module constant**

In `app/artgen_panel.py`, add near the other module-level constants (just after the `_dd`/`_check`/`_row`/`_spin` helper definitions block, before the `ArtgenPanel` class):

```python
# Max artgen generations allowed to run at once. Chat backends (vLLM etc.)
# batch concurrent requests, so a small cap improves throughput without
# overwhelming the server or flooding the gallery.
_MAX_CONCURRENT_ARTGEN = 3
```

- [ ] **Step 4: Replace the state flag (init)**

Change line 126 from:
```python
        self._generating: bool = False
```
to:
```python
        self._active_count: int = 0            # artgen jobs currently in flight (0.._MAX_CONCURRENT_ARTGEN)
```

- [ ] **Step 5: Rewrite `_drain_queue` + add button helpers**

Replace the entire `_drain_queue` method (currently lines ~860-878) with:

```python
    def _drain_queue(self) -> None:
        """Launch queued generations up to the concurrency cap; update the button."""
        while self._active_count < _MAX_CONCURRENT_ARTGEN and self._gen_queue:
            gen_name, args = self._gen_queue.popleft()
            self._active_count += 1
            self._ensure_ticker()
            threading.Thread(
                target=self._run_generation,
                args=(gen_name, args),
                daemon=True,
            ).start()
        self._update_gen_button()

    @staticmethod
    def _gen_button_label(active: int, queued: int) -> str:
        """Label for the Generate button given active + queued job counts."""
        if active == 0:
            return "✦ Generate"
        if queued == 0:
            return f"Generating… ({active} running)"
        return f"Generating… ({active} running, +{queued})"

    def _update_gen_button(self) -> None:
        self._gen_btn.set_label(
            self._gen_button_label(self._active_count, len(self._gen_queue))
        )
```

- [ ] **Step 6: Replace the ticker methods**

Replace `_begin_llm_timer`, `_tick_llm_timer`, and `_cancel_llm_timer` (currently lines ~1375-1392) with:

```python
    def _ensure_ticker(self) -> None:
        """Start the shared aggregate ticker if not already running (main thread)."""
        if self._llm_timer_id is None:
            self._llm_t0 = time.monotonic()
            self._llm_timer_id = GLib.timeout_add(500, self._tick_llm_timer)
            self._tick_llm_timer()

    def _tick_llm_timer(self) -> bool:
        elapsed = int(time.monotonic() - self._llm_t0)
        self._set_status(f"Generating {self._active_count} job(s)… {elapsed}s")
        return GLib.SOURCE_CONTINUE

    def _stop_ticker(self) -> "int | None":
        """Stop the ticker if running; return elapsed seconds (or None)."""
        elapsed = None
        if self._llm_timer_id is not None:
            GLib.source_remove(self._llm_timer_id)
            self._llm_timer_id = None
            elapsed = int(time.monotonic() - self._llm_t0)
        return elapsed
```

- [ ] **Step 7: Drop the per-job ticker call in `_run_generation`**

Remove line 1074 (`GLib.idle_add(self._begin_llm_timer, t0)`) entirely. Keep the preceding `t0 = time.monotonic()` (it feeds the record's `generation_seconds`). The lines currently read:
```python
            t0 = time.monotonic()
            GLib.idle_add(self._begin_llm_timer, t0)
```
After: only
```python
            t0 = time.monotonic()
```

- [ ] **Step 8: Rewrite `_finish_success`**

Replace `_finish_success` (currently lines ~1396-1420) with:

```python
    def _finish_success(self, artifact: str, out_path_str: str, rec: "MediaRecord | None" = None) -> None:
        self._active_count = max(0, self._active_count - 1)
        self._last_out_path = Path(out_path_str)

        if rec is not None:
            self._gallery.prepend_record(rec)
            if self._watch._records:
                self._watch._records.insert(0, rec)
        else:
            self._gallery.refresh()
        self._gallery_tab_btn.set_active(True)
        self._sub_stack.set_visible_child_name("gallery")
        self._gallery.scroll_to_top()

        self._drain_queue()                        # refill freed slot(s)
        if self._active_count == 0:
            elapsed = self._stop_ticker()
            self._set_status(f"Done  ({elapsed}s)" if elapsed is not None else "Done")

        if self._auto_gen:
            self._auto_gen_error_streak = 0
            if self._active_count < _MAX_CONCURRENT_ARTGEN:
                self._auto_maybe_schedule()
```

- [ ] **Step 9: Rewrite `_finish_error`**

Replace `_finish_error` (currently lines ~1422-1442) with:

```python
    def _finish_error(self, msg: str) -> None:
        self._active_count = max(0, self._active_count - 1)
        self._set_status(f"Error: {msg[:80]}")

        self._drain_queue()                        # refill freed slot(s)
        if self._active_count == 0:
            self._stop_ticker()

        if self._auto_gen:
            self._auto_gen_error_streak += 1
            if self._auto_gen_error_streak >= 3:
                self._auto_stop("3 errors in a row — auto-generate paused")
                try:
                    dlg = Gtk.AlertDialog.new(
                        "Auto-generate stopped after 3 consecutive failures.\n"
                        "Check that a language model is running (tt-ctl start artgen-qwen3-8b or tt-ctl start prompt-server)."
                    )
                    dlg.show(self.get_root())
                except AttributeError:
                    pass  # GTK < 4.10; status bar message is sufficient
            elif self._active_count < _MAX_CONCURRENT_ARTGEN:
                self._auto_maybe_schedule()
```

- [ ] **Step 10: Remap the 4 auto-gen `_generating` reads**

Each is a mechanical swap to the counter:

`_on_auto_switch_changed` (currently `if not self._generating:` at ~line 1618):
```python
            if self._active_count < _MAX_CONCURRENT_ARTGEN:
                self._auto_maybe_schedule()
```

`_auto_fire` (currently `if self._generating:` at ~line 1674):
```python
        if self._active_count >= _MAX_CONCURRENT_ARTGEN:
            # All concurrency slots busy — check again shortly
            self._auto_gen_countdown = 1.0
            self._auto_status_lbl.set_label("Waiting for generation…")
            self._auto_gen_timer_id = GLib.timeout_add(100, self._auto_tick)
            return
```

`_auto_fire_with_theme` guard (currently `if self._generating:` / `return` at ~line 1743):
```python
        if self._active_count >= _MAX_CONCURRENT_ARTGEN:
            return
```

`_auto_fire_with_theme` launch (currently `self._generating = True` + `self._gen_btn.set_label("Generating…")` at ~lines 1766-1767):
```python
        self._active_count += 1
        self._ensure_ticker()
        self._update_gen_button()
```

- [ ] **Step 11: Run tests to verify they pass**

Run: `/usr/bin/python3 -m pytest tests/test_artgen_concurrency.py -q`
Expected: PASS (7 tests).

- [ ] **Step 12: Full panel-related regression check**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_artgen_panel_health.py tests/test_artgen_panel_codeart.py tests/test_servers_popover_discovery.py tests/test_artgen_concurrency.py -q`
Expected: all PASS (no regressions in the panel's other behaviors).

- [ ] **Step 13: Commit**

```bash
git add app/artgen_panel.py tests/test_artgen_concurrency.py
git commit -m "feat(artgen): run up to 3 concurrent generations (manual + auto-gen)"
```

---

### Task 2: Release 0.11.0 (version, changelog, full suite, PR update)

**Files:**
- Modify: `VERSION`, `debian/changelog`

- [ ] **Step 1: Run the full suite**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q`
Expected: all pass except the known 1 environment skip; the new concurrency tests are included. No failures.

- [ ] **Step 2: Bump VERSION**

Set `VERSION` to a single line: `0.11.0`

- [ ] **Step 3: Prepend the changelog stanza**

Prepend to `debian/changelog`:

```
tt-local-generator (0.11.0) noble; urgency=medium

  * artgen: run up to 3 generations concurrently (manual queue + auto-gen)
    instead of strictly serial — chat backends batch requests, so this improves
    throughput. Aggregate progress ("Generating N job(s)… Xs"); cap is the
    _MAX_CONCURRENT_ARTGEN constant.
  * artgen: raise the LLM call timeout 300s -> 600s so large code generations on
    a 70B don't hit the client ceiling.
  * codeart: add a plugins.html card and ship two starred example outputs
    (recursion-tree.py, markov-poem.pl) under docs/assets/artgen/.

 -- Taylor Singletary <tsingletary@tenstorrent.com>  Wed, 08 Jul 2026 00:00:00 +0000

```

- [ ] **Step 4: Commit**

```bash
git add VERSION debian/changelog
git commit -m "chore: release 0.11.0 — concurrent artgen jobs, 600s timeout, codeart examples"
```

- [ ] **Step 5: Push and update PR #20**

```bash
git push origin tt-ad/4-chips
GH_TOKEN="" gh pr edit 20 --title "Release v0.11.0: codeart generator, concurrent artgen jobs, artgen server discovery, P300X2 image models"
```
(Update the PR body's version references from 0.10.0 to 0.11.0 as well; a `gh pr edit 20 --body-file <updated>` is acceptable. Note: `gh` must be invoked with `GH_TOKEN=""` so keyring auth is used.)

---

## Self-Review

**Spec coverage:**
- Cap 3 constant → Task 1 Step 3. ✓
- `_generating`→`_active_count` (all sites) → Steps 4, 5, 8, 9, 10. ✓
- Launch-up-to-cap drain loop → Step 5; tests Step 1 (`test_drain_launches_up_to_cap`, `test_drain_respects_existing_active`). ✓
- Finish decrement + refill + count==0 handling → Steps 8, 9; tests (`test_finish_error_decrements_and_refills`, `test_finish_error_never_negative`). ✓
- Aggregate ticker (idempotent start, stop on →0) → Step 6; test (`test_ensure_ticker_idempotent`). Remove per-job call → Step 7. ✓
- Button label helper → Step 5; test (`test_button_label_variants`). ✓
- Auto-gen concurrency (manual + auto) → Step 10; test (`test_auto_fire_waits_when_at_cap`). ✓
- Preserve auto-gen error-streak stop + delay pacing → Step 9 keeps the streak/stop; `_auto_maybe_schedule` unchanged (it doesn't read `_generating`). ✓
- Lock-free main-thread invariant → preserved (no worker-thread mutations added). ✓
- Version 0.11.0 folding timeout + examples + concurrency → Task 2. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code; commands have expected output. The PR-body update in Task 2 Step 5 names the exact edit; acceptable (not a code placeholder).

**Type consistency:** `_active_count: int`, `_MAX_CONCURRENT_ARTGEN` const, `_gen_button_label(active, queued) -> str`, `_ensure_ticker()/_tick_llm_timer()->bool/_stop_ticker()->int|None`, `_update_gen_button()` — names identical across steps and tests. `_auto_maybe_schedule` referenced but not redefined (exists already; unchanged).
