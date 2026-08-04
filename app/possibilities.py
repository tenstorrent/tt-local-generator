"""The "Start something" possibilities wall for the Create surface.

A full-width wall of per-medium exemplar cards ("make one like this"). Each
tile's art resolves in priority order so creative options are present whether
you have 0 saved pieces or 600, WITHOUT the app hard-depending on shipped
sample assets:

  1. YOUR latest piece of that medium (personal; gets richer as you create)
  2. a CURATED sample — from a "demo"/favorites playlist if you have one, or
     (future) an optional curated-samples .deb that drops records into the
     same store. Discovered by name via `curated_playlist_matcher`.
  3. a per-medium GRADIENT + the medium's icon (always works, no assets).

Tapping a tile calls `on_pick(medium, example_idea)` — the Create surface uses
that to seed its existing composer (select the medium chip + fill the prompt
entry). This widget never generates and never reads generation params, so it
cannot affect `CreateView._collect_params()`.
"""
from __future__ import annotations

import os
import sys
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GdkPixbuf  # noqa: E402


# Example ideas seed the composer on a tile pick. Drawn straight from the app's
# OWN sources so they read in the house voice, not generic filler:
#   - native mediums (image/video/animate): representative lines from the
#     prompt generator's seed corpus (app/prompts/markov_seed.txt).
#   - artgen mediums: the evocative THEMES behind curated "favorite" productions
#     (the per-type playlists — Verses, Ansis, Palettes, Codearts, … — and
#     "The Demo"), not the templated internal prompts.
# Keyed by medium id, with a per-kind fallback for an unknown/new medium.
_EXAMPLE_IDEAS_BY_ID = {
    "image": "a Moog Minimoog on a kitchen table in a dark apartment, one desk lamp",
    "video": "a crop picker walks an empty furrow at 5am, the valley still grey",
    "animate": "an elderly fisherman turns toward the horizon, a quiet smile at dawn",
    "verse": "the final cartridge, the last checkpoint",
    "ansi": "a death's-head moth, glowing wings spread wide",
    "palette": "a bioluminescent tidal flat at 3am",
    "landscape": "an otherworldly, dreamlike vista",
    "constellation": "an invented star chart",
    "codeart": "the nature of recursion",
}
_EXAMPLE_IDEAS_BY_KIND = {
    "image": "a Moog Minimoog on a kitchen table, one desk lamp, Hopper stillness",
    "video": "a crop picker walks an empty furrow at 5am, the valley still grey",
    "gif": "an elderly fisherman turns toward the horizon, a quiet smile at dawn",
    "text": "the final cartridge, the last checkpoint",
}
# Deterministic per-kind gradient CSS class (defined in main_window _CSS via
# `poss-grad-*`). Falls back to `poss-grad-image`.
_GRADIENT_CLASS_BY_KIND = {
    "image": "poss-grad-image",
    "video": "poss-grad-video",
    "gif": "poss-grad-gif",
    "text": "poss-grad-text",
}
_TILE_W, _TILE_H = 200, 104

# Curated BUNDLED tile art that ships with the app (app/assets/), keyed by
# medium id. Always present, so it's the top-priority source for a tile — used
# for mediums where a specific hand-picked example reads better than "your
# latest". The Image tile uses a World's Fair (Montreal Expo 67) generated
# image rather than whatever raster you happened to make last.
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_BUNDLED_TILE_ART = {
    "image": "tile-image-montreal-1967.jpg",
}


def _bundled_tile_art_path(medium) -> "Optional[str]":
    """Absolute path to a medium's bundled tile image, or None if it has none
    (or the asset is missing — never crash the wall over a packaging slip)."""
    fn = _BUNDLED_TILE_ART.get(getattr(medium, "id", None))
    if not fn:
        return None
    p = os.path.join(_ASSETS_DIR, fn)
    return p if os.path.exists(p) else None


def example_idea_for(medium) -> str:
    return (_EXAMPLE_IDEAS_BY_ID.get(medium.id)
            or _EXAMPLE_IDEAS_BY_KIND.get(medium.kind)
            or "something new")


def _default_curated_matcher(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in ("demo", "sample", "showcase", "favorite", "favourite"))


class PossibilitiesWall(Gtk.Box):
    def __init__(self, *, mediums_fn: Callable[[], list], on_pick,
                 store=None, curated_playlist_matcher: Optional[Callable[[str], bool]] = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("possibilities-wall")
        self._mediums_fn = mediums_fn
        self._on_pick = on_pick
        if store is None:
            from media_store import media_store as store  # lazy: avoid import at test-collect
        self._store = store
        self._match = curated_playlist_matcher or _default_curated_matcher
        self._cards = 0

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.add_css_class("possibilities-head")
        title = Gtk.Label(label="Start something", xalign=0.0)
        title.add_css_class("possibilities-title")
        title.set_hexpand(True)
        # "Surprise me" now lives in the bottom CTA bar (CreateView wires a button
        # to `surprise()`), keeping all actions in the reserved button area — so
        # the wall header is just the title.
        head.append(title)
        self.append(head)

        # A single-row horizontal SHELF (not a wrapping grid): always present as
        # inspiration, but minimal vertical footprint so the Create surface fits
        # without scrolling. Scrolls horizontally when the mediums overflow.
        self._flow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._flow.add_css_class("possibilities-shelf")
        shelf_scroll = Gtk.ScrolledWindow()
        shelf_scroll.add_css_class("possibilities-shelf-scroll")
        shelf_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        shelf_scroll.set_child(self._flow)
        self.append(shelf_scroll)

        self.refresh()

    # ---- public --------------------------------------------------------
    def card_count(self) -> int:
        return self._cards

    def refresh(self) -> None:
        child = self._flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._flow.remove(child)
            child = nxt
        self._cards = 0
        try:
            mediums = list(self._mediums_fn() or [])
        except Exception:
            mediums = []
        for m in mediums:
            self._flow.append(self._make_card(m))
            self._cards += 1

    # ---- internals -----------------------------------------------------
    def _make_card(self, medium) -> Gtk.Widget:
        card = Gtk.Button()
        card.add_css_class("possibilities-card")
        card.set_size_request(_TILE_W, _TILE_H)
        card.connect("clicked", lambda _b, mm=medium: self._activate_card(mm))

        overlay = Gtk.Overlay()
        kind, payload = self._resolve_tile_art(medium)
        art = self._build_art(kind, payload, medium)
        art.set_size_request(_TILE_W, _TILE_H)
        overlay.set_child(art)

        cap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        cap.add_css_class("possibilities-cap")
        cap.set_valign(Gtk.Align.END)
        med = Gtk.Label(label=f"{medium.icon} {medium.label}", xalign=0.0)
        med.add_css_class("possibilities-med")
        eg = Gtk.Label(label=f"e.g. {example_idea_for(medium)}", xalign=0.0)
        eg.add_css_class("possibilities-eg")
        eg.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        cap.append(med)
        cap.append(eg)
        overlay.add_overlay(cap)

        card.set_child(overlay)
        return card

    def _build_art(self, kind: str, payload, medium) -> Gtk.Widget:
        if kind == "thumb":
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(payload, _TILE_W, _TILE_H, False)
                pic = Gtk.Picture.new_for_pixbuf(pb)
                pic.set_content_fit(Gtk.ContentFit.COVER)
                return pic
            except Exception:
                pass  # fall through to gradient
        box = Gtk.Box()
        box.add_css_class(_GRADIENT_CLASS_BY_KIND.get(medium.kind, "poss-grad-image"))
        icon = Gtk.Label(label=medium.icon)
        icon.add_css_class("possibilities-grad-icon")
        icon.set_hexpand(True); icon.set_vexpand(True)
        box.append(icon)
        return box

    def _is_curated_for(self, name: str, key: str) -> bool:
        """A playlist is a curated art source for a medium if it's a
        demo/favorite playlist (via the injected matcher) OR a per-type
        playlist whose name is the plural of the medium/generator — e.g.
        "Ansis"->ansi, "Palettes"->palette, "Animatediffs"->animatediff."""
        try:
            if self._match(name):
                return True
        except Exception:
            pass
        n = (name or "").lower().strip()
        return bool(key) and (n.rstrip("s") == key or n.rstrip("s") == key.rstrip("s"))

    def _resolve_tile_art(self, medium):
        mt = "artgen" if medium.source == "artgen" else medium.id
        gt = medium.generator if medium.source == "artgen" else None
        # A hand-picked BUNDLED example (ships with the app) wins over
        # everything — e.g. the Image tile's World's Fair Montreal '67 image.
        bundled = _bundled_tile_art_path(medium)
        if bundled is not None:
            return ("thumb", bundled)
        # Tile art comes ONLY from CURATED collections — the user's per-type
        # playlists (Ansis, Palettes, Verses, Animatediffs, …) and any
        # demo/favorite playlist — preferring starred picks. We deliberately do
        # NOT surface an arbitrary recent generation (that put unflattering /
        # test images on the medium tiles); a medium with no curated sample
        # gets the clean per-kind gradient instead.
        key = (medium.generator or medium.id or "").lower()
        try:
            playlists = self._store.list_playlists()
        except Exception:
            playlists = []
        for pl in playlists:
            if not self._is_curated_for(pl.get("name", ""), key):
                continue
            try:
                recs = self._store.playlist_records(pl["id"])
            except Exception:
                continue
            recs = sorted(recs, key=lambda r: -int(getattr(r, "starred", 0) or 0))
            for r in recs:
                if getattr(r, "media_type", None) != mt:
                    continue
                if gt is not None and getattr(r, "generator_type", None) != gt:
                    continue
                t = getattr(r, "thumbnail_path", None)
                if t and os.path.exists(t):
                    return ("thumb", t)

        # Fallback for NATIVE mediums (image/video/animate): your most recent
        # piece of that medium, so a tile shows a real example instead of a bare
        # gradient+icon once you've created anything (the wall gets richer as you
        # use it). Deliberately native-only — artgen mediums stay curated-only,
        # since surfacing an arbitrary recent artgen put unflattering/test
        # artifacts on those tiles (the reason curated-only was introduced). A
        # native medium you've never generated in still degrades to the gradient.
        if medium.source == "native":
            try:
                recs = self._store.query(media_type=mt, limit=8)
            except Exception:
                recs = []
            for r in recs:
                t = getattr(r, "thumbnail_path", None)
                if t and os.path.exists(t):
                    return ("thumb", t)
        return ("gradient", None)

    def _activate_card(self, medium) -> None:
        # Unlike the art-resolution swallows (which degrade to a valid gradient),
        # a raising `on_pick` is an integration bug in the Create-surface wiring,
        # not a cosmetic miss — leave a stderr breadcrumb instead of a silent no-op.
        try:
            self._on_pick(medium, example_idea_for(medium))
        except Exception as exc:  # pragma: no cover - defensive
            print(f"PossibilitiesWall: on_pick failed for {getattr(medium, 'id', '?')}: {exc}",
                  file=sys.stderr)

    def surprise(self) -> None:
        """Pick a medium for the user and seed the composer — same as tapping a
        tile, but chosen for them. Public so the CTA bar's "Surprise me" button
        (owned by CreateView) can drive it."""
        try:
            mediums = list(self._mediums_fn() or [])
        except Exception:
            mediums = []
        if not mediums:
            return
        # Deterministic-free choice without Date/random import concerns: rotate
        # by a monotonically advancing counter kept on the instance.
        self._surprise_i = (getattr(self, "_surprise_i", -1) + 1) % len(mediums)
        self._activate_card(mediums[self._surprise_i])
