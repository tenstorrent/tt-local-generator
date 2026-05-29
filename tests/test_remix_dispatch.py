"""Unit tests for dispatch_remix routing logic."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from artgen import RemixContext


def _make_ctx(**kwargs) -> RemixContext:
    defaults = dict(
        source_record={},
        source_type="video",
        target_type="video",
        hint="a red car",
        seed_image_path="",
        ref_video_path="",
        target_label="",
        negative_hint="",
    )
    defaults.update(kwargs)
    return RemixContext(**defaults)


def test_dispatch_video_switches_source():
    from remix_dispatch import dispatch_remix
    controls = MagicMock()
    ctx = _make_ctx(target_type="video", hint="a red car", seed_image_path="/tmp/thumb.jpg")
    dispatch_remix(ctx, controls, MagicMock(), flash_fn=MagicMock())
    controls.switch_to_source.assert_called_once_with("video")
    controls.populate_prompts.assert_called_once_with("a red car", "", "/tmp/thumb.jpg")


def test_dispatch_animate_switches_source():
    from remix_dispatch import dispatch_remix
    controls = MagicMock()
    ctx = _make_ctx(target_type="animate", ref_video_path="/tmp/src.mp4", hint="style guide")
    dispatch_remix(ctx, controls, MagicMock(), flash_fn=MagicMock())
    controls.switch_to_source.assert_called_once_with("animate")
    assert controls._ref_video_path == "/tmp/src.mp4"


def test_dispatch_image_switches_source():
    from remix_dispatch import dispatch_remix
    controls = MagicMock()
    ctx = _make_ctx(target_type="image", hint="a fox")
    dispatch_remix(ctx, controls, MagicMock(), flash_fn=MagicMock())
    controls.switch_to_source.assert_called_once_with("image")


def test_dispatch_same_populates_prompts_only():
    from remix_dispatch import dispatch_remix
    controls = MagicMock()
    ctx = _make_ctx(target_type="same", hint="a red car", negative_hint="blurry")
    dispatch_remix(ctx, controls, MagicMock(), flash_fn=MagicMock())
    controls.populate_prompts.assert_called_once_with("a red car", "blurry")
    controls.switch_to_source.assert_not_called()


def test_dispatch_artgen_target_switches_to_art():
    from remix_dispatch import dispatch_remix
    controls = MagicMock()
    artgen_panel = MagicMock()
    ctx = _make_ctx(target_type="verse", hint="volcanic empire", target_label="Verse")
    dispatch_remix(ctx, controls, artgen_panel, flash_fn=MagicMock())
    controls._src_art_btn.set_active.assert_called_once_with(True)
    artgen_panel.set_generator.assert_called_once_with("verse")
    artgen_panel.set_theme.assert_called_once_with("volcanic empire")


def test_dispatch_flash_status_called():
    from remix_dispatch import dispatch_remix
    flash = MagicMock()
    controls = MagicMock()
    ctx = _make_ctx(target_type="video", target_label="Video (I2V)")
    dispatch_remix(ctx, controls, MagicMock(), flash_fn=flash)
    flash.assert_called_once()
    assert "Video (I2V)" in flash.call_args[0][0]
