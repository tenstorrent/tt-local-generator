# Artgen Gallery — Create / Curate / Watch

**Date:** 2026-04-30
**Status:** Approved

---

## Overview

The artgen tab is redesigned from a single generate-and-preview panel into a full create / curate / watch experience consistent with how the app handles video and image content. Every generation is automatically saved and catalogued. The user can browse, filter, delete, playlist, and watch their artgen library.

The foundation is a new **unified SQLite media store** that replaces the existing `history.json` and `playlists.json` files, enabling live-query playlists, cross-type mixing, and efficient filtering across all media types.

---

## 1. Data Layer — `media_store.py`

### Database

`~/.local/share/tt-video-gen/media.db` (SQLite, WAL mode)

### Schema

```sql
CREATE TABLE media (
    id              TEXT PRIMARY KEY,           -- UUID
    media_type      TEXT NOT NULL,              -- "video" | "image" | "animate" | "artgen"
    created_at      TEXT NOT NULL,              -- ISO 8601
    file_path       TEXT NOT NULL,              -- .mp4 / .jpg / .svg / .txt / .ans
    thumbnail_path  TEXT,                       -- .jpg or .png rendered thumbnail
    prompt          TEXT NOT NULL DEFAULT '',   -- user-visible prompt / theme
    model_id        TEXT NOT NULL DEFAULT '',   -- "Qwen/Qwen3-8B", "tt-wan2.2", etc.
    generator_type  TEXT,                       -- "landscape" | "verse" | "skyline" | ... (artgen only)
    params          TEXT NOT NULL DEFAULT '{}', -- JSON blob of type-specific generation params
    starred         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_media_type        ON media(media_type);
CREATE INDEX idx_generator_type    ON media(generator_type);
CREATE INDEX idx_media_created_at  ON media(created_at DESC);

CREATE TABLE playlists (
    id          TEXT PRIMARY KEY,   -- UUID
    name        TEXT NOT NULL,
    auto_gen    INTEGER NOT NULL DEFAULT 1,
    filter_expr TEXT,               -- SQL WHERE clause for live playlists; NULL = hand-curated
    created_at  TEXT NOT NULL
);

CREATE TABLE playlist_items (
    playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    media_id    TEXT NOT NULL REFERENCES media(id)     ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, media_id)
);
```

**Live playlists** are defined by `filter_expr`. Examples:
- `"landscape"` auto-playlist: `generator_type = 'landscape'`
- `"all artgen"`: `media_type = 'artgen'`
- `"starred"`: `starred = 1`
- Hand-curated playlists: `filter_expr IS NULL`, items in `playlist_items`

**`params` examples:**
- artgen landscape: `{"palette": "sunset", "mountains": true, "clouds": false, "glitch": false, "generation_seconds": 24}`
- artgen verse: `{"form": "haiku", "theme": "winter forges", "count": 3, "generation_seconds": 11}`
- video: `{"negative_prompt": "...", "seed_image": null, "fps": 24, "resolution": "720p"}`

`generation_seconds` is stored in `params` (not a top-level column) for artgen records so the detail view can display it without schema additions.

### Public API

`media_store.py` exposes a module-level `MediaStore` singleton:

```python
media_store.add(record: MediaRecord) -> str          # returns new id
media_store.get(id: str) -> MediaRecord | None
media_store.delete(id: str) -> bool                  # removes DB row, file, and thumbnail
media_store.star(id: str, starred: bool) -> None
media_store.query(                                   # returns newest-first
    media_type: str | None = None,
    generator_type: str | None = None,
    starred: bool | None = None,
    limit: int | None = None,
) -> list[MediaRecord]
media_store.playlist_records(playlist_id: str) -> list[MediaRecord]
media_store.auto_playlist_types() -> list[str]       # distinct generator_types present
```

`MediaRecord` is a dataclass mirroring the schema columns, with a `params_dict` property that deserialises the JSON blob.

### Migration

On first launch, `media_store.py` checks for `media.db`. If absent:
1. Reads `history.json` → inserts rows with appropriate `media_type` and `params`
2. Reads `playlists.json` → inserts `playlists` + `playlist_items` rows
3. Renames originals to `history.json.bak` and `playlists.json.bak`

All existing consumers (`history_store.py`, `playlist_store.py`) are updated to delegate to `media_store` internally, preserving their public APIs during the transition. They are not deleted — they become thin wrappers.

---

## 2. Artgen Storage

Artgen artifacts are stored in `~/.local/share/tt-video-gen/artgen/`:

```
artgen/
  20260430_143022_a1b2c3d4.svg     # SVG artifacts
  20260430_143022_a1b2c3d4.txt     # text / verse / palette
  20260430_143022_a1b2c3d4.ans     # ANSI art
  thumbnails/
    20260430_143022_a1b2c3d4.png   # rendered thumbnail (SVG→PNG via librsvg or cairosvg)
```

Thumbnail generation:
- `.svg` → render at 320×240 via librsvg (`Rsvg.Handle`) if available, else save a copy of the SVG as thumbnail
- `.txt` / `.ans` → render text to a small PIL/Pillow image with monospace font; fall back to a type-badge placeholder PNG if Pillow not installed

---

## 3. Artgen Tab UI

The artgen tab gains a three-section sub-navigation header: **Create · Gallery · Watch**. This header is always visible within the artgen tab.

### 3a. Create

Layout: 240px left controls column + right panel showing the last 4 generations as a mini-grid.

**Left column (unchanged from current, plus one new row):**
- Type dropdown
- Type-specific controls (palette, checkboxes, sliders) — unchanged for each type
- **Theme Inspiration row** (new, present for all generator types): a text entry pre-filled with a seed theme, plus an **✦ Inspire** button that calls `prompt_client.generate_prompt()` (port 8001, Qwen3-0.6B) to rewrite the theme entry with a novel suggestion. Falls back gracefully if prompt server is offline.
- Generate button with elapsed timer
- Server status dot + model label

**Right panel:**
- Label: "Latest generations — click to go to Gallery"
- 4-card mini-grid of the most recent artgen records, newest at top-left with a teal border highlight
- "→ View all N in Gallery" link that switches to the Gallery sub-tab
- When a generation completes, the new card animates into position at top-left (crossfade, 120ms)
- **Empty state** (no records yet): placeholder card with a "✦" icon and text "Your generations will appear here"

### 3b. Gallery

Full-width grid with filter chips and a detail view.

**Filter bar (top):**
- Chips: All · landscape · skyline · verse · geometric · circuit · constellation · ansi · palette · freeform (only chips with ≥1 record are shown)
- Right side: Playlists dropdown (same popover as main gallery) · **▶ Watch** button (launches Watch for active filter)

**Card grid:**
- `Gtk.FlowBox`, same pattern as `GalleryWidget`
- Each card: thumbnail image, type badge (bottom-left), timestamp (bottom-right)
- Selected card highlighted with teal border
- Clicking a card navigates to the **Detail view** (sub-tab C below)
- New generations appear at top-left immediately after creation, with a brief highlight animation

**Detail view (full-pane, entered by clicking a card):**
- Header: `← Gallery` back link · artifact title (type + params summary) · `‹ ›` prev/next arrows
- Body: large artifact display on the left (~65% width) — SVG rendered via `Gtk.Picture`, text in a `Gtk.TextView` (monospace, scrollable), ANSI in a styled text view
- Right sidebar (~35%): creation timestamp, model, generation time, full params (rendered from `params_dict`), LLM prompt (expandable), star toggle, `+ Playlist` button (same popover), `Open file` button, destructive `🗑 Delete` button
- `‹ ›` arrows and `← →` keyboard navigation step through the active filter without returning to the grid
- `Esc` or `← Gallery` returns to the grid with the card still selected

### 3c. Watch

Slideshow of the active filter or playlist. "Fullscreen" means the artifact fills the artgen tab's content area — Watch does not open a new window or use a system fullscreen API. The artgen sub-navigation header is hidden while Watch is active.

**Layout:**
- Artifact fills the entire pane (SVG scaled to contain, text centered with large font, ANSI in terminal-style block)
- Overlay UI fades out after 3 seconds of no mouse movement; returns on any mouse move or keypress

**Overlay elements (when visible):**
- Top bar: `← Gallery` · playlist name + position (`3 / 12`) · `✕` close
- Left/right center: large `‹` / `›` hit areas (semi-transparent circles)
- Bottom: scrubber progress bar (auto-advance countdown), dwell timer label, play/pause button
- Bottom-left: artifact metadata (type, palette/theme, model, date) — dimmed
- Bottom-right: filmstrip of 5 neighbouring thumbnails, current centered + highlighted

**Controls:**
| Key | Action |
|-----|--------|
| `Space` | Play / pause auto-advance |
| `← →` | Previous / next |
| `Esc` | Return to Gallery |
| `Del` | Delete current item (confirm dialog) |
| `S` | Star / unstar current item |
| Mouse move | Show overlay |

**Dwell time:** 10 seconds default, configurable in Preferences (5–60 s range).

**Transitions:** crossfade 400ms between artifacts (SVG and text both fade; no slide — avoids layout reflow).

---

## 4. Auto-Playlists

Auto-playlists are live SQL views — no separate data structure. They are generated automatically for every `generator_type` that has at least one record in the store.

On startup and whenever a new artgen record is added, the app calls `media_store.auto_playlist_types()` and ensures one live playlist row exists per type (inserting if missing, never deleting hand-curated rows).

Live playlist rows have `filter_expr = "generator_type = '<type>'"`. Calling `media_store.playlist_records(id)` for a live playlist runs the filter query rather than joining `playlist_items`.

These auto-playlists appear in the Playlists popover (same UI as video playlists) and in the Watch filter bar. Users can also create additional hand-curated playlists that mix artgen types, videos, and images freely.

---

## 5. Prompt Server Integration

The **✦ Inspire** button in the Create tab calls the existing three-tier prompt pipeline (`prompt_client.generate_prompt()`) with a short artgen-specific system prompt that steers the output toward themes, moods, and subjects rather than cinematic video descriptions.

The result seeds the type-relevant text field:
- landscape / skyline → mood/atmosphere description
- verse → theme entry
- constellation → culture/lore seed
- ansi / freeform → subject entry

If the prompt server (port 8001) is offline, the button generates a random theme algorithmically from `word_banks.py` instead (tier-1 fallback, no LLM needed).

---

## 6. File Map

| File | Role |
|------|------|
| `app/media_store.py` | New unified SQLite store (replaces h+p stores) |
| `app/history_store.py` | Thin wrapper → `media_store` (public API preserved) |
| `app/playlist_store.py` | Thin wrapper → `media_store` (public API preserved) |
| `app/artgen_panel.py` | Redesigned: Create + Gallery + Watch sub-tabs |
| `app/artgen_gallery.py` | New: `ArtgenGallery` widget (card grid + filter bar) |
| `app/artgen_detail.py` | New: `ArtgenDetail` widget (full-pane detail + ‹ › nav) |
| `app/artgen_watch.py` | New: `ArtgenWatch` widget (fullscreen slideshow) |
| `app/artgen/` | Unchanged: generators, `__init__.py`, `cli.py` |

---

## 7. Out of Scope

- TT-TV (attractor) integration with artgen (not blocked, just deferred)
- Batch artgen generation
- Artgen items in the main video/image gallery tab
- Export / share features
