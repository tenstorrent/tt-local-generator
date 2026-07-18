# Showcase every media type's specialness in every context — design

**Date:** 2026-07-17
**Branch:** feat/pipeline-editor
**Status:** approved (user directive: "review each possible media type and make sure you're showcasing its specialness in every context it is displayed")

## Problem

A full media-type × display-context audit (`.superpowers/sdd/` audit, 2026-07-17)
found that several contexts silently DEGRADE media they don't specifically
handle, because the rich rendering logic is copy-pasted 3-4× and has drifted.
Worst offenders, prioritized:

1. **CreateResultPanel** (the FIRST place a user sees their creation) claims
   **"Result file not found"** for successful `.svg` (5 generators), `.json`
   (palette), and `.py` (codeart) generations — `_artifact_kind`
   (`create_view.py:2273`) only knows image/.mp4/.gif/.txt/.ans. `.ans` renders
   as raw escape codes; `.md`/verse renders unformatted.
2. **ANSI on TT-TV renders as raw escape gibberish for EVERY ANSI artifact made
   today** — `attractor._parse_ansi_grid` only matches the legacy `\x1b[48;5;Nm `
   (bg+space) form; the generator now emits only `\x1b[38;5;Nm█` (fg+block,
   `artgen/generators/ansi.py:76-79,228`), so the grid parse always yields zero
   rows and falls back to raw text. **Live bug.**
3. **Artgen AnimateDiff `.gif` never animates on TT-TV** — `attractor._load_slot`
   has no `.gif` branch; it freezes on frame 1.
4. Native gif on TT-TV uses the GStreamer `Gtk.Video` path that
   `main_window.py:2110` documents as unreliable for gif; every other context
   uses `GdkPixbufAnimationIter`.
5. CreateResultPanel recents strip degrades harder (text/ansi/svg/json → a bare
   "TXT"/"?" chip).
6. `artgen_thumb.make_thumbnail` bakes a garbage PNG for `.json`/`.ans` (raw
   bytes text-rendered); any blind `thumbnail_path` consumer shows it.
7. `codeart` `.py` has no first-class handling anywhere (grey placeholder / prose
   markdown that strips indentation).
8. Three renderers reimplemented 3-4×: ANSI grid, palette swatches, gif-animate
   driver — the drift in #2 is a direct consequence.

## Approach: define each media type once, reuse everywhere

Extract the rich rendering into one dependency-free-leaf module and have every
context use it. `artgen_detail.py` is already a leaf (`artgen_watch.py` already
reuses its HTML builders); there is NO circular-import obstacle to
`create_view`/`attractor` importing a shared module.

### Media type → "special" render (the contract)

| type | ext | special render |
|---|---|---|
| image | png/jpg/jpeg/webp | crisp `Gtk.Picture` (contain-fit) |
| video | mp4 | inline player (detail) / hover-play (card) / poster (result v1) |
| gif | gif | **animated** (`GdkPixbufAnimationIter`), everywhere |
| ANSI | ans | colored character grid (BOTH `\x1b[38;5;Nm█` and `\x1b[48;5;Nm ` forms) |
| palette | json (colors[]) | color swatch grid |
| verse/text | txt/md | formatted reading view (verse-mode CSS for verse) |
| codeart | py | **monospaced, indentation-preserved** code view (not prose markdown) |
| svg | svg | vector `Gtk.Picture` |

## Components

### 1. `app/artgen_render.py` (NEW — shared)
- Pure builders moved from `artgen_detail.py` (public names, no leading `_`):
  `ansi_to_html`, `palette_to_html`, `md_to_html`, `derive_title`, `luminance`,
  the xterm-256 table, and a single `parse_ansi_grid` that handles BOTH escape
  formats (fg+block AND bg+space) — the one place that knows ANSI, so a parser
  can never drift from the generator again.
- `code_to_html(text, title)` — monospace `<pre>`, indentation preserved, for
  codeart `.py`.
- `AnimatedGifWidget` moved here (only needs Gtk/GdkPixbuf/GLib; self-manages its
  timer, cancels on unrealize).
- `artgen_detail.py` and `artgen_watch.py` delegate to these (no behavior change;
  removes 3 copies of the gif driver + the drifting parsers). Keep thin
  back-compat shims (`_ansi_to_html = ansi_to_html`) only if other modules import
  the underscore names.

### 2. `CreateResultPanel` (`create_view.py`)
- `_artifact_kind` recognizes `.svg`/`.json`/`.md`/`.py` (delegate to
  `create_mediums._ARTGEN_KIND` + extension rather than a narrower hand-list).
- `_build_artifact_widget` rich branches: image→Picture; video→poster
  (unchanged); gif→animate (done in 0.47.4); svg→vector Picture; ansi/palette/
  md/code/text→a `WebKit.WebView` "reading" view built from `artgen_render`
  (mirrors `ArtgenDetail`). **Never** "Result file not found" when the file
  exists.
- Recents strip `_build_recent_card`: real thumbnail for image/gif/svg; a
  type-appropriate mini-preview/chip otherwise (no bare "?").

### 3. TT-TV attractor (`attractor.py`)
- Add `.gif` branch → `AnimatedGifWidget` (artgen gifs animate).
- Native gif (`media_type=="animatediff"`) → `GdkPixbufAnimationIter`/
  `AnimatedGifWidget`, not the fragile `Gtk.Video`.
- Replace the bespoke ANSI/palette parsers with `artgen_render.parse_ansi_grid`/
  `palette_to_html` (fixes the live raw-ANSI bug + dedups).

### 4. Thumbnail hygiene + codeart (`artgen_thumb.py`)
- `.json`→swatch PNG, `.ans`→color-grid PNG (real mini-render via the shared
  parser + PIL/cairo, NOT raw-bytes text), `.py`→monospace code PNG. So blind
  `thumbnail_path` consumers (recents, attractor fallback, pipeline previews) get
  an honest preview.

## Out of scope (honest degrade by design)
- `pipeline_studio` node/hero thumbnails and `pipeline_portfolio_view`: fast
  pixbuf-grid surfaces with a fixed contract; a placeholder/static tile for
  non-raster artgen types is acceptable. May later fall back to the fixed
  `make_thumbnail` for a nicer static tile, but rich/animated rendering doesn't
  fit that contract.

## Invariants
- Generation routing, `collect()`, and record fields unchanged.
- GTK threading: gif timers on the main thread; cancel on
  clear/replace/unrealize (no timer leaks).
- `_CSS` / `b"""..."""` byte literals ASCII-only (glyphs/emoji in Python string
  labels only). Palette: tt-vscode-toolkit (Create/gallery), forest-teal
  (artgen HTML reading views keep their existing theme).
- No behavior change for `ArtgenDetail`/`ArtgenWatch` beyond delegation.

## Testing
Per component: both ANSI formats → grid (not raw); palette → swatches; verse →
formatted; codeart → monospace w/ indentation; gif → animates; svg → vector;
image → picture. CreateResultPanel: every artgen kind shows its rich widget and
never "Result file not found" for an existing file. Attractor: an fg+block ANSI
artifact renders as a grid; an artgen gif animates. make_thumbnail: .json/.ans/
.py produce sensible raster thumbnails (not raw-bytes text). artgen_detail/watch
regression-tested to still render each type after delegation.

## Version
Minor bump (broad user-visible improvement). `VERSION` → `0.48.0`. Local only.
