# Multi-chip AnimateDiff — Step 0 findings + decision

Date: 2026-07-09 · Hardware: QB2, 4× P300c (all healthy)

## Empirical result (decisive)

Ran two `generate.py` processes **concurrently** on device 0 and device 1 with the
**same seed=42**, same prompt, 2 frames / 4 steps / lightning (mimics the app's
multi-chip launch). Result:

- `dev0.gif` and `dev1.gif` are **byte-identical** (md5 `dcd643c6…`), 0 pixel delta
  on every frame.

**Conclusion: the Blackhole path is fully deterministic across chips. There is NO
per-device / concurrency drift.**

## What this means for the "glitch"

- With the current "same seed on every chip" behaviour (0.8.1 fix), all N shards
  are identical, so concatenation yields the same K-frame clip repeated N× — this
  IS issue #21's "duped frames," not a blend.
- The beloved glitch therefore cannot be the current same-seed path. It matches
  the **pre-fix** behaviour: **different seed per chip + frame interleaving**.
  Corroborating evidence: `_stitch_gifs` still contains a vestigial
  `interleaved = all_frames` variable — the original stitcher interleaved frames;
  the 0.8.1 "fix" switched to concatenate but left the name behind.

### Design implications
- **Seed spread is essential** for Remix — `seed_spread=0` reproduces the #21
  dupe bug; `>=1` gives genuinely different per-chip clips (the morph). Keep the
  default at 1 (or higher).
- **Add a stitch-order lever to Remix**: `interleave` (frame 0←chip0, frame1←chip1,
  … — the "classic glitch") vs `concatenate` (chip clips back-to-back). Interleave
  is the most likely source of the "blends things together" effect; make it
  available and probably the Remix default.

## Parallel-Coherent feasibility

`generate_frames_temporal` (animatediff_ttnn/temporal_attention.py) already
contains mesh frame-sharding (`plan_frame_sharding`, "one CFG-doubled frame per
chip per pass") plus cross-frame temporal attention. BUT `generate.py` opens a
**single-chip** device (`MeshShape(1,1)` / `device_ids=[0]`) on purpose: the SD
demo UNet loads weights with `to_torch()` (no `mesh_composer`), so
`ShardTensorToMesh` across >1 chip crashes at model-load (documented in CLAUDE.md
and the code comment at examples/generate.py:392). Also, same-seed determinism
means independent per-chip processes can't produce a continuous long animation on
their own (they'd be identical).

**Resolving parallel-coherent = fixing mesh weight-loading for the SD demo UNet**
— deep TTNN work, out of scope for this release.

## DECISION

`COHERENT_IMPL = "sequential-chain"` (via existing `--chain-save`/`--chain-from`).
Parallel-coherent is a documented follow-up gated on tt-animatediff mesh
weight-loading.

Plan amendment: add a **stitch-order (interleave | concatenate)** lever to Remix
(Tasks 2/3/7/8), default **interleave** (the classic glitch).
