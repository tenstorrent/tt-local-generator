# Pipeline Portfolio View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the phase grid in the center pane with a scrollable card-based portfolio view for completed pipeline runs, so users see their artifacts beautifully instead of a spreadsheet of ✓ marks.

**Architecture:** A new `PipelinePortfolioView` GTK widget (one file) renders each pipeline job as a vertical card: seed image → inline video (autoplay loop) → poem text → poem image. The `MainWindow._on_pipeline_load_run()` decides which view to show based on run status — portfolio for `done`, phase grid for `running`/`failed`. The gallery stack gets a second named child `"pipeline-portfolio"` alongside the existing `"pipeline"` (phase grid), and `_on_source_change` / `_on_pipeline_run` toggle between them.

**Tech Stack:** Python 3.12, GTK4 via PyGObject, `Gtk.Video` for inline autoplay, `GdkPixbuf` for async thumbnails (same pattern as `PhaseGridWidget`), `GLib.idle_add` for all widget updates from threads.

---

## File structure

| File | Role | New/Modify |
|---|---|---|
| `app/pipeline_portfolio_view.py` | `PipelinePortfolioView` widget + `PortfolioJobCard` widget | **New** |
| `app/main_window.py` | Add portfolio view to gallery stack; toggle grid↔portfolio in load/run callbacks | Modify |
| `tests/test_pipeline_portfolio_view.py` | Unit tests for data-extraction helpers (no display) | **New** |

`phase_grid_widget.py` and `pipeline_panel.py` are **not modified** — the grid stays for active/failed runs.

---

## Key data contracts

The portfolio view reads from a `PipelineStore` run record. Given the World's Fair run structure:

```
run.job_states[job_name] = {
    "1": {"status": "done", "detail": "/path/node1_image.png"},  # seed image
    "4": {"status": "done", "detail": "/path/node4_video.mp4"},  # video (SkyReels)
    "5": {"status": "done", "detail": "In the IBM Pavilion..."},  # poem (text)
    "6": {"status": "done", "detail": "/path/node6_image.png"},  # poem image
}
```

The portfolio view needs to extract these into a structured `JobArtifacts` dict. This extraction logic is pure Python and fully testable without GTK.

**Node classification** (derived from the spec's `phases_from_spec()` output):
- `model == "FLUX"` and `label != "Flux.1-schnell"` → seed image (first FLUX node)
- `model == "SkyReels"` → video
- `model == "Llama"` → poem (text)
- `model == "FLUX"` and `label == "Flux.1-schnell"` → poem image (second FLUX node)
- Everything else → utility artifacts (shown in collapsed "More" section)

---

## Task 1: Data extraction helper (pure Python, testable)

**Files:**
- Create: `app/pipeline_portfolio_view.py`
- Create: `tests/test_pipeline_portfolio_view.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_pipeline_portfolio_view.py`:

```python
"""Tests for PipelinePortfolioView data extraction — no GTK required."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def make_phases():
    return [
        {"id": "1", "label": "Generate",       "model": "FLUX"},
        {"id": "2", "label": "Blip",            "model": "BLIP"},
        {"id": "3", "label": "Rmbg",            "model": "RMBG"},
        {"id": "4", "label": "Depth",           "model": "GLPN"},
        {"id": "5", "label": "Compose",         "model": "compose"},
        {"id": "6", "label": "Skyreels",        "model": "SkyReels"},
        {"id": "7", "label": "Artgen",          "model": "Llama"},
        {"id": "8", "label": "Flux.1-schnell",  "model": "FLUX"},
        {"id": "9", "label": "Collect",         "model": "save"},
    ]


def make_job_states():
    return {
        "1": {"status": "done", "detail": "/tmp/node1_seed.png",  "elapsed_s": 3.1},
        "2": {"status": "done", "detail": "The IBM pavilion...",   "elapsed_s": 5.0},
        "3": {"status": "done", "detail": "/tmp/node3_fg.png",    "elapsed_s": 8.0},
        "4": {"status": "done", "detail": "/tmp/node4_depth.png", "elapsed_s": 33.0},
        "5": {"status": "done", "detail": "IBM Pavilion prompt",  "elapsed_s": 0.1},
        "6": {"status": "done", "detail": "/tmp/node6_video.mp4", "elapsed_s": 600.0},
        "7": {"status": "done", "detail": "In the dome,\nVisitors rise", "elapsed_s": 5.0},
        "8": {"status": "done", "detail": "/tmp/node8_poem.png",  "elapsed_s": 3.0},
        "9": {"status": "done", "detail": "",                     "elapsed_s": 0.5},
    }


def test_extract_seed_image():
    from pipeline_portfolio_view import extract_job_artifacts
    artifacts = extract_job_artifacts(make_job_states(), make_phases())
    assert artifacts["seed_image"] == "/tmp/node1_seed.png"


def test_extract_video():
    from pipeline_portfolio_view import extract_job_artifacts
    artifacts = extract_job_artifacts(make_job_states(), make_phases())
    assert artifacts["video"] == "/tmp/node6_video.mp4"


def test_extract_poem_text():
    from pipeline_portfolio_view import extract_job_artifacts
    artifacts = extract_job_artifacts(make_job_states(), make_phases())
    assert artifacts["poem"] == "In the dome,\nVisitors rise"


def test_extract_poem_image():
    from pipeline_portfolio_view import extract_job_artifacts
    artifacts = extract_job_artifacts(make_job_states(), make_phases())
    assert artifacts["poem_image"] == "/tmp/node8_poem.png"


def test_extract_missing_video_returns_none():
    from pipeline_portfolio_view import extract_job_artifacts
    states = make_job_states()
    del states["6"]
    artifacts = extract_job_artifacts(states, make_phases())
    assert artifacts["video"] is None


def test_extract_skipped_node_returns_none():
    from pipeline_portfolio_view import extract_job_artifacts
    states = make_job_states()
    states["6"] = {"status": "skipped", "detail": "fog/exterior"}
    artifacts = extract_job_artifacts(states, make_phases())
    assert artifacts["video"] is None


def test_extract_partial_run_no_crash():
    from pipeline_portfolio_view import extract_job_artifacts
    # Only seed image done, rest pending/missing
    states = {"1": {"status": "done", "detail": "/tmp/seed.png"}}
    artifacts = extract_job_artifacts(states, make_phases())
    assert artifacts["seed_image"] == "/tmp/seed.png"
    assert artifacts["video"] is None
    assert artifacts["poem"] is None
    assert artifacts["poem_image"] is None


def test_run_has_portfolio_artifacts():
    from pipeline_portfolio_view import run_has_portfolio_artifacts
    states = make_job_states()
    # Needs at least seed image + one other artifact
    assert run_has_portfolio_artifacts({"job1": states}, make_phases()) is True


def test_run_without_artifacts_not_portfolio():
    from pipeline_portfolio_view import run_has_portfolio_artifacts
    # Only text nodes — nothing visually rich
    states = {
        "5": {"status": "done", "detail": "a prompt"},
        "7": {"status": "done", "detail": "a poem"},
    }
    assert run_has_portfolio_artifacts({"job1": states}, make_phases()) is False
```

- [ ] **Step 2: Run to confirm ModuleNotFoundError**

```bash
cd ~/code/tt-local-generator
/usr/bin/python3 -m pytest tests/test_pipeline_portfolio_view.py -v 2>&1 | head -5
```

Expected: `ModuleNotFoundError: No module named 'pipeline_portfolio_view'`

- [ ] **Step 3: Implement `extract_job_artifacts` and `run_has_portfolio_artifacts`**

Create `app/pipeline_portfolio_view.py`:

```python
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
PipelinePortfolioView — card-based portfolio display for completed pipeline runs.

Two logical parts:
  Pure helpers (extract_job_artifacts, run_has_portfolio_artifacts) — no GTK,
  fully testable. Extract the narrative artifacts from a job's node states.

  PipelinePortfolioView (GTK) — scrollable Gtk.Box of PortfolioJobCards, one
  per job. Each card shows: seed image, inline video (autoplay loop), poem
  text, poem image. Thumbnails load asynchronously; video autoplays silently.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


# ── Pure data helpers ─────────────────────────────────────────────────────────

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_VIDEO_SUFFIXES = {".mp4", ".webm", ".mov"}

# Which model produces which narrative artifact.
# "FLUX" appears twice (seed + poem image); the FIRST FLUX node is the seed,
# the LAST FLUX node is the poem image.
_SEED_MODELS    = {"FLUX"}
_VIDEO_MODELS   = {"SkyReels"}
_POEM_MODELS    = {"Llama"}
# poem image = last FLUX node (detected by position, not model name alone)


def extract_job_artifacts(
    job_states: dict[str, dict],
    phases: list[dict],
) -> dict[str, Optional[str]]:
    """Extract the four narrative artifacts from one job's node states.

    Returns:
        {
            "seed_image":  str path or None,
            "video":       str path or None,
            "poem":        str text or None,
            "poem_image":  str path or None,
        }

    Nodes with status != "done" or with empty/missing detail are treated as None.
    Text nodes (poems, captions) are detected by their detail not being a file path.
    """
    result: dict[str, Optional[str]] = {
        "seed_image": None,
        "video":      None,
        "poem":       None,
        "poem_image": None,
    }

    # Build a lookup: node_id → model from phases
    node_to_model = {p["id"]: p["model"] for p in phases}

    # Collect all FLUX done nodes in order to distinguish seed vs poem image
    flux_image_nodes: list[tuple[int, str]] = []  # (node_id_int, path)

    for node_id, state in job_states.items():
        if state.get("status") != "done":
            continue
        detail = state.get("detail", "")
        if not detail:
            continue

        model = node_to_model.get(node_id, "")
        is_file_path = detail.startswith("/")
        suffix = Path(detail).suffix.lower() if is_file_path else ""
        is_image = suffix in _IMAGE_SUFFIXES
        is_video = suffix in _VIDEO_SUFFIXES

        if model in _VIDEO_MODELS and is_video and Path(detail).exists():
            result["video"] = detail
        elif model in _POEM_MODELS and not is_file_path:
            result["poem"] = detail
        elif model in _SEED_MODELS and is_image:
            # Collect all FLUX image nodes; assign seed/poem_image after loop
            try:
                flux_image_nodes.append((int(node_id), detail))
            except ValueError:
                pass

    # First FLUX image node = seed, last = poem image
    if flux_image_nodes:
        flux_image_nodes.sort(key=lambda x: x[0])
        first_path = flux_image_nodes[0][1]
        last_path  = flux_image_nodes[-1][1]
        if Path(first_path).exists():
            result["seed_image"] = first_path
        if len(flux_image_nodes) > 1 and Path(last_path).exists():
            result["poem_image"] = last_path
        elif len(flux_image_nodes) == 1 and Path(first_path).exists():
            # Only one FLUX node — treat as seed image only
            result["seed_image"] = first_path

    return result


def run_has_portfolio_artifacts(
    job_states_by_job: dict[str, dict[str, dict]],
    phases: list[dict],
) -> bool:
    """Return True if at least one job has a seed image AND one other artifact.

    Used to decide whether to show the portfolio view or the phase grid for
    a completed run.
    """
    for job_states in job_states_by_job.values():
        arts = extract_job_artifacts(job_states, phases)
        has_seed = arts["seed_image"] is not None
        has_other = any(arts[k] is not None for k in ("video", "poem", "poem_image"))
        if has_seed and has_other:
            return True
    return False
```

- [ ] **Step 4: Run tests — all 9 must pass**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_portfolio_view.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Run full suite — no regressions**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=short 2>/dev/null | tail -3
```

Expected: 575 passed (566 + 9), 1 skipped.

- [ ] **Step 6: Commit**

```bash
git add app/pipeline_portfolio_view.py tests/test_pipeline_portfolio_view.py
git commit -m "feat: pipeline_portfolio_view — extract_job_artifacts + run_has_portfolio_artifacts helpers"
```

---

## Task 2: PortfolioJobCard GTK widget

**Files:**
- Modify: `app/pipeline_portfolio_view.py` — add `PortfolioJobCard` class inside `if _GTK_AVAILABLE:`

- [ ] **Step 1: Add GTK imports and `PortfolioJobCard` class**

Append to `app/pipeline_portfolio_view.py` after the pure helpers:

```python
# ── GTK portfolio widgets ─────────────────────────────────────────────────────

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    _GTK_AVAILABLE = True
except ImportError:
    _GTK_AVAILABLE = False


if _GTK_AVAILABLE:
    import threading

    class PortfolioJobCard(Gtk.Box):
        """
        A single job's portfolio card — displayed vertically in the portfolio view.

        Layout (top to bottom):
          ┌─ job name header ──────────────────────────────┐
          │ seed image (square, full width)                │
          │ video (960×544 aspect, autoplay muted loop)    │
          │ poem text (italic, readable size)              │
          │ poem image (square, full width)                │
          └────────────────────────────────────────────────┘

        Images and videos load asynchronously; placeholders shown immediately.
        All widget updates from threads go through GLib.idle_add.
        """

        # Card width in pixels — portfolio fills available space, cards are uniform
        CARD_WIDTH = 360

        def __init__(
            self,
            job_name: str,
            artifacts: dict[str, Optional[str]],
            on_artifact_click: Optional[callable] = None,
        ) -> None:
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self._job_name = job_name
            self._artifacts = artifacts
            self._on_click = on_artifact_click
            self.add_css_class("portfolio-job-card")
            self.set_size_request(self.CARD_WIDTH, -1)
            self._build()

        def _build(self) -> None:
            # ── Job name header ───────────────────────────────────────────────
            hdr = Gtk.Label(label=self._job_name)
            hdr.set_xalign(0)
            hdr.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
            hdr.set_margin_start(12)
            hdr.set_margin_end(12)
            hdr.set_margin_top(10)
            hdr.set_margin_bottom(6)
            hdr.add_css_class("portfolio-card-title")
            self.append(hdr)

            # ── Seed image ────────────────────────────────────────────────────
            path = self._artifacts.get("seed_image")
            self.append(self._make_image_section(path, "seed image · FLUX"))

            # ── Video ─────────────────────────────────────────────────────────
            video_path = self._artifacts.get("video")
            self.append(self._make_video_section(video_path))

            # ── Poem text ─────────────────────────────────────────────────────
            poem = self._artifacts.get("poem")
            if poem:
                self.append(self._make_poem_section(poem))

            # ── Poem image ────────────────────────────────────────────────────
            poem_img = self._artifacts.get("poem_image")
            if poem_img:
                self.append(self._make_image_section(poem_img, "poem image · FLUX"))

        def _make_image_section(self, path: Optional[str], label_text: str) -> Gtk.Box:
            """Square image section with async thumbnail load."""
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.add_css_class("portfolio-image-section")

            # Fixed-aspect placeholder container
            frame = Gtk.Box()
            frame.set_size_request(self.CARD_WIDTH, self.CARD_WIDTH)
            frame.add_css_class("portfolio-image-frame")
            frame.set_halign(Gtk.Align.FILL)

            if path and Path(path).exists():
                # Placeholder label replaced asynchronously
                placeholder = Gtk.Label(label="🖼")
                placeholder.set_halign(Gtk.Align.CENTER)
                placeholder.set_valign(Gtk.Align.CENTER)
                frame.append(placeholder)
                self._load_image_async(path, frame, placeholder)
                if self._on_click:
                    gesture = Gtk.GestureClick()
                    gesture.connect(
                        "pressed",
                        lambda g, n, x, y, p=path: self._on_click(p, "image")
                    )
                    frame.add_controller(gesture)
                    frame.add_css_class("portfolio-clickable")
            else:
                empty = Gtk.Label(label="—")
                empty.add_css_class("muted")
                empty.set_halign(Gtk.Align.CENTER)
                empty.set_valign(Gtk.Align.CENTER)
                frame.append(empty)

            box.append(frame)

            lbl = Gtk.Label(label=label_text)
            lbl.set_xalign(0)
            lbl.set_margin_start(12)
            lbl.set_margin_top(3)
            lbl.set_margin_bottom(6)
            lbl.add_css_class("portfolio-artifact-label")
            box.append(lbl)
            return box

        def _make_video_section(self, path: Optional[str]) -> Gtk.Box:
            """16:9 video section with autoplay muted loop."""
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.add_css_class("portfolio-video-section")

            vid_w = self.CARD_WIDTH
            vid_h = int(vid_w * 544 / 960)  # 960×544 SkyReels native ratio

            frame = Gtk.Box()
            frame.set_size_request(vid_w, vid_h)
            frame.add_css_class("portfolio-image-frame")

            if path and Path(path).exists():
                video = Gtk.Video()
                video.set_autoplay(True)
                video.set_loop(True)
                video.set_size_request(vid_w, vid_h)
                video.set_halign(Gtk.Align.FILL)
                video.set_valign(Gtk.Align.FILL)
                from gi.repository import Gio
                video.set_file(Gio.File.new_for_path(path))
                frame.append(video)
                if self._on_click:
                    gesture = Gtk.GestureClick()
                    gesture.connect(
                        "pressed",
                        lambda g, n, x, y, p=path: self._on_click(p, "video")
                    )
                    frame.add_controller(gesture)
                    frame.add_css_class("portfolio-clickable")
            else:
                placeholder = Gtk.Label(label="▶  no video")
                placeholder.add_css_class("muted")
                placeholder.set_halign(Gtk.Align.CENTER)
                placeholder.set_valign(Gtk.Align.CENTER)
                frame.append(placeholder)

            box.append(frame)

            lbl = Gtk.Label(label="video · SkyReels V2 I2V")
            lbl.set_xalign(0)
            lbl.set_margin_start(12)
            lbl.set_margin_top(3)
            lbl.set_margin_bottom(6)
            lbl.add_css_class("portfolio-artifact-label")
            box.append(lbl)
            return box

        def _make_poem_section(self, poem: str) -> Gtk.Box:
            """Readable poem text block."""
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.add_css_class("portfolio-poem-section")
            box.set_margin_start(12)
            box.set_margin_end(12)
            box.set_margin_top(8)
            box.set_margin_bottom(8)

            lbl = Gtk.Label(label="poem · Llama")
            lbl.set_xalign(0)
            lbl.add_css_class("portfolio-artifact-label")
            box.append(lbl)

            poem_lbl = Gtk.Label()
            poem_lbl.set_markup(f"<i>{GLib.markup_escape_text(poem)}</i>")
            poem_lbl.set_xalign(0)
            poem_lbl.set_wrap(True)
            poem_lbl.set_max_width_chars(38)
            poem_lbl.set_margin_top(4)
            poem_lbl.add_css_class("portfolio-poem-text")
            if self._on_click:
                gesture = Gtk.GestureClick()
                gesture.connect(
                    "pressed",
                    lambda g, n, x, y, p=poem: self._on_click(p, "text")
                )
                poem_lbl.add_controller(gesture)
            box.append(poem_lbl)
            return box

        @staticmethod
        def _load_image_async(
            path: str,
            container: Gtk.Box,
            placeholder: Gtk.Label,
        ) -> None:
            """Load image as GdkPixbuf in a daemon thread; swap in on main thread."""
            card_w = PortfolioJobCard.CARD_WIDTH

            def _load() -> None:
                try:
                    from gi.repository import GdkPixbuf
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        path, card_w, card_w, True
                    )
                except Exception:
                    return

                def _swap(pb=pb, c=container, ph=placeholder) -> bool:
                    try:
                        c.remove(ph)
                        img = Gtk.Image.new_from_pixbuf(pb)
                        img.set_size_request(card_w, card_w)
                        img.set_halign(Gtk.Align.FILL)
                        img.set_valign(Gtk.Align.FILL)
                        c.append(img)
                    except Exception:
                        pass
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(_swap)

            threading.Thread(target=_load, daemon=True).start()
```

- [ ] **Step 2: Run tests — existing 9 still pass, no GTK needed**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_portfolio_view.py -v 2>/dev/null | tail -3
```

Expected: 9 passed.

- [ ] **Step 3: Commit**

```bash
git add app/pipeline_portfolio_view.py
git commit -m "feat: PortfolioJobCard — seed image, autoplay video, poem text, poem image with async loading"
```

---

## Task 3: PipelinePortfolioView container + CSS

**Files:**
- Modify: `app/pipeline_portfolio_view.py` — add `PipelinePortfolioView` class
- Modify: `app/main_window.py` — add portfolio CSS to the CSS block (~line 828)

- [ ] **Step 1: Add CSS to main_window.py**

Find the line `button.pipeline-source-btn:checked {` in `app/main_window.py`. Immediately before that block add:

```css
/* -- Pipeline portfolio view ----------------------------------------------- */
.portfolio-job-card {
    background-color: @tt_bg_card;
    border: 1px solid @tt_border;
    border-radius: 8px;
    overflow: hidden;
}
.portfolio-card-title {
    font-size: 13px;
    font-weight: 700;
    color: @tt_text;
}
.portfolio-image-frame {
    background-color: #000;
    overflow: hidden;
}
.portfolio-artifact-label {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: @tt_text_muted;
}
.portfolio-poem-section {
    background-color: @tt_bg_dark;
    border-top: 1px solid @tt_border;
    border-bottom: 1px solid @tt_border;
}
.portfolio-poem-text {
    font-size: 12px;
    color: @tt_text2;
    line-height: 1.7;
}
.portfolio-video-section { }
.portfolio-image-section { }
.portfolio-clickable { cursor: default; }
```

Note: GTK4 CSS does not support `cursor: pointer` — do NOT add it. Clickability is communicated by hover background instead.

Actually add a hover effect for clickable sections. Replace `.portfolio-clickable { cursor: default; }` with:
```css
.portfolio-clickable:hover { background-color: alpha(@tt_accent, 0.08); }
```

- [ ] **Step 2: Add `PipelinePortfolioView` to `pipeline_portfolio_view.py`**

Append inside the `if _GTK_AVAILABLE:` block, after `PortfolioJobCard`:

```python
    class PipelinePortfolioView(Gtk.ScrolledWindow):
        """
        Scrollable horizontal strip of PortfolioJobCards — one per pipeline job.

        Replaces the PhaseGridWidget in the center pane when a completed run
        is loaded. Each card is CARD_WIDTH px wide; the user scrolls horizontally
        to browse jobs.

        Usage:
            view = PipelinePortfolioView(on_artifact_click=callback)
            view.load_run(run_record, phases)
            gallery_stack.add_named(view, "pipeline-portfolio")
        """

        def __init__(
            self,
            on_artifact_click: Optional[callable] = None,
        ) -> None:
            super().__init__()
            self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
            self.set_vexpand(True)
            self.set_hexpand(True)
            self._on_artifact_click = on_artifact_click
            self._cards_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=16,
            )
            self._cards_box.set_margin_start(16)
            self._cards_box.set_margin_end(16)
            self._cards_box.set_margin_top(12)
            self._cards_box.set_margin_bottom(12)
            self.set_child(self._cards_box)

        def load_run(
            self,
            run: dict,
            phases: list[dict],
        ) -> None:
            """Populate the portfolio from a PipelineStore run record.

            Clears existing cards and builds one PortfolioJobCard per job,
            in the order defined by run["jobs"]. Jobs with no artifacts get
            a minimal card showing the job name only.

            Must be called on the GTK main thread.
            """
            # Clear existing cards
            while child := self._cards_box.get_first_child():
                self._cards_box.remove(child)

            jobs_order = [j["name"] for j in run.get("jobs", [])]
            job_states_map = run.get("job_states", {})

            for job_name in jobs_order:
                job_states = job_states_map.get(job_name, {})
                artifacts = extract_job_artifacts(job_states, phases)
                card = PortfolioJobCard(
                    job_name=job_name,
                    artifacts=artifacts,
                    on_artifact_click=self._on_artifact_click,
                )
                self._cards_box.append(card)

        def clear(self) -> None:
            """Remove all cards (called when switching away from a completed run)."""
            while child := self._cards_box.get_first_child():
                self._cards_box.remove(child)
```

- [ ] **Step 3: Run tests — still 9 passing**

```bash
/usr/bin/python3 -m pytest tests/test_pipeline_portfolio_view.py -v 2>/dev/null | tail -3
```

- [ ] **Step 4: Run full suite**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=short 2>/dev/null | tail -3
```

Expected: 575 passed, 1 skipped.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline_portfolio_view.py app/main_window.py
git commit -m "feat: PipelinePortfolioView container + CSS — horizontal scrollable job card strip"
```

---

## Task 4: Wire into MainWindow

**Files:**
- Modify: `app/main_window.py` — add portfolio view to gallery stack, toggle between grid and portfolio

The key logic: when `_on_pipeline_load_run()` is called for a **completed** run that has portfolio artifacts → show `PipelinePortfolioView`. When a run is **active** → show `PhaseGridWidget`. When a run is **failed** with partial work → show `PhaseGridWidget` (so user can see which cells failed and retry).

- [ ] **Step 1: Import and instantiate PipelinePortfolioView in `_build()`**

Find the block in `app/main_window.py` around line 7335 that creates `_pipeline_panel` and `_phase_grid`. Add after `_phase_grid`:

```python
        from pipeline_portfolio_view import PipelinePortfolioView
        self._pipeline_portfolio = PipelinePortfolioView(
            on_artifact_click=self._on_pipeline_portfolio_artifact_click,
        )
        self._gallery_stack.add_named(self._pipeline_portfolio, "pipeline-portfolio")
```

- [ ] **Step 2: Add `_on_pipeline_portfolio_artifact_click` callback**

Add this method to `MainWindow` near the other pipeline callbacks:

```python
    def _on_pipeline_portfolio_artifact_click(
        self, detail: str, artifact_type: str
    ) -> None:
        """Called when user clicks an artifact in the portfolio view.

        Routes to the detail pane exactly like phase grid cell clicks,
        using the same _on_pipeline_cell_click logic with a synthetic node_id.
        """
        self._on_pipeline_cell_click("portfolio", "?", detail)
```

- [ ] **Step 3: Update `_on_pipeline_load_run()` to choose grid vs portfolio**

Find `_on_pipeline_load_run()` in `app/main_window.py`. Replace the body with:

```python
    def _on_pipeline_load_run(self, run_id: str) -> None:
        """Load a past run — shows portfolio view for done runs, grid for others."""
        from pipeline_store import PipelineStore
        from phase_grid_widget import GridState
        from pipeline_panel import phases_from_spec
        from pipeline_portfolio_view import run_has_portfolio_artifacts

        store = PipelineStore()
        run = store.get_run(run_id)
        if not run:
            return

        phases = phases_from_spec(run.get("spec_path", ""))
        status = run.get("status", "")

        # Decide which center view to use
        use_portfolio = (
            status == "done"
            and run_has_portfolio_artifacts(run.get("job_states", {}), phases)
            and hasattr(self, "_pipeline_portfolio")
        )

        if use_portfolio:
            # Load portfolio view and show it
            self._pipeline_portfolio.load_run(run, phases)
            self._gallery_stack.set_visible_child_name("pipeline-portfolio")
        else:
            # Fall back to phase grid (active run, failed run, or no visual artifacts)
            state = GridState.from_run_record(run, phases)
            if hasattr(self, "_phase_grid"):
                self._phase_grid._state = state
                self._phase_grid._build()
            self._gallery_stack.set_visible_child_name("pipeline")
```

- [ ] **Step 4: Update `_on_pipeline_run()` to always show the grid (run is starting)**

Find `_on_pipeline_run()` in `app/main_window.py`. After rebuilding the phase grid state, ensure the grid (not portfolio) is shown:

```python
    def _on_pipeline_run(self, jobs: list, spec_path: str, param_overrides: dict) -> None:
        from pipeline_runner import PipelineRunner
        from phase_grid_widget import GridState
        from pipeline_panel import phases_from_spec
        phases = phases_from_spec(spec_path)
        state = GridState(jobs=[j["name"] for j in jobs], phases=phases)
        self._phase_grid._state = state
        self._phase_grid._build()
        # Always show the phase grid while a run is active
        self._gallery_stack.set_visible_child_name("pipeline")
        self._pipeline_runner = PipelineRunner(idle_add=GLib.idle_add)
        self._pipeline_runner.start(
            spec_path=spec_path,
            jobs=jobs,
            param_overrides=param_overrides,
            on_node_update=self._on_pipeline_node_update,
            on_run_finished=self._on_pipeline_run_finished,
        )
```

- [ ] **Step 5: Update `_on_pipeline_run_finished()` to switch to portfolio on success**

Find `_on_pipeline_run_finished()`. Add a portfolio switch after the panel update:

```python
    def _on_pipeline_run_finished(self, success: bool) -> None:
        """Called on GTK main thread when run subprocess exits."""
        if hasattr(self, "_pipeline_panel"):
            self._pipeline_panel.set_running(
                False,
                "✅ Pipeline complete — scroll right to browse" if success
                else "❌ Pipeline failed — check grid for details"
            )
        if success and hasattr(self, "_pipeline_runner") and self._pipeline_runner:
            run_id = self._pipeline_runner._run_id
            if run_id:
                # Give the store a moment to finish writing then switch to portfolio
                GLib.timeout_add(800, lambda rid=run_id: (
                    self._on_pipeline_load_run(rid) or GLib.SOURCE_REMOVE
                ))
```

- [ ] **Step 6: Update `_restore_pipeline_run()` — already calls `_on_pipeline_load_run`**

`_restore_pipeline_run()` already calls `_on_pipeline_load_run()` which now handles the portfolio/grid decision automatically. No change needed here.

- [ ] **Step 7: Update `_on_source_change()` — ensure portfolio is cleared when leaving pipeline**

Find `_on_source_change()`. In the `else` branch (leaving pipeline), add:

```python
        else:
            # Leaving pipeline — clear portfolio to free memory
            if hasattr(self, "_pipeline_portfolio"):
                GLib.idle_add(self._pipeline_portfolio.clear)
            # ... rest of existing else block unchanged
```

Add this BEFORE the existing `if hasattr(self, "_pipeline_panel")` check.

- [ ] **Step 8: Run tests**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=short 2>/dev/null | tail -3
```

Expected: 575 passed, 1 skipped.

- [ ] **Step 9: Quick smoke test**

```bash
timeout 8 /usr/bin/python3 app/main.py 2>&1 | grep -iE "error|traceback|exception" | head -5 || echo "launch ok"
```

- [ ] **Step 10: Commit**

```bash
git add app/main_window.py
git commit -m "feat: wire PipelinePortfolioView into MainWindow — portfolio for done runs, grid for active/failed"
```

---

## Task 5: Import existing World's Fair run into store (data seeding)

The production `pipeline-index.json` must have a run record with the correct artifact paths for the portfolio view to display on first launch. This task verifies the existing data and re-seeds if needed.

- [ ] **Step 1: Verify the store has a valid done run**

```bash
cd ~/code/tt-local-generator
python3 -c "
import sys; sys.path.insert(0,'app')
from pipeline_store import PipelineStore
from pipeline_panel import phases_from_spec
from pipeline_portfolio_view import run_has_portfolio_artifacts
store = PipelineStore()
runs = store.list_runs(limit=3)
for r in runs:
    phases = phases_from_spec(r.get('spec_path',''))
    has_p = run_has_portfolio_artifacts(r.get('job_states',{}), phases)
    print(r['id'][:8], r['status'], 'has_portfolio:', has_p, len(r['jobs']), 'jobs')
"
```

Expected output: at least one run with `status: done` and `has_portfolio: True`.

- [ ] **Step 2: If no valid run, re-seed with the World's Fair artifacts**

Only run this step if Step 1 shows no valid portfolio run. The World's Fair videos live at `~/.local/share/tt-local-generator/workflow-runs/20260602_092516_5fairs/`.

```bash
python3 << 'EOF'
import sys, json
from pathlib import Path
sys.path.insert(0, 'app')
from pipeline_store import PipelineStore

RUN_ROOT = Path.home() / ".local/share/tt-local-generator/workflow-runs/20260602_092516_5fairs"
SPEC_PATH = str(Path("docs/examples/workflows/1964-worlds-fair.json").resolve())
FAIRS = [
    ("1964-ny",      "1964 NY — IBM People Wall"),
    ("1939-ny",      "1939 NY — Elektro the Robot"),
    ("1893-chicago", "1893 Chicago — Tesla's Light"),
    ("1970-osaka",   "1970 Osaka — Fog Pavilion"),
    ("1967-montreal","1967 Montreal — Circle-Vision"),
]

store = PipelineStore()
jobs = [{"name": n, "enabled": True} for _, n in FAIRS]
run_id = store.create_run(SPEC_PATH, "World's Fair 2026", jobs, {}, 0, "")
store.update_output_dir(run_id, str(RUN_ROOT))
for fair_key, fair_name in FAIRS:
    rj = RUN_ROOT / fair_key / "results.json"
    if not rj.exists(): continue
    data = json.loads(rj.read_text())
    for nid, nd in data.items():
        if not nid.isdigit(): continue
        for k in ["video_path","image_path","depth_path","poem","video_prompt"]:
            if k in nd and nd[k]:
                store.update_node(run_id, fair_name, nid, "done", str(nd[k])[:120])
                break
store.finish_run(run_id, success=True)
print(f"Seeded run {run_id[:8]}")
EOF
```

- [ ] **Step 3: Launch the app and verify portfolio appears**

```bash
./tt-gen &
```

Expected: App opens with Pipeline tab active, center pane shows 5 horizontal job cards with seed images and videos visible. Clicking a video card opens the detail pane on the right.

- [ ] **Step 4: Run full test suite one final time**

```bash
/usr/bin/python3 -m pytest tests/ -q --tb=short 2>/dev/null | tail -3
```

- [ ] **Step 5: Final commit and push**

```bash
git add -A
git commit -m "feat: seed World's Fair portfolio run for first-launch experience"
git push
```

---

## Self-review

**Spec coverage check:**

| Requirement | Task |
|---|---|
| Portfolio replaces grid for completed runs | Task 4, `_on_pipeline_load_run` |
| Grid kept for active/failed runs | Task 4, `_on_pipeline_run` + `_on_pipeline_load_run` |
| Each job gets a card | Task 3, `PipelinePortfolioView.load_run` |
| Seed image in card | Task 2, `_make_image_section` |
| Inline video autoplay muted loop | Task 2, `_make_video_section` |
| Poem text readable | Task 2, `_make_poem_section` |
| Poem image in card | Task 2, `_make_image_section` |
| Async thumbnail loading | Task 2, `_load_image_async` |
| Click artifact → detail pane | Task 4, `_on_pipeline_portfolio_artifact_click` |
| Switch to portfolio after run completes | Task 4, `_on_pipeline_run_finished` |
| Clear portfolio when leaving Pipeline tab | Task 4, `_on_source_change` |
| No GTK in pure helpers | Task 1, `extract_job_artifacts` |
| 9 unit tests for helpers | Task 1 |
| World's Fair run visible on first launch | Task 5 |

**Placeholder scan:** None found — all code blocks are complete.

**Type consistency:** `extract_job_artifacts(job_states: dict[str,dict], phases: list[dict]) -> dict[str, Optional[str]]` is consistent across Task 1 (implementation) and Task 3 (`PipelinePortfolioView.load_run` which calls it). `run_has_portfolio_artifacts(job_states_by_job: dict[str,dict[str,dict]], phases: list[dict]) -> bool` matches between Task 1 and Task 4 (`_on_pipeline_load_run`).
