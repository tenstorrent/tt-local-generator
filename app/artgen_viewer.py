#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
ArtgenViewerWindow — standalone full-screen viewer for artgen media.

"Unify gallery interaction" Task 5: net-new full-screen viewer for the
artgen media types (svg/gif/ansi/palette/verse/markdown/codeart) that the
native `main_window.VideoPlayerWindow`/`ImageViewerWindow` don't handle.
Mirrors their shape exactly:

  - `Gtk.Window`, `set_transient_for(parent_window)`, `maximize()` on open
  - title = a short prompt snippet (or a derived title when there's no prompt)
  - a bottom control strip: "⛶ Fullscreen" / "✕ Close"
  - a `Gtk.EventControllerKey`: F/f toggles fullscreen, Escape closes
  - no prev/next navigation (that stays on `ArtgenDetail`'s sidebar)

The artifact body widget comes from `artgen_render.render_artifact_widget`,
the ext -> renderer dispatch extracted out of `ArtgenDetail._render` as part
of this same task (see `artgen_render.py`'s module docstring). Sharing that
dispatch means this window and `ArtgenDetail`'s persistent pane can never
disagree about how a given file renders.
"""
from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from artgen_render import derive_title as _derive_title, render_artifact_widget


class ArtgenViewerWindow(Gtk.Window):
    """Full-screen standalone window for one artgen artifact.

    Display-only: no delete/star/remix affordances and no prev/next stepping
    (those stay on `ArtgenDetail`'s sidebar/nav-arrows) — the same minimal
    "just the artifact + Fullscreen/Close" shape as `VideoPlayerWindow`/
    `ImageViewerWindow`.
    """

    def __init__(self, record, parent_window: "Gtk.Window | None"):
        super().__init__()
        self.set_transient_for(parent_window)
        self.set_modal(False)  # non-modal, matching VideoPlayerWindow/ImageViewerWindow

        self.set_title(self._compute_title(record))
        self.set_default_size(1280, 720)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(outer)

        # ── Artifact body ────────────────────────────────────────────────
        self._body = render_artifact_widget(record)
        self._body.set_hexpand(True)
        self._body.set_vexpand(True)
        outer.append(self._body)

        # ── Control strip ────────────────────────────────────────────────
        ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ctrl.set_margin_start(12)
        ctrl.set_margin_end(12)
        ctrl.set_margin_top(6)
        ctrl.set_margin_bottom(6)
        outer.append(ctrl)

        fs_btn = Gtk.Button(label="⛶ Fullscreen")
        fs_btn.set_tooltip_text("Toggle fullscreen (F)")
        fs_btn.connect("clicked", lambda _: self._toggle_fullscreen())
        ctrl.append(fs_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        ctrl.append(spacer)

        close_btn = Gtk.Button(label="✕ Close")
        close_btn.connect("clicked", lambda _: self.close())
        ctrl.append(close_btn)

        # ── Keyboard shortcuts ───────────────────────────────────────────
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key)
        self.add_controller(key_ctrl)

        # ── Cleanup ──────────────────────────────────────────────────────
        # `AnimatedGifWidget` (the gif branch's body widget) already cancels
        # its own GLib timer on its own "unrealize" signal, which fires
        # synchronously when the window is realized and then closed. This
        # connection on the WINDOW's own "unrealize" is a belt-and-suspenders
        # safety net (in case a future body widget type doesn't self-cancel)
        # and drops our own reference to the body widget so nothing keeps a
        # WebView alive past close. NOTE: PyGObject's "destroy" signal on a
        # never-realized (never `present()`-ed) top-level window does not
        # fire synchronously on `.close()`/`.destroy()` -- only once the
        # Python wrapper is garbage-collected -- so "unrealize" (which DOES
        # fire synchronously once the window has been realized/presented) is
        # the reliable hook here, not "destroy".
        self.connect("unrealize", self._on_unrealize)

        self.maximize()

    @staticmethod
    def _compute_title(record) -> str:
        """Short prompt snippet, or a derived title when there's no prompt.

        Mirrors `VideoPlayerWindow`/`ImageViewerWindow`'s
        `record.prompt[:60]+"…"` snippet; falls back to
        `artgen_render.derive_title` (verse/freeform/ansi-aware) for artgen
        records that have no prompt, then to the bare generator type, then to
        a generic label so the title bar is never blank.
        """
        prompt = (getattr(record, "prompt", "") or "").strip()
        if prompt:
            return prompt if len(prompt) <= 60 else prompt[:60] + "…"
        gen_type = getattr(record, "generator_type", "") or ""
        params = getattr(record, "params_dict", None) or {}
        derived = _derive_title(gen_type, params)
        return derived or gen_type or "Artifact"

    def _toggle_fullscreen(self) -> None:
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def _on_key(self, _ctrl, keyval, _keycode, _state) -> bool:
        # Gdk.KEY_Escape = 0xff1b, Gdk.KEY_f/F = 0x66/0x46
        if keyval == 0xFF1B:   # Escape
            self.close()
            return True
        if keyval in (0x66, 0x46):  # f / F
            self._toggle_fullscreen()
            return True
        return False

    def _on_unrealize(self, _widget) -> None:
        timer_id = getattr(self._body, "_timer_id", None)
        if timer_id is not None:
            from gi.repository import GLib
            GLib.source_remove(timer_id)
            self._body._timer_id = None
        self._body = None
