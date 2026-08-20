"""The "Start something" possibilities wall for the Create surface.

A full-width wall of per-medium exemplar cards ("make one like this"). Each
tile's art resolves in priority order so creative options are present whether
you have 0 saved pieces or 600, WITHOUT the app hard-depending on shipped
sample assets:

  1. a STARRED piece of that medium — an explicit user pick wins over
     everything ("star an image and it becomes this tile").
  2. a hand-picked BUNDLED example that ships with the app (only some mediums,
     e.g. the Image tile) — the default before you've starred your own.
  3. a CURATED sample — from a "demo"/favorites playlist (or a per-type
     playlist), star-sorted. Discovered by name via `curated_playlist_matcher`.
  4. (native mediums only) YOUR most recent piece of that medium.
  5. a per-medium GRADIENT + the medium's icon (always works, no assets).

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


# Example ideas seed the composer on a tile pick. Each medium has its OWN pool
# of distinct, evocative lines written in that art type's voice — a color
# palette, a poem, an ANSI moth and a photographic still should never suggest
# the same thing. The wall rotates through a pool (see `_pick_from_pool`) so
# repeat visits vary and no two tiles echo each other. Every medium the app can
# surface (native image/video + every artgen generator) has an entry here, so
# nothing falls back to a generic per-kind line and duplicates a neighbor.
#
# These are the DEFAULT suggestions. When a tile actually shows an explicitly
# STARRED piece of the medium (tier 1 of `_resolve_tile_art`), the tile's real
# prompt is used instead — the copy then describes exactly what's on the tile
# (see `_example_for`).
_EXAMPLE_POOLS_BY_ID = {
    # native
    "image": [
        "a Moog Minimoog on a kitchen table, one desk lamp, Hopper stillness",
        "a rain-slick Tokyo alley reflecting a single red sign",
        "a lighthouse keeper reading by lantern while the storm leans in",
        "a greenhouse overrun with ferns, morning fog on the glass",
        "a chrome diner at closing time, cold coffee and neon",
    ],
    "video": [
        "a crop picker walks an empty furrow at 5am, the valley still grey",
        "steam curls off a city grate as a train rumbles underneath",
        "a paper boat rides the gutter stream after the rain stops",
        "curtains breathe in an empty room, afternoon light shifting",
        "a welder's sparks arc across a dark garage in slow motion",
    ],
    # artgen — text
    "verse": [
        "the final cartridge, the last checkpoint",
        "what the tide leaves, and what it takes back",
        "a letter never sent, folded twice",
        "the hum a house keeps after everyone leaves",
        "instructions for forgetting a name",
    ],
    "freeform": [
        "a manifesto for a machine that dreams",
        "found notes from a city that never woke",
        "the user manual for a feeling",
        "an inventory of things almost said",
    ],
    "codeart": [
        "the nature of recursion",
        "a function that returns its own shadow",
        "an infinite loop that learns to rest",
        "sorting a list of small regrets",
    ],
    # artgen — visual (SVG / grid / swatches)
    "landscape": [
        "twin moons over a salt desert",
        "a floating archipelago at dusk",
        "canyons carved from colored glass",
        "an otherworldly, dreamlike vista",
    ],
    "skyline": [
        "a neon megacity in the rain",
        "a retro-future skyline at golden hour",
        "silhouetted towers under a slow aurora",
        "a harbor city waking at dawn",
    ],
    "constellation": [
        "the constellation of the Forgotten Clockmaker",
        "a zodiac for deep-sea creatures",
        "stars that spell a lost alphabet",
        "an invented star chart",
    ],
    "geometric": [
        "nested impossible polygons",
        "a tessellation that never quite repeats",
        "concentric order dissolving into noise",
        "a mandala built from circuitry",
    ],
    "circuit": [
        "a schematic for a machine that hums lullabies",
        "copper traces branching like river deltas",
        "a synth patch drawn as a city map",
        "the wiring diagram of a heartbeat",
    ],
    "palette": [
        "a bioluminescent tidal flat at 3am",
        "the colors of a thunderstorm over wheat",
        "a sun-bleached seaside postcard",
        "embers and ash after the bonfire",
    ],
    "ansi": [
        "a death's-head moth, wings spread and glowing",
        "a chrome dragon coiled around a full moon",
        "a neon skull grinning in the void",
        "a pixel spaceship crossing a lo-fi planet",
    ],
    "ansi-image": [
        "a portrait rebuilt from color blocks",
        "a photograph dissolved into terminal glyphs",
        "a landscape redrawn in 256 colors",
    ],
    # artgen — gif (folded into Video today, kept for completeness)
    "animatediff": [
        "an elderly fisherman turns toward the horizon at dawn",
        "a paper crane unfolds itself in reverse",
        "a candle flame bends in an unseen draft",
    ],
}
# Per-KIND fallback pools — only reached by a brand-new medium id not yet listed
# above (a freshly dropped-in plugin). Kept generic on purpose; add the real
# medium to `_EXAMPLE_POOLS_BY_ID` when it lands.
_EXAMPLE_POOLS_BY_KIND = {
    "image": _EXAMPLE_POOLS_BY_ID["image"],
    "video": _EXAMPLE_POOLS_BY_ID["video"],
    "gif": _EXAMPLE_POOLS_BY_ID["animatediff"],
    "text": [
        "a small truth told in a strange voice",
        "something worth saying, said sideways",
    ],
}
_FALLBACK_EXAMPLE = "something new"
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
# medium id. The DEFAULT tile art for these mediums until you star your own
# piece of the medium (a star overrides it — see _resolve_tile_art) — a
# hand-picked example that reads better than an arbitrary recent generation.
# The Image tile uses a World's Fair (Montreal Expo 67) generated image.
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_BUNDLED_TILE_ART = {
    # Curated clean-install defaults, promoted from starred favorites so a
    # fresh install's wall shows real art on these tiles instead of a bare
    # gradient. A user's OWN starred piece of the medium still overrides these
    # (tier 1 in _resolve_tile_art). video/palette have no curated default yet
    # and fall through to the per-kind gradient.
    "image": "tile-image-3ad8978c.png",
    "ansi": "tile-ansi-9eb3f7f4.png",
    "verse": "tile-verse-0719899b.png",
    "landscape": "tile-landscape-faa70536.png",
    "constellation": "tile-constellation-23026e1d.png",
    "codeart": "tile-codeart-791456d8.png",
}


def _bundled_tile_art_path(medium) -> "Optional[str]":
    """Absolute path to a medium's bundled tile image, or None if it has none
    (or the asset is missing — never crash the wall over a packaging slip)."""
    fn = _BUNDLED_TILE_ART.get(getattr(medium, "id", None))
    if not fn:
        return None
    p = os.path.join(_ASSETS_DIR, fn)
    return p if os.path.exists(p) else None


def _pool_for(medium) -> list:
    """The example-idea pool for a medium: its own list, else a per-kind
    fallback, else a single generic line. Never empty."""
    return (_EXAMPLE_POOLS_BY_ID.get(getattr(medium, "id", None))
            or _EXAMPLE_POOLS_BY_KIND.get(getattr(medium, "kind", None))
            or [_FALLBACK_EXAMPLE])


def example_idea_for(medium) -> str:
    """A stable default example for a medium (the first of its pool). The wall
    itself rotates through the pool per build — see `PossibilitiesWall.
    _pick_from_pool` — but external callers get a deterministic single line."""
    return _pool_for(medium)[0]


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
        self._pool_i: dict = {}  # per-medium rotation cursor into its example pool

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

        overlay = Gtk.Overlay()
        kind, payload = self._resolve_tile_art(medium)
        art = self._build_art(kind, payload, medium)
        art.set_size_request(_TILE_W, _TILE_H)
        overlay.set_child(art)

        # The example line is computed ONCE here and captured in the click
        # closure, so what the caption shows is exactly what tapping seeds —
        # including the tie-to-shown case (a starred piece's own prompt).
        example = self._example_for(medium)
        card.connect("clicked", lambda _b, mm=medium, ex=example: self._activate_card(mm, ex))

        cap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        cap.add_css_class("possibilities-cap")
        cap.set_valign(Gtk.Align.END)
        med = Gtk.Label(label=f"{medium.icon} {medium.label}", xalign=0.0)
        med.add_css_class("possibilities-med")
        eg = Gtk.Label(label=f"e.g. {example}", xalign=0.0)
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
                # Zoom-to-fill (like CSS `object-fit: cover`), never stretch.
                # preserve_aspect_ratio=True keeps the image undistorted, loaded
                # into a generous 2x box so ContentFit.COVER (below) can crop-to-
                # fill the tile crisply. The old `False` here pre-stretched the
                # pixbuf to the exact tile size, distorting a non-square image
                # and leaving COVER nothing to crop.
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    payload, _TILE_W * 2, _TILE_H * 2, True
                )
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

    def _pick_from_pool(self, medium) -> str:
        """Next example line for a medium, rotating through its pool so repeat
        builds vary and no two tiles land on the same generic filler."""
        pool = _pool_for(medium)
        mid = getattr(medium, "id", None)
        i = self._pool_i.get(mid, 0) % len(pool)
        self._pool_i[mid] = i + 1
        return pool[i]

    def _starred_record_for(self, medium):
        """The starred record whose art the tile actually shows (tier 1 of
        `_resolve_tile_art`), or None. Mirrors that tier's exact condition
        (starred + a thumbnail that exists on disk) so the copy can only ever
        describe a piece that is genuinely the one on the tile."""
        mt = "artgen" if medium.source == "artgen" else medium.id
        gt = medium.generator if medium.source == "artgen" else None
        try:
            for r in (self._store.query(media_type=mt, generator_type=gt,
                                        starred=True, limit=1) or []):
                t = getattr(r, "thumbnail_path", None)
                if t and os.path.exists(t):
                    return r
        except Exception:
            pass
        return None

    def _example_for(self, medium) -> str:
        """The example line for a tile. When the tile shows an explicitly
        STARRED piece, use that piece's own prompt — the copy then describes
        exactly what's on the tile ("make one like THIS"). Otherwise rotate
        through the medium's curated pool. Falls back safely on any error."""
        try:
            rec = self._starred_record_for(medium)
            if rec is not None:
                prompt = (getattr(rec, "prompt", "") or "").strip()
                if prompt:
                    return prompt
        except Exception:
            pass
        return self._pick_from_pool(medium)

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
        # 1. An explicit user pick wins over everything — the most-recent
        #    STARRED piece of this medium. Starring IS curation (unlike an
        #    arbitrary recent generation, which we deliberately don't surface),
        #    so it overrides even the bundled default: "star an image and it
        #    becomes this tile."
        try:
            for r in (self._store.query(media_type=mt, generator_type=gt,
                                        starred=True, limit=1) or []):
                t = getattr(r, "thumbnail_path", None)
                if t and os.path.exists(t):
                    return ("thumb", t)
        except Exception:
            pass
        # 2. A hand-picked BUNDLED example (ships with the app) — the default
        #    when you haven't starred your own piece of this medium (e.g. the
        #    Image tile's World's Fair Montreal '67 image).
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

    def _activate_card(self, medium, example: "Optional[str]" = None) -> None:
        # `example` is the exact line the tapped tile displayed (passed from
        # _make_card so caption and seed always match); surprise()/direct calls
        # pass None and we resolve it fresh.
        idea = example if example is not None else self._example_for(medium)
        # Unlike the art-resolution swallows (which degrade to a valid gradient),
        # a raising `on_pick` is an integration bug in the Create-surface wiring,
        # not a cosmetic miss — leave a stderr breadcrumb instead of a silent no-op.
        try:
            self._on_pick(medium, idea)
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
