"""Task 7: "🧩 Remix as pipeline…" bridge from the Generative Art gallery.

ArtgenGallery/ArtgenDetail grew a parallel `on_remix_as_pipeline` seam next to
the then-existing `on_remix` (RemixPopover) seam, plus a "🧩 Remix as
pipeline…" button next to the then-existing "🔀 Remix" affordance.

SP-3d-5: `ArtgenPanel` (which used to forward its own `on_remix_as_pipeline`
the same way it forwarded `on_remix`) is deleted — main_window.py now wires
`self._artgen_gallery.on_remix_as_pipeline = self._remix_as_pipeline`
directly, the same pattern the three native `GalleryWidget`s already use (see
`test_main_window_create_view_mount.py` /
`test_main_window_pipelines.py::test_main_window_wires_artgen_gallery_on_remix_as_pipeline_source`
for the main_window-level wiring guard). The ArtgenGallery/ArtgenDetail tests
below are unaffected by that deletion.

UPDATE (Task 8, remix-pipeline-unification): the `on_remix`/RemixPopover seam
described above is now GONE from both ArtgenGallery and ArtgenDetail — remix
means exactly one thing (seed a pipeline), so each card/sidebar has exactly
one "🔀 Remix" button, wired to `on_remix_as_pipeline`. The two
`_remix_as_pipeline_btn` "invokes callback" / "noop without callback" tests
on each side are unaffected by the consolidation since they never depended
on the popover seam existing. The two tests that DID exercise the now-deleted
`on_remix` seam were replaced: `test_gallery_card_existing_remix_seam_untouched`
-> `test_gallery_card_has_single_remix_affordance`, and
`test_detail_existing_remix_seam_untouched` -> `test_detail_has_single_remix_affordance`.
(The original gallery-side test asserted on a plain instance attribute —
`gallery.on_remix = lambda ...` — which Python lets you set even though
nothing reads it once the popover button is gone; that assertion would have
kept "passing" vacuously forever, hiding exactly this kind of regression.)

Creating GTK widgets needs a display; the full suite runs under xvfb. When no
display is available this module skips itself (matches the repo's headless
fallback for GTK-widget tests).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


def _media_record(tmp_path, filename="lore.txt", content="Once upon a time...",
                   generator_type="lore", thumbnail_path="", media_id="mr-1"):
    """Build a real MediaRecord backed by a real file on disk (or no file at
    all when content is None)."""
    from media_store import MediaRecord

    p = tmp_path / filename
    if content is not None:
        p.write_text(content, encoding="utf-8")
    return MediaRecord(
        id=media_id,
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


# ── ArtgenGallery card ────────────────────────────────────────────────────────

def test_gallery_card_remix_as_pipeline_button_invokes_callback(tmp_path):
    from artgen_gallery import ArtgenGallery

    gallery = ArtgenGallery()
    calls = []
    gallery.on_remix_as_pipeline = lambda r: calls.append(r)
    rec = _media_record(tmp_path)

    overlay = gallery._make_card(rec)

    overlay._remix_as_pipeline_btn.emit("clicked")

    assert calls == [rec]


def test_gallery_card_remix_as_pipeline_button_noop_without_callback(tmp_path):
    from artgen_gallery import ArtgenGallery

    gallery = ArtgenGallery()
    rec = _media_record(tmp_path)
    overlay = gallery._make_card(rec)

    overlay._remix_as_pipeline_btn.emit("clicked")  # must not raise


def test_gallery_card_has_single_remix_affordance(tmp_path):
    """Task 8 follow-up (remix-pipeline-unification): the former parallel
    "🔀 Remix" popover seam (`on_remix`) is gone from ArtgenGallery --
    `on_remix_as_pipeline` (wired to the card's one remaining button,
    relabeled to the canonical "🔀 Remix") is the only remix affordance
    left. Replaces the old test_gallery_card_existing_remix_seam_untouched,
    which asserted on a plain instance attribute (`gallery.on_remix = ...`)
    that Python lets you set even though nothing reads it once the popover
    button is deleted -- that assertion would have kept "passing" vacuously
    forever, giving false confidence."""
    from artgen_gallery import ArtgenGallery

    gallery = ArtgenGallery()
    assert not hasattr(gallery, "on_remix")

    pipeline_calls = []
    gallery.on_remix_as_pipeline = lambda r: pipeline_calls.append(r)
    rec = _media_record(tmp_path)

    overlay = gallery._make_card(rec)
    overlay._remix_as_pipeline_btn.emit("clicked")

    assert pipeline_calls == [rec]
    assert overlay._remix_as_pipeline_btn.get_label() == "🔀 Remix"


# ── ArtgenDetail sidebar ──────────────────────────────────────────────────────

def test_detail_remix_as_pipeline_button_invokes_callback(tmp_path):
    from artgen_detail import ArtgenDetail

    detail = ArtgenDetail()
    calls = []
    detail.on_remix_as_pipeline = lambda r: calls.append(r)
    rec = _media_record(tmp_path)
    detail.show_record(rec.id, [rec])

    detail._remix_as_pipeline_btn.emit("clicked")

    assert calls == [rec]


def test_detail_remix_as_pipeline_button_noop_without_callback(tmp_path):
    from artgen_detail import ArtgenDetail

    detail = ArtgenDetail()
    rec = _media_record(tmp_path)
    detail.show_record(rec.id, [rec])

    detail._remix_as_pipeline_btn.emit("clicked")  # must not raise


def test_detail_has_single_remix_affordance(tmp_path):
    """Task 8 follow-up (remix-pipeline-unification): the former parallel
    "🔀 Remix" popover button (`_seed_btn`) and its `on_remix` seam are gone
    from ArtgenDetail's sidebar -- `_remix_as_pipeline_btn` (relabeled to the
    canonical "🔀 Remix") is the only remix button left. Replaces the old
    test_detail_existing_remix_seam_untouched, whose premise (two independent
    remix seams) no longer holds now that the popover seam has been deleted
    outright rather than merely left unwired."""
    from artgen_detail import ArtgenDetail

    detail = ArtgenDetail()
    assert not hasattr(detail, "_seed_btn")
    assert not hasattr(detail, "on_remix")
    rec = _media_record(tmp_path)
    detail.show_record(rec.id, [rec])

    assert detail._remix_as_pipeline_btn.get_label() == "🔀 Remix"
