# Branch code review — `feat/pipeline-editor`

**Date:** 2026-08-11
**Branch:** `feat/pipeline-editor` @ `74d10c1` (VERSION 0.77.1)
**Base:** `main` (merge-base `0b60571`)
**Scope:** 376 commits · 300 files · +77.7K / −9.2K — the Pipeline Studio, Create
surface, activity viz, model-status layer, 0.19 media models (Wan2.2-I2V,
FLUX.1-dev), and the patch-verification harness.

## Method

Five parallel domain reviewers audited the branch against the project's hard
invariants (GTK single-thread via `GLib.idle_add`, `collect()` byte-identity,
`GLib.timeout_add` teardown on unrealize, `_CSS` bytes-literal ASCII-only,
fail-soft UI). Domains: **pipeline** (studio/engine/view-model/recipes/
intent-vocab/spec-remix), **Create** (view/param-panels/mediums/possibilities/
chips/roles/model-picker), **main_window** integration, **backend** (model-status/
servers/capability-discovery/ready-to-run/server-manager/api-client/worker/
animatediff), and **media** (artgen render/galleries/viewer/thumb/activity-viz/
showcase). Every Critical and each spot-checked Important was then **re-verified
by the controller against the live code** (repros run, code quoted) before
landing in this report — guarding against reviewer false-positives.

## Verdict

**14 findings: 3 Critical, 5 Important, 6 Minor.** No new crash-on-startup or
`collect()`-corruption defects. The Criticals are silent-wrong-behavior bugs —
a feature that looks like it works but doesn't — which is the class this
whole-branch pass exists to surface. The much-larger already-task-reviewed
surface (Create collect-path, model-status lock discipline, `_native_generate_args`
routing, ANSI/thumbnail rendering, `_register_pipeline_final` fail-soft+dedup)
re-verified **clean**.

**Fix policy for this pass:** all Critical + Important ("major") issues fixed;
Minors documented for follow-up.

---

## Critical

### C1 — Remix per-step model picker silently no-ops (VERIFIED)
`app/spec_remix.py` `apply_edits` · `app/pipeline_studio.py` `RemixView.current_spec`/`_collect_edits`

`apply_edits` writes an edit into a node's `inputs` only when the key already
exists (`if key not in inputs: continue`). Muse-built pipelines never carry a
literal `"model"` key (the curated goal templates don't set one), so when a user
picks a model in a step's `ModelPickerRow`, the edit is collected but **dropped**
by `current_spec()` — the executed spec is byte-identical to the untouched one,
and the run silently falls to `_backend_for`'s default (FLUX). Directly negates
the pipeline UX overhaul's headline per-step model picker and the documented
`_backend_for`-parity invariant.

Verified: `apply_edits({'1':{...'inputs':{'prompt':'a cat'}}}, {'1':{'model':'sdxl'}})`
→ inputs unchanged, `model` gone.

**Fix:** in `RemixView`, back-fill a literal `"model"` key (the picker's resolved
value) into the node's `inputs` at collect time so `apply_edits` has a key to
overwrite — OR give model-picker edits an insert-if-absent merge path. Add an
end-to-end test asserting the pick survives into `current_spec()`.

### C2 — `looping-animation` Muse goal discards the seed (VERIFIED)
`app/recipes.py` (goal `looping-animation`) · `app/spec_remix.py` `seed_spec`

`seed_spec` assigns a step's seed/wired input, then does `inputs.update(params)`
— so a caller literal on the canonical input key overwrites the wire. The
`looping-animation` goal (offered in scoped/seeded mode, `applies_to="both"`)
still carries a blank-canvas placeholder `"prompt": "a dreamy, seamlessly
looping scene"` on its `TTLGAnimateDiff` step (canonical key `prompt`), so
"make this palette/text into a looping animation" **always** produces the
generic default — the palette→prompt adapter (v0.73.0) never reaches the run.

Verified: `build_seed_spec(looping-animation, prepend_steps=(palette-adapter,))`
→ AnimateDiff `prompt` input is the literal string, not the `["1","prompt"]` wire.

**Fix:** drop the `"prompt"` literal from the goal (keep `seamless_loop: True`);
pre-fill the placeholder at the UI layer for blank-canvas mode only. Update
`tests/test_palette_to_animatediff_e2e.py` to exercise the **real** curated goal,
not a synthetic empty-params one.

### C3 — Every artgen model reads READY together in production (VERIFIED)
`app/model_status.py:469` · `app/server_manager.py:391` (`_check_sdef`)

`_tick()` computes `healthy = health.get(key, False) or (key == matched)`, relying
on each artgen key's own `health_fn` entry being absent. But the real
`server_manager.status_all()` calls `_check_sdef`, which for `runner_key is None`
returns `True` on any 2xx — and all six `artgen-*` ServerDefs share one
`health_url` (`localhost:8002/v1/models`) with `runner_key=None`. So the moment
one artgen model answers, `health.get(key)` is `True` for **all six**; the
`match_model_id`/`matched` gating (the v0.47.0 "per-model identity" fix) is dead
weight and the "every artgen model reads ready" bug is still live — masked only
because every test injects a hand-built `health_fn`.

Verified: 6 artgen keys, 1 distinct `health_url`, all `runner_key=None`;
`_check_sdef` returns `True` on 2xx when `runner_key is None`.

**Fix:** make `_check_sdef` compare the `/v1/models` body's id against
`sdef.model_id` for the shared-port artgen family (model-specific at the source),
OR in `_tick()` stop trusting `health.get(key)` for artgen-capability keys and
rely on `matched`. Add a regression test driving the real all-artgen-True health
map through `_tick()`.

---

## Important

### I1 — `capability_discovery` marks AnimateDiff as needing a media server (VERIFIED)
`app/capability_discovery.py:84` — `_NATIVE_BACKEND_FAMILY["TTLGAnimateDiff"] = "media"`.
AnimateDiff is a self-contained no-server Blackhole generator everywhere else
(`pipeline_engine._h_animatediff`, `animatediff.uses_llm=False`, the "Video is
Video" no-server default). Gating its `live` flag on `is_backend_up("media")`
lists it as *latent* in the Pipeline composer/Muse when no media server runs —
undercutting the "always an immediate path to a result" guarantee.
**Fix:** remove `TTLGAnimateDiff` from `_NATIVE_BACKEND_FAMILY` (falls through to
always-`live`), or give it a chip-presence family instead of a server family.

### I2 — Transform-failure error is silently swallowed for artgen mediums (VERIFIED)
`app/main_window.py:6790` (`_gallery_for_type` raises `ValueError` for `"artgen"`)
reached via `_on_error` ← `_active_gallery` ← `_current_medium_source()=="artgen"`.
`_on_error` is shared by the Forge transform error path (`_on_transform_card`);
when a transform fails while an artgen Create medium is active, the `ValueError`
propagates out of the `idle_add` callback and the rest of `_on_error` never runs
— no status message, no pending-card cleanup, silent failure.
**Fix:** give the transform error path its own lightweight status handler, or make
the gallery lookup tolerate `"artgen"` with a caught fallback.

### I3 — Orphaned `AnimatedGifWidget` leaks its decode timer forever
`app/artgen_render.py` (`AnimatedGifWidget.__init__` starts `GLib.timeout_add`
unconditionally; only `_on_unrealize` cancels) · `app/artgen_gallery.py`
(idle-deferred hover-swap `_do_swap` bails without attaching if the subtree was
torn down). A widget that's never attached is never realized, so its timer never
cancels — it decodes frames forever on an invisible `Gtk.Picture`. Racing hover +
delete/filter accumulates leaked decode loops burning CPU.
**Fix:** add a public `dispose()`/`close()` that cancels `_timer_id` regardless of
realize state; call it in `_do_swap`'s bail-out. (Optionally don't arm the timer
until first realize.)

### I4 — `ActivityVizWidget` no-WebKit stub never stops its telemetry thread
`app/activity_viz.py` — `_stop_telemetry` is wired only to `self._webview`'s
unrealize; in the `_WEBKIT_OK=False` branch `_webview is None`, so if `set_running(True)`
is ever called on a no-WebKit build and the widget is torn down, the 1.5 s
`tt-smi`-spawning daemon thread runs forever and keeps the widget alive.
**Fix:** `self.connect("unrealize", lambda *_: self._stop_telemetry())` on the
widget itself, unconditionally, before the no-WebKit early return.

### I5 — Showcase mislabels a done-but-unembeddable step as "pending"
`app/showcase.py` `_placeholder_tile` — `status not in ("pending","running","failed")`
collapses to `"pending"`. A `"done"` step whose asset can't embed (unsupported
kind, too large, unreadable) renders as "pending" on the shared showcase page,
contradicting the module's "honest placeholder" goal.
**Fix:** carry a distinct `"unavailable"` status for done-but-not-embedded.

---

## Minor (M1–M4 FIXED in `1a1…`; M5 is an intentional non-bug)

- **M1** ✅ `create_view.py` now guards the WebKit import (`_WEBKIT_OK`) and the
  reading view degrades to plain scrollable text — a box without
  `gir1.2-webkit-6.0` imports the whole Create surface instead of failing.
- **M2** ✅ `_register_pipeline_final_artgen` now does its thumbnail/record/store
  I/O on a daemon thread with the gallery refresh via `GLib.idle_add`, matching
  the native sibling (no main-thread hitch on a large `.ans`/`.json` artifact).
- **M3** ✅ Documented the deliberate `coherent`-mode exception (each segment
  gets the full `timeout`; total ≈ N×`timeout`) in both `run_subprocess` and
  `_run_coherent_chain` — a per-segment budget would truncate a valid long chain,
  so the behavior is intentional, now stated.
- **M4** ✅ No-thumbnail GIF gallery cards render a genuinely static first frame
  (`GdkPixbuf`/`Gdk.Texture`) instead of a live `AnimatedGifWidget` — no decode
  timer runs in the grid; hover still animates.
- **M5** (no change) `ArtgenParamPanel.collect()` returns `{}` before `build()` —
  documented, intentional; kept documented.

## Verified clean (no action)

`collect()`/`_collect_params()` byte-identity across RoleZonePanel/ModifierPills/
model-picker sentinels/possibilities; `ModelStatusService` lock discipline (I/O
outside lock, notify after release, subscriber isolation); `ready_to_run.plan_switch`
decision-only + `Status.__str__` `.lower()` fix; the new `wan2.2-i2v`/`flux-dev`
ServerDefs; `_native_generate_args` routing + seed-image guards; `_create_job_active`
hygiene on every terminal path; `_register_pipeline_final` fail-soft + run-once
dedup; `parse_ansi_grid` both formats; `artgen_thumb` per-extension dispatch;
`_backend_for` exact-then-substring; `ModelPickerRow` double-`notify` guard;
GTK-free guarantee for worker/api_client/server_manager/model_status; no dangling
ControlPanel/ArtgenPanel/RemixPopover references.

---

## Fix status

**All 8 majors (3 Critical + 5 Important) fixed and landed** (VERSION 0.77.2),
each with a regression test; full suite **2242 passed / 1 skip / 3 deselect**.

| ID | Fix | Commit |
|----|-----|--------|
| C1 | `apply_edits` inserts a `model` edit even when absent → per-step model picker reaches the executed spec | `b97f9b5` |
| C2 | `seed_spec` no longer lets a param clobber a wired/seeded canonical input → seeded looping-animation keeps the seed; blank-canvas placeholder preserved | `b97f9b5` |
| C3 | `_check_sdef` confirms the loaded `/v1/models` id matches `model_id` for the shared-port artgen family → per-model readiness (not all-six-together) | `c68d1f5` |
| I1 | `TTLGAnimateDiff` removed from the media backend family → always-live in the composer (no-server generator) | `c68d1f5` |
| I2 | `_on_error` tolerates an artgen-active medium → transform failures show a status instead of silently aborting | `f5dffb3` |
| I3 | `AnimatedGifWidget.cancel_animation()` + gallery bail-out call → orphaned never-realized gif widgets no longer leak a decode timer | `b530ef8` |
| I4 | `ActivityVizWidget` connects its own `unrealize`→`_stop_telemetry` unconditionally → no leaked telemetry thread on no-WebKit builds | `b530ef8` |
| I5 | showcase maps done-but-unembeddable → `unavailable` (not `pending`) | `b530ef8` |

**Minors** (M1–M5) are documented above and left as follow-ups — none are
correctness/leak bugs on a reachable hot path (M1 robustness, M2 UI-hitch
consistency, M3 docstring, M4 pre-existing, M5 intentional).

**Verification:** every Critical was reproduced against live code before fixing
(the C1/C2 repros run; C3's preconditions confirmed — 6 artgen keys, 1 URL, all
`runner_key=None`). No reviewer false-positives survived.
