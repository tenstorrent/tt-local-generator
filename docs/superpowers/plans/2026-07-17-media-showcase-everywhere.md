# Showcase every media type everywhere — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`). Version bump + changelog fold into the FINAL task.

**Goal:** Define each media type's rich render once (a shared module) and use it in every context, so no surface silently degrades a type. Fixes: CreateResultPanel "Result file not found"/raw-text for svg/palette/ansi/verse/codeart; live raw-ANSI + non-animating gif on TT-TV; garbage `.json`/`.ans` thumbnails; codeart never handled; 3-4× duplicated renderers.

**Architecture:** New leaf module `app/artgen_render.py` holds the pure HTML builders + a single ANSI parser (both escape formats) + `AnimatedGifWidget`. `artgen_detail`/`artgen_watch` delegate to it (dedup); `create_view`/`attractor`/`artgen_thumb` consume it. Spec: `docs/superpowers/specs/2026-07-17-media-showcase-everywhere-design.md`. Audit facts (file:line) are in that spec.

**Tech Stack:** Python 3, GTK4/PyGObject, WebKit (existing dep), PIL/cairo (existing), pytest (xvfb).

## Global Constraints
- Generation routing, `collect()`, record fields UNCHANGED. No behavior change for `ArtgenDetail`/`ArtgenWatch` beyond delegating to the shared module.
- GTK threading: gif timers on the main thread; cancel on clear/replace/unrealize (no leaks).
- `_CSS`/`b"""..."""` byte literals ASCII-only (glyphs/emoji in Python string labels). Palette: tt-vscode-toolkit for Create/gallery chrome; artgen HTML reading views keep their existing (forest-teal) theme.
- Out of scope: `pipeline_studio`/`pipeline_portfolio_view` thumbnails (honest static degrade by design) — do NOT touch.
- System python `/usr/bin/python3`. Tests: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module --deselect tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`. Local commits only — DO NOT push.

---

### Task 1: Shared `app/artgen_render.py` + delegate detail/watch
**Files:** Create `app/artgen_render.py`; modify `app/artgen_detail.py`, `app/artgen_watch.py`; `tests/test_artgen_render.py` (new).
**Produces:** `ansi_to_html(raw)->str`, `palette_to_html(data)->str`, `md_to_html(raw,title="",verse_mode=False)->str`, `code_to_html(raw,title="")->str`, `derive_title(gen_type,params)->str`, `luminance(hex)->float`, `parse_ansi_grid(raw)->list[list[(char,fg,bg)]]` (handles BOTH `\x1b[38;5;Nm█` fg+block AND `\x1b[48;5;Nm ` bg+space), and `class AnimatedGifWidget(Gtk.Overlay|Gtk.Picture)` (self-managed `GdkPixbufAnimationIter` timer, cancels on unrealize).

- [ ] Failing tests: `parse_ansi_grid` yields non-empty rows with correct colors for a fg+block sample AND a bg+space sample; `ansi_to_html` output contains colored cells (not raw `\x1b`); `palette_to_html` renders one swatch per color; `md_to_html` verse-mode differs from prose; `code_to_html` preserves leading indentation inside `<pre>`; `AnimatedGifWidget` on a 2-frame gif advances frames (assert iter/timer set). → FAIL.
- [ ] Implement: MOVE the builders out of `artgen_detail.py` into `artgen_render.py` (public names). MOVE `_AnimatedGifWidget` from `artgen_gallery.py` into `artgen_render.py` as `AnimatedGifWidget`; leave a `from artgen_render import AnimatedGifWidget as _AnimatedGifWidget` shim in `artgen_gallery.py` so its callers are untouched. Add `code_to_html`. In `artgen_detail.py`/`artgen_watch.py`, import from `artgen_render` and delete the local copies (keep underscore back-compat aliases only where another module imports them — grep first: `_ansi_to_html`/`_palette_to_html`/`_md_to_html`/`_derive_title` are imported by `artgen_watch`; re-export from those modules OR update the import). Have `ArtgenDetail._animate_gif`/`ArtgenWatch._animate_gif` delegate to `AnimatedGifWidget` (or the shared driver).
- [ ] Run new tests → PASS. Full suite (deselect flakes) → PASS (artgen_detail/watch render unchanged). Commit `feat(render): shared artgen_render module (ansi/palette/md/code/gif), dedup detail+watch`.

---

### Task 2: CreateResultPanel rich rendering for every type
**Files:** `app/create_view.py` (`_artifact_kind`, `CreateResultPanel._build_artifact_widget`, `_build_recent_card`); `tests/test_create_result_panel.py` (extend).
**Consumes:** `artgen_render` (Task 1).

- [ ] Failing tests (xvfb, build a record per kind pointing at a real tiny artifact file): an `.svg` record → a vector `Gtk.Picture` (NOT the "Result file not found" placeholder); a palette `.json` → a WebView/swatch widget (not placeholder); an `.ans` → a rich reading view (NOT a `Gtk.TextView` of raw escapes); a `.md`/verse `.txt` → formatted reading view; a `.py` codeart → monospace reading view; and crucially: for ANY kind whose file EXISTS, the result is never the "Result file not found" label. Recents strip: an `.svg`/`.gif` recent shows a thumbnail; a text recent shows a type chip (no bare "?"). → FAIL.
- [ ] Implement: `_artifact_kind` recognizes `.svg`/`.json`/`.md`/`.py` (delegate to `create_mediums._ARTGEN_KIND` + ext; keep image/video/gif/text). `_build_artifact_widget`: image→Picture; video→poster (unchanged); gif→animate (already `AnimatedGifWidget`); svg→`Gtk.Picture.new_for_filename`; ansi/palette/md/code/text→a `WebKit.WebView` "reading" view built from `artgen_render` (choose builder by kind/ext: ansi→ansi_to_html, palette-json→palette_to_html, .py→code_to_html, else md_to_html; verse-mode when generator_type=="verse"). Missing/unreadable file → honest placeholder (keep that path ONLY for genuinely-absent files). `_build_recent_card`: real thumbnail for image/gif/svg, else a compact type-labeled chip.
- [ ] Run → PASS. Full suite → PASS. Commit `feat(create): CreateResultPanel showcases every artgen type (svg/palette/ansi/verse/code/gif)`.

---

### Task 3: TT-TV attractor — animate gifs, fix ANSI, dedup
**Files:** `app/attractor.py` (`_load_slot` + its ANSI/palette/gif branches); `tests/test_attractor*.py` (extend/new).
**Consumes:** `artgen_render` (Task 1).

- [ ] Failing tests: `_load_slot` (or the extracted per-type loader) for an artgen `.ans` file in the CURRENT fg+block format produces a color grid (assert non-empty parsed rows / a grid widget), NOT the raw-text fallback; an artgen `.gif` produces an animating widget (`AnimatedGifWidget`), not a static Picture; a native `media_type=="animatediff"` `.gif` uses the GdkPixbuf/AnimatedGifWidget path, not `Gtk.Video`. → FAIL.
- [ ] Implement: add a `.gif` branch in the artgen dispatch → `AnimatedGifWidget`; route native gif records through the same GdkPixbuf path; replace `_parse_ansi_grid`/bespoke palette parser with `artgen_render.parse_ansi_grid`/`palette_to_html` (keep the attractor's own cairo/DrawingArea host if it wants a widget, but feed it the shared parser's rows). Preserve existing image/video/text behavior.
- [ ] Run → PASS. Full suite → PASS. Commit `fix(tttv): animate artgen+native gifs; ANSI color grid via shared parser`.

---

### Task 4: Thumbnail hygiene + codeart (`artgen_thumb`) + FINALE
**Files:** `app/artgen_thumb.py` (`make_thumbnail`); `tests/test_artgen_thumb.py` (extend); `VERSION`; `debian/changelog`; `CLAUDE.md`.
**Consumes:** `artgen_render` (Task 1).

- [ ] Failing tests: `make_thumbnail` for a palette `.json` produces a raster PNG that is a swatch render (not raw-JSON-bytes text); for an `.ans` (fg+block) a color-grid PNG (not raw-escape text); for a `.py` a monospace code PNG (not the grey placeholder). Assert each is a valid raster of sane size and NOT the text-of-bytes render (e.g. distinct from feeding the same bytes through the old text path). `.txt`/`.md`/`.svg`/`.gif`/raster behavior unchanged. → FAIL.
- [ ] Implement: in `make_thumbnail`, add `.json`→swatch PNG (parse colors, draw swatches via PIL/cairo, using `artgen_render` for parsing), `.ans`→color-grid PNG (via `parse_ansi_grid` + PIL/cairo), `.py`→monospace render (add to code path). Keep `.svg` (rsvg) and raster (PIL first-frame) branches. Only genuine text (.txt/.md) uses the monospace text render; unknown binary → placeholder (never raw-bytes text).
- [ ] Run → PASS. Full suite → PASS.
- [ ] `VERSION` → `0.48.0`; prepend `debian/changelog` stanza (every media type now shows its rich form in every context — Create result panel renders svg/palette/ansi/verse/code/gif properly instead of "file not found"/raw text; TT-TV animates gifs and fixes ANSI; real thumbnails for palette/ansi/code; shared `artgen_render` ends renderer drift). author "Taylor Singletary <tsingletary@tenstorrent.com>", noble, urgency medium. Update CLAUDE.md (new `artgen_render` shared module + the per-context showcase guarantee).
- [ ] Commit `feat(render): real thumbnails for palette/ansi/code; media-showcase finale (v0.48.0)`.

---

## Notes for the executor
- Order: Task 1 (foundation) → 2/3/4 (consumers). 2, 3, 4 are independent of each other after Task 1.
- Do NOT change generation, collect(), or pipeline-studio thumbnails.
- Preserve `ArtgenDetail`/`ArtgenWatch` output exactly (delegation only). Grep underscore-name imports before deleting the originals.
