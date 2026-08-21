# Demo collection

A small, curated set of representative generations that ships **with** the app,
so a fresh install opens with real art to look at instead of an empty gallery.

- `manifest.json` — one entry per item: original ids, `media_type` /
  `generator_type` / `model_id`, the untouched generation `params`, the
  original generation `prompt` (kept for provenance), and a cleaned `caption`
  (what the gallery displays).
- `media/` — the media files, **exactly as generated** (no re-encoding /
  compression).
- `thumbnails/` — the matching preview thumbnails.

## How it gets into a library

`app/demo_seed.py` (`tt-ctl seed-demo`) copies the media + thumbnails into the
library's storage (`<storage>/demo-collection/`) and registers each item in
`media.db`, grouped in a **"Welcome to tt-local-generator"** playlist. It is
**idempotent** (records are keyed by id; re-running seeds nothing new; `--force`
replaces existing records) and GTK-free. The shipped art is seeded as the
DEFAULT — never auto-favorited — so a user's own stars stay meaningful.

- On install, the `.deb` postinst runs `tt-ctl seed-demo` for the invoking user
  (fail-soft — a seed failure never aborts package configuration).
- Manually / for dev: `tt-ctl seed-demo` (`--db PATH`, `--collection-dir PATH`,
  `--force`).

The "Welcome to tt-local-generator" playlist name is also recognized by the
Create surface's "Start something" wall as a curated tile source
(`possibilities._default_curated_matcher` matches "welcome").

## Re-curating

Regenerate this directory from a library with the Demo Curator tool (a visual
picker), or by hand: drop the media + thumbnail files in and add a
`manifest.json` entry. Keep media uncompressed. Captions are display text; leave
the original `prompt` intact for provenance.
