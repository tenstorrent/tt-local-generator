"""
Tests for mounting Pipeline Studio (Discover+Open) in the main window
(SP-C Phase 1, Task 5).

Constructing the full `MainWindow` (GalleryWidget, DetailPanel, history load,
health workers, ...) is heavy and network/disk dependent, so — mirroring the
existing pattern in tests/test_main_window_animate_inputs.py — these tests
build a minimal `MainWindow` via `__new__` with `Gtk.ApplicationWindow
.__init__` patched out, then hand-populate only the handful of real Gtk widgets
and collaborators the seam under test (`_show_pipelines` / `_hide_pipelines` /
`_on_pipelines_toggled`) actually touches: `_gallery_stack` and `_detail_wrap`.
`_current_medium_source` (SP-3d-3, replacing ControlPanel's
`get_model_source()` — ControlPanel itself is deleted, SP-3d-5) is stubbed
directly rather than via a `_controls` stand-in.

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
    obj._detail_wrap = Gtk.Box()
    obj._detail_wrap.set_visible(True)

    # SP-3d-3/5: `_current_medium_source()` replaced ControlPanel's
    # `get_model_source()` as the "what am I currently making" source;
    # ControlPanel itself is deleted (SP-3d-5), so stub the new method
    # directly instead of a `_controls` stand-in.
    obj._current_medium_source = MagicMock(return_value="video")

    # Task 5 (model picker): `_show_pipelines` now threads `_status_service`
    # into `PipelineStudio` -- `None` is a legitimate real value too (the
    # picker falls back to its own no-service degrade), so a bare stub is
    # enough for this harness.
    obj._status_service = None

    # Bind the real (unbound) methods under test so `self` resolves correctly.
    obj._show_pipelines = mw.MainWindow._show_pipelines.__get__(obj)
    obj._hide_pipelines = mw.MainWindow._hide_pipelines.__get__(obj)
    obj._on_pipelines_toggled = mw.MainWindow._on_pipelines_toggled.__get__(obj)
    obj._sync_gallery_to_source = mw.MainWindow._sync_gallery_to_source.__get__(obj)
    obj._uncheck_pipelines_toggle_if_active = mw.MainWindow._uncheck_pipelines_toggle_if_active.__get__(obj)
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
    # Pipeline Studio is full-width: the detail pane collapses (ControlPanel's
    # `_ctrl_wrapper`, which used to collapse alongside it, is deleted SP-3d-5).
    assert obj._detail_wrap.get_visible() is False


def test_show_pipelines_reuses_instance_on_second_activation(tmp_path, monkeypatch):
    """Repeat activation doesn't rebuild PipelineStudio (avoids re-scanning history)."""
    obj = _make_mw(tmp_path, monkeypatch)

    obj._show_pipelines()
    first = obj._pipeline_studio
    obj._hide_pipelines()
    obj._show_pipelines()

    assert obj._pipeline_studio is first


def test_ensure_pipeline_studio_flag_on_passes_on_leave_none(tmp_path, monkeypatch):
    """Regression (final-review fix): `_ensure_pipeline_studio` constructed
    `PipelineStudio(..., on_leave=self._on_pipeline_leave)` UNCONDITIONALLY,
    but `PipelineStudio._on_run_back` / `_on_back_to_discover` gate on
    `if self._on_leave is not None:` rather than on the pipeline-mode flag.
    So with the flag ON, the studio's own Back buttons called `on_leave` ->
    `_hide_pipelines()` (ejecting to the app Library) instead of keeping the
    pre-existing "-> discover" behavior (design spec §D: "When the flag is
    ON (on_leave=None), _on_run_back keeps today's -> 'discover' behavior").
    Real wiring must pass `on_leave=None` when the flag is on.
    """
    import main_window as mw

    monkeypatch.setattr(mw.app_settings, "PIPELINE_MODE_ENABLED", True, raising=False)
    obj = _make_mw(tmp_path, monkeypatch)

    obj._ensure_pipeline_studio()

    assert obj._pipeline_studio._on_leave is None


def test_ensure_pipeline_studio_flag_off_passes_on_pipeline_leave(tmp_path, monkeypatch):
    """Flag OFF (remix-without-pipeline-mode path) keeps on_leave wired to
    `_on_pipeline_leave`, the studio's own Back-to-Library seam -- unchanged
    by the final-review fix above.
    """
    import main_window as mw

    monkeypatch.setattr(mw.app_settings, "PIPELINE_MODE_ENABLED", False, raising=False)
    obj = _make_mw(tmp_path, monkeypatch)

    obj._ensure_pipeline_studio()

    assert obj._pipeline_studio._on_leave == obj._on_pipeline_leave


def test_hide_pipelines_restores_current_source_view(tmp_path, monkeypatch):
    """Leaving Pipelines restores the gallery/side-panel state for the active source."""
    obj = _make_mw(tmp_path, monkeypatch)

    obj._show_pipelines()
    obj._hide_pipelines()

    assert obj._gallery_stack.get_visible_child_name() == "video"
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
    """Switching the gallery to a medium page while Pipelines is showing must
    visually uncheck the Pipelines toggle button, making the two mutually
    exclusive.

    Regression: previously the toggle stayed "checked" after a source tab was
    clicked — the gallery correctly switched away from Pipelines, but the
    toolbar button only self-corrected the next time Pipelines itself was
    clicked. Wired via the button's real "toggled" signal (not a direct call)
    so this also proves the fix doesn't recurse back into `_hide_pipelines` /
    `_sync_gallery_to_source` (SP-3d-5: replaces ControlPanel-era
    `_on_source_change`, deleted with the class) when the toggle is flipped
    off programmatically.
    """
    obj = _make_mw(tmp_path, monkeypatch)
    obj._pipelines_btn = Gtk.ToggleButton()
    obj._pipelines_btn.connect("toggled", obj._on_pipelines_toggled)

    obj._pipelines_btn.set_active(True)
    assert obj._gallery_stack.get_visible_child_name() == "pipelines"
    assert obj._pipelines_btn.get_active() is True

    # Simulate landing on the "video" medium page while Pipelines is showing
    # (what _hide_pipelines does via _current_medium_source()).
    obj._sync_gallery_to_source("video")

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

    This is the flag-ON path specifically (task-5-brief.md): PIPELINE_MODE_ENABLED
    is False by default, in which case `_remix_as_pipeline` never touches
    `_pipelines_btn` at all — see test_remix_flag_off_enters_muse_not_discover
    below for that branch.
    """
    import main_window as mw
    monkeypatch.setattr(mw.app_settings, "PIPELINE_MODE_ENABLED", True, raising=False)
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


# ── Task 7: generative-art gallery bridge (MediaRecord branch) ───────────────
#
# _remix_as_pipeline also accepts a media_store.MediaRecord (the artgen
# gallery's own record type, distinct from history_store.GenerationRecord).
# It dispatches on record type: a MediaRecord resolves its seed via
# artgen_kind.artgen_seed_kind(file_path, generator_type) instead of the
# GenerationRecord media_type table above. The GenerationRecord branch above
# must keep working unchanged.

def _make_media_record(tmp_path, filename="lore.txt", content="Once upon a time...",
                        generator_type="lore", thumbnail_path="", **kwargs):
    """Minimal MediaRecord builder backed by a real file (or none, when
    content is None, to exercise the missing-file fallback)."""
    from media_store import MediaRecord

    p = tmp_path / filename
    if content is not None:
        p.write_text(content, encoding="utf-8")
    base = dict(
        id="artgen-1",
        media_type="artgen",
        created_at="2026-07-01T00:00:00Z",
        file_path=str(p),
        thumbnail_path=thumbnail_path,
        prompt="a lore prompt",
        model_id="artgen-qwen3-8b",
        generator_type=generator_type,
        params="{}",
        starred=0,
    )
    base.update(kwargs)
    return MediaRecord(**base)


def test_remix_as_pipeline_media_record_text_seeds_content_with_no_thumb(tmp_path, monkeypatch):
    """A lore .txt MediaRecord with real content seeds (content, 'text', None)
    — text seeds show the "text" heading with no image thumbnail."""
    obj = _make_mw_for_remix(tmp_path, monkeypatch)
    rec = _make_media_record(tmp_path, content="Once upon a time, a forge dreamed.")

    obj._remix_as_pipeline(rec)

    assert obj._gallery_stack.get_visible_child_name() == "pipelines"
    obj._pipeline_studio.show_muse.assert_called_once_with(
        seed_artifact=("Once upon a time, a forge dreamed.", "text", None)
    )


def test_remix_as_pipeline_media_record_image_kind_resolves_with_thumb(tmp_path, monkeypatch):
    """An image-kind artgen artifact (e.g. an SVG banner) resolves like the
    GenerationRecord image branch: (path, kind, thumb)."""
    obj = _make_mw_for_remix(tmp_path, monkeypatch)
    thumb = tmp_path / "banner_thumb.jpg"
    thumb.write_bytes(b"fake-jpg")
    rec = _make_media_record(
        tmp_path, filename="banner.svg", content="<svg></svg>",
        generator_type="banner", thumbnail_path=str(thumb),
    )

    obj._remix_as_pipeline(rec)

    obj._pipeline_studio.show_muse.assert_called_once_with(
        seed_artifact=(str(tmp_path / "banner.svg"), "image", str(thumb))
    )


def test_remix_as_pipeline_palette_seeds_muse(tmp_path, monkeypatch):
    """A .json artgen artifact whose generator_type is "palette" is now a
    seedable kind (Task 3 of remix-pipeline-unification taught
    artgen_kind.artgen_seed_kind to return "palette" for it, overriding the
    generic .json -> None mapping) — it SEEDS the Muse instead of falling
    back to blank. seed_artifact is (file_path, "palette", thumbnail_path),
    matching the "image"/"gif" resolution branch in
    _resolve_artgen_media_seed (kind is neither None nor "text", so it falls
    through to the generic file-exists-> seed branch)."""
    obj = _make_mw_for_remix(tmp_path, monkeypatch)
    rec = _make_media_record(
        tmp_path, filename="palette.json", content='{"colors": []}',
        generator_type="palette",
    )

    obj._remix_as_pipeline(rec)

    obj._pipeline_studio.show_muse.assert_called_once_with(
        seed_artifact=(str(tmp_path / "palette.json"), "palette", "")
    )


def test_remix_as_pipeline_media_record_missing_file_falls_back_to_blank_muse(tmp_path, monkeypatch):
    """A record whose file was never written to disk must never crash — blank
    muse instead of a seeded one."""
    obj = _make_mw_for_remix(tmp_path, monkeypatch)
    rec = _make_media_record(tmp_path, content=None)

    obj._remix_as_pipeline(rec)

    obj._pipeline_studio.show_muse.assert_called_once_with(seed_artifact=None)


def test_remix_as_pipeline_media_record_empty_text_falls_back_to_blank_muse(tmp_path, monkeypatch):
    """Whitespace-only lore content is treated as empty — blank muse."""
    obj = _make_mw_for_remix(tmp_path, monkeypatch)
    rec = _make_media_record(tmp_path, content="   \n\t  ")

    obj._remix_as_pipeline(rec)

    obj._pipeline_studio.show_muse.assert_called_once_with(seed_artifact=None)


def test_remix_as_pipeline_generation_record_path_unaffected_by_media_record_branch(tmp_path, monkeypatch):
    """The pre-existing GenerationRecord branch (video/image galleries) keeps
    working unchanged now that MediaRecord dispatch has been added."""
    obj = _make_mw_for_remix(tmp_path, monkeypatch)
    img_path = tmp_path / "art.png"
    img_path.write_bytes(b"fake-png")
    rec = _make_record(media_type="image", image_path=str(img_path))

    obj._remix_as_pipeline(rec)

    obj._pipeline_studio.show_muse.assert_called_once_with(
        seed_artifact=(str(img_path), "image", rec.thumbnail_path)
    )


# ── Task 5 (remix-without-pipeline-mode): flag-OFF routing ──────────────────
#
# When app_settings.PIPELINE_MODE_ENABLED is False, `_remix_as_pipeline` must
# never route through the studio's Discover page (there's no "Pipelines"
# toggle to activate at all — `_pipelines_btn` stays None) — it enters the
# studio scoped straight to the Muse goal chooser. `_on_pipeline_leave` is
# the studio's "Back" seam for that flag-off path, returning to the app
# Library via the existing `_hide_pipelines`.

class _FakeStack:
    """Records the last `set_visible_child_name` call — enough to assert
    which gallery-stack page routing landed on without a real Gtk.Stack."""

    def __init__(self):
        self.last = None

    def set_visible_child_name(self, name):
        self.last = name


def test_remix_flag_off_enters_muse_not_discover(tmp_path, monkeypatch):
    import main_window as mw

    monkeypatch.setattr(mw.app_settings, "PIPELINE_MODE_ENABLED", False, raising=False)
    obj = _make_mw(tmp_path, monkeypatch)
    obj._remix_as_pipeline = mw.MainWindow._remix_as_pipeline.__get__(obj)

    calls = {"discover": 0, "muse": None, "ensured": 0}

    class _FakeStudio:
        def show_discover(self):
            calls["discover"] += 1

        def show_muse(self, seed_artifact=None):
            calls["muse"] = seed_artifact

    obj._pipeline_studio = _FakeStudio()
    monkeypatch.setattr(obj, "_ensure_pipeline_studio", lambda: calls.__setitem__("ensured", 1))
    obj._gallery_stack = _FakeStack()
    obj._loop_nav = {}

    img_path = tmp_path / "art.png"
    img_path.write_bytes(b"fake-png")
    rec = _make_record(media_type="image", image_path=str(img_path))

    obj._remix_as_pipeline(rec)

    assert calls["ensured"] == 1
    assert calls["muse"] is not None            # seeded muse
    assert calls["discover"] == 0               # never lands on studio Discover
    assert obj._gallery_stack.last == "pipelines"


def test_remix_flag_off_hides_detail_pane(tmp_path, monkeypatch):
    """Regression (deep-review Fix A): the flag-OFF branch used to leave
    `_detail_wrap` visible, so the studio rendered squeezed beside it — the
    flag-ON path (`_show_pipelines`) already hides it for exactly this
    reason. `_detail_wrap` must be hidden here too."""
    import main_window as mw

    monkeypatch.setattr(mw.app_settings, "PIPELINE_MODE_ENABLED", False, raising=False)
    obj = _make_mw(tmp_path, monkeypatch)
    obj._remix_as_pipeline = mw.MainWindow._remix_as_pipeline.__get__(obj)

    class _FakeStudio:
        def show_muse(self, seed_artifact=None):
            pass

    obj._pipeline_studio = _FakeStudio()
    monkeypatch.setattr(obj, "_ensure_pipeline_studio", lambda: None)
    # Real Gtk.Box already set up by _make_mw, visible True.
    assert obj._detail_wrap.get_visible() is True

    rec = _make_record(media_type="image", image_path=str(tmp_path / "art.png"))

    obj._remix_as_pipeline(rec)

    assert obj._detail_wrap.get_visible() is False


def test_worker_callbacks_noop_after_close(tmp_path, monkeypatch):
    """`_on_progress`/`_on_finished`/`_on_error` are `GLib.idle_add` targets
    that can fire after the window has closed (`_alive = False`, set in
    `do_close_request`) if a worker thread finishes late. Each must guard on
    `self._alive` as its first line, exactly like `_on_status_snapshot`
    already does, so a post-close callback can't touch torn-down widgets.

    This harness object has none of the collaborators these methods would
    normally touch (`_set_status`, `_gen_gallery`, `_create_view`, ...) — if
    the `_alive` guard didn't return first, any of these calls would raise
    `AttributeError` instead of returning `False`.
    """
    import main_window as mw

    obj = _make_mw(tmp_path, monkeypatch)
    obj._on_progress = mw.MainWindow._on_progress.__get__(obj)
    obj._on_finished = mw.MainWindow._on_finished.__get__(obj)
    obj._on_error = mw.MainWindow._on_error.__get__(obj)
    obj._alive = False

    rec = _make_record()

    assert obj._on_progress("x", None) is False
    assert obj._on_finished(rec) is False
    assert obj._on_error("x") is False


def test_on_pipeline_leave_returns_to_library(tmp_path, monkeypatch):
    import main_window as mw

    obj = _make_mw(tmp_path, monkeypatch)
    obj._on_pipeline_leave = mw.MainWindow._on_pipeline_leave.__get__(obj)
    hidden = []
    monkeypatch.setattr(obj, "_hide_pipelines", lambda: hidden.append(True))

    obj._on_pipeline_leave()

    assert hidden == [True]


def test_main_window_wires_artgen_gallery_on_remix_as_pipeline_source():
    """Regression guard: main_window.py must wire
    `self._artgen_gallery.on_remix_as_pipeline = self._remix_as_pipeline`
    (mirrors test_workflow_popover_not_imported_at_startup's source-text
    style — a full MainWindow() construction is too heavy/network-dependent
    to build in tests, see the module docstring above).

    SP-3d-5: `ArtgenPanel` is deleted — Discover's artgen gallery page is now
    the standalone `ArtgenGallery`, wired the same way the three native
    `GalleryWidget`s already are.

    Task 8 (remix-pipeline-unification): the former parallel `on_remix`
    (popover) wiring is gone — `on_remix_as_pipeline` is the single surviving
    seam, since ArtgenGallery now has exactly one remix button.
    """
    src = (Path(__file__).parent.parent / "app" / "main_window.py").read_text()
    assert "self._artgen_gallery.on_remix = self._on_remix_card" not in src
    assert "self._artgen_gallery.on_remix_as_pipeline = self._remix_as_pipeline" in src
    assert "class ArtgenPanel(Gtk.Box):" not in src
