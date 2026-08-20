# Running-model identity & model-specific status dots — design

**Date:** 2026-07-17
**Branch:** feat/pipeline-editor
**Status:** approved (design fork answered: full generality — unknown running models appear as selectable entries)

## Problem

Two related defects in how Create/Discover reflect running chat (artgen/prompt)
models:

1. **The READY dot is too general.** `ModelStatusService._tick()`
   (`app/model_status.py:321`) discards the model id returned by
   `detect_artgen_endpoint()` (`base, _model_id = self._detect_fn()`), then marks
   **every** server whose capabilities include `artgen`/`prompt` as READY the
   moment any chat endpoint answers (`model_status.py:346-348`). So all six
   registered artgen entries (Qwen3-8B, Qwen3-32B, Llama-3.3-70B,
   DeepSeek-R1-70B, …) light green together regardless of which one is actually
   loaded — or even when the running model is none of them.

2. **No generality for unknown models.** Create's model list only iterates the
   static `server_manager.SERVERS` keys (`create_view._scoped_model_keys`,
   `_build_model_door`). A model started outside the app that isn't one of the
   six registered ids (the user's `qwen3.6-27b` example) can never appear *as
   itself* — it just makes all six known ones falsely light up.

**Already true (leveraged, not changed):** `detect_artgen_endpoint()` returns
`(url, model_id)` with the real `/v1/models` id (`app/artgen/__init__.py:231`),
and artgen generation shells out to `tt-ctl artgen <generator>` with the chat
endpoint auto-resolved by `detect_artgen_endpoint()` — the artgen model dropdown
collects **no** "model" field and does not affect routing. So a detected model is
already *reachable*; it is only invisible in the UI and mis-represented by the
dot.

## Scope

**artgen/text only.** Image/video/animate servers share port 8000 and already
disambiguate via the `runner_key`/`/tt-liveness` `runner_in_use` check — their
dots are correct and untouched. Generation routing (detect-based) is untouched.

## Design

### 1. `server_manager.py` — a served-model-id hint on chat ServerDefs

Add an optional `model_id: str | None = None` field to `ServerDef`. Populate it
for the six artgen chat servers + `prompt-server` with the id the server reports
at `/v1/models` (best-known served id, e.g. `"Qwen/Qwen3-8B"`,
`"meta-llama/Llama-3.3-70B-Instruct"`, `"Qwen/Qwen3-0.6B"`). It is a matching
hint; the matcher (below) also falls back to `label`, so an imperfect value
still works. No behavior change for servers that leave it `None`.

### 2. `model_status.py` — track the running model & make readiness model-specific

**Pure matcher (module-level, unit-tested):**

```python
def match_model_id(detected_id, servers):
    """Return the SERVERS key whose model best matches `detected_id`, or None.

    Normalizes both sides — last '/'-segment, lowercase, strip every
    non-alphanumeric — then matches when the normalized strings are equal or
    one contains the other (shorter side >= 4 chars, to keep 'qwen3-8b' and
    'qwen3-32b' distinct). Only artgen/prompt-capability servers are
    considered. `model_id` is used when present, else `label`.
    """
```

- `Qwen/Qwen3-8B` → `artgen-qwen3-8b`; `meta-llama/Llama-3.3-70B-Instruct` →
  `artgen-llama-3.3-70b` (containment); `qwen3.6-27b` → `None`.

**`_tick()` changes:**

- Keep the detected id: `base, model_id = self._detect_fn()`.
- Compute `matched = match_model_id(model_id, SERVERS)` when `base is not None`.
- Replace the blanket capability marking. An artgen/prompt key becomes healthy
  via detect **only** when `key == matched`. Managed per-key health
  (`health.get(key)`) is unchanged — so `prompt-server`'s own port-8001 health
  still resolves it independently of the sweep.
- Under `self._lock`, record running-artgen state: `_artgen_model_id`,
  `_artgen_url`, `_artgen_matched_key` (all cleared when `base is None`).
- Include a change in that state in the change-detection that drives
  `_notify()` (so subscribers refresh when the running model appears/changes,
  even if no per-key Status flips).

**New accessor:**

```python
ArtgenModelInfo = namedtuple("ArtgenModelInfo", "model_id url matched_key")
def running_artgen_model(self) -> ArtgenModelInfo | None: ...
```

Returns the current detected chat model (with `matched_key=None` when it matches
no registered server), or `None` when no chat endpoint is up. Lock-guarded.

`ready_keys`/`running_or_starting` signatures are unchanged; they now naturally
return only the matched key.

### 3. `create_view.py` — model-specific dots + a "detected" entry

- `_model_dot_glyph(key)` already reads per-key `Status` — no change; it becomes
  correct once (2) stops collapsing all keys to READY.
- Read `self._status_service.running_artgen_model()` alongside each snapshot.
- **Dynamic detected entry** — when a running artgen model has
  `matched_key is None` (unknown), inject one synthetic entry into BOTH:
  - the scoped model dropdown for text/artgen mediums (`_populate_model_dropdown`
    / `_scoped_model_keys`), and
  - the Model door "Text" group (`_build_model_door`),
  labeled `<model_id> (detected)` with a ● (READY) dot. It is selectable but
  inert for generation (artgen collects no "model" field; routing is detect-
  based), so `collect()` is unaffected. When a running model DOES match a known
  key, no synthetic entry is added — only that key lights.
- **Auto-select:** prefer `running_or_starting("artgen")` (the matched key); when
  the running model is unknown, auto-select the synthetic detected entry. Only in
  the fresh-populate branch (a manual pick is preserved, per the v0.28.1 fix).

## Invariants

- `RoleZonePanel.collect()` / artgen param collection byte-for-byte unchanged
  (no "model" field added for artgen mediums).
- Generation routing unchanged (detect-based).
- Image/video/animate dots unchanged.
- `_CSS` byte literals stay ASCII-only; the ● / ◌ / ◐ / "(detected)" glyphs and
  text live in Python string labels.
- Palette: tt-vscode-toolkit variant.

## Testing

- **`match_model_id`** (pure): vendor-prefix strip, case, separators, exact,
  containment, 3-8b vs 3-32b distinctness, no-match → None, empty/None input.
- **`model_status._tick`** (fakes, no threads): endpoint up serving a known id →
  only that key READY, other artgen keys OFF; unknown id → no artgen key READY
  and `running_artgen_model()` returns it with `matched_key=None`; endpoint down
  → `running_artgen_model()` is None; `prompt-server` managed health resolves
  independently of the sweep; a change in running model triggers `_notify`.
- **`create_view`** (xvfb widget): unknown detected model injects exactly one
  selectable ● entry in the text dropdown and the door "Text" group; known model
  lights only its own entry (others ◌); no detected model → no synthetic entry;
  `collect()` for an artgen medium is unchanged with/without the detected entry.

## Version

Minor bump (new user-visible behavior). `VERSION` → `0.47.0`; changelog stanza.
Local only — no push.
