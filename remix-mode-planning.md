# Remix Mode — Design Planning

> Saved from conversation on 2026-05-21. Next branch after current changes ship.

## The Core Distinction

**Generate** = pure creation from parameters (subject, style, model, seed).
Input is intent. Output is new media.

**Remix** = transformation of an existing artifact.
Input is existing media + a transformation hint. Output is new media derived from the source.

Generate is what the app does today. Remix is the next mode.

---

## The Type Compatibility Graph

Every media type is a node. Edges declare what transformations are valid.

```
verse  ──────────────→  video  (verse as narrator / voiceover seed)
verse  ──────────────→  image  (illustrate the verse)
palette ─────────────→  image  (paint an image from this palette)
palette ─────────────→  video  (color grade / mood the video)
haiku  ──────────────→  video
haiku  ──────────────→  image
landscape ───────────→  video  (animate the scene)
landscape ───────────→  image  (render as flat art)
geometry ────────────→  image
svg    ──────────────→  image  (rasterize / upscale)
gif (animatediff) ───→  video  (extend / upscale / reinterpret)
image  ──────────────→  video  (img2vid — already supported in SkyReels)
image  ──────────────→  image  (restyle via FLUX)
video  ──────────────→  gif    (extract + restyle frames)
```

When **music** is added later: `verse → music`, `palette → music` (mood-to-audio),
`video → music` (soundtrack generation). Music just adds nodes — no existing
code changes.

---

## RemixContext Object

```python
@dataclass
class RemixContext:
    source_record: dict          # the history record being remixed
    source_type: str             # "verse", "palette", "image", "video", "gif", etc.
    target_type: str             # what we're transforming into
    hint: str                    # extracted text/data from source (verse text, palette hex list, etc.)
```

The `hint` is the cross-pollination: the verse text becomes a video prompt seed,
the palette hex list becomes an image color prompt, the haiku becomes a scene description.

---

## UI Design

**Where Remix lives**: In the **detail panel** as a secondary action row below
the primary metadata. Not in the main generate flow.

```
┌─────────────────────────────────────┐
│  [thumbnail]   Title / metadata     │
│                Created: 2026-05-21  │
│                                     │
│  Remix → [ 🎬 Video ] [ 🖼 Image ]  │
│            [ 📝 Verse ]             │
│                                     │
│  [ ▶ Play ]  [ 📤 Export ]  [ 🗑 ] │
└─────────────────────────────────────┘
```

Only valid destination types appear. A verse record shows "Video" and "Image" buttons.
A palette shows "Image" and "Video". A GIF shows "Video". An image already shows
"Video" (that's the existing SkyReels I2V flow — Remix unifies it under one model).

**The Remix action**: clicking a destination type:
1. Builds a `RemixContext`
2. Extracts `hint` from the source (parse verse text, extract palette colors, etc.)
3. Switches to the appropriate generator tab with the hint pre-loaded
4. (Optionally) marks the new generation as "remixed from [source id]" in history

---

## What Makes Remix Feel Pluggable

- `ArtGenerator` subclasses declare `accepts_remix_from: tuple[str, ...]` — which
  source types they can receive as context.
- `ArtGenerator` subclasses declare `can_remix_to: tuple[str, ...]` — which
  destination types they can feed into.
- The Remix UI builds its button list by walking those declarations — no hardcoded
  compatibility table in the UI.
- Adding music later = add a `MusicGenerator` with `accepts_remix_from=("verse", "palette", "video")`.

---

## Implementation Sketch (future branch)

1. Add `accepts_remix_from` / `can_remix_to` to `ArtGenerator` base class.
2. Tag all existing generators.
3. Add `RemixContext` dataclass to `artgen/__init__.py`.
4. Add `extract_remix_hint(record) -> str` to each generator (or a default that
   uses the stored prompt text).
5. In `DetailPanel`, add "Remix →" row populated from compatibility declarations.
6. Wire click → `RemixContext` → target panel pre-fill.
7. Store `remix_source_id` in history records so lineage is traceable.

---

## Open Questions

- Does Remix always cross into a *different* media type, or can you remix
  verse → verse (riff on an existing verse)?
- Should the Remix action open a confirmation / preview step, or go directly
  to the generate tab?
- For image → video remixes, the source image should pre-fill the I2V image
  picker (this overlaps with the existing SkyReels image-to-video path —
  Remix should subsume it).
- Music timeline: post-QB2 launch, probably the first non-visual generator.
  Start with short ambient clips from mood/palette inputs.
