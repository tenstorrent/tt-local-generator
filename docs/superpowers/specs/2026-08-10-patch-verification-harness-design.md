# Fail-Loud Patch-Verification Harness — Design

**Date:** 2026-08-10
**Branch:** `feat/pipeline-editor`
**Status:** Approved (brainstorm), pending implementation plan

## Problem / goal

Our `tt-inference-server` media patches drift silently. The canonical proof:
`apply_patches.sh` Steps 7/8/9 rotted undetected after the 0.18.0 model_spec.py→YAML
migration killed their text-injection anchor — the steps printed
`ERROR: could not find insertion anchor` and **kept going**, so a broken patch
shipped without anyone noticing (repaired in v0.76.0). The root cause is not the
bind-mount mechanism (bind-mounting whole new modules is the *correct* strategy
per tt-vscode-toolkit's `monkeypatch-ttnn.md`, which cites tt-local-generator as
its canonical example) — it is the **absence of a fail-loud signal when a
patch's premise no longer holds**.

**Goal:** add a fail-loud verification harness that turns silent patch drift into
a loud, build-breaking failure — keeping every patch exactly where it is (on
bind-mounts / text-injects) and adopting the *philosophy* of tt-vscode-toolkit's
`tt_patches.py` (do the least; make it loud when it stops being valid), not its
in-process attribute-patching machinery (which does not apply, since we
deliberately introduce **no** container pre-import hook).

## Scope (locked in brainstorm)

**In:**
1. **Injector-anchor verification** — every `apply_patches.sh` text-inject/append
   step declares the anchor string(s) it depends on; a harness asserts each
   anchor still exists in the target vendored file. A missing anchor is a hard,
   named failure — never a warning that scrolls past.
2. **A declarative patch manifest** — one Python module that is the single source
   of truth for every patch and injector step: its kind, target, anchors,
   bind-mount destination, one-line premise, and optional version ceiling.
3. **`apply_patches.sh` hardened** — calls the verifier as a fail-loud gate before
   any inject/append, AND each inject step itself exits non-zero on a missing
   anchor (belt-and-suspenders: no half-patched vendor tree can be produced).
4. **Packaging & installers matched** — the shipped `.deb` currently carries an
   **unpatched** vendor tree (nothing on the packaged path ever runs
   `apply_patches.sh`). CI is changed to apply **and** verify patches at build
   time so the shipped vendor is actually patched, `debian/rules` gains a
   verify-before-ship gate, and the new modules are confirmed to package.

**Out / deferred (with rationale):**
- **True image-diff drift detection** (`docker create/cp` the media image, diff
  each bind-mount patch against the real upstream file to detect *moved* or
  *absorbed* patches) — declared as first-class hooks in the manifest (each
  `bind_mount` entry carries its container destination path + version ceiling)
  but not implemented here; it needs the media image pulled and is the effort the
  0.19 spec already deferred. This design makes adding it a new probe over the
  same manifest, not a rewrite.
- **In-container runtime harness** (a `tt_patches.py` `wrap`/`set_default` applied
  before the media server imports) — rejected in brainstorm: it requires a new
  container pre-import mechanism (sitecustomize/PYTHONSTARTUP) that does not exist
  today, is exactly the kind of change that "breaks device init on this image,"
  and cannot be validated from the dev session. Our patch surface is also ~90%
  whole-module ADDs and whole-file DEEP-FIX rewrites, which the toolkit lesson
  itself says belong on bind-mounts.
- **Install-time patching in `debian/postinst`** — rejected: root-owned `/usr/lib`
  writes, the no-`.git` snapshot would trigger a network clone, and it is harder
  to validate than the build-time path.

## Hard constraints

- **Hardware-free.** Everything here is verifiable from the dev session and in
  CI — no TT hardware, no running container. Automated tests cover the manifest,
  the probes, and the fail-loud behavior.
- **Pure/GTK-free modules.** `patch_manifest.py` and `patch_verify.py` import only
  the standard library (mirroring `worker.py`/`server_manager.py`/`history_store.py`'s
  zero-GUI rule), so they unit-test without a display.
- **Fail loud, never silent.** A missing anchor, an unparseable patch file, or an
  inconsistent manifest exits non-zero with a message naming the patch, the file,
  and the anchor. The old `ERROR … ; continue` pattern is eliminated.
- **Additive to the working v0.76.0 patch flow.** The verifier gates
  `apply_patches.sh`; it does not change what any patch does. A green verify run
  against today's vendored tree must pass unchanged.
- **Version discipline:** bump `VERSION` (0.76.0 → **0.77.0**, a normal minor for
  new tooling). Prepend a `debian/changelog` stanza.

## Components

### A. `app/patch_manifest.py` — the single source of truth

A pure module exposing a list of declarative entries (a frozen dataclass
`PatchEntry`), one per patch file and per injector step. Fields:

- `id: str` — stable identifier (e.g. `"inject-tt_dit-mounts"`, `"append-animate-yaml"`,
  `"bind-constants"`).
- `kind: Literal["inject", "append", "bind_mount"]`.
- `target: str` — repo-relative path *inside the vendor tree* the step writes to
  (for `inject`/`append`, e.g. `workflows/run_docker_server.py`,
  `workflows/model_specs/dev/video.yaml`); for `bind_mount`, the patch **source**
  path under `patches/` (e.g. `patches/media_server_config/config/constants.py`).
- `anchors: tuple[str, ...]` — for `inject`/`append`: the literal string(s) that
  MUST be present in `target` for the step to work (e.g.
  `"    for key, value in docker_env_vars.items():"` for the mount-loop injects;
  the HF-repo-ID markers `Skywork/SkyReels-V2-DF-1.3B-540P-Diffusers`,
  `Skywork/SkyReels-V2-I2V-14B-540P`, `Wan-AI/Wan2.2-Animate-14B-Diffusers` for
  the YAML appends). Empty for `bind_mount`.
- `dest: str | None` — for `bind_mount`: the container destination module path the
  overlay lands on (e.g. `tt-metal/server/config/constants.py`,
  `tt-metal/models/tt_dit/pipelines/wan/pipeline_wan.py`). The hook the deferred
  image-diff probe will read. `None` for inject/append.
- `premise: str` — one line: why this patch exists (e.g. "adds CANARY_TASK_IDS the
  0.9.0 image references at import but does not define").
- `version_ceiling: str | None` — optional semver ceiling; when the current
  vendored version exceeds it, the patch is *suspected absorbed* and the verifier
  emits a soft warning (not a hard failure). `None` = no ceiling declared.

The initial manifest enumerates: the three `run_docker_server.py` mount-loop
injects (Steps 2/4/5, shared anchor), the two `video.yaml` appends (Steps 6/7),
and every `bind_mount` file under `patches/media_server_config/**`,
`patches/tt_dit/**`, `patches/models/**` (each with its `dest` + `premise`).

**Decision:** a Python module, not a YAML/JSON data file — it is importable and
unit-testable with no parser dependency, matching the repo's pure-module pattern.

### B. `app/patch_verify.py` — the harness

Borrows tt-vscode-toolkit's philosophy; vendors verbatim only the two pure,
dependency-free pieces that apply, and adds host-side probes:

- `class PatchError(RuntimeError)` — a patch premise no longer holds (verbatim from
  the toolkit).
- `version_at_most(current: str, ceiling: str) -> bool` — verbatim from the
  toolkit (leading-numeric-segment compare, zero-padded, suffix-tolerant,
  dependency-free). Used for the soft absorbed-patch warning.
- `verify(vendor_root: str | Path, manifest=PATCHES) -> list[ProbeResult]` — the
  collect-all-then-report core (mirrors `tt_patches.verify`'s shape: run every
  probe, collect failures, do **not** stop on the first). For each entry:
  - `inject`/`append`: assert every `anchor` is a substring of the file at
    `vendor_root / entry.target`; a missing file or missing anchor is a failed
    `ProbeResult` naming entry id + target + the specific anchor.
  - `bind_mount`: assert the patch source file exists under the repo `patches/`
    tree and `py_compile`s cleanly (catches a syntactically broken patch); record
    its `dest` for the deferred image-diff. A `version_ceiling` exceeded by the
    current vendored version yields a `warning` result (not a failure).
- `ProbeResult` — a small frozen dataclass `(id, ok, level, message)` where
  `level ∈ {"error", "warning"}`.
- `require(vendor_root, manifest=PATCHES) -> None` — the hard-gate entry point:
  runs `verify`, and if any `error`-level result is present, raises `PatchError`
  with a message listing every failure. This is what `apply_patches.sh` calls.
- `__main__` CLI — `python3 app/patch_verify.py --vendor <path> [--manifest-only]`.
  Prints each failure/warning, exits non-zero if any `error` result (or if
  `require` would raise). `--manifest-only` checks internal manifest consistency
  (no duplicate ids, every `inject`/`append` has ≥1 anchor, every `bind_mount` has
  a `dest`, every declared patch source path exists) without needing a vendor
  tree — the check CI runs on any change to `patches/` or the manifest.

The current vendored version (for `version_at_most`) is read from a new one-line
`vendor/VENDOR_VERSION` file (e.g. `0.19.0`) written by `snapshot_vendor.sh`
alongside the existing `VENDOR_SHA`; absent → version checks are skipped (no
crash).

### C. `bin/apply_patches.sh` — gated + hard-abort

- **Gate first:** as its first real action (after resolving `$TT_INFER`), run
  `python3 "$REPO_ROOT/app/patch_verify.py" --vendor "$TT_INFER"`. Non-zero exit
  aborts the whole script (`set -e` already in effect / explicit check) with the
  verifier's message — so a drifted anchor stops the run before it produces a
  half-patched tree.
- **Per-step hard-abort:** each inject/append step's embedded Python already
  `sys.exit(1)`s when its target file is missing; extend the inject steps
  (2/4/5) so a **missing anchor** also `sys.exit(1)`s (today the anchored insert
  can no-op silently if the anchor moved). The gate makes this redundant in the
  happy path, but it guarantees no code path can silently skip an inject.

### D. Packaging & installers — matched to the gate

The `.deb` today ships an **unpatched** vendor snapshot (nothing on the packaged
path runs `apply_patches.sh`; CI runs `snapshot_vendor.sh` then
`dpkg-buildpackage` with no patch step). Decision: **apply + verify at build
time** so the shipped vendor is patched and drift breaks the build.

- **CI (`.github/workflows/release-deb.yml`):** after the existing
  `snapshot_vendor.sh` step and before `dpkg-buildpackage`, add a step that runs
  `bin/apply_patches.sh` against the freshly-snapshotted `vendor/tt-inference-server`.
  Because `apply_patches.sh` now gates on `patch_verify`, a drifted anchor fails
  the release build loudly. Result: the shipped `.deb` vendor is patched **and**
  verified.
- **`debian/rules` verify-before-ship gate:** in `override_dh_install`, after the
  `cp -r vendor …` block, run `python3 app/patch_verify.py --vendor
  debian/tt-local-generator/usr/lib/tt-local-generator/vendor/tt-inference-server`
  and abort the package build if it fails. This protects a *local*
  `dpkg-buildpackage` (which does not go through the CI apply step) from silently
  shipping an unpatched/undrifted vendor — it fails loud, telling the builder to
  run `apply_patches.sh` first.
- **Module packaging (confirm, no change expected):** `app/patch_manifest.py` and
  `app/patch_verify.py` land under `/usr/lib/tt-local-generator/app/` via the
  existing `debian/rules:30` `cp -r app bin patches plugins …`. A test asserts
  both files are present in the staged tree list.
- **`bin/quickstart.sh` status:** its "Patches applied?" check
  (greps `run_docker_server.py` for `tt_dit_patches_dir`) gains a second line that
  runs `patch_verify.py --vendor "$REPO_ROOT/vendor/tt-inference-server"` and
  reports pass/fail, so the dev status board surfaces drift too.
- **`bin/snapshot_vendor.sh`:** write `vendor/VENDOR_VERSION` (the release version
  string, e.g. `0.19.0`) next to the existing `VENDOR_SHA` stamp, for the
  `version_at_most` soft-warning input.
- **Docs:** `INSTALL_deb.md` / `docs/UPGRADE.md` note that CI now applies+verifies
  patches at build time (the shipped `.deb` vendor is patched), and that a local
  `.deb` build must run `apply_patches.sh` first or the build fails the verify
  gate.

## Testing (no hardware)

- **Manifest consistency:** `patch_verify.py --manifest-only` passes on the real
  manifest (no dup ids; every inject/append has ≥1 anchor; every bind_mount has a
  `dest`; every declared patch source path exists under `patches/`).
- **Anchor present → pass:** `verify()` over a fixture vendor tree containing all
  declared anchors returns zero `error` results.
- **Anchor removed → fail loud (the Steps-7/8/9 regression):** a fixture vendor
  tree with one anchor string deleted → `verify()` returns an `error` result
  naming that entry/anchor, and `require()` raises `PatchError`. This is the test
  that would have caught the original bug.
- **Broken patch file → fail:** a `bind_mount` entry whose source file has a syntax
  error → an `error` result from the `py_compile` probe.
- **`version_at_most` boundaries:** mirror the toolkit's own cases (equal,
  below, above, suffix-tolerant, zero-padded).
- **Absorbed-patch warning:** current version above a `version_ceiling` → a
  `warning`-level result, not an `error` (does not fail the gate).
- **Packaging:** a test asserts `patch_manifest.py`/`patch_verify.py` are in the
  `debian/rules` copy set (grep the rules `cp` line), mirroring the existing
  `test_regression_guards.py` script-text assertions. The existing
  `tests/test_apply_patches_animate.py` is extended to assert the gate call is
  present in `apply_patches.sh`.
- Full suite green with the documented flake deselects.

**Explicitly NOT tested here (deferred):** the image-diff probe (needs the media
image); that CI's build-time `apply_patches.sh` step succeeds end-to-end against
a real snapshot (a CI/hardware-adjacent concern, exercised when the release
workflow next runs).

## Critical files

- `app/patch_manifest.py` — NEW, the declarative source of truth.
- `app/patch_verify.py` — NEW, the harness (`PatchError`, `version_at_most`,
  `verify`, `require`, `ProbeResult`, CLI).
- `bin/apply_patches.sh` — call the gate first; per-step hard-abort on missing
  anchor.
- `.github/workflows/release-deb.yml` — apply+verify patches after snapshot,
  before build.
- `debian/rules` — verify-before-ship gate after the vendor copy.
- `bin/snapshot_vendor.sh` — write `vendor/VENDOR_VERSION`.
- `bin/quickstart.sh` — surface verify status.
- `tests/test_patch_verify.py` — NEW; extend `tests/test_apply_patches_animate.py`,
  `tests/test_regression_guards.py`.
- `VERSION` (→0.77.0), `debian/changelog`, `CLAUDE.md`, `INSTALL_deb.md`,
  `docs/UPGRADE.md`.

## Open items for the plan (confirm, don't guess)

- The exact current set of `inject`/`append` anchors in the v0.76.0
  `apply_patches.sh` (read the live script — Steps 2/4/5 share the
  `docker_env_vars.items()` anchor; Steps 6/7 use the HF-repo-ID markers) and the
  exact `dest` container path for every `bind_mount` patch file (from the
  mount-loop `dst=` expressions).
- Whether `debian/rules`' `override_dh_install` can run `python3` at package-build
  time in the CI environment (it can — it is just a shell step — but confirm the
  interpreter path and that `app/` is already staged at that point in the rule).
- The precise string `bin/quickstart.sh` should check / display for the verify
  status line, matched to its existing `pass`/`fail_s` helpers.
