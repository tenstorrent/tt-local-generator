# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
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
