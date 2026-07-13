"""Task 7: "🧩 Remix as pipeline…" bridge from the Generative Art gallery.

ArtgenGallery/ArtgenDetail grow a parallel `on_remix_as_pipeline` seam next to
the existing `on_remix` (RemixPopover) seam, and a "🧩 Remix as pipeline…"
button next to the existing "🔀 Remix" affordance. `ArtgenPanel` forwards its
own `on_remix_as_pipeline` the same way it already forwards `on_remix`.

Creating GTK widgets needs a display; the full suite runs under xvfb. When no
display is available this module skips itself (matches the repo's headless
fallback for GTK-widget tests).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

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


def test_gallery_card_existing_remix_seam_untouched(tmp_path):
    """The pre-existing "🔀 Remix" seam (on_remix / RemixPopover) still fires
    independently of the new pipeline button — clicking one must not touch
    the other."""
    from artgen_gallery import ArtgenGallery

    gallery = ArtgenGallery()
    remix_calls = []
    pipeline_calls = []
    gallery.on_remix = lambda r: remix_calls.append(r)
    gallery.on_remix_as_pipeline = lambda r: pipeline_calls.append(r)
    rec = _media_record(tmp_path)

    overlay = gallery._make_card(rec)
    overlay._remix_as_pipeline_btn.emit("clicked")

    assert pipeline_calls == [rec]
    assert remix_calls == []


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


def test_detail_existing_remix_seam_untouched(tmp_path):
    from artgen_detail import ArtgenDetail

    detail = ArtgenDetail()
    remix_calls = []
    pipeline_calls = []
    detail.on_remix = lambda r: remix_calls.append(r)
    detail.on_remix_as_pipeline = lambda r: pipeline_calls.append(r)
    rec = _media_record(tmp_path)
    detail.show_record(rec.id, [rec])

    detail._remix_as_pipeline_btn.emit("clicked")

    assert pipeline_calls == [rec]
    assert remix_calls == []


# ── ArtgenPanel forwarding ────────────────────────────────────────────────────

def test_artgen_panel_forwards_remix_as_pipeline_to_owner_callback():
    """_on_remix_as_pipeline_record calls self.on_remix_as_pipeline(rec) when set,
    mirroring _on_remix_record's forwarding of on_remix."""
    import artgen_panel

    panel = artgen_panel.ArtgenPanel.__new__(artgen_panel.ArtgenPanel)
    calls = []
    panel.on_remix_as_pipeline = lambda r: calls.append(r)
    rec = object()

    artgen_panel.ArtgenPanel._on_remix_as_pipeline_record(panel, rec)

    assert calls == [rec]


def test_artgen_panel_remix_as_pipeline_noop_without_owner_callback():
    import artgen_panel

    panel = artgen_panel.ArtgenPanel.__new__(artgen_panel.ArtgenPanel)
    panel.on_remix_as_pipeline = None

    artgen_panel.ArtgenPanel._on_remix_as_pipeline_record(panel, object())  # must not raise


def test_artgen_panel_build_wires_gallery_and_detail_seams():
    """_build() wires self._gallery.on_remix_as_pipeline and
    self._detail.on_remix_as_pipeline to the panel's forwarding handler, the
    same way it already wires on_remix for both."""
    import artgen_panel

    with patch("artgen.detect_artgen_endpoint", return_value=(None, None)):
        panel = artgen_panel.ArtgenPanel()

    assert panel._gallery.on_remix_as_pipeline == panel._on_remix_as_pipeline_record
    assert panel._detail.on_remix_as_pipeline == panel._on_remix_as_pipeline_record
    # Existing on_remix wiring must be untouched.
    assert panel._gallery.on_remix == panel._on_remix_record
    assert panel._detail.on_remix == panel._on_remix_record
