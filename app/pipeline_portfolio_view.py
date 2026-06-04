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

_VIDEO_MODELS = {"SkyReels"}
_POEM_MODELS  = {"Llama"}
# Seed image = first FLUX node (lowest node_id) with an image file
# Poem image = last FLUX node (highest node_id) with an image file,
#              only set when there are 2+ FLUX image nodes in the run


def extract_job_artifacts(
    job_states: dict[str, dict],
    phases: list[dict],
) -> dict[str, Optional[str]]:
    """Extract the four narrative artifacts from one job's node states.

    Walks every node in ``job_states`` and, for nodes whose status is "done"
    and whose detail is non-empty, classifies the output by inspecting the
    node's model type (from ``phases``) and the detail value (file path vs.
    plain text).

    Artifact rules
    ~~~~~~~~~~~~~~
    seed_image
        The detail path of the FLUX node with the **lowest** numeric node_id
        that resolves to an existing image file.  The first FLUX image node is
        the freshly generated seed before any post-processing.

    video
        The detail path of any SkyReels node that resolves to an existing
        video file.

    poem
        The detail text of any Llama node whose detail is *not* a file path
        (i.e. does not start with "/").

    poem_image
        The detail path of the FLUX node with the **highest** numeric node_id
        that resolves to an existing image file, but only when at least two
        FLUX image nodes are present.  The final FLUX image node is the
        poem-illustration generated after text creation.

    Parameters
    ----------
    job_states:
        Mapping of node_id (str) → state dict.  Each state dict must have at
        least ``"status"`` and ``"detail"`` keys.
    phases:
        Ordered list of phase descriptors, each with ``"id"`` and ``"model"``
        keys.  Used to resolve the model type for each node_id.

    Returns
    -------
    dict with keys ``seed_image``, ``video``, ``poem``, ``poem_image`` —
    each is a ``str`` (path or text) or ``None`` when not found.
    """
    result: dict[str, Optional[str]] = {
        "seed_image": None,
        "video":      None,
        "poem":       None,
        "poem_image": None,
    }

    # Build a lookup from node_id string → model name so we can classify each
    # node without iterating phases on every node.
    node_to_model: dict[str, str] = {p["id"]: p["model"] for p in phases}

    # Accumulate all FLUX image nodes so we can pick first/last after the loop.
    flux_image_nodes: list[tuple[int, str]] = []

    for node_id, state in job_states.items():
        # Skip anything that didn't complete successfully.
        if state.get("status") != "done":
            continue

        detail: str = state.get("detail", "")
        if not detail:
            continue

        model = node_to_model.get(node_id, "")

        # Determine whether the detail value is a file path.  We treat strings
        # beginning with "/" as absolute file paths; anything else is plain text
        # (e.g. prompts, poems, BLIP captions).
        is_file_path = detail.startswith("/")
        suffix = Path(detail).suffix.lower() if is_file_path else ""
        is_image = suffix in _IMAGE_SUFFIXES
        is_video_file = suffix in _VIDEO_SUFFIXES

        if model in _VIDEO_MODELS and is_video_file and Path(detail).exists():
            # SkyReels output — the generated video clip.
            result["video"] = detail

        elif model in _POEM_MODELS and not is_file_path:
            # Llama/artgen output — the poem or narrative text.
            result["poem"] = detail

        elif model == "FLUX" and is_image and Path(detail).exists():
            # FLUX image output — collect for first/last selection below.
            try:
                flux_image_nodes.append((int(node_id), detail))
            except ValueError:
                # Non-integer node ids are sorted lexicographically; skip them
                # here to avoid confusing ordering between e.g. "1" and "10".
                pass

    # Assign seed_image (first FLUX) and poem_image (last FLUX, only when ≥2).
    if flux_image_nodes:
        flux_image_nodes.sort(key=lambda x: x[0])
        result["seed_image"] = flux_image_nodes[0][1]
        if len(flux_image_nodes) >= 2:
            result["poem_image"] = flux_image_nodes[-1][1]

    return result


def run_has_portfolio_artifacts(
    job_states_by_job: dict[str, dict[str, dict]],
    phases: list[dict],
) -> bool:
    """Return True if at least one job has a seed image AND one other visual artifact.

    Used to decide whether to show the portfolio view or the phase-grid
    spreadsheet for a completed run.  Requires at minimum:

        seed_image  +  (video  OR  poem_image)

    A run with only text outputs (poems, prompts) does not qualify — the
    portfolio view is intended for runs that produced imagery.

    Parameters
    ----------
    job_states_by_job:
        Mapping of job_name → job_states dict (same shape as the ``job_states``
        argument of ``extract_job_artifacts``).
    phases:
        Same phase descriptor list passed to ``extract_job_artifacts``.
    """
    for job_states in job_states_by_job.values():
        arts = extract_job_artifacts(job_states, phases)
        has_seed = arts["seed_image"] is not None
        has_visual_other = arts["video"] is not None or arts["poem_image"] is not None
        if has_seed and has_visual_other:
            return True
    return False


# ── GTK portfolio widgets ─────────────────────────────────────────────────────

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk  # noqa: F401
    _GTK_AVAILABLE = True
except ImportError:
    _GTK_AVAILABLE = False

if _GTK_AVAILABLE:
    import threading as _threading

    class PortfolioJobCard(Gtk.Box):
        """
        A single pipeline job's portfolio card — vertical layout:
          job title → seed image → video (autoplay muted) → poem text → poem image

        Images load asynchronously via daemon threads + GLib.idle_add.
        Clicking any artifact calls on_artifact_click(detail, artifact_type).
        """

        CARD_WIDTH = 360

        def __init__(
            self,
            job_name: str,
            artifacts: dict,
            on_artifact_click=None,
        ) -> None:
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self._job_name = job_name
            self._artifacts = artifacts
            self._on_click = on_artifact_click
            self.add_css_class("portfolio-job-card")
            self.set_size_request(self.CARD_WIDTH, -1)
            self._build()

        def _build(self) -> None:
            # Job name header
            hdr = Gtk.Label(label=self._job_name)
            hdr.set_xalign(0)
            hdr.set_ellipsize(3)
            hdr.set_margin_start(12)
            hdr.set_margin_end(12)
            hdr.set_margin_top(10)
            hdr.set_margin_bottom(6)
            hdr.add_css_class("portfolio-card-title")
            self.append(hdr)

            # Seed image
            self.append(self._make_image_section(
                self._artifacts.get("seed_image"), "seed image · FLUX"
            ))

            # Video
            self.append(self._make_video_section(self._artifacts.get("video")))

            # Poem text (only if present)
            poem = self._artifacts.get("poem")
            if poem:
                self.append(self._make_poem_section(poem))

            # Poem image (only if present)
            poem_img = self._artifacts.get("poem_image")
            if poem_img:
                self.append(self._make_image_section(poem_img, "poem image · FLUX"))

        def _make_image_section(self, path, label_text: str) -> Gtk.Box:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.add_css_class("portfolio-image-section")

            frame = Gtk.Box()
            frame.set_size_request(self.CARD_WIDTH, self.CARD_WIDTH)
            frame.add_css_class("portfolio-image-frame")
            frame.set_halign(Gtk.Align.FILL)

            if path and Path(path).exists():
                placeholder = Gtk.Label(label="🖼")
                placeholder.set_halign(Gtk.Align.CENTER)
                placeholder.set_valign(Gtk.Align.CENTER)
                frame.append(placeholder)
                self._load_image_async(path, frame, placeholder)
                if self._on_click:
                    gesture = Gtk.GestureClick()
                    gesture.connect(
                        "pressed",
                        lambda g, n, x, y, p=path: self._on_click(p, "image"),
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

        def _make_video_section(self, path) -> Gtk.Box:
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
                        lambda g, n, x, y, p=path: self._on_click(p, "video"),
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
            poem_lbl.set_markup(
                f"<i>{GLib.markup_escape_text(poem)}</i>"
            )
            poem_lbl.set_xalign(0)
            poem_lbl.set_wrap(True)
            poem_lbl.set_max_width_chars(38)
            poem_lbl.set_margin_top(4)
            poem_lbl.add_css_class("portfolio-poem-text")
            if self._on_click:
                gesture = Gtk.GestureClick()
                gesture.connect(
                    "pressed",
                    lambda g, n, x, y, p=poem: self._on_click(p, "text"),
                )
                poem_lbl.add_controller(gesture)
            box.append(poem_lbl)
            return box

        @staticmethod
        def _load_image_async(path: str, container: Gtk.Box, placeholder: Gtk.Label) -> None:
            """Load pixbuf off-thread, swap placeholder on main thread via idle_add."""
            card_w = PortfolioJobCard.CARD_WIDTH

            def _load() -> None:
                # NOTE: no GTK widget calls are allowed here — we are on a
                # background daemon thread.  Only GLib.idle_add is safe.
                try:
                    from gi.repository import GdkPixbuf
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, card_w, card_w, True)
                except Exception:
                    return

                def _swap(pb=pb, c=container, ph=placeholder) -> bool:
                    # Runs on the GTK main thread via idle_add — widget ops safe.
                    # Default-arg capture (pb=pb etc.) prevents late-binding bugs.
                    try:
                        # Guard: if the container was detached by a card/grid
                        # rebuild that ran before this idle callback fired,
                        # skip silently to avoid operating on orphaned widgets.
                        if c.get_parent() is None:
                            return GLib.SOURCE_REMOVE
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

            _threading.Thread(target=_load, daemon=True).start()

    class PipelinePortfolioView(Gtk.ScrolledWindow):
        """
        Horizontal scrollable strip of PortfolioJobCards — one per pipeline job.

        Replaces PhaseGridWidget in the gallery stack for completed runs.
        Load with load_run(run_record, phases). Clear with clear().
        """

        def __init__(self, on_artifact_click=None) -> None:
            super().__init__()
            # Both axes scroll: horizontal to browse jobs, vertical for tall cards
            self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
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

        def load_run(self, run: dict, phases: list[dict]) -> None:
            """Populate cards from a PipelineStore run record. Call on GTK main thread."""
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
            """Remove all cards — call when leaving pipeline mode."""
            while child := self._cards_box.get_first_child():
                self._cards_box.remove(child)
