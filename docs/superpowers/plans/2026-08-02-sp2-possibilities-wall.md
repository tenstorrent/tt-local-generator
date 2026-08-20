# SP-2: "Start something" Possibilities Wall + empty-state Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the Create surface open to a wall of per-medium possibilities ("make one like this") so creative options are present with 0 saved pieces or 600 — never a blank form.

**Architecture:** A new self-contained widget `app/possibilities.py::PossibilitiesWall` (a `Gtk.Box`) renders one card per medium; each tile's art resolves **your latest of that medium → a curated "demo"/favorites playlist → a per-medium gradient fallback**, so the app never hard-depends on shipped sample assets (an optional curated-samples `.deb` is a future, separate source that slots into the same "curated" tier). Tapping a tile seeds the EXISTING composer (selects the medium chip + fills `_prompt_entry`); `_collect_params()` is untouched.

**Tech Stack:** Python 3 (`/usr/bin/python3`), GTK4/PyGObject, GdkPixbuf, pytest under `xvfb-run`.

## Global Constraints

- **Generation untouched:** `CreateView._collect_params()` returns byte-for-byte the same dict whether the wall exists or not; a tile-pick is exactly equivalent to selecting that medium chip + typing that prompt by hand. Pinned by a regression test.
- **No hard asset dependency:** the wall must render fully (gradient tier) on a brand-new install with an empty store and no curated playlist. Curated sources (demo/favorites playlist, future samples `.deb`) are used only when present.
- **GTK threading:** all `media_store` reads happen on the main thread in the build path (quick SQLite reads, same as gallery `refresh`) — no background threads introduced. Any `GLib.idle_add` only if a load is deferred.
- `_CSS` byte literals stay ASCII-only; medium glyphs/icons come from `Medium.icon` (Python str), never CSS.
- Palette tokens only in CSS. Injectable `store`/`mediums_fn` params so tests drive fakes with no real DB/display coupling.
- System `/usr/bin/python3`; tests `xvfb-run --auto-servernum /usr/bin/python3 -m pytest`. Bump `VERSION` + changelog. Local commits only; do not push.

## Reference facts (from the codebase)

- `Medium` (`app/create_mediums.py:35`, frozen dataclass): `id, label, icon, kind ("image"|"video"|"gif"|"text"), source ("native"|"artgen"), generator (Optional[str]), uses_llm`. Native ids: `"image"/"video"/"animate"`; artgen id == generator name (`"verse"`, `"ansi"`, …).
- `media_store` (`app/media_store.py`): `query(media_type=None, generator_type=None, starred=None, limit=None) -> list[MediaRecord]` (newest-first); `list_playlists() -> list[dict]` (each has `id`,`name`); `playlist_records(playlist_id) -> list[MediaRecord]`. `MediaRecord` fields include `media_type`, `generator_type`, `thumbnail_path`, `file_path`, `prompt`. Singleton: `from media_store import media_store`.
- **Medium → store query mapping:** native image/video/animate → `media_type=<id>`, `generator_type=None`; artgen → `media_type="artgen"`, `generator_type=medium.generator`.
- `CreateView` (`app/create_view.py`): form column is the `content` Gtk.Box assembled at ~972-981 (doors_row first). Seeding plumbing that already exists: `self._chip_buttons[medium_id].set_active(True)` fires `_select_medium`→`_swap_panel`; `self._prompt_entry.set_text(...)`; `self._doors["idea"].set_active(True)` switches to idea entry mode; `self._mediums_fn()` returns the medium list. `_collect_params()` is at ~2210-2278.

---

### Task 1: `PossibilitiesWall` widget

**Files:**
- Create: `app/possibilities.py`
- Test: `tests/test_possibilities_wall.py`

**Interfaces:**
- Produces: `class PossibilitiesWall(Gtk.Box)` with ctor `(self, *, mediums_fn, on_pick, store=None, curated_playlist_matcher=None)`; `on_pick` is `(medium: Medium, example_idea: str) -> None`. Public method `refresh()` rebuilds cards. Module fn `example_idea_for(medium) -> str`.

- [ ] **Step 1: Write failing tests** (`tests/test_possibilities_wall.py`)

Mirror the GTK-probe/skip header used by `tests/test_main_window_loop_nav.py` (lines 22-40). Use a fake store and fake mediums:

```python
from types import SimpleNamespace

def _medium(id, label="X", icon="*", kind="image", source="native", generator=None):
    return SimpleNamespace(id=id, label=label, icon=icon, kind=kind, source=source, generator=generator, uses_llm=True)

class _FakeStore:
    def __init__(self, latest=None, playlists=None, playlist_recs=None):
        self._latest = latest or {}          # (media_type, generator_type) -> [MediaRecord-likes]
        self._playlists = playlists or []    # list of {"id","name"}
        self._playlist_recs = playlist_recs or {}  # id -> [records]
    def query(self, media_type=None, generator_type=None, starred=None, limit=None):
        recs = self._latest.get((media_type, generator_type), [])
        return recs[:limit] if limit else recs
    def list_playlists(self): return self._playlists
    def playlist_records(self, pid): return self._playlist_recs.get(pid, [])

def _rec(mt, gt=None, thumb="/x.png"):
    return SimpleNamespace(media_type=mt, generator_type=gt, thumbnail_path=thumb, file_path=thumb, prompt="p")

def test_wall_builds_one_card_per_medium(tmp_path, monkeypatch):
    from possibilities import PossibilitiesWall
    meds = [_medium("image"), _medium("verse", kind="text", source="artgen", generator="verse")]
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: None, store=_FakeStore())
    # count Gtk.Button descendants tagged as medium cards
    assert wall.card_count() == 2

def test_pick_fires_on_pick_with_medium_and_idea(tmp_path):
    from possibilities import PossibilitiesWall
    picked = []
    meds = [_medium("image")]
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m, i: picked.append((m.id, i)), store=_FakeStore())
    wall._activate_card(meds[0])          # simulate a tile click
    assert picked and picked[0][0] == "image" and isinstance(picked[0][1], str) and picked[0][1]

def test_art_resolution_prefers_your_latest(tmp_path):
    from possibilities import PossibilitiesWall
    meds = [_medium("image")]
    store = _FakeStore(latest={("image", None): [_rec("image", thumb=str(tmp_path/'mine.png'))]})
    (tmp_path/'mine.png').write_bytes(b"\x89PNG\r\n")   # exists on disk
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m,i: None, store=store)
    kind, payload = wall._resolve_tile_art(meds[0])
    assert kind == "thumb" and payload == str(tmp_path/'mine.png')

def test_art_resolution_falls_back_to_curated_then_gradient(tmp_path):
    from possibilities import PossibilitiesWall
    meds = [_medium("verse", kind="text", source="artgen", generator="verse")]
    # no personal work; a curated playlist named "demo" holds a matching artgen/verse rec
    thumb = tmp_path/'curated.png'; thumb.write_bytes(b"\x89PNG\r\n")
    store = _FakeStore(playlists=[{"id":"p1","name":"The Demo"}],
                       playlist_recs={"p1":[_rec("artgen","verse",str(thumb))]})
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m,i: None, store=store)
    assert wall._resolve_tile_art(meds[0]) == ("thumb", str(thumb))
    # with NOTHING anywhere -> gradient tier, never raises
    bare = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m,i: None, store=_FakeStore())
    assert bare._resolve_tile_art(meds[0])[0] == "gradient"

def test_empty_store_builds_all_gradient_no_exception(tmp_path):
    from possibilities import PossibilitiesWall
    meds = [_medium("image"), _medium("video", kind="video"), _medium("ansi", kind="text", source="artgen", generator="ansi")]
    wall = PossibilitiesWall(mediums_fn=lambda: meds, on_pick=lambda m,i: None, store=_FakeStore())
    assert wall.card_count() == 3
```

- [ ] **Step 2: Run — verify fail** (`ModuleNotFoundError: possibilities`).
Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_possibilities_wall.py -v`

- [ ] **Step 3: Implement `app/possibilities.py`**

```python
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
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GdkPixbuf, GLib  # noqa: E402


# Example ideas seed the composer on a tile pick. Keyed by medium id, with a
# per-kind fallback so an unknown/new medium still gets a sensible seed. Copy
# is placeholder — safe to revise freely.
_EXAMPLE_IDEAS_BY_ID = {
    "image": "a lighthouse keeper at dawn",
    "video": "a coastal storm rolling in",
    "animate": "a candle flame flickering",
    "verse": "a haiku about silicon",
    "ansi": "a retro BBS dragon",
    "palette": "sulfuric emberfall",
    "landscape": "a misty fjord at golden hour",
    "constellation": "a Norse star chart",
    "codeart": "a turtle-graphics bloom",
}
_EXAMPLE_IDEAS_BY_KIND = {
    "image": "a lighthouse keeper at dawn",
    "video": "a coastal storm rolling in",
    "gif": "a candle flame flickering",
    "text": "something small and luminous",
}
# Deterministic per-kind gradient CSS class (defined in main_window _CSS via
# `poss-grad-*`). Falls back to `poss-grad-image`.
_GRADIENT_CLASS_BY_KIND = {
    "image": "poss-grad-image",
    "video": "poss-grad-video",
    "gif": "poss-grad-gif",
    "text": "poss-grad-text",
}
_TILE_W, _TILE_H = 230, 150


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
        surprise = Gtk.Button(label="✨ Surprise me")
        surprise.add_css_class("possibilities-surprise")
        surprise.connect("clicked", self._on_surprise)
        head.append(title)
        head.append(surprise)
        self.append(head)

        self._flow = Gtk.FlowBox()
        self._flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flow.set_max_children_per_line(8)
        self._flow.set_column_spacing(14)
        self._flow.set_row_spacing(14)
        self._flow.set_homogeneous(True)
        self.append(self._flow)

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

    def _resolve_tile_art(self, medium):
        mt = "artgen" if medium.source == "artgen" else medium.id
        gt = medium.generator if medium.source == "artgen" else None
        # 1. your latest
        try:
            recs = self._store.query(media_type=mt, generator_type=gt, limit=1)
            for r in recs:
                t = getattr(r, "thumbnail_path", None)
                if t and os.path.exists(t):
                    return ("thumb", t)
        except Exception:
            pass
        # 2. curated playlist
        try:
            for pl in self._store.list_playlists():
                if not self._match(pl.get("name", "")):
                    continue
                for r in self._store.playlist_records(pl["id"]):
                    if getattr(r, "media_type", None) != mt:
                        continue
                    if gt is not None and getattr(r, "generator_type", None) != gt:
                        continue
                    t = getattr(r, "thumbnail_path", None)
                    if t and os.path.exists(t):
                        return ("thumb", t)
        except Exception:
            pass
        # 3. gradient
        return ("gradient", None)

    def _activate_card(self, medium) -> None:
        try:
            self._on_pick(medium, example_idea_for(medium))
        except Exception:
            pass

    def _on_surprise(self, _btn) -> None:
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
```

Note: `Gtk.Picture.new_from_file_at_scale`→ use `GdkPixbuf.Pixbuf.new_from_file_at_scale` as written. If `set_content_fit`/`ContentFit` is unavailable on the installed GTK, drop those two lines (the pixbuf already sized). The tests only touch `_resolve_tile_art`, `card_count`, `_activate_card` — not real pixbuf loading — so they pass headless.

- [ ] **Step 4: Run — verify pass.**
Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_possibilities_wall.py -v` → all pass.

- [ ] **Step 5: Add the gradient + wall CSS** to `_CSS` in `app/main_window.py` (ASCII-only). Near the create-surface CSS, append:

```
.possibilities-wall { padding: 6px 2px; }
.possibilities-title { font-size: 20px; font-weight: bold; color: @tt_text; }
.possibilities-surprise {
    background-color: @tt_accent; color: @tt_bg_darkest;
    border-radius: 10px; padding: 8px 16px; font-weight: bold;
}
.possibilities-card {
    padding: 0; border-radius: 12px; border: 1px solid @tt_border;
    background-color: @tt_bg_dark;
}
.possibilities-card:hover { border-color: @tt_accent; }
.possibilities-cap {
    padding: 8px 10px;
    background-image: linear-gradient(to top, rgba(4,18,23,0.93), rgba(4,18,23,0.0));
    border-radius: 0 0 12px 12px;
}
.possibilities-med { font-size: 13px; font-weight: bold; color: @tt_text; }
.possibilities-eg { font-size: 11px; color: @tt_text_muted; }
.possibilities-grad-icon { font-size: 34px; opacity: 0.55; }
.poss-grad-image { background-image: linear-gradient(135deg, #123240, #3aa89e); }
.poss-grad-video { background-image: linear-gradient(135deg, #123f4a, #e0a24a); }
.poss-grad-gif   { background-image: linear-gradient(135deg, #1a2b4a, #7d3a8f); }
.poss-grad-text  { background-image: linear-gradient(135deg, #0b1f28, #2b6373); }
```

(These four gradient hexes are decorative fallback art, not brand chrome — acceptable literal use, mirroring the existing artgen placeholder gradients. Keep them ASCII.)

- [ ] **Step 6: Commit**
```bash
git add app/possibilities.py tests/test_possibilities_wall.py app/main_window.py
git commit -m "feat(create): PossibilitiesWall widget (your-work -> curated -> gradient art) (SP-2 t1)"
```

---

### Task 2: Mount the wall in the Create surface + seed the composer

**Files:**
- Modify: `app/create_view.py` — import + construct `PossibilitiesWall`, mount atop the form column, add the pick handler.
- Test: `tests/test_create_view_possibilities.py`

**Interfaces:**
- Consumes Task 1's `PossibilitiesWall(mediums_fn, on_pick, store, curated_playlist_matcher)`.
- Produces: `CreateView._on_possibility_picked(medium, idea)` and a `self._possibilities` attribute. `_collect_params()` unchanged.

- [ ] **Step 1: Write failing tests** (`tests/test_create_view_possibilities.py`)

Reuse the CreateView test harness pattern from `tests/test_create_view.py` (import it or copy its `_make_view` builder). Assert:

```python
def test_createview_has_possibilities_wall(...):
    view = _make_view()               # existing harness
    assert getattr(view, "_possibilities", None) is not None
    assert view._possibilities.card_count() >= 1

def test_pick_selects_medium_and_seeds_prompt(...):
    view = _make_view()
    meds = view._mediums_fn()
    target = meds[0]
    view._on_possibility_picked(target, "a test idea")
    assert view._active_medium.id == target.id
    assert view._prompt_entry.get_text() == "a test idea"

def test_collect_params_unchanged_by_pick(...):
    # picking a tile == manually selecting that medium + typing that prompt
    view = _make_view()
    m = view._mediums_fn()[0]
    view._on_possibility_picked(m, "abc")
    picked = view._collect_params()
    # manual equivalent
    view2 = _make_view()
    view2._chip_buttons[m.id].set_active(True)
    view2._prompt_entry.set_text("abc")
    view2._doors["idea"].set_active(True)
    manual = view2._collect_params()
    assert picked == manual
```

- [ ] **Step 2: Run — verify fail** (`_possibilities`/`_on_possibility_picked` don't exist).

- [ ] **Step 3: Implement.** In `app/create_view.py`:

Import near the other `create_param_panels` imports (top of file):
```python
from possibilities import PossibilitiesWall
```

In `__init__`, immediately BEFORE building the form column (`content = ...` at ~972), construct the wall (defensive: never let a wall failure break Create):
```python
        try:
            self._possibilities = PossibilitiesWall(
                mediums_fn=self._mediums_fn,
                on_pick=self._on_possibility_picked,
            )
        except Exception:
            self._possibilities = None
```

Mount it as the FIRST child of the form `content` box — insert `content.append(self._possibilities)` (guarded by `if self._possibilities is not None`) BEFORE the existing `content.append(self._build_doors_row())` line (~973). (It scrolls with the rest of the Create form; the persistent composer arrives in SP-4.)

Add the handler method:
```python
    def _on_possibility_picked(self, medium, idea: str) -> None:
        """Seed the existing composer from a possibilities tile: select the
        medium chip (fires `_select_medium`/`_swap_panel`), switch to idea
        entry, and fill the prompt. Pure convenience — touches no generation
        param; `_collect_params()` is identical to doing this by hand."""
        btn = self._chip_buttons.get(medium.id)
        if btn is not None and not btn.get_active():
            btn.set_active(True)
        elif btn is not None:
            self._select_medium(medium)      # already active -> ensure panel matches
        door = self._doors.get("idea")
        if door is not None and not door.get_active():
            door.set_active(True)
        if getattr(self, "_prompt_entry", None) is not None:
            self._prompt_entry.set_text(idea)
            self._prompt_entry.grab_focus()
```

- [ ] **Step 4: Run — verify pass.**
Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_view_possibilities.py -v`

- [ ] **Step 5: Run the CreateView regression suites** (collect/CTA equality must be untouched):
Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_view.py tests/test_create_view_detected_model.py tests/test_role_zone_panel.py -q`
Expected: PASS.

- [ ] **Step 6: Bump + changelog + commit.** `VERSION` → `0.53.0`; prepend a `debian/changelog` stanza (feat: Create surface opens to a possibilities wall; art = your work → curated demo/favorites playlist → gradient; optional curated-samples package is a future source). Commit:
```bash
git add app/create_view.py tests/test_create_view_possibilities.py VERSION debian/changelog \
        docs/superpowers/plans/2026-08-02-sp2-possibilities-wall.md
git commit -m "feat(create): open to the Start-something possibilities wall (SP-2 t2)"
```

### Finalize

Full suite green (deselect the two known flakes). Update CLAUDE.md with a short "Possibilities wall" note (widget location, the your-work→curated→gradient art tiers, the optional curated-samples `.deb` as a future curated source, and that it only seeds the composer — `_collect_params` unaffected).

## Verification

- `tests/test_possibilities_wall.py`, `tests/test_create_view_possibilities.py`, and the CreateView regression suites green under xvfb.
- Live (`./tt-gen` → ✨ Create): the surface opens to "Start something" — a wall of medium tiles (your latest of each where you have one, else gradient+icon), ✨ Surprise me, and clicking a tile selects that medium and fills the prompt with an example idea. On a fresh profile (empty store), every tile is a gradient — no blank page, no crash.
