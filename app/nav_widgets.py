"""Breadcrumb + ContextTray — GTK views that render a NavState.

Dumb views: they subscribe to a NavState and rebuild on change (deferred via
GLib.idle_add, so an off-thread context mutation is safe). Every action goes
out through an injected callback; the widgets never mutate NavState or touch
generation. Glyphs live in Python str labels (never CSS).
"""
from __future__ import annotations

from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib  # noqa: E402


def _clear(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


class Breadcrumb(Gtk.Box):
    def __init__(self, nav_state, on_navigate: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("nav-breadcrumb")
        self._ns = nav_state
        self._on_navigate = on_navigate
        nav_state.subscribe(lambda _s: GLib.idle_add(self._render))
        self._render()

    def _render(self) -> bool:
        _clear(self)
        crumbs = self._ns.crumbs()
        for i, cr in enumerate(crumbs):
            if i:
                sep = Gtk.Label(label="›")
                sep.add_css_class("nav-crumb-sep")
                self.append(sep)
            if cr.target:
                b = Gtk.Button(label=cr.label)
                b.add_css_class("nav-crumb-link")
                b.connect("clicked", lambda _b, t=cr.target: self._on_navigate(t))
                self.append(b)
            else:
                l = Gtk.Label(label=cr.label)
                l.add_css_class("nav-crumb-here")
                self.append(l)
        self.set_visible(len(crumbs) > 0)
        return False  # GLib.idle_add: run once


class ContextTray(Gtk.Box):
    def __init__(self, nav_state, on_resume: Callable[[str], None],
                 on_dismiss: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("nav-context-tray")
        self._ns = nav_state
        self._on_resume = on_resume
        self._on_dismiss = on_dismiss
        nav_state.subscribe(lambda _s: GLib.idle_add(self._render))
        self._render()

    def _render(self) -> bool:
        _clear(self)
        ctxs = self._ns.contexts()
        for ctx in ctxs:
            chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            chip.add_css_class("nav-ctx-chip")
            if ctx.running:
                dot = Gtk.Label(label="●")
                dot.add_css_class("nav-ctx-dot")
                chip.append(dot)
            open_btn = Gtk.Button(label=ctx.label)
            open_btn.add_css_class("nav-ctx-open")
            open_btn.connect("clicked", lambda _b, i=ctx.id: self._on_resume(i))
            chip.append(open_btn)
            close_btn = Gtk.Button(label="✕")
            close_btn.add_css_class("nav-ctx-close")
            close_btn.connect("clicked", lambda _b, i=ctx.id: self._on_dismiss(i))
            chip.append(close_btn)
            self.append(chip)
        self.set_visible(len(ctxs) > 0)
        return False
