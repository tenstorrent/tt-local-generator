# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Architectural boundary: Remix vs. Workflow
==========================================

Remix — interactive, single-step, immediate
--------------------------------------------
Triggered from a card's context menu (right-click) or hover bar (⟳ button).
The user selects a source type and a target generation type, presses Go, and one
new generation is submitted.  Result appears as a single output card in the gallery.

Implementation path:
  RemixContext (artgen/remix_context.py)
  → dispatch_remix() [this module]
  → controls.switch_to_source() + controls.populate_prompts()
    (or artgen_panel.set_generator() for art targets)
  → normal ControlPanel/ArtgenPanel tab flow, same as a hand-typed prompt

Output record: a standard GenerationRecord.  Remix provenance is optionally
preserved in extra_meta._source_id (the source card's record id) and
extra_meta._transform (the remix action label, e.g. "animate", "reimagine").

Workflow — batch, multi-step, persistent
-----------------------------------------
Triggered from the Workflow toolbar button (⚙) or via `tt-ctl run <spec>`.
Runs a JSON node-graph spec with N processing nodes, producing M output
artifacts that are collected into a named playlist.

Implementation path:
  WorkflowPopover._run_workflow()
  → subprocess: bin/run_workflow.sh <spec.json>
  → GLib.io_add_watch progress listener (_on_run_stdout)
  → playlist stored in history, browsable in gallery

Output records: GenerationRecord objects with model_id starting with "workflow"
(e.g. "workflow", "workflow-v2").  This prefix is the sentinel used throughout
the codebase to exclude these records from per-model video counts.

Could Remix be a 1-node Workflow?
-----------------------------------
Architecturally yes — a single TTLGTextToImage node driven by a RemixContext
would be equivalent.  But the UX intent is different, so they are kept separate:

  Remix    : synchronous-feeling, no board reset, no server switch, stays on
             the current tab, result appears inline in the gallery immediately.
  Workflow : async subprocess, produces a playlist, survives app close, may
             reset the board between nodes to switch models.

They share no code intentionally.  Keeping them separate is correct.

dispatch_remix — pure routing logic for remix actions.

No GTK imports. Tested independently in tests/test_remix_dispatch.py.
Called from MainWindow._dispatch_remix on the GTK main thread.

Routing rules
─────────────
• target_type == "animate"
    Switch to the animate source tab and populate prompts with hint + seed image.
    (Motion reference video not consumed — Animate UI no longer has that picker.)
• target_type in _VIDEO_SOURCES or "video"
    Switch to the video source tab and populate prompts (with optional seed image).
• target_type == "image"
    Switch to the image source tab and populate prompts.
• target_type == "same"
    Populate prompts only — no source switch. Reruns the current source type.
• Any other target_type (artgen generators: "verse", "palette", "landscape", …)
    Activate the Generative Art source button, switch artgen_panel to the named
    generator, and pre-fill its theme/subject entry.

Always calls flash_fn with a human-readable confirmation string.
"""
from __future__ import annotations

from typing import Callable, Any

from artgen import RemixContext

# Video-family source types that all map to the "video" tab in ControlPanel.
_VIDEO_SOURCES = {"wan2", "mochi", "skyreels", "animatediff"}


def dispatch_remix(
    ctx: RemixContext,
    controls: Any,
    artgen_panel: Any,
    flash_fn: Callable[[str], None],
) -> None:
    """Route a RemixContext to the appropriate tab and pre-fill controls.

    Must be called from the GTK main thread (all GTK calls happen here).

    Args:
        ctx         : fully-resolved RemixContext produced by RemixPopover.
        controls    : ControlPanel instance (or a mock in tests).
        artgen_panel: ArtgenPanel instance (or a mock in tests).
        flash_fn    : callable(str) — posts a timed status flash to the status bar.
    """
    target = ctx.target_type

    if target == "animate":
        # Switch to animate source and carry the prompt and seed image.
        # Note: the Animate tab no longer has a separate motion-reference video
        # picker in the UI — character image uses the seed well. ctx.ref_video_path
        # is resolved by RemixPopover but not consumed here; future work if motion
        # reference is restored to the UI.
        controls.switch_to_source("animate")
        controls.populate_prompts(ctx.hint, ctx.negative_hint, ctx.seed_image_path)

    elif target in _VIDEO_SOURCES or target == "video":
        # Any video-family target maps to the standard video source tab.
        controls.switch_to_source("video")
        controls.populate_prompts(ctx.hint, ctx.negative_hint, ctx.seed_image_path)

    elif target == "image":
        # Image generation tab.
        controls.switch_to_source("image")
        controls.populate_prompts(ctx.hint, ctx.negative_hint, ctx.seed_image_path)

    elif target == "same":
        # Re-run the same generation type with updated prompt/negative.
        # Do NOT switch source — keep the current tab active.
        controls.populate_prompts(ctx.hint, ctx.negative_hint)

    else:
        # Artgen target: verse, palette, landscape, skyline, geometric, etc.
        # Activate the 🎨 Generative Art source toggle so the gallery stack
        # switches to ArtgenPanel, then pre-fill the generator and theme.
        controls._src_art_btn.set_active(True)
        artgen_panel.set_generator(target)
        artgen_panel.set_theme(ctx.hint)

    flash_fn(f"Remix ready — {ctx.target_label} ✓")
