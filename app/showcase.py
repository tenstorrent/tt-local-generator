# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Showcase generator for Pipeline Studio (SP-C Phase 3, Task 1).

Turns a finished run (a `pipeline_view_model.RunView`) into a single,
self-contained HTML file: the results it produced plus the pipeline recipe
that made them — dark forest-teal editorial styling, no external references
(no CDN/font links, no `http(s)://` src/href — everything is inlined so the
file is safe to open offline or drop into an Artifact-style CSP sandbox).

This module productizes a builder proved by hand against a real run (see the
`1964-worlds-fair-showcase.html` scratchpad script this was lifted from) —
same template, same downscale+base64 approach, generalised to loop over an
arbitrary `RunView` instead of hardcoding four fixed artifact paths.

Design: pure core + one impure encoder
---------------------------------------
`build_showcase_html()` is pure — it never touches disk or imports PIL. It
takes an `encode_asset(path, kind) -> str | None` callable and calls it once
per step that has a real artifact; the caller decides how that turns into a
data URI (or returns None, which renders an honest placeholder tile instead
of fabricating an image/thumbnail that doesn't exist). This is what makes the
builder unit-testable with a fake encoder and no real files (see
`tests/test_showcase.py`).

`default_encode_asset()` is the one production encoder: PIL for images
(downscale + base64 JPEG/PNG), raw base64 for video, UTF-8 text for text
artifacts. It is the *only* function in this module that imports PIL or
touches the filesystem — everything else in this file has zero I/O.

`write_showcase()` is the glue: builds the HTML (via the pure core) and
writes it to `dest_dir / showcase_<slug>_<n>.html`, where `n` is a
collision-safe counter (not a timestamp/random suffix) so repeated runs
against the same title produce deterministic, inspectable filenames.
"""
from __future__ import annotations

import base64
import html
import io
import re
from pathlib import Path
from typing import Callable

from pipeline_view_model import RunView, StepView

# `encode_asset(path, kind, max_px=...)` — `max_px` is always passed by the
# builder (hero vs gallery request different caps, see `_HERO_MAX_PX` /
# `_GALLERY_MAX_PX` below), so the alias is intentionally loose (`...`)
# rather than a fixed 3-arg signature: any callable that accepts a `max_px`
# keyword (with a default, for callers that don't care) fits.
EncodeAssetFn = Callable[..., "str | None"]

# Hero embeds large (full-bleed at the top of the page); gallery thumbnails
# are much smaller on screen, so encoding them at hero resolution just wastes
# bytes in the self-contained HTML. See `default_encode_asset`'s `max_px` arg.
_HERO_MAX_PX = 1000
_GALLERY_MAX_PX = 680

# ── Output-kind -> encode_asset "kind" mapping ────────────────────────────────
#
# `Intent.output_kind` uses a richer vocabulary ("image"|"video"|"text"|"gif"|
# "playlist") than `encode_asset` needs to know about. A GIF is still a raster
# image as far as PIL/base64-embedding is concerned, so it maps to "image".
# "playlist" (TTLGAddToPlaylist) never has a file-backed artifact_path (see
# pipeline_view_model._OUTPUT_KIND) so it never reaches encode_asset at all —
# it's omitted here deliberately rather than mapped to something misleading.
_ASSET_KIND: dict[str, str] = {
    "image": "image",
    "gif": "image",
    "video": "video",
    "text": "text",
}

# Text-snippet gallery tiles are truncated so one verbose artgen text blob
# doesn't visually dominate the "what it made" grid.
_TEXT_SNIPPET_MAX = 240

# Size guards for default_encode_asset — see its docstring.
_MAX_IMAGE_BYTES = 40 * 1024 * 1024   # 40 MB source image, pre-downscale
_MAX_VIDEO_BYTES = 80 * 1024 * 1024   # 80 MB — base64 already ~+33% on top
_MAX_TEXT_BYTES = 2 * 1024 * 1024     # 2 MB of inlined text is already a lot


def _asset_kind(output_kind: "str | None") -> "str | None":
    """Map an Intent.output_kind to the "image"|"video"|"text" encode_asset expects."""
    if output_kind is None:
        return None
    return _ASSET_KIND.get(output_kind)


def _esc(value: object) -> str:
    """HTML-escape any value we interpolate as text content or an attribute.

    Recipe labels/titles/model names ultimately trace back to spec files a
    user could have edited by hand, so treat all of it as untrusted text —
    escaping keeps a stray `<`/`&`/`"` from corrupting the page structure.
    """
    return html.escape(str(value), quote=True)


def _slugify(title: str) -> str:
    """Filesystem-safe slug for the showcase filename (lowercase, hyphenated)."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    return slug or "showcase"


def _find_hero(run_view: RunView) -> "tuple[StepView, str] | tuple[None, None]":
    """First step whose artifact is image/video — the hero candidate.

    Deliberately re-derives this from `run_view.steps` rather than trusting
    `run_view.hero_path` alone: this module needs to know *which kind*
    (image vs video) the hero is in order to call `encode_asset` correctly,
    and re-scanning is cheap and avoids a second source of truth going stale
    relative to `pipeline_view_model`'s own hero-selection logic.

    Note this deliberately diverges from `pipeline_view_model._HERO_KINDS`
    (which excludes "gif"): `_ASSET_KIND` maps "gif" to "image", so a GIF step
    is hero-eligible here even though the view model wouldn't pick it as
    `RunView.hero_path`. That's fine — this module only needs *an* image/video
    to feature, not agreement with the view model's specific hero choice.
    """
    for step in run_view.steps:
        if not step.artifact_path:
            continue
        kind = _asset_kind(step.intent.output_kind)
        if kind in ("image", "video"):
            return step, kind
    return None, None


def _hero_html(run_view: RunView, encode_asset: EncodeAssetFn) -> "tuple[str, str | None, str | None]":
    """Build the hero figure, and report which step/path asset it embedded.

    Returns `(html, embedded_node_id, embedded_path)`. The caller
    (`build_showcase_html`) uses `embedded_path` to skip re-embedding that
    exact asset in the gallery — so for a FAN-OUT hero step (many stills) the
    gallery still shows the OTHER stills, only the one hero image is excluded.
    Both id and path are `None` when there's no hero candidate, or when
    `encode_asset` declined — nothing was embedded, so the step is left free
    to still get honest gallery tiles.
    """
    step, kind = _find_hero(run_view)
    if step is None:
        return "", None, None
    encoded = encode_asset(step.artifact_path, kind, max_px=_HERO_MAX_PX)
    if not encoded:
        return "", None, None
    if kind == "video":
        html = (
            f'<figure class="hero"><video src="{encoded}" autoplay loop muted '
            f'playsinline controls></video></figure>'
        )
    else:
        html = f'<figure class="hero"><img src="{encoded}" alt="{_esc(run_view.title)}"></figure>'
    return html, step.node_id, step.artifact_path


def _placeholder_tile(label: str, status: str) -> str:
    """An honest "we don't have this yet / it failed" tile — never a fabricated asset."""
    status_word = status if status in ("pending", "running", "failed") else "pending"
    glyph = "&#9679;" if status_word == "running" else ("&#10005;" if status_word == "failed" else "&#182;")
    return (
        f'<div class="art placeholder {_esc(status_word)}">'
        f'<div class="im placeholder-mark">{glyph}</div>'
        f'<div class="t">{_esc(label)}</div>'
        f'<div class="s">{_esc(status_word)}</div>'
        f'</div>'
    )


def _gallery_tile(step: StepView, encode_asset: EncodeAssetFn) -> str:
    """A gallery tile for the step's single/representative artifact (or a
    text/placeholder tile when it has no on-disk file)."""
    return _gallery_tile_for_path(step, step.artifact_path, encode_asset)


def _gallery_tile_for_path(step: StepView, path: "str | None", encode_asset: EncodeAssetFn) -> str:
    label = f"{step.intent.verb} {step.intent.noun}".strip()
    kind = _asset_kind(step.intent.output_kind)

    encoded = None
    if step.status == "done" and path and kind:
        encoded = encode_asset(path, kind, max_px=_GALLERY_MAX_PX)

    if not encoded:
        # Covers: not done yet, no artifact on disk, unrecognized output kind,
        # or encode_asset itself declining (missing/unreadable/oversized file).
        # Never fabricate a thumbnail/snippet in any of these cases.
        return _placeholder_tile(label, step.status)

    model_html = f'<div class="s">{_esc(step.intent.model_label)}</div>' if step.intent.model_label else ""

    if kind == "image":
        body = f'<div class="im" style="background-image:url(&quot;{encoded}&quot;)"></div>'
    elif kind == "video":
        body = f'<video class="im" src="{encoded}" muted loop autoplay playsinline></video>'
    else:  # text
        snippet = str(encoded).strip()
        if len(snippet) > _TEXT_SNIPPET_MAX:
            snippet = snippet[:_TEXT_SNIPPET_MAX].rstrip() + "…"
        body = f'<div class="im text-snip">{_esc(snippet)}</div>'

    return f'<div class="art">{body}<div class="t">{_esc(label)}</div>{model_html}</div>'


def _recipe_html(recipe: "list[str]") -> str:
    parts: list[str] = []
    for i, step_label in enumerate(recipe):
        if i:
            parts.append('<span class="ar">&rarr;</span>')
        parts.append(f'<span class="r">{_esc(step_label)}</span>')
    return "".join(parts)


# ── The dark forest-teal editorial template ───────────────────────────────────
#
# Lifted from the proven scratchpad builder (1964-worlds-fair-showcase.html):
# same palette/typography/layout, generalised so the gallery and hero are
# built from an arbitrary list of steps instead of four hardcoded paths.
_STYLE = """
  :root{ --base:#071a19; --surf:#0d2b2a; --surf2:#12403d; --line:rgba(116,197,223,.16);
         --ink:#eef8f6; --mut:#9bc0ba; --faint:#6f948d; --accent:#37a7c9; --teal:#4FD1C5;
         --gold:#F6BC42;
         --emoji:"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji","Twemoji Mozilla";
         --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif,var(--emoji);
         --mono:ui-monospace,"SFMono-Regular","Cascadia Code","Berkeley Mono",Menlo,monospace,var(--emoji); }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0}
  body{background:radial-gradient(1000px 460px at 50% -12%,#0f3a38 0%,var(--base) 58%);}
  .wrap{min-height:100%;color:var(--ink);font-family:var(--sans);line-height:1.6;
        -webkit-font-smoothing:antialiased;padding:0 20px 72px}
  .col{max-width:860px;margin:0 auto}
  header{text-align:center;padding:64px 0 8px}
  .eyebrow{font-size:11px;letter-spacing:.26em;text-transform:uppercase;color:var(--gold);font-weight:700}
  h1{font-size:clamp(34px,7vw,52px);line-height:1.02;letter-spacing:-.02em;margin:14px 0 10px;font-weight:800}
  .hero{margin:34px auto 0;border-radius:16px;overflow:hidden;border:1px solid var(--line);
        box-shadow:0 30px 70px rgba(0,0,0,.55)}
  .hero img,.hero video{display:block;width:100%;height:auto}
  .rule{width:40px;height:2px;background:var(--gold);border-radius:2px;margin:56px auto}
  h2{font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:var(--teal);font-weight:700;text-align:center;margin:0 0 22px}
  .gallery{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
  @media(max-width:560px){.gallery{grid-template-columns:1fr 1fr}}
  .art{background:var(--surf);border:1px solid var(--line);border-radius:12px;overflow:hidden}
  .art .im{aspect-ratio:1/1;background:#0a1f1e center/cover no-repeat}
  .art .t{font-size:12px;color:var(--ink);padding:9px 11px 2px;font-weight:600}
  .art .s{font-size:10.5px;color:var(--faint);padding:0 11px 11px;font-family:var(--mono)}
  .art .im.text-snip{aspect-ratio:auto;display:flex;align-items:center;justify-content:center;
                      padding:14px;font-size:11px;color:var(--mut);font-style:italic;text-align:center}
  .art.placeholder{border-style:dashed;opacity:.72}
  .art.placeholder .im{display:flex;align-items:center;justify-content:center;color:var(--faint);font-size:26px}
  .pipe{background:var(--surf);border:1px solid var(--line);border-radius:14px;padding:22px;margin-top:14px}
  .pipe p{font-size:13px;color:var(--mut);margin:0 0 16px;text-align:center}
  .recipe{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:center}
  .r{font-family:var(--mono);font-size:12px;background:var(--surf2);border:1px solid var(--line);
     border-radius:8px;padding:6px 11px;color:#dcefe9;white-space:nowrap}
  .ar{color:var(--accent);font-weight:700}
  footer{text-align:center;margin-top:56px;font-size:12.5px;color:var(--faint);line-height:1.9}
  footer b{color:var(--mut);font-weight:600}
"""


def build_showcase_html(run_view: RunView, *, encode_asset: EncodeAssetFn) -> str:
    """Build the full, self-contained showcase page for `run_view`.

    `encode_asset(path, kind) -> str | None` is the only source of embedded
    asset data — this function never opens a file itself. A `None` result
    (missing/unreadable/oversized artifact, or a step that hasn't produced
    one yet) always renders an honest placeholder tile, never a fabricated
    thumbnail.
    """
    hero_html, hero_node_id, hero_path = _hero_html(run_view, encode_asset)
    # Render a gallery tile per ON-DISK artifact so a FAN-OUT step (e.g. one
    # image per lore fragment) shows every still, not just one. The hero's
    # exact asset is embedded once above, so it's skipped here (only that one
    # path — a fan-out hero step's OTHER stills still appear). A step with no
    # file artifacts (text/pending) falls back to a single text/placeholder
    # tile via `_gallery_tile`.
    tiles: "list[str]" = []
    for step in run_view.steps:
        paths = [p for p in step.artifact_paths if p != hero_path]
        if paths:
            tiles.extend(_gallery_tile_for_path(step, p, encode_asset) for p in paths)
        elif step.node_id != hero_node_id:
            tiles.append(_gallery_tile(step, encode_asset))
    gallery_html = "".join(tiles)
    recipe_html = _recipe_html(run_view.recipe)
    title = _esc(run_view.title)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — a tt-local-generator showcase</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="wrap"><div class="col">
  <header>
    <div class="eyebrow">Cross-modal pipeline &middot; Tenstorrent</div>
    <h1>{title}</h1>
  </header>

  {hero_html}

  <div class="rule"></div>

  <h2>What it made</h2>
  <div class="gallery">
    {gallery_html}
  </div>

  <div class="rule"></div>

  <h2>The pipeline behind it</h2>
  <div class="pipe">
    <p>Shown so anyone can see how it was made &mdash; and remix it.</p>
    <div class="recipe">{recipe_html}</div>
  </div>

  <footer>
    Made with <b>tt-local-generator</b> on Tenstorrent hardware.
  </footer>
</div></div>
</body>
</html>
"""


def default_encode_asset(path: "str | None", kind: str, max_px: int = _HERO_MAX_PX) -> "str | None":
    """The one impure encoder — PIL + disk I/O live here and nowhere else.

    image -> downscale (LANCZOS, capped at `max_px` on the long edge) then
             base64 JPEG (or PNG if the source has real alpha) data URI.
             The builder passes a smaller cap for gallery thumbnails
             (`_GALLERY_MAX_PX`) than for the hero (`_HERO_MAX_PX`) — the
             default here only matters for callers that invoke this function
             directly without going through `build_showcase_html`.
    video -> base64 the raw file as a `data:video/mp4;base64,...` URI.
             (`max_px` is not meaningful for video and is ignored.)
    text  -> the file's decoded text content (the builder inlines it as a
             gallery snippet, no data URI needed; `max_px` is ignored).

    Returns None for a missing path, an unreadable/corrupt file, an
    oversized file (see the `_MAX_*_BYTES` guards — a multi-hundred-MB video
    would make the resulting HTML impractical to open/share), or a kind this
    function doesn't know how to encode.
    """
    if not path:
        return None
    p = Path(path)
    try:
        if not p.is_file():
            return None
        size = p.stat().st_size

        if kind == "image":
            if size > _MAX_IMAGE_BYTES:
                return None
            from PIL import Image  # local import: PIL is optional at module load

            im = Image.open(p)
            im.load()
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            fmt = "PNG" if has_alpha else "JPEG"
            if fmt == "JPEG" and im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((max_px, max_px), Image.LANCZOS)
            buf = io.BytesIO()
            if fmt == "JPEG":
                im.save(buf, "JPEG", quality=82, optimize=True)
                mime = "image/jpeg"
            else:
                im.save(buf, "PNG", optimize=True)
                mime = "image/png"
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:{mime};base64,{b64}"

        if kind == "video":
            if size > _MAX_VIDEO_BYTES:
                return None
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            return f"data:video/mp4;base64,{b64}"

        if kind == "text":
            if size > _MAX_TEXT_BYTES:
                return None
            return p.read_text(encoding="utf-8", errors="replace")

        return None
    except Exception:
        # Any decode/IO failure (corrupt image, permission error, etc.) is
        # "we don't have this asset" from the builder's point of view — never
        # propagate an exception up into a showcase build.
        return None


def write_showcase(
    run_view: RunView,
    dest_dir: "str | Path",
    *,
    encode_asset: EncodeAssetFn = default_encode_asset,
) -> str:
    """Build the showcase HTML and write it to `dest_dir`, returning the path.

    Filename is `showcase_<slug(title)>_<n>.html` where `n` is a
    collision-safe counter over existing files matching that pattern —
    deliberately not a timestamp or random suffix, so repeated builds against
    the same run/title are deterministic and inspectable.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    slug = _slugify(run_view.title)

    n = 0
    while (dest / f"showcase_{slug}_{n}.html").exists():
        n += 1
    out_path = dest / f"showcase_{slug}_{n}.html"

    out_path.write_text(build_showcase_html(run_view, encode_asset=encode_asset), encoding="utf-8")
    return str(out_path)
