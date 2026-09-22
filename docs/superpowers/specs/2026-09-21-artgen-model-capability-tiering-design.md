# Model-capability-aware ANSI prompting — Design

**Date:** 2026-09-21
**Branch:** `support/tt-model-manager`
**Status:** Draft (design) — pending user review → implementation plan

## Problem / goal

Bringing up `Qwen/Qwen3.8-27B` (served via tt-model-manager, container profile
`qwen3.8-27b-dflash2-vision-p300x2-q4kv`) exposed that the `ansi` generator's
3-pass pipeline (`plugins/ansi/plugin.py`, mirrored in
`app/artgen/generators/ansi.py`) assumes a level of instruction-following and
output diversity that a heavily quantized (`q4kv`) + speculative-decoding
(`dflash2`) model doesn't have. Live testing against the running container
today showed:

- Whole-canvas ASCII composition (pass 1) collapses into a short repeating
  token loop (`.,;:;:;:;:...` forever) regardless of temperature or
  repetition/frequency penalty tuning.
- The freeform "creatively assign a color per cell" colorization prompt
  (pass 3) makes the model **ramble through visible reasoning text**
  ("Let's analyze the input image...") and burn its entire token budget
  without ever emitting the requested escape-code rows.
- Basic instruction-following (e.g. "write a haiku") works fine on the same
  model — the failure is specific to large, low-semantic-anchor, repetitive
  tasks (fill a big grid freely; decide N independent things at once), not a
  general model or serving breakage.
- A different prompting shape reliably avoided both failures (validated live,
  see "Validated technique" below): small, explicitly-anchored, mechanical
  sub-tasks instead of one big creative one.

**Goal:** scale the ANSI generator's prompting strategy to the serving
model's apparent capability, so a constrained model gets a prompting style
it can actually follow, while a capable model's behavior is completely
unchanged. Additionally, audit the other nine LLM-backed artgen generators
for the same failure class and leave a prioritized, non-implemented backlog.

**Explicitly out of scope for this pass:** implementing tier-aware prompting
for any generator other than `ansi`. That's backlog (Section 6).

## Validated technique (live, against Qwen/Qwen3.8-27B today)

1. **Band-segmented structure, not whole-canvas.** Split the canvas into
   thirds (top/middle/bottom) and issue one call per band instead of one call
   for the whole grid.
2. **Explicit per-row content, not "vary it."** A generic "make each row
   different" instruction still collapsed. Naming *what* each specific row
   should contain (e.g. "Row 3: heavier rain, more `/` mixed with `:` `;`")
   did not.
3. **JSON array of row-strings, not a raw text grid.** Asking for
   `["...", "...", ...]` broke the repetition attractor more reliably than
   asking for a bare multi-line block, even holding content instructions
   constant.
4. **Legend-then-mechanical-apply, not creative colorization.** A single call
   asking the model to *decide* colors for an entire grid produced rambling
   analysis instead of output. Splitting into (a) one small call that
   produces a fixed `character → xterm color` mapping, then (b) calls that
   only ever *apply* that mapping character-by-character ("mechanical, do not
   decide creatively") reliably produced correct output. At small scale this
   needed to be one call per row — a single whole-canvas mechanical-apply
   call for the same legend still ran past its token budget and dropped row
   boundaries partway through.
5. **Boundary-independent parsing as a safety net.** Because (4) can still
   drop row/line breaks under load, the colorized pass-3 output should be
   parsed by regex-extracting `(color, char)` pairs
   (`\x1b\[38;5;(\d+)m(.)`) and re-chunking into rows ourselves, rather than
   trusting the model's own line breaks. This is strictly safer than the
   current row-splitting and should apply to every tier's pass-3 output, not
   just the small-tier one.

Pass 2 (block-character refinement) needed no changes — the existing
whole-canvas prompt worked correctly on the assembled small-tier grid in
testing.

## Section 1 — Capability tiering module

New file: **`app/model_capability.py`** (GTK-free, stdlib-only — same shape
as `app/field_roles.py`/`app/chip_config.py`).

```python
Tier = Literal["small", "medium", "large"]

def parse_model_scale_b(model_id: str) -> float | None:
    """Extract a <number>B parameter-count token from a model id.
    Handles "Qwen3-32B", "Qwen/Qwen3.8-27B", "Llama-3.3-70B-Instruct",
    "Qwen3-0.6B". Returns None if nothing matches."""

def has_constrained_hint(model_id: str) -> bool:
    """Case-insensitive match against known quantization/speculative-decoding
    markers in the id itself: q4, q8, int4, int8, awq, gptq, fp8, dflash,
    speculative, draft."""

_KNOWN_CONSTRAINED_IDS: frozenset[str] = frozenset({
    "Qwen/Qwen3.8-27B",  # served via tt-model-manager profile
                         # qwen3.8-27b-dflash2-vision-p300x2-q4kv (2026-09-21) —
                         # the HF weights id itself carries no quant/speculative
                         # marker; the q4kv+dflash2 profile lives only in the
                         # serving container's docker label, which this app
                         # doesn't read. Add future characterized ids here.
})

def model_tier(model_id: str) -> Tier:
    """size-based base tier, downgraded one notch if constrained.
    >=30B -> large; 8B-30B -> medium; <8B -> small.
    Unparseable size defaults to large (assume capable; never punish a
    model we haven't characterized).
    Downgrade one tier (large->medium->small, floor at small) if
    has_constrained_hint(model_id) or model_id in _KNOWN_CONSTRAINED_IDS."""
```

**Why an explicit override list, not a docker-label lookup:** the served
`/v1/models` id for this exact case (`Qwen/Qwen3.8-27B`) carries no
quantization signal at all — that only exists in the serving container's
`org.tenstorrent.tt-model` docker label
(`qwen3.8-27b-dflash2-vision-p300x2-q4kv`), confirmed live via `docker
inspect` today. Reading docker labels from `artgen`/`model_capability` would
add a docker dependency and container-naming assumption to a currently pure
HTTP-only detection path, for a case an explicit list already covers cheaply.
The regex hint still catches the common case of HF repos that self-report
quantization in their own id (e.g. `TheBloke/...-GPTQ`).

Per user decision, three tiers exist even though only `small` and `large`
have been hardware-validated today; `medium` is a reasoned middle ground for
an 8-30B unquantized model, explicitly flagged as unvalidated (see Section
2).

## Section 2 — Wiring into `_make_call_fn`

`app/artgen/cli.py::_make_call_fn` computes the tier once and stamps it onto
the closure it already builds:

```python
def _make_call_fn(model_id: str, base_url: str, args):
    tier = model_capability.model_tier(model_id)

    def _call_fn(prompt, system=None, max_tokens=None):
        ...  # unchanged

    _call_fn.model_tier = tier
    return _call_fn
```

No change to the `call_fn(prompt, system=None, max_tokens=None) -> str`
contract every generator already depends on. A generator that doesn't care
about tier (all nine non-ANSI ones, today) is completely unaffected —
`getattr(call_fn, "model_tier", "large")` is the read pattern, defaulting to
today's behavior for any call_fn built by a path this change doesn't touch
(e.g. a hand-rolled call_fn in a test).

`mcp_server.py`'s `_make_call_fn` (the second implementation CLAUDE.md
already flags as a duplicate-to-keep-in-sync, same shape as the
`animatediff.uses_llm` dual-class gotcha) needs a slightly different change,
not just the identical one-liner — checked its actual body and it doesn't
know the model id upfront the way `cli.py`'s does. It discovers
`(endpoint, model)` **lazily, inside** the returned `_call_fn`, on every
call, specifically so a server started after the MCP server launches is
still picked up (per its own docstring). There is no upfront `model_id` to
stamp a tier from before the closure is built.

Fix: do one upfront `artgen.detect_artgen_endpoint(preferred_url=preferred)`
call in `_make_call_fn` itself (before defining `_call_fn`), compute
`model_capability.model_tier(model)` from that result, and stamp it onto the
returned closure — `_call_fn` itself is otherwise unchanged and still
re-detects per real invocation as today, so the endpoint/model can still
refresh call-to-call. The tier is therefore a snapshot taken once per
`generate_artifact` invocation (matching when a generator actually reads
it), not re-evaluated per pass. If no endpoint is reachable at
`_make_call_fn` time, tier stamping is skipped (`getattr(...,
"large")` fallback covers it) rather than raising early — the existing
"raise inside `_call_fn` on no endpoint" behavior is preserved for the
actual generation attempt.

## Section 3 — ANSI generator: tier-specific `generate_artifact`

Both `plugins/ansi/plugin.py` (canonical — this is what `plugin_loader`
actually registers and what `tests/test_artgen_generators.py` loads) and
`app/artgen/generators/ansi.py` (the stale-but-kept-in-sync mirror, per the
existing `animatediff` precedent in CLAUDE.md) get the same change.

`generate_artifact` reads `tier = getattr(call_fn, "model_tier", "large")`
and dispatches:

- **`large`** → `_generate_large(...)`: today's exact pass1/pass2/pass3 body,
  moved verbatim into a helper. Byte-identical behavior — this is the
  regression-safety net for every currently-working model.
- **`medium`** → `_generate_medium(...)`: pass 1 and pass 2 unchanged
  (whole-canvas, same prompts as `large` — no evidence of failure at this
  tier); pass 3 replaced by legend-then-whole-canvas-mechanical-apply (new
  `_build_legend_prompt` + `_build_mechanical_colorize_prompt`), parsed with
  the boundary-independent `(color, char)` extractor from the technique
  section. **Explicitly marked in a code comment and in this spec as
  unvalidated against real hardware** — no medium-tier model was available
  during this work. Follow-up: validate against e.g. a plain (non-quantized)
  Qwen3-8B or similar and adjust if the whole-canvas mechanical-apply call
  still runs out of budget (fall back to the small-tier per-row loop if so).
- **`small`** → `_generate_small(...)`: pass 1 replaced by band-segmented
  generation (new `_build_band_prompt(band_name, row_specs, width,
  n_rows, style)`, called once per band — default 3 bands for the existing
  12-20 row canvases), assembling bands top-to-bottom into the full grid;
  pass 2 unchanged (validated against the assembled small-tier grid); pass 3
  = legend generation + per-row mechanical-apply loop (one call per row),
  parsed the same boundary-independent way.

Row-content instructions for band mode are style-aware, generalizing what
was hand-written for the "scene" style during validation — `_STYLE_HINTS`
already has per-style spatial guidance (`bbs`, `landscape`, `portrait`,
`logo`, `scene`); band mode derives its default per-row descriptions from
the same table rather than a new hardcoded lighthouse-specific set, plus a
new `_BAND_ROW_HINTS[style]` table giving each band a short list of
per-row-within-band phrases (e.g. scene's top band: "sparse faint texture",
"light texture", "denser texture", "densest texture, closest to
midground") to replace the exact "rain streak" wording that was specific to
today's live test subject.

`_normalize_grid` (existing) still runs on every assembled/refined grid
regardless of tier — it's the existing pad/truncate safety net and needs no
change.

## Section 4 — Error handling / fail-soft behavior

Consistent with the rest of this codebase's artgen generators: no tier-aware
call raises on a malformed/short LLM response. Missing rows are padded with
blank rows (existing `_normalize_grid` behavior); a colorize call that
returns fewer `(color, char)` pairs than the row width pads the remainder
using the legend's own mapping for whatever character was at that position
(matching the pad behavior already prototyped live), never crashing the
generation. Tier detection itself (`model_tier`) never raises — an
unparseable model id string just falls through to `large`.

## Section 5 — Testing approach

Pure-function unit tests, no live model required (mirrors the existing
`test_field_roles.py`/`test_chip_config.py` style):

- `tests/test_model_capability.py`: `parse_model_scale_b` against a table of
  real ids (including `Qwen/Qwen3.8-27B` — size parses to 27, base tier
  `medium`, but the explicit-override list forces it down to `small`);
  `has_constrained_hint` against known-quantized and known-clean ids;
  `model_tier` end-to-end table including the unparseable-defaults-to-large
  case and the "known override wins even though the id alone looks clean"
  case.
- `tests/test_artgen_generators.py` (existing file, extended): a fake
  `call_fn` with `.model_tier` set to each of the three values drives
  `AnsiGenerator.generate_artifact` and asserts (a) `large` produces byte-
  identical prompts/call count to today's behavior (regression pin — this is
  the one that must never break), (b) `small` makes 3 band calls + N
  per-row colorize calls (not 3 total calls), (c) `medium` makes the
  legend + single mechanical-apply shape. Band assembly, boundary-
  independent parsing, and the pad-on-shortfall behavior each get a direct
  unit test independent of tier dispatch.
- No test depends on a real model or the live QB2 board — everything here
  is prompt-construction and response-parsing logic, testable with canned
  fake responses (including deliberately malformed ones, to exercise the
  fail-soft paths).

## Section 6 — Backlog: other generators (audit, not implemented this pass)

Surveyed all ten LLM-backed generators' actual prompt bodies
(`plugins/*/plugin.py`, the canonical runtime copies). Ranked by resemblance
to today's failure (a large, low-semantic-anchor, repetitive single-shot ask
— many near-identical elements, or exact coordinate lists, requested in one
completion):

1. **skyline** (highest risk) — "high" density explicitly asks for 28-38
   buildings x 2-8 windows each in one call: 300+ near-identical small SVG
   elements with only mild per-item variation. Same shape as ANSI's row
   problem, just SVG rects instead of ASCII rows. Recommended fix (not
   built): band by depth layer (background/midground/foreground) or by
   building, similar in spirit to ANSI's band-segmented pass 1.
2. **landscape** — 35-50 background stars, 4-6 cloud groups of 3-5
   ellipses, plus 3 mountain-polygon layers each needing 8-12 irregular
   peaks with an exact-coordinate closure constraint ("points string must
   begin with `0,{h}` and end with `{w},{h}`"). Same failure class:
   many repeated elements + exact coordinates in one shot. Recommended fix:
   split into one call per layer (sky/stars, clouds, mountains), matching
   ANSI's band split.
3. **constellation** — background-star scatter (`star_count*3` to `+20`)
   plus N named principal stars is a smaller-scale version of the same
   risk. Recommended fix: separate the bulk background-star scatter (a
   mechanical, low-semantic task well suited to a legend/formula rather than
   per-star LLM decisions) from the small number of named principal stars
   (which genuinely benefits from LLM creativity).

Low risk, no changes recommended: **verse**, **emoji-storyteller**,
**palette**, **circuit**, **codeart** (unbounded but not rigidly
enumerative), **geometric** (a qualitative "high complexity" hint rather
than a hard element count — the model has room to naturally taper off
instead of being locked into a fixed count). **freeform** is risk-neutral by
design — its risk is whatever the end user's own prompt asks for, not
something the generator itself imposes.

**Cross-cutting note, independent of tiering:** none of the ten generators
override `max_tokens` per call today — every one rides the CLI's flat 4096
default (`app/artgen/cli.py`). That's orthogonal to capability tiering but
worth revisiting alongside skyline/landscape/constellation later: a
constrained-tier model burning its budget on the first few elements has no
reserve left for the rest, compounding the repetition-collapse risk.

## Files touched (this pass)

- `app/model_capability.py` (new)
- `tests/test_model_capability.py` (new)
- `plugins/ansi/plugin.py` (tier-aware `generate_artifact` + new band/legend
  prompt builders + boundary-independent pass-3 parser)
- `app/artgen/generators/ansi.py` (mirrored, per existing dual-copy
  convention)
- `app/artgen/cli.py::_make_call_fn` (stamp `.model_tier` on the closure,
  using the `model_id` it already resolves upfront)
- `app/mcp_server.py::_make_call_fn` (add one upfront endpoint-detection
  call to resolve a model id before stamping the tier — see Section 2 for
  why this isn't the identical change)
- `tests/test_artgen_generators.py` (extended with tier-dispatch cases)

Not touched this pass: skyline/landscape/constellation (Section 6 backlog),
any other generator, `create_mediums.py`/GUI surfaces (tier detection is
CLI/MCP-path only for now — the GUI's artgen jobs also route through
`_make_call_fn` in `cli.py`/`mcp_server.py`, so this is not a gap, just
noting no GUI-specific code changes are needed).
