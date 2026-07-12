"""
Tests for mounting Pipeline Studio (Discover+Open) in the main window
(SP-C Phase 1, Task 5).

Constructing the full `MainWindow` (ControlPanel, GalleryWidget, DetailPanel,
history load, health workers, ...) is heavy and network/disk dependent, so —
mirroring the existing pattern in tests/test_main_window_animate_inputs.py —
these tests build a minimal `MainWindow` via `__new__` with `Gtk.ApplicationWindow
.__init__` patched out, then hand-populate only the handful of real Gtk widgets
and collaborators the seam under test (`_show_pipelines` / `_hide_pipelines` /
`_on_pipelines_toggled`) actually touches: `_gallery_stack`, `_ctrl_wrapper`,
`_detail_wrap`, and a stand-in `_controls` exposing `get_model_source()`.

`PipelineStore.list_runs` is monkeypatched to a small fixture (following
tests/test_pipeline_studio.py's own convention of pointing pipeline_store's
module-level paths at tmp_path) so PipelineStudio's background load thread
never touches the real user's pipeline history/disk.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the system PyGObject package is importable inside the venv, and that
# app/ is on sys.path for `import main_window` / `import pipeline_studio`.
_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    Gtk.Entry()  # probe: raises without a usable display
except Exception:  # pragma: no cover - environment-dependent
    pytest.skip("no GTK display available", allow_module_level=True)


def _make_mw(tmp_path, monkeypatch):
    """Minimal MainWindow harness exposing only what _show_pipelines touches."""
    import pipeline_store
    monkeypatch.setattr(pipeline_store, "_INDEX_PATH", tmp_path / "pipeline-index.json")
    monkeypatch.setattr(pipeline_store, "_RUNS_DIR", tmp_path / "runs")

    import main_window as mw

    with patch("main_window.Gtk.ApplicationWindow.__init__", return_value=None):
        obj = mw.MainWindow.__new__(mw.MainWindow)

    obj._pipeline_studio = None
    obj._gallery_stack = Gtk.Stack()
    obj._gallery_stack.add_named(Gtk.Box(), "video")
    obj._gallery_stack.set_visible_child_name("video")
    obj._ctrl_wrapper = Gtk.Box()
    obj._ctrl_wrapper.set_visible(True)
    obj._detail_wrap = Gtk.Box()
    obj._detail_wrap.set_visible(True)

    fake_controls = MagicMock()
    fake_controls.get_model_source.return_value = "video"
    obj._controls = fake_controls

    # Bind the real (unbound) methods under test so `self` resolves correctly.
    obj._show_pipelines = mw.MainWindow._show_pipelines.__get__(obj)
    obj._hide_pipelines = mw.MainWindow._hide_pipelines.__get__(obj)
    obj._on_pipelines_toggled = mw.MainWindow._on_pipelines_toggled.__get__(obj)
    obj._on_source_change = mw.MainWindow._on_source_change.__get__(obj)
    obj._rebuild_context_menu = MagicMock()
    obj.lookup_action = MagicMock(return_value=None)

    return obj


def test_show_pipelines_lazily_constructs_pipeline_studio(tmp_path, monkeypatch):
    """First activation constructs PipelineStudio and mounts it on the gallery stack."""
    from pipeline_studio import PipelineStudio

    obj = _make_mw(tmp_path, monkeypatch)
    assert obj._pipeline_studio is None

    obj._show_pipelines()

    assert isinstance(obj._pipeline_studio, PipelineStudio)
    assert obj._gallery_stack.get_child_by_name("pipelines") is obj._pipeline_studio
    assert obj._gallery_stack.get_visible_child_name() == "pipelines"
    # Pipeline Studio is full-width, like artgen mode: side panels collapse.
    assert obj._ctrl_wrapper.get_visible() is False
    assert obj._detail_wrap.get_visible() is False


def test_show_pipelines_reuses_instance_on_second_activation(tmp_path, monkeypatch):
    """Repeat activation doesn't rebuild PipelineStudio (avoids re-scanning history)."""
    obj = _make_mw(tmp_path, monkeypatch)

    obj._show_pipelines()
    first = obj._pipeline_studio
    obj._hide_pipelines()
    obj._show_pipelines()

    assert obj._pipeline_studio is first


def test_hide_pipelines_restores_current_source_view(tmp_path, monkeypatch):
    """Leaving Pipelines restores the gallery/side-panel state for the active source."""
    obj = _make_mw(tmp_path, monkeypatch)

    obj._show_pipelines()
    obj._hide_pipelines()

    assert obj._gallery_stack.get_visible_child_name() == "video"
    assert obj._ctrl_wrapper.get_visible() is True
    assert obj._detail_wrap.get_visible() is True


def test_pipelines_toggle_button_drives_show_and_hide(tmp_path, monkeypatch):
    """The toolbar toggle handler dispatches to _show_pipelines / _hide_pipelines."""
    obj = _make_mw(tmp_path, monkeypatch)
    btn = Gtk.ToggleButton()

    btn.set_active(True)
    obj._on_pipelines_toggled(btn)
    assert obj._gallery_stack.get_visible_child_name() == "pipelines"

    btn.set_active(False)
    obj._on_pipelines_toggled(btn)
    assert obj._gallery_stack.get_visible_child_name() == "video"


def test_reentering_pipelines_resets_inner_stack_to_discover(tmp_path, monkeypatch):
    """Re-entering Pipelines must never land on a stale Open page.

    Drives PipelineStudio's inner stack to "open" directly (the seam
    _show_run/_on_open_run use), leaves Pipelines, then re-enters — the
    inner stack must be back on "discover".
    """
    obj = _make_mw(tmp_path, monkeypatch)

    obj._show_pipelines()
    obj._pipeline_studio.stack.set_visible_child_name("open")
    obj._hide_pipelines()

    obj._show_pipelines()

    assert obj._pipeline_studio.stack.get_visible_child_name() == "discover"


def test_source_change_unchecks_pipelines_toggle(tmp_path, monkeypatch):
    """Selecting a source tab while Pipelines is showing must visually uncheck
    the Pipelines toggle button, making the two mutually exclusive.

    Regression: previously the toggle stayed "checked" after a source tab was
    clicked — the gallery correctly switched away from Pipelines, but the
    toolbar button only self-corrected the next time Pipelines itself was
    clicked. Wired via the button's real "toggled" signal (not a direct call)
    so this also proves the fix doesn't recurse back into _hide_pipelines /
    _on_source_change when the toggle is flipped off programmatically.
    """
    obj = _make_mw(tmp_path, monkeypatch)
    obj._pipelines_btn = Gtk.ToggleButton()
    obj._pipelines_btn.connect("toggled", obj._on_pipelines_toggled)

    obj._pipelines_btn.set_active(True)
    assert obj._gallery_stack.get_visible_child_name() == "pipelines"
    assert obj._pipelines_btn.get_active() is True

    # Simulate clicking the "video" source tab while Pipelines is showing.
    obj._on_source_change("video")

    assert obj._gallery_stack.get_visible_child_name() == "video"
    assert obj._pipelines_btn.get_active() is False


# ── Task 5: "Remix as pipeline…" bridge ──────────────────────────────────────
#
# GenerationCard's hover action bar and DetailPanel's action row grow a
# "🧩 Remix as pipeline…" button next to the existing "🔀 Remix" button,
# wired via a parallel `remix_as_pipeline_cb(record)` seam. MainWindow wires
# both to `_remix_as_pipeline`, which resolves the record's primary artifact,
# activates the Pipelines area, and opens Pipeline Studio's Muse scoped to
# that artifact — falling back to a blank muse if the artifact is missing.

def _make_record(**kwargs):
    """Minimal GenerationRecord builder (mirrors tests/test_forge_transforms.py)."""
    from history_store import GenerationRecord
    import uuid
    base = dict(
        id=str(uuid.uuid4()),
        prompt="test prompt",
        negative_prompt="",
        num_inference_steps=20,
        seed=42,
        video_path="/nonexistent/video.mp4",
        thumbnail_path="/nonexistent/thumb.jpg",
        created_at="2026-01-01T00:00:00+00:00",
        media_type="video",
        image_path="",
        model="wan2",
        extra_meta={},
    )
    base.update(kwargs)
    return GenerationRecord(**base)


def test_generation_card_remix_as_pipeline_button_invokes_callback():
    """Clicking "🧩 Remix as pipeline…" on a card calls remix_as_pipeline_cb(record)."""
    from main_window import GenerationCard

    rec = _make_record(media_type="image", image_path="/nonexistent/image.png")
    calls = []
    card = GenerationCard(
        rec,
        select_cb=lambda c: None,
        delete_cb=lambda r: None,
        remix_as_pipeline_cb=lambda r: calls.append(r),
    )

    card._remix_as_pipeline_btn.emit("clicked")

    assert len(calls) == 1
    assert calls[0] is rec


def test_generation_card_remix_as_pipeline_button_noop_without_callback():
    """The button is present even when remix_as_pipeline_cb is None, and no-ops safely."""
    from main_window import GenerationCard

    rec = _make_record()
    card = GenerationCard(
        rec,
        select_cb=lambda c: None,
        delete_cb=lambda r: None,
    )

    card._remix_as_pipeline_btn.emit("clicked")  # must not raise


def test_gallery_widget_forwards_remix_as_pipeline_cb_to_cards():
    """GalleryWidget's remix_as_pipeline_cb constructor kwarg reaches each card."""
    from main_window import GalleryWidget

    calls = []
    gallery = GalleryWidget(
        select_cb=lambda r: None,
        delete_cb=lambda r: None,
        remix_as_pipeline_cb=lambda r: calls.append(r),
    )
    rec = _make_record(media_type="image", image_path="/nonexistent/image.png")
    gallery.load_history([rec])

    card = gallery._cards[0]
    card._remix_as_pipeline_btn.emit("clicked")

    assert len(calls) == 1
    assert calls[0].id == rec.id


def test_detail_panel_remix_as_pipeline_button_invokes_callback():
    """Clicking "🧩 Remix as pipeline…" in the detail panel calls remix_as_pipeline_cb(record)."""
    from main_window import DetailPanel

    rec = _make_record(media_type="image", image_path="/nonexistent/image.png")
    calls = []
    panel = DetailPanel()
    panel.show_record(rec, remix_cb=lambda r: None,
                       remix_as_pipeline_cb=lambda r: calls.append(r))

    panel._remix_as_pipeline_btn.emit("clicked")

    assert len(calls) == 1
    assert calls[0] is rec


def _make_mw_for_remix(tmp_path, monkeypatch):
    """Like _make_mw, but also pre-registers a 'pipelines' stack child and a
    mocked, already-constructed `_pipeline_studio` — the seam
    `_remix_as_pipeline` hands off to."""
    import main_window as mw

    obj = _make_mw(tmp_path, monkeypatch)
    obj._gallery_stack.add_named(Gtk.Box(), "pipelines")
    obj._pipeline_studio = MagicMock()
    obj._remix_as_pipeline = mw.MainWindow._remix_as_pipeline.__get__(obj)
    return obj


def test_remix_as_pipeline_resolves_seed_artifact_for_image_record(tmp_path, monkeypatch):
    """A normal, existing image record resolves to (path, 'image', thumb) and
    activates Pipelines before opening the scoped muse."""
    obj = _make_mw_for_remix(tmp_path, monkeypatch)

    img_path = tmp_path / "art.png"
    img_path.write_bytes(b"fake-png")
    thumb_path = tmp_path / "art_thumb.jpg"
    thumb_path.write_bytes(b"fake-jpg")
    rec = _make_record(
        media_type="image", image_path=str(img_path), thumbnail_path=str(thumb_path),
    )

    obj._remix_as_pipeline(rec)

    assert obj._gallery_stack.get_visible_child_name() == "pipelines"
    obj._pipeline_studio.show_muse.assert_called_once_with(
        seed_artifact=(str(img_path), "image", str(thumb_path))
    )


def test_remix_as_pipeline_falls_back_to_blank_muse_when_media_missing(tmp_path, monkeypatch):
    """A record whose media file doesn't exist on disk must never crash — it
    opens a blank muse instead of a seeded one."""
    obj = _make_mw_for_remix(tmp_path, monkeypatch)

    rec = _make_record(
        media_type="image", image_path=str(tmp_path / "does_not_exist.png"),
    )
    assert rec.media_exists is False

    obj._remix_as_pipeline(rec)

    assert obj._gallery_stack.get_visible_child_name() == "pipelines"
    obj._pipeline_studio.show_muse.assert_called_once_with(seed_artifact=None)


def test_remix_as_pipeline_video_and_animate_map_to_video_kind(tmp_path, monkeypatch):
    """Both 'video' and 'animate' media_type records resolve to kind 'video'."""
    for media_type in ("video", "animate"):
        obj = _make_mw_for_remix(tmp_path, monkeypatch)
        vid_path = tmp_path / f"{media_type}.mp4"
        vid_path.write_bytes(b"fake-mp4")
        rec = _make_record(media_type=media_type, video_path=str(vid_path))

        obj._remix_as_pipeline(rec)

        obj._pipeline_studio.show_muse.assert_called_once_with(
            seed_artifact=(str(vid_path), "video", rec.thumbnail_path)
        )


def test_remix_as_pipeline_animatediff_maps_to_gif_kind(tmp_path, monkeypatch):
    """An animatediff record resolves to kind 'gif'."""
    obj = _make_mw_for_remix(tmp_path, monkeypatch)
    gif_path = tmp_path / "loop.gif"
    gif_path.write_bytes(b"fake-gif")
    rec = _make_record(media_type="animatediff", video_path=str(gif_path))

    obj._remix_as_pipeline(rec)

    obj._pipeline_studio.show_muse.assert_called_once_with(
        seed_artifact=(str(gif_path), "gif", rec.thumbnail_path)
    )


def test_remix_as_pipeline_activates_via_pipelines_toggle_button(tmp_path, monkeypatch):
    """The production activation path — `_pipelines_btn.set_active(True)` firing
    the "toggled" signal into `_on_pipelines_toggled` → `_show_pipelines()` — is
    what runs on every real card click (the toolbar button always exists by then).

    Unlike the other _remix_as_pipeline tests (which have no `_pipelines_btn`, so
    they exercise only the `else: self._show_pipelines()` fallback), this attaches
    a real inactive ToggleButton wired exactly as production wires it, and asserts
    the button ends up active AND show_muse is reached with the resolved tuple via
    that toggle→signal path. Removing the `set_active(True)` branch must break this.
    """
    obj = _make_mw_for_remix(tmp_path, monkeypatch)
    obj._pipelines_btn = Gtk.ToggleButton()
    obj._pipelines_btn.connect("toggled", obj._on_pipelines_toggled)
    assert obj._pipelines_btn.get_active() is False

    img_path = tmp_path / "art.png"
    img_path.write_bytes(b"fake-png")
    thumb_path = tmp_path / "art_thumb.jpg"
    thumb_path.write_bytes(b"fake-jpg")
    rec = _make_record(
        media_type="image", image_path=str(img_path), thumbnail_path=str(thumb_path),
    )

    obj._remix_as_pipeline(rec)

    # (a) the toggle button was driven active — proves the set_active(True) branch ran.
    assert obj._pipelines_btn.get_active() is True
    # …which flipped the gallery stack to Pipelines via the real signal handler.
    assert obj._gallery_stack.get_visible_child_name() == "pipelines"
    # (b) show_muse still reached with the correct seed tuple through that path.
    obj._pipeline_studio.show_muse.assert_called_once_with(
        seed_artifact=(str(img_path), "image", str(thumb_path))
    )


def test_remix_as_pipeline_unresolvable_kind_falls_back_to_blank_muse(tmp_path, monkeypatch):
    """A media_type with no known kind mapping (e.g. 'artgen') falls back to
    a blank muse even though the file exists on disk."""
    obj = _make_mw_for_remix(tmp_path, monkeypatch)
    vid_path = tmp_path / "art.mp4"
    vid_path.write_bytes(b"fake-mp4")
    # media_type "artgen" is not in the kind-mapping table; media_file_path
    # falls back to video_path (see GenerationRecord.media_file_path), which
    # does exist on disk — so this exercises the "kind unresolved" branch of
    # the fallback specifically, not the "file missing" branch.
    rec = _make_record(media_type="artgen", video_path=str(vid_path))
    assert rec.media_exists is True

    obj._remix_as_pipeline(rec)

    obj._pipeline_studio.show_muse.assert_called_once_with(seed_artifact=None)
