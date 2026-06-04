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


# ── GTK portfolio widgets (populated in later tasks) ─────────────────────────

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk  # noqa: F401  (used in later tasks)
    _GTK_AVAILABLE = True
except ImportError:
    _GTK_AVAILABLE = False
