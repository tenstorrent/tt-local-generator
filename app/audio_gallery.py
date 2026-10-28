#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Library page for songs (media_type="audio", generator_type="music").

Three pieces, all GTK-only and free of main_window imports so create_view and main_window can both use them:

* `build_audio_widget(record, path, exists)`: the player block (waveform, `Gtk.MediaControls`, length note, lyrics,
  credit, "Open externally"). The Create result panel and the Library detail both render a song through it, so the
  two cannot drift apart. The stream it made is kept on `widget.audio_stream` so the owner can pause it.
* `AudioGallery`: a grid of cards, newest first. Same size and click contract as `ArtgenGallery`: one
  `Gtk.GestureClick` per card, a click calls `on_card_activated(media_id)`. A card has a star and a delete button and
  no Remix button, because no seed kind exists for audio.
* `AudioDetail`: the right-pane view of one song: title, star, delete, and the player block above.

Double-click and "Fullscreen": the detail IS the viewer (a player and text have nothing to enlarge), so a
double-click does the same as a single click and there is no Fullscreen button.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

import gallery_layout  # noqa: E402
from media_store import MediaRecord  # noqa: E402

_log = logging.getLogger(__name__)


class _LazyStore:
    """Resolve `media_store.media_store` on each use, so a reloaded module (tests) or a swapped singleton is honoured."""

    def __getattr__(self, name):
        import media_store
        return getattr(media_store._get_media_store(), name)


_ms = _LazyStore()

_BOTTOM_BAR_H = 28
_PLACEHOLDER = "🎵"


def open_externally(path: str) -> None:
    """Hand a file to the desktop's default application. Fail-soft: a missing handler only logs."""
    try:
        from gi.repository import Gio
        Gio.AppInfo.launch_default_for_uri(Path(path).resolve().as_uri(), None)
    except Exception:  # noqa: BLE001
        _log.debug("could not open %s externally", path, exc_info=True)


def _inside_storage(path: str) -> bool:
    """True when `path` is a file under the Library storage directory (after resolving links and `..`)."""
    if not path:
        return False
    try:
        import media_store
        Path(path).resolve().relative_to(Path(media_store.STORAGE_DIR).resolve())
        return True
    except (ValueError, OSError):
        return False


def remove_song_files(rec) -> None:
    """Delete a song's audio file and waveform picture after its row is gone. Only files inside the Library
    storage directory are touched, and a missing file is not an error."""
    for attr in ("file_path", "thumbnail_path"):
        path = getattr(rec, attr, "") or ""
        if isinstance(path, str) and _inside_storage(path):
            try:
                Path(path).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                _log.debug("could not remove %s", path, exc_info=True)


def build_audio_widget(record, path: str, exists: bool = True,
                       on_use_seed: "Optional[Callable[[int], None]]" = None) -> Gtk.Widget:
    """A finished song: waveform picture, play/pause + seek bar (`Gtk.MediaControls` on a `Gtk.MediaFile`, the
    GStreamer path video uses), the lyrics read-only, the credit line, and "Open externally".

    When the file is missing the controls are replaced by "Audio file not found" and Open externally is disabled.
    When GStreamer cannot play the file the controls are replaced by a one-line note, so no dead player is shown.
    The seed the server used is shown (selectable). With `on_use_seed`, a "Use this seed" button hands it back so the
    same clip can be asked for again.
    The returned box carries `audio_stream` (the `Gtk.MediaFile`, or None) so the owner can pause it."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class("create-result-audio")
    box.audio_stream = None
    params = getattr(record, "params_dict", None) or {}

    title = Gtk.Label(label="🎵 " + (getattr(record, "prompt", "") or "Music"), xalign=0.0)
    title.set_wrap(True)
    title.set_selectable(True)
    box.append(title)

    thumb = getattr(record, "thumbnail_path", "") or ""
    if exists and isinstance(thumb, str) and thumb and Path(thumb).exists():
        try:
            wave = Gtk.Picture.new_for_filename(thumb)
            wave.set_can_shrink(True)
            wave.set_content_fit(Gtk.ContentFit.CONTAIN)
            wave.set_size_request(-1, 120)
            wave.add_css_class("create-result-picture")
            box.append(wave)
        except Exception:  # noqa: BLE001 - the waveform is decoration
            _log.debug("waveform picture failed", exc_info=True)

    note = Gtk.Label(xalign=0.0)
    note.set_wrap(True)
    note.set_visible(False)
    if not exists:
        note.set_label("Audio file not found.")
        note.set_visible(True)
    else:
        note.set_label("This machine could not play the song here. Use Open externally.")
        try:
            stream = Gtk.MediaFile.new_for_filename(path)
            controls = Gtk.MediaControls.new(stream)
            box.audio_stream = stream

            def _on_error(s, _pspec, c=controls, n=note):
                if s.get_error() is not None:
                    c.set_visible(False)
                    n.set_visible(True)

            stream.connect("notify::error", _on_error)
            box.append(controls)
            _on_error(stream, None)   # an error set while the stream was being built never fires notify
        except Exception:  # noqa: BLE001 - no playback support: the note and the button below still work
            note.set_visible(True)
    box.append(note)

    got, asked = params.get("seconds_got"), params.get("seconds_asked")
    if isinstance(got, (int, float)):
        length = f"{got:.0f} s"
        if params.get("ended") and isinstance(asked, (int, float)) and got + 0.5 < asked:
            length += f". You asked for {asked:.0f} s; the song ended when the lyrics did."
        len_lbl = Gtk.Label(label=length, xalign=0.0)
        len_lbl.set_wrap(True)
        len_lbl.add_css_class("dim-label")
        box.append(len_lbl)

    lyrics = params.get("lyrics")
    if isinstance(lyrics, str) and lyrics.strip():
        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.get_buffer().set_text(lyrics)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(80)
        sw.set_max_content_height(220)
        sw.set_propagate_natural_height(True)
        sw.set_child(tv)
        box.append(sw)

    seed = params.get("seed")
    if isinstance(seed, int) and not isinstance(seed, bool):
        seed_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        seed_lbl = Gtk.Label(label=f"Seed {seed}", xalign=0.0)
        seed_lbl.set_selectable(True)
        seed_lbl.add_css_class("dim-label")
        seed_row.append(seed_lbl)
        if on_use_seed is not None:
            use = Gtk.Button(label="Use this seed")
            use.set_tooltip_text("Put this seed in the Music form to ask for the same clip again.")
            use.connect("clicked", lambda _b, s=seed: on_use_seed(s))
            seed_row.append(use)
        box.append(seed_row)

    credit = params.get("credit")
    if credit:
        cr = Gtk.Label(label=f"Made with {credit}", xalign=0.0)
        cr.set_wrap(True)
        cr.add_css_class("dim-label")
        box.append(cr)

    open_btn = Gtk.Button(label="Open externally")
    open_btn.set_halign(Gtk.Align.START)
    open_btn.set_sensitive(exists)
    open_btn.connect("clicked", lambda _b, p=path: open_externally(p))
    box.append(open_btn)
    return box


def _pause(stream) -> None:
    try:
        if stream is not None and stream.get_playing():
            stream.pause()
    except Exception:  # noqa: BLE001
        pass


def _confirm_delete(parent, rec, on_confirmed: Callable[[str], None]) -> None:
    """Ask before deleting; call `on_confirmed(rec.id)` only on Delete."""
    from time_utils import fmt_local_date
    dialog = Gtk.AlertDialog()
    dialog.set_message("Delete this song?")
    dialog.set_detail(f"{(rec.prompt or 'Music')[:60]} — {fmt_local_date(rec.created_at)}")
    dialog.set_buttons(["Cancel", "Delete"])
    dialog.set_cancel_button(0)
    dialog.set_default_button(0)

    def _done(d, result):
        try:
            idx = d.choose_finish(result)
        except Exception:  # noqa: BLE001 - dismissed
            return
        if idx == 1:
            on_confirmed(rec.id)

    dialog.choose(parent, None, _done)


class AudioGallery(Gtk.Box):
    """Grid of song cards, newest first.

    on_card_activated(media_id)  - single or double click on a card
    on_card_deleted(media_id)    - after the card's own delete removed the record
    on_card_starred(media_id, starred)
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.on_card_activated: Optional[Callable[[str], None]] = None
        self.on_card_deleted: Optional[Callable[[str], None]] = None
        self.on_card_starred: Optional[Callable[[str, bool], None]] = None
        self._records: list[MediaRecord] = []
        self._tile_w = gallery_layout.TILE_W
        self._tile_h = gallery_layout.TILE_H
        self._cards: dict[str, Gtk.Overlay] = {}
        self._empty = Gtk.Label(label="No songs yet. Make one with Create, Music.")
        self._empty.add_css_class("muted")
        self._empty.set_margin_top(24)
        self._flow = Gtk.FlowBox()
        self._flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flow.set_homogeneous(True)
        self._flow.set_valign(Gtk.Align.START)
        self._flow.set_max_children_per_line(30)
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_vexpand(True)
        self._scroll.set_child(self._flow)
        self.append(self._empty)
        self.append(self._scroll)

    # ── Data ────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Reload every audio record from the store (newest first) and rebuild the grid."""
        self._records = _ms.query(media_type="audio")
        self._rebuild()

    def records(self) -> list[MediaRecord]:
        return list(self._records)

    def _delete_card(self, media_id: str) -> None:
        """Remove the row, then the song's files, then the card; tell the owner."""
        rec = _ms.get(media_id)
        _ms.delete(media_id)
        if rec is not None:
            remove_song_files(rec)
        self.remove_record(media_id)
        if self.on_card_deleted:
            self.on_card_deleted(media_id)

    def remove_record(self, media_id: str) -> None:
        self._records = [r for r in self._records if r.id != media_id]
        self._rebuild()

    def set_tile_size(self, width: int, height: int) -> None:
        self._tile_w, self._tile_h = width, height
        self._rebuild()

    def cards(self) -> list[Gtk.Overlay]:
        return list(self._cards.values())

    def activate(self, media_id: str) -> None:
        if self.on_card_activated:
            self.on_card_activated(media_id)

    # ── Cards ───────────────────────────────────────────────────────────────

    def _rebuild(self) -> None:
        while child := self._flow.get_first_child():
            self._flow.remove(child)
        self._cards = {}
        for rec in self._records:
            card = self._make_card(rec)
            self._cards[rec.id] = card
            self._flow.append(card)
        self._empty.set_visible(not self._records)

    def _make_card(self, rec: MediaRecord) -> Gtk.Overlay:
        tile_w, tile_h = self._tile_w, self._tile_h
        overlay = Gtk.Overlay()
        overlay.set_size_request(tile_w, tile_h)
        overlay.add_css_class("artgen-card")
        overlay.set_overflow(Gtk.Overflow.HIDDEN)
        overlay._media_id = rec.id

        base = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content = self._card_content(rec)
        base.append(gallery_layout.pin_fixed_zone(content, tile_w, max(tile_h - _BOTTOM_BAR_H, 1)))
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bottom.add_css_class("artgen-card-bottom")
        badge = Gtk.Label(label="MUSIC")
        badge.add_css_class("artgen-type-badge")
        bottom.append(badge)
        got = rec.params_dict.get("seconds_got")
        if isinstance(got, (int, float)):
            bottom.append(Gtk.Label(label=f"{got:.0f} s"))
        ts = Gtk.Label(label=rec.created_at[5:10] if len(rec.created_at) >= 10 else "")
        ts.add_css_class("muted")
        ts.set_hexpand(True)
        ts.set_xalign(1.0)
        bottom.append(ts)
        base.append(bottom)
        overlay.set_child(base)

        # Hover actions: star and delete. No Remix: no seed kind exists for audio.
        rev = Gtk.Revealer()
        rev.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        rev.set_transition_duration(100)
        rev.set_halign(Gtk.Align.END)
        rev.set_valign(Gtk.Align.START)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        actions.set_margin_top(3)
        actions.set_margin_end(3)
        actions.add_css_class("artgen-card-hover-actions")

        star_btn = Gtk.Button(label="★" if rec.starred else "☆")
        star_btn.add_css_class("artgen-card-action-btn")
        star_btn.set_tooltip_text("Unstar" if rec.starred else "Star")

        def _on_star(_b, _rec=rec, _sb=star_btn):
            new = not bool(_rec.starred)
            _ms.star(_rec.id, new)
            _rec.starred = int(new)
            _sb.set_label("★" if new else "☆")
            _sb.set_tooltip_text("Unstar" if new else "Star")
            if self.on_card_starred:
                self.on_card_starred(_rec.id, new)

        star_btn.connect("clicked", _on_star)
        actions.append(star_btn)

        del_btn = Gtk.Button(label="🗑")
        del_btn.add_css_class("artgen-card-action-btn")
        del_btn.set_tooltip_text("Delete")

        def _deleted(media_id: str):
            self._delete_card(media_id)

        del_btn.connect("clicked", lambda _b, _r=rec, _o=overlay: _confirm_delete(_o.get_root(), _r, _deleted))
        actions.append(del_btn)
        rev.set_child(actions)
        overlay.add_overlay(rev)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: rev.set_reveal_child(True))
        motion.connect("leave", lambda *_: rev.set_reveal_child(False))
        overlay.add_controller(motion)

        click = Gtk.GestureClick()
        click.set_button(1)
        # Single and double click both open the detail: the detail is the viewer for a song.
        click.connect("pressed", lambda _g, _n, _x, _y, _id=rec.id: self.activate(_id))
        overlay.add_controller(click)
        return overlay

    @staticmethod
    def _card_content(rec: MediaRecord) -> Gtk.Widget:
        """The waveform when the thumbnail exists, else a note glyph and the first words of the prompt."""
        thumb = rec.thumbnail_path or ""
        if thumb and Path(thumb).exists():
            try:
                pic = Gtk.Picture.new_for_filename(thumb)
                pic.set_content_fit(Gtk.ContentFit.COVER)
                pic.set_can_shrink(True)
                return pic
            except Exception:  # noqa: BLE001
                _log.debug("waveform card picture failed", exc_info=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_valign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        glyph = Gtk.Label(label=_PLACEHOLDER)
        glyph.add_css_class("title-1")
        box.append(glyph)
        words = Gtk.Label(label=(rec.prompt or "Music")[:60])
        words.set_wrap(True)
        words.set_max_width_chars(24)
        words.add_css_class("muted")
        box.append(words)
        return box


class AudioDetail(Gtk.Box):
    """Right-pane view of one song.

    on_deleted(media_id) - after the user confirmed and the record is gone
    on_starred(media_id, starred)
    on_back()            - the last song was deleted (the owner collapses the pane)
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.on_deleted: Optional[Callable[[str], None]] = None
        self.on_starred: Optional[Callable[[str, bool], None]] = None
        self.on_back: Optional[Callable[[], None]] = None
        self._records: list[MediaRecord] = []
        self._idx = 0
        self._stream = None
        self._syncing = False
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        self.set_margin_start(12)
        self.set_margin_end(12)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._count = Gtk.Label(xalign=0.0)
        self._count.add_css_class("dim-label")
        self._count.set_hexpand(True)
        header.append(self._count)
        self._star_btn = Gtk.ToggleButton(label="☆  Star")
        self._star_btn.connect("toggled", self._on_star_toggled)
        header.append(self._star_btn)
        self._del_btn = Gtk.Button(label="🗑  Delete")
        self._del_btn.connect("clicked", self._on_delete)
        header.append(self._del_btn)
        self.append(header)

        self._body = Gtk.ScrolledWindow()
        self._body.set_vexpand(True)
        self._body.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.append(self._body)
        self._render()

    # ── Public ──────────────────────────────────────────────────────────────

    def show_record(self, media_id: str, records: list[MediaRecord]) -> None:
        self._records = list(records)
        self._idx = next((i for i, r in enumerate(self._records) if r.id == media_id), 0)
        self._render()

    def current_id(self) -> Optional[str]:
        return self._records[self._idx].id if self._records else None

    def pause(self) -> None:
        """Pause playback. Called when the pane switches away or collapses: a hidden stack child keeps playing."""
        _pause(self._stream)

    # ── Render ──────────────────────────────────────────────────────────────

    def _render(self) -> None:
        _pause(self._stream)
        self._stream = None
        has = bool(self._records)
        self._star_btn.set_sensitive(has)
        self._del_btn.set_sensitive(has)
        if not has:
            self._count.set_label("")
            empty = Gtk.Label(label="No song selected.")
            empty.add_css_class("muted")
            self._body.set_child(empty)
            return
        rec = self._records[self._idx]
        self._count.set_label(f"{self._idx + 1} of {len(self._records)}")
        self._syncing = True
        try:
            self._star_btn.set_active(bool(rec.starred))
            self._star_btn.set_label("★  Starred" if rec.starred else "☆  Star")
        finally:
            self._syncing = False
        path = rec.file_path or ""
        exists = bool(path) and Path(path).exists()
        widget = build_audio_widget(rec, path, exists)
        self._stream = getattr(widget, "audio_stream", None)
        self._body.set_child(widget)

    # ── Handlers ────────────────────────────────────────────────────────────

    def _on_star_toggled(self, btn: Gtk.ToggleButton) -> None:
        if self._syncing or not self._records:
            return
        rec = self._records[self._idx]
        starred = btn.get_active()
        _ms.star(rec.id, starred)
        rec.starred = int(starred)
        btn.set_label("★  Starred" if starred else "☆  Star")
        if self.on_starred:
            self.on_starred(rec.id, starred)

    def _on_delete(self, _btn) -> None:
        if self._records:
            _confirm_delete(self.get_root(), self._records[self._idx], self._delete_confirmed_for)

    def _delete_confirmed_for(self, media_id: str) -> None:
        """Delete the record, tell the owner, then step to the next song or call on_back when none are left."""
        rec = _ms.get(media_id)
        _ms.delete(media_id)
        if rec is not None:
            remove_song_files(rec)
        self._records = [r for r in self._records if r.id != media_id]
        if self.on_deleted:
            self.on_deleted(media_id)
        if self._records:
            self._idx = min(self._idx, len(self._records) - 1)
            self._render()
        else:
            self._render()
            if self.on_back:
                self.on_back()
