# Fail-Loud Patch-Verification Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn silent tt-inference-server patch drift into a loud, build-breaking failure — via a declarative patch manifest + a pure host-side verify harness, wired as a gate into `apply_patches.sh`, CI, and `debian/rules`.

**Architecture:** Two new pure/stdlib-only modules under `app/` — `patch_manifest.py` (the single declarative source of truth for every patch and injector step) and `patch_verify.py` (fail-loud probes borrowing tt-vscode-toolkit's `PatchError`/`version_at_most`/`verify` philosophy). `apply_patches.sh` calls the verifier as its first action; CI applies+verifies patches at build time so the shipped `.deb` vendor is actually patched; `debian/rules` gains a verify-before-ship gate. No container hook, no runtime attribute-patching — verification is host-side and hardware-free.

**Tech Stack:** Python 3 (system `/usr/bin/python3`), stdlib only (no GTK, no third-party), bash, GitHub Actions, debhelper. Tests via `xvfb-run --auto-servernum /usr/bin/python3 -m pytest` (though these modules need no display).

## Global Constraints

Every task's requirements implicitly include this section. Values copied verbatim from the spec (`docs/superpowers/specs/2026-08-10-patch-verification-harness-design.md`).

- **Hardware-free.** Everything is verifiable in the dev session and CI — no TT hardware, no running container. The image-diff drift check is explicitly DEFERRED (the manifest's `bind_mount` `dest` field is its hook); do not implement it.
- **Pure / GTK-free modules.** `patch_manifest.py` and `patch_verify.py` import stdlib ONLY (mirroring `worker.py`/`server_manager.py`/`history_store.py`). No `gi`, no third-party. They must import standalone.
- **Fail loud, never silent.** A missing anchor, a missing append-target file, an unparseable patch file, or an inconsistent manifest → a non-zero exit / raised `PatchError` naming the entry id, the file, and the specific anchor. Never a warning that scrolls past.
- **Additive to the working v0.76.0 flow.** The verifier GATES `apply_patches.sh`; it changes what no patch does. A green verify run against today's real vendored tree must pass. The inject steps (2/4/5) ALREADY `sys.exit(1)` on a missing anchor (`bin/apply_patches.sh:147-151`) — do NOT re-add per-step aborts; only add the gate call.
- **No container pre-import hook.** Do not add sitecustomize/PYTHONSTARTUP or any `tt_patches` `wrap`/`set_default`. Verification is host-side only.
- **Version discipline:** `VERSION` → **0.77.0** (done once, Task 7; do not bump per task). Prepend a `debian/changelog` stanza.
- **Local commits only.** Do NOT push, PR, or merge (the controller pushes on explicit request). Frequent commits per task.
- **Known-flake deselects** for full-suite runs (Task 7):
  `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`,
  `tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`,
  `tests/test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen`.

## Confirmed values (from the live repo; no guessing)

- **Inject anchor** (Steps 2/4/5, all share it), in `$TT_INFER/workflows/run_docker_server.py`:
  `    for key, value in docker_env_vars.items():` (`bin/apply_patches.sh:145,231,290`).
- **Append target** (Steps 6/7): `workflows/model_specs/dev/video.yaml` (`bin/apply_patches.sh:317`). The 0.18.0 migration moved the catalog here from the now-absent `workflows/model_spec.py` — so "the target file exists at this path" IS the drift signal for appends (no pre-existing anchor string is needed to append).
- **Bind-mount dest formula** (mirrors the mount loops):
  `patches/media_server_config/<rel>` → `tt-metal/server/<rel>` (`bin/apply_patches.sh:213`);
  `patches/tt_dit/<rel>` → `tt-metal/models/tt_dit/<rel>` (`:125`);
  `patches/models/<rel>` → `tt-metal/models/<rel>` (per `patches/README.md`).
- **Vendor stamp location:** `snapshot_vendor.sh` writes `vendor/VENDOR_SHA` at the repo `vendor/` dir (sibling of `tt-inference-server/`), `bin/snapshot_vendor.sh:95`. `VENDOR_VERSION` (Task 4) goes beside it. `apply_patches.sh` passes `$TT_INFER = vendor/tt-inference-server`, so the verifier reads `vendor_root.parent / "VENDOR_VERSION"`.
- **Packaging copy:** `debian/rules:30-31` `cp -r app bin patches plugins …/usr/lib/tt-local-generator/` (so the two new `app/` modules ship for free); `:35-53` copies `vendor/`.
- **CI:** `.github/workflows/release-deb.yml:27-30` runs `snapshot_vendor.sh`, `:46` runs `dpkg-buildpackage` — no patch/verify step today.
- **Test import style:** `sys.path.insert(0, str(Path(__file__).parent.parent / "app"))` then `import <module>`.

---

## Task 1: `app/patch_manifest.py` — declarative source of truth

**Files:**
- Create: `app/patch_manifest.py`
- Test: `tests/test_patch_manifest.py`

**Interfaces:**
- Produces: `PatchEntry` (frozen dataclass: `id, kind, target, anchors, dest, premise, version_ceiling`); `manifest(patches_root=_PATCHES) -> tuple[PatchEntry, ...]`; `PATCHES` (the built default); `manifest_issues(entries=None) -> list[str]`.

- [ ] **Step 1: Write the failing test**

`tests/test_patch_manifest.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import patch_manifest as pm

def test_manifest_has_the_inject_and_append_entries():
    ids = {e.id for e in pm.PATCHES}
    assert "inject-run-docker-server-mounts" in ids
    assert "append-skyreels-video-yaml" in ids
    assert "append-animate-video-yaml" in ids

def test_inject_entry_carries_the_docker_env_vars_anchor():
    e = next(e for e in pm.PATCHES if e.id == "inject-run-docker-server-mounts")
    assert e.kind == "inject"
    assert e.target == "workflows/run_docker_server.py"
    assert "    for key, value in docker_env_vars.items():" in e.anchors

def test_append_entries_target_video_yaml_with_no_anchor():
    for eid in ("append-skyreels-video-yaml", "append-animate-video-yaml"):
        e = next(e for e in pm.PATCHES if e.id == eid)
        assert e.kind == "append"
        assert e.target == "workflows/model_specs/dev/video.yaml"
        assert e.anchors == ()

def test_bind_mounts_are_discovered_with_correct_dest():
    by_target = {e.target: e for e in pm.PATCHES if e.kind == "bind_mount"}
    # constants.py overlays the server config module
    c = by_target["patches/media_server_config/config/constants.py"]
    assert c.dest == "tt-metal/server/config/constants.py"
    # a tt_dit pipeline overlays under tt-metal/models/tt_dit/
    w = by_target["patches/tt_dit/pipelines/wan/pipeline_wan.py"]
    assert w.dest == "tt-metal/models/tt_dit/pipelines/wan/pipeline_wan.py"

def test_manifest_is_internally_consistent():
    assert pm.manifest_issues() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_patch_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: patch_manifest`.

- [ ] **Step 3: Write `app/patch_manifest.py`**

```python
"""Declarative source of truth for every tt-inference-server patch and injector
step. Pure/stdlib-only (no gi, no third-party) so it imports and unit-tests
without a display. See docs/superpowers/specs/2026-08-10-patch-verification-harness-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent  # app/ -> repo root
_PATCHES = _REPO_ROOT / "patches"

# dest-base per bind-mount source tree — mirrors the mount loops in
# bin/apply_patches.sh (media_server_config -> tt-metal/server, etc.).
_BIND_TREES: dict[str, str] = {
    "media_server_config": "tt-metal/server",
    "tt_dit": "tt-metal/models/tt_dit",
    "models": "tt-metal/models",
}

# Optional per-file metadata for auto-discovered bind-mount patches, keyed by
# "<tree>/<rel>". (premise, version_ceiling). Absent -> ("", None).
_BIND_META: dict[str, tuple[str, str | None]] = {}


@dataclass(frozen=True)
class PatchEntry:
    id: str
    kind: str                       # "inject" | "append" | "bind_mount"
    target: str                     # vendor-relative path (inject/append) OR
                                    # repo-relative patch source (bind_mount)
    anchors: tuple[str, ...] = ()
    dest: str | None = None         # container overlay path (bind_mount only)
    premise: str = ""
    version_ceiling: str | None = None


# Hand-declared inject/append entries — the drift-prone injector anchors.
_DECLARED: tuple[PatchEntry, ...] = (
    PatchEntry(
        id="inject-run-docker-server-mounts",
        kind="inject",
        target="workflows/run_docker_server.py",
        anchors=("    for key, value in docker_env_vars.items():",),
        premise=(
            "Steps 2/4/5 insert the tt_dit / media_server_config / HF_HOME "
            "bind-mount loops immediately before this line."
        ),
    ),
    PatchEntry(
        id="append-skyreels-video-yaml",
        kind="append",
        target="workflows/model_specs/dev/video.yaml",
        premise=(
            "Step 6 appends SkyReels T2V/I2V catalog entries here. 0.18.0 moved "
            "the catalog to this file from the now-absent workflows/model_spec.py."
        ),
    ),
    PatchEntry(
        id="append-animate-video-yaml",
        kind="append",
        target="workflows/model_specs/dev/video.yaml",
        premise="Step 7 appends the Wan2.2-Animate catalog entry here.",
    ),
)


def _discover_bind_mounts(patches_root: Path = _PATCHES) -> tuple[PatchEntry, ...]:
    out: list[PatchEntry] = []
    for tree, dest_base in _BIND_TREES.items():
        root = patches_root / tree
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*.py")):
            rel = f.relative_to(root).as_posix()
            src_rel = f.relative_to(patches_root).as_posix()
            premise, ceiling = _BIND_META.get(f"{tree}/{rel}", ("", None))
            out.append(
                PatchEntry(
                    id=f"bind-{tree}-{rel}",
                    kind="bind_mount",
                    target=f"patches/{src_rel}",
                    dest=f"{dest_base}/{rel}",
                    premise=premise,
                    version_ceiling=ceiling,
                )
            )
    return tuple(out)


def manifest(patches_root: Path = _PATCHES) -> tuple[PatchEntry, ...]:
    return _DECLARED + _discover_bind_mounts(patches_root)


PATCHES: tuple[PatchEntry, ...] = manifest()


def manifest_issues(entries: tuple[PatchEntry, ...] | None = None) -> list[str]:
    """Internal-consistency check (no vendor tree needed). Empty list == OK."""
    entries = entries if entries is not None else PATCHES
    issues: list[str] = []
    seen: set[str] = set()
    for e in entries:
        if e.id in seen:
            issues.append(f"duplicate id: {e.id}")
        seen.add(e.id)
        if e.kind not in ("inject", "append", "bind_mount"):
            issues.append(f"{e.id}: unknown kind {e.kind!r}")
        if e.kind == "inject" and not e.anchors:
            issues.append(f"{e.id}: inject entry has no anchors")
        if e.kind == "bind_mount":
            if not e.dest:
                issues.append(f"{e.id}: bind_mount entry has no dest")
            if not (_REPO_ROOT / e.target).is_file():
                issues.append(f"{e.id}: patch source missing: {e.target}")
    return issues
```

- [ ] **Step 4: Run to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_patch_manifest.py -v`
Expected: PASS (all 5). If `test_bind_mounts_are_discovered_with_correct_dest` fails, confirm the two named patch files still exist under `patches/` and adjust the test's example paths to two that do (report the change).

- [ ] **Step 5: Commit**

```bash
git add app/patch_manifest.py tests/test_patch_manifest.py
git commit -m "feat(patches): declarative patch manifest (inject/append anchors + bind-mount discovery)"
```

---

## Task 2: `app/patch_verify.py` — the fail-loud harness

**Files:**
- Create: `app/patch_verify.py`
- Test: `tests/test_patch_verify.py`

**Interfaces:**
- Consumes: `patch_manifest.PatchEntry`, `patch_manifest.PATCHES`, `patch_manifest.manifest_issues`.
- Produces: `PatchError(RuntimeError)`; `version_at_most(current, ceiling) -> bool`; `ProbeResult(id, ok, level, message)`; `verify(vendor_root, entries=None) -> list[ProbeResult]`; `require(vendor_root, entries=None) -> None` (raises `PatchError` on any error-level result); `main(argv=None) -> int` (CLI).

- [ ] **Step 1: Write the failing test**

`tests/test_patch_verify.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import patch_verify as pv
import patch_manifest as pm
import pytest


def _fixture_vendor(tmp_path, *, anchor=True, yaml=True):
    """Build a minimal vendor tree that satisfies the declared inject/append entries."""
    root = tmp_path / "tt-inference-server"
    wf = root / "workflows"
    wf.mkdir(parents=True)
    rds = wf / "run_docker_server.py"
    body = "def build():\n"
    if anchor:
        body += "    for key, value in docker_env_vars.items():\n        pass\n"
    rds.write_text(body)
    catalog = wf / "model_specs" / "dev"
    catalog.mkdir(parents=True)
    if yaml:
        (catalog / "video.yaml").write_text("- weights:\n    - X/Y\n")
    return root


# Only the declared (inject/append) entries, so tests don't depend on the real
# bind-mount patch files under patches/.
_DECLARED = tuple(e for e in pm.PATCHES if e.kind in ("inject", "append"))


def test_verify_passes_when_anchor_and_yaml_present(tmp_path):
    root = _fixture_vendor(tmp_path)
    results = pv.verify(root, _DECLARED)
    assert [r for r in results if r.level == "error" and not r.ok] == []


def test_missing_anchor_is_a_loud_error(tmp_path):
    # This is the Steps-7/8/9 regression: the anchor moved/vanished upstream.
    root = _fixture_vendor(tmp_path, anchor=False)
    results = pv.verify(root, _DECLARED)
    errs = [r for r in results if r.level == "error" and not r.ok]
    assert any("inject-run-docker-server-mounts" in r.id for r in errs)


def test_require_raises_patcherror_on_missing_anchor(tmp_path):
    root = _fixture_vendor(tmp_path, anchor=False)
    with pytest.raises(pv.PatchError):
        pv.require(root, _DECLARED)


def test_missing_append_target_is_a_loud_error(tmp_path):
    root = _fixture_vendor(tmp_path, yaml=False)
    results = pv.verify(root, _DECLARED)
    errs = [r for r in results if r.level == "error" and not r.ok]
    assert any(r.id.startswith("append-") for r in errs)


def test_broken_bind_mount_patch_fails(tmp_path):
    bad = pm.PatchEntry(
        id="bind-broken", kind="bind_mount",
        target="patches/does_not_exist_broken.py",
        dest="tt-metal/server/x.py",
    )
    # A missing source file is an error (manifest_issues also catches this).
    results = pv.verify(tmp_path / "tt-inference-server", (bad,))
    assert any(not r.ok and r.level == "error" for r in results)


def test_version_at_most_boundaries():
    assert pv.version_at_most("0.19.0", "0.19.0") is True
    assert pv.version_at_most("0.18.9", "0.19.0") is True
    assert pv.version_at_most("0.20.0", "0.19.0") is False
    assert pv.version_at_most("0.19.0rc1", "0.19.0") is True
    assert pv.version_at_most("0.19", "0.19.0") is True


def test_cli_exits_nonzero_on_missing_anchor(tmp_path):
    root = _fixture_vendor(tmp_path, anchor=False)
    rc = pv.main(["--vendor", str(root)])
    assert rc != 0


def test_cli_manifest_only_passes_on_real_manifest():
    assert pv.main(["--manifest-only"]) == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_patch_verify.py -v`
Expected: FAIL — `ModuleNotFoundError: patch_verify`.

- [ ] **Step 3: Write `app/patch_verify.py`**

```python
"""Fail-loud, host-side verification of the tt-inference-server patch surface.

Borrows the philosophy (and the two pure, dependency-free helpers PatchError +
version_at_most) of tt-vscode-toolkit's tt_patches.py, but does NOT do in-process
attribute patching — our patches stay on bind-mounts. This module only ASSERTS
that each patch's premise still holds against a vendored tree, so drift fails
loud instead of shipping silently. Pure/stdlib-only.
"""
from __future__ import annotations

import argparse
import logging
import py_compile
import sys
from dataclasses import dataclass
from pathlib import Path

import patch_manifest as pm

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent


class PatchError(RuntimeError):
    """A patch premise no longer holds — upstream moved/renamed/absorbed it."""


@dataclass(frozen=True)
class ProbeResult:
    id: str
    ok: bool
    level: str          # "error" | "warning"
    message: str


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for segment in version.lstrip("vV").split("."):
        digits = ""
        for ch in segment:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits == "":
            break
        parts.append(int(digits))
    return tuple(parts)


def version_at_most(current: str, ceiling: str) -> bool:
    """True if current <= ceiling (leading-numeric compare, zero-padded,
    suffix-tolerant). Verbatim behavior from tt_patches.version_at_most."""
    a = _version_tuple(current)
    b = _version_tuple(ceiling)
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return a <= b


def _current_vendor_version(vendor_root: Path) -> str | None:
    """Read vendor/VENDOR_VERSION (sibling of the tt-inference-server dir)."""
    f = vendor_root.parent / "VENDOR_VERSION"
    try:
        return f.read_text().strip() or None
    except OSError:
        return None


def verify(vendor_root, entries=None) -> list[ProbeResult]:
    """Run every probe; collect ALL results (never stop at the first failure)."""
    vendor_root = Path(vendor_root)
    entries = entries if entries is not None else pm.PATCHES
    current = _current_vendor_version(vendor_root)
    results: list[ProbeResult] = []

    for e in entries:
        if e.kind == "inject":
            target = vendor_root / e.target
            if not target.is_file():
                results.append(ProbeResult(e.id, False, "error",
                    f"{e.id}: inject target missing: {e.target}"))
                continue
            text = target.read_text()
            missing = [a for a in e.anchors if a not in text]
            if missing:
                results.append(ProbeResult(e.id, False, "error",
                    f"{e.id}: anchor(s) gone from {e.target}: {missing!r} "
                    f"(upstream moved? — {e.premise})"))
            else:
                results.append(ProbeResult(e.id, True, "error", f"{e.id}: ok"))

        elif e.kind == "append":
            target = vendor_root / e.target
            if not target.is_file():
                results.append(ProbeResult(e.id, False, "error",
                    f"{e.id}: append target missing: {e.target} "
                    f"(catalog moved? — {e.premise})"))
            else:
                results.append(ProbeResult(e.id, True, "error", f"{e.id}: ok"))

        elif e.kind == "bind_mount":
            src = _REPO_ROOT / e.target
            if not src.is_file():
                results.append(ProbeResult(e.id, False, "error",
                    f"{e.id}: patch source missing: {e.target}"))
                continue
            try:
                py_compile.compile(str(src), doraise=True)
            except py_compile.PyCompileError as exc:
                results.append(ProbeResult(e.id, False, "error",
                    f"{e.id}: patch source does not compile: {exc}"))
                continue
            # Soft absorbed-patch warning if we know the vendor version.
            if current and e.version_ceiling and not version_at_most(
                    current, e.version_ceiling):
                results.append(ProbeResult(e.id, False, "warning",
                    f"{e.id}: vendor {current} > ceiling {e.version_ceiling}: "
                    f"patch may be absorbed upstream — re-check ({e.premise})"))
            else:
                results.append(ProbeResult(e.id, True, "bind_mount", f"{e.id}: ok"))

    return results


def require(vendor_root, entries=None) -> None:
    """Raise PatchError if any error-level probe failed."""
    results = verify(vendor_root, entries)
    errs = [r for r in results if r.level == "error" and not r.ok]
    if errs:
        raise PatchError(
            "patch verification failed:\n"
            + "\n".join(f"  - {r.message}" for r in errs)
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify tt-inference-server patches.")
    ap.add_argument("--vendor", help="path to the vendored tt-inference-server tree")
    ap.add_argument("--manifest-only", action="store_true",
                    help="check manifest internal consistency only (no vendor tree)")
    args = ap.parse_args(argv)

    issues = pm.manifest_issues()
    for i in issues:
        print(f"MANIFEST: {i}", file=sys.stderr)
    if args.manifest_only:
        return 1 if issues else 0
    if issues:
        return 1

    if not args.vendor:
        print("error: --vendor is required (or use --manifest-only)", file=sys.stderr)
        return 2

    results = verify(args.vendor)
    errs = [r for r in results if r.level == "error" and not r.ok]
    warns = [r for r in results if r.level == "warning" and not r.ok]
    for r in warns:
        print(f"WARN:  {r.message}", file=sys.stderr)
    for r in errs:
        print(f"ERROR: {r.message}", file=sys.stderr)
    if errs:
        print(f"patch verification FAILED: {len(errs)} error(s).", file=sys.stderr)
        return 1
    print(f"patch verification OK ({len(results)} probes, {len(warns)} warning(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: `verify()` tags passing bind-mount results with `level="bind_mount"` so they are neither counted as errors nor warnings; the `require`/CLI error filter keys on `level == "error"`. If you prefer, tag passing bind-mount results `level="ok"` — just keep the error filter (`level == "error" and not r.ok`) and the warning filter (`level == "warning" and not r.ok`) consistent. Ensure `test_broken_bind_mount_patch_fails` still sees an `error`-level failure.

- [ ] **Step 4: Run to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_patch_verify.py -v`
Expected: PASS (all 8). Then run the REAL manifest against the REAL vendored tree to prove no false positive on today's tree (if `vendor/tt-inference-server` is patched locally):
`/usr/bin/python3 app/patch_verify.py --vendor vendor/tt-inference-server` → exits 0 (or reports only real drift). If the local vendor is unpatched/absent, run `--manifest-only` instead: `/usr/bin/python3 app/patch_verify.py --manifest-only` → exits 0. Report which you ran.

- [ ] **Step 5: Commit**

```bash
git add app/patch_verify.py tests/test_patch_verify.py
git commit -m "feat(patches): fail-loud patch-verify harness (anchor/append/compile probes + CLI)"
```

---

## Task 3: Gate `bin/apply_patches.sh` on the verifier

**Files:**
- Modify: `bin/apply_patches.sh` (after `$TT_INFER` resolution + the `RDS` existence check, before Step 1)
- Test: extend `tests/test_apply_patches_animate.py`

**Interfaces:**
- Consumes: `app/patch_verify.py` CLI (`--vendor`).

**Context:** `$TT_INFER` is resolved by `bin/apply_patches.sh:41-62`; `RDS="$TT_INFER/workflows/run_docker_server.py"` at `:70`. Insert the gate immediately after the existing `RDS` existence guard, so a drifted anchor stops the whole run up front (in addition to the per-step aborts that already exist).

- [ ] **Step 1: Write the failing test (append to `tests/test_apply_patches_animate.py`)**

```python
def test_apply_patches_calls_the_verify_gate():
    text = SCRIPT.read_text()
    # The verifier must be invoked as a gate (fail-loud) before patching.
    assert "patch_verify.py" in text
    assert "--vendor" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_apply_patches_animate.py::test_apply_patches_calls_the_verify_gate -v`
Expected: FAIL — `patch_verify.py` not yet referenced.

- [ ] **Step 3: Add the gate to `bin/apply_patches.sh`**

After the `RDS` existence check (around `bin/apply_patches.sh:70-74`), insert:

```bash
# ── Gate: verify every patch premise still holds before touching anything ────
# A drifted injector anchor or a moved catalog file aborts the whole run here,
# loudly, instead of producing a half-patched vendor tree (the Steps-7/8/9 class
# of bug). Pure/stdlib; needs no hardware.
echo "0. Verifying patch premises against $TT_INFER"
if ! /usr/bin/python3 "$REPO_ROOT/app/patch_verify.py" --vendor "$TT_INFER"; then
    echo "ERROR: patch verification failed — see messages above. Aborting." >&2
    echo "  A patch's target moved/renamed upstream. Fix the patch or the" >&2
    echo "  manifest (app/patch_manifest.py) before re-running." >&2
    exit 1
fi
```

(`REPO_ROOT` is already defined at `:26`. Use `/usr/bin/python3` per the repo's system-python rule.)

- [ ] **Step 4: Run to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_apply_patches_animate.py -v` (whole file — confirm the pre-existing tests still pass).
Also `bash -n bin/apply_patches.sh` → syntax clean.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/apply_patches.sh tests/test_apply_patches_animate.py
git commit -m "feat(patches): gate apply_patches.sh on the fail-loud verifier"
```

---

## Task 4: `VENDOR_VERSION` stamp for the absorbed-patch warning

**Files:**
- Modify: `bin/snapshot_vendor.sh` (write `vendor/VENDOR_VERSION` next to the `VENDOR_SHA` stamp)
- Test: extend `tests/test_patch_verify.py`

**Interfaces:**
- Produces: `vendor/VENDOR_VERSION` (a one-line version string, e.g. `0.19.0`) that `patch_verify._current_vendor_version` reads.

**Context:** `snapshot_vendor.sh` writes `vendor/VENDOR_SHA` (`bin/snapshot_vendor.sh:95`). Add a sibling `VENDOR_VERSION`. The version is the tt-inference-server release the SHA corresponds to — currently `0.19.0`. Source it from an optional `--version` flag defaulting to a `DEFAULT_VENDOR_VERSION="0.19.0"` constant (mirroring the existing `DEFAULT_SHA` pattern at `:27-31`).

- [ ] **Step 1: Write the failing test (append to `tests/test_patch_verify.py`)**

```python
def test_current_vendor_version_reads_sibling_file(tmp_path):
    vroot = tmp_path / "tt-inference-server"
    vroot.mkdir(parents=True)
    (tmp_path / "VENDOR_VERSION").write_text("0.19.0\n")
    assert pv._current_vendor_version(vroot) == "0.19.0"

def test_absorbed_patch_warns_not_errors(tmp_path):
    vroot = tmp_path / "tt-inference-server"
    (vroot / "workflows").mkdir(parents=True)
    (tmp_path / "VENDOR_VERSION").write_text("0.30.0\n")  # above the ceiling
    entry = pm.PatchEntry(
        id="bind-ceiling", kind="bind_mount",
        target="app/patch_verify.py",  # any real, compilable file under repo root
        dest="tt-metal/server/x.py", version_ceiling="0.19.0",
    )
    results = pv.verify(vroot, (entry,))
    assert any(r.level == "warning" and not r.ok for r in results)
    assert [r for r in results if r.level == "error" and not r.ok] == []
```

(Note: `target="app/patch_verify.py"` is a real, compilable file relative to the repo root, so the compile probe passes and only the ceiling warning fires. This validates the warning path without a real over-ceiling patch.)

- [ ] **Step 2: Run to verify it fails / passes as expected**

Run: `/usr/bin/python3 -m pytest tests/test_patch_verify.py -k "vendor_version or absorbed" -v`
Expected: these two PASS already if Task 2's `_current_vendor_version` + warning logic are correct (Task 4 mainly adds the snapshot writer). If `test_absorbed_patch_warns_not_errors` fails, fix the warning branch in `patch_verify.verify` (it must emit `level="warning"`, not an error, when `current > ceiling`). Report which.

- [ ] **Step 3: Write `vendor/VENDOR_VERSION` in `bin/snapshot_vendor.sh`**

Near the `DEFAULT_SHA` block (`:27-31`), add:

```bash
DEFAULT_VENDOR_VERSION="0.19.0"   # tt-inference-server release the pinned SHA is from
```

Where the script writes `vendor/VENDOR_SHA` (`:95`), also write the version (mirror the exact path form the script already uses for `VENDOR_SHA`):

```bash
echo "$DEFAULT_VENDOR_VERSION" > "$(dirname "$VENDOR_DIR")/VENDOR_VERSION"
echo "  stamped VENDOR_VERSION=$DEFAULT_VENDOR_VERSION"
```

(If the script accepts a `--sha` flag as CI uses, add a parallel optional `--version` flag defaulting to `DEFAULT_VENDOR_VERSION`; keep it simple — the constant is sufficient.)

Then create the file now so the repo has it (the harness reads it):

```bash
echo "0.19.0" > vendor/VENDOR_VERSION
```

- [ ] **Step 4: Run to verify**

Run: `/usr/bin/python3 -m pytest tests/test_patch_verify.py -v` (all pass) and `bash -n bin/snapshot_vendor.sh`.
Confirm `vendor/VENDOR_VERSION` exists and reads `0.19.0`. Note: `vendor/` may be gitignored — check `git status vendor/VENDOR_VERSION`; if ignored, `git add -f vendor/VENDOR_VERSION` (matching how `VENDOR_SHA` is tracked — `git ls-files vendor/VENDOR_SHA` first) and report what you found.

- [ ] **Step 5: Commit**

```bash
git add bin/snapshot_vendor.sh tests/test_patch_verify.py
git add -f vendor/VENDOR_VERSION 2>/dev/null || git add vendor/VENDOR_VERSION
git commit -m "feat(patches): stamp vendor/VENDOR_VERSION for the absorbed-patch warning"
```

---

## Task 5: Build-time apply+verify in CI + verify-before-ship gate in `debian/rules`

**Files:**
- Modify: `.github/workflows/release-deb.yml` (add an apply+verify step after snapshot, before build)
- Modify: `debian/rules` (verify-before-ship gate after the `cp -r vendor` block)
- Test: extend `tests/test_regression_guards.py`

**Interfaces:**
- Consumes: `bin/apply_patches.sh` (now gated), `app/patch_verify.py`.

**Context:** `release-deb.yml:27-30` snapshots the vendor tree, `:46` builds — no patch/verify today, so the shipped `.deb` vendor is UNPATCHED. `debian/rules:35-53` copies `vendor/` into the staged package. The two new `app/` modules ship for free via `debian/rules:30-31`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_regression_guards.py`)**

Follow the file's existing style (it reads script/workflow text and asserts on it):

```python
def test_ci_applies_and_verifies_patches_before_build():
    wf = (REPO_ROOT / ".github" / "workflows" / "release-deb.yml").read_text()
    assert "apply_patches.sh" in wf, "CI must apply patches so the shipped vendor is patched"

def test_debian_rules_verifies_vendor_before_ship():
    rules = (REPO_ROOT / "debian" / "rules").read_text()
    assert "patch_verify.py" in rules, "debian/rules must verify the staged vendor before shipping"

def test_new_patch_modules_are_packaged():
    rules = (REPO_ROOT / "debian" / "rules").read_text()
    # app/ is copied wholesale, so the modules ship; assert the copy line exists.
    assert "cp -r app bin patches plugins" in rules
```

(Use the file's existing `REPO_ROOT`/path constant; match its import style.)

- [ ] **Step 2: Run to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_regression_guards.py -k "ci_applies or debian_rules_verifies or new_patch_modules" -v`
Expected: FAIL on the first two (no `apply_patches.sh`/`patch_verify.py` refs yet); the third may already pass.

- [ ] **Step 3: Add the CI apply+verify step**

In `.github/workflows/release-deb.yml`, immediately AFTER the "Snapshot vendored tt-inference-server" step (`:27-30`) and BEFORE `dpkg-buildpackage`, add:

```yaml
      - name: Apply + verify tt-inference-server patches
        run: |
          # Patch the freshly-snapshotted vendor tree so the shipped .deb vendor
          # is actually patched; apply_patches.sh gates on app/patch_verify.py,
          # so drift fails the release build loudly.
          bin/apply_patches.sh vendor/tt-inference-server
```

(Pass the explicit vendor path so it does not try to bootstrap-clone. Confirm `apply_patches.sh` accepts a positional path arg — it does, `bin/apply_patches.sh:47-49`.)

- [ ] **Step 4: Add the `debian/rules` verify-before-ship gate**

In `debian/rules`, after the `cp -r vendor …` block (`:35-53`), add a verify step against the STAGED vendor tree so a local `dpkg-buildpackage` (which skips the CI apply step) fails loud rather than shipping an unpatched/undrifted vendor:

```make
	# Verify-before-ship: the staged vendor tree must pass patch verification
	# (fail loud instead of silently shipping an unpatched/drifted vendor).
	/usr/bin/python3 app/patch_verify.py \
	    --vendor debian/tt-local-generator/usr/lib/tt-local-generator/vendor/tt-inference-server
```

(This runs from the repo root during the build, so `app/patch_verify.py` resolves. If the staged vendor is unpatched, the inject anchor is present but the *injects* are not — verify checks the ANCHOR exists, which it does in an unpatched tree, so this gate confirms the anchor/catalog premises hold on the shipped tree. It does NOT assert the injects were applied — that is the CI apply step's job. If you want the gate to also assert the vendor was patched, additionally `grep -q tt_dit_patches_dir <staged run_docker_server.py>` and fail if absent; add this only if it does not overreach the spec — note the decision in your report.)

- [ ] **Step 5: Run to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_regression_guards.py -v` (the three new + the pre-existing all pass). Validate YAML/make syntax as best you can headlessly: `/usr/bin/python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release-deb.yml'))"` (if `pyyaml` present; else skip and report) and `make -n -f debian/rules 2>/dev/null || true` (best-effort).

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release-deb.yml debian/rules tests/test_regression_guards.py
git commit -m "feat(patches): CI applies+verifies patches at build; debian/rules verify-before-ship"
```

---

## Task 6: Surface verify status in `bin/quickstart.sh`

**Files:**
- Modify: `bin/quickstart.sh` (the "Patches applied?" status check, `:275-291`)

**Interfaces:**
- Consumes: `app/patch_verify.py` CLI.

**Context:** `quickstart.sh:275-282` checks whether patches are applied by grepping `run_docker_server.py` for `tt_dit_patches_dir`, using `pass`/`fail_s`/`info` helpers. Add a second status line that runs the verifier and reports pass/fail, so the dev status board surfaces drift.

- [ ] **Step 1: Add the verify status line**

After the existing "Patches applied" pass/fail block (`:275-282`), add (matching the file's helper names — confirm they are `pass`/`fail_s`/`info` before using):

```bash
# Patch premises still hold? (fail-loud verify — catches drift the apply step
# would otherwise hit.)
_vroot="$REPO_ROOT/vendor/tt-inference-server"
if [[ -d "$_vroot" ]] && /usr/bin/python3 "$REPO_ROOT/app/patch_verify.py" --vendor "$_vroot" >/dev/null 2>&1; then
    pass "Patch premises verified"
elif [[ -d "$_vroot" ]]; then
    fail_s "Patch drift detected"
    info "Fix: /usr/bin/python3 app/patch_verify.py --vendor vendor/tt-inference-server"
fi
```

- [ ] **Step 2: Verify shape**

Run: `bash -n bin/quickstart.sh` → syntax clean. Confirm the helper names (`pass`/`fail_s`/`info`) match the file's actual definitions (grep them first); if they differ, use the file's real helpers. Report the helper names you found.

- [ ] **Step 3: Commit**

```bash
git add bin/quickstart.sh
git commit -m "feat(patches): surface patch-verify status in quickstart"
```

---

## Task 7: Finalize — version, changelog, docs, full suite

**Files:**
- Modify: `VERSION`; `debian/changelog`; `CLAUDE.md`; `INSTALL_deb.md`; `docs/UPGRADE.md`

**Interfaces:** consumes everything above.

- [ ] **Step 1: Bump `VERSION`**

Set `VERSION` (single line) to:

```
0.77.0
```

- [ ] **Step 2: Prepend a `debian/changelog` stanza**

New top stanza, version `0.77.0`, distribution `noble`, summarizing: a fail-loud patch-verification harness (`app/patch_manifest.py` + `app/patch_verify.py`) gating `apply_patches.sh`; CI now applies+verifies patches at build time so the shipped `.deb` vendor is patched (previously it shipped unpatched); `debian/rules` verify-before-ship gate; `vendor/VENDOR_VERSION` stamp; quickstart surfaces drift. Match the trailer format of the existing top stanza (maintainer + RFC-2822 date line).

- [ ] **Step 3: Update docs**

- `CLAUDE.md` — add a short "Patch verification (v0.77.0)" note under the "Vendored `tt-inference-server`" section: the manifest + verifier, the `apply_patches.sh` gate, that CI applies+verifies at build time (the shipped `.deb` vendor is now patched, closing the prior unpatched-vendor gap), the `debian/rules` verify-before-ship gate, and that image-diff drift detection remains a declared follow-up (the `bind_mount` `dest`/`version_ceiling` are its hooks).
- `INSTALL_deb.md` — note the shipped `.deb` vendor is patched+verified at build time; a LOCAL `dpkg-buildpackage` must run `bin/apply_patches.sh vendor/tt-inference-server` first (or `snapshot_vendor.sh` then apply) or the build fails the verify-before-ship gate.
- `docs/UPGRADE.md` — when bumping the vendored version/SHA, update `vendor/VENDOR_VERSION` too, and run `app/patch_verify.py --vendor vendor/tt-inference-server` to catch anchors/catalog paths the new upstream moved (the drift signal); a `version_ceiling` warning means a patch may now be absorbed upstream and can potentially be dropped.

- [ ] **Step 4: Full suite (documented deselects)**

```bash
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q \
  --deselect tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module \
  --deselect tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes \
  --deselect tests/test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen
```

Expected: green (plus the `test_regression_guards` env-skip when `docs/assets/` is absent). Also run the real-tree smoke: `/usr/bin/python3 app/patch_verify.py --manifest-only` → exit 0.

- [ ] **Step 5: Commit**

```bash
git add VERSION debian/changelog CLAUDE.md INSTALL_deb.md docs/UPGRADE.md
git commit -m "chore: VERSION 0.77.0 + changelog + docs for the patch-verification harness"
```

---

## Self-Review (plan author)

**Spec coverage:**
- Component A (`patch_manifest.py`) → Task 1. ✓
- Component B (`patch_verify.py`: PatchError, version_at_most, verify/require, ProbeResult, CLI) → Task 2 (+ VENDOR_VERSION read in Task 4). ✓
- Component C (`apply_patches.sh` gate; per-step aborts already exist) → Task 3. ✓
- Component D (CI apply+verify; `debian/rules` verify-before-ship; module packaging; quickstart; VENDOR_VERSION; docs) → Tasks 4, 5, 6, 7. ✓
- Testing section (manifest consistency, anchor present/absent, append-target missing, broken bind_mount, version_at_most boundaries, absorbed-patch warning, packaging membership) → Tasks 1/2/4/5. ✓
- Deferred (image-diff, in-container harness) → not implemented; `dest`/`version_ceiling` hooks present. ✓
- VERSION 0.77.0 + changelog → Task 7. ✓

**Placeholder scan:** All code is concrete. The one judgment call (whether the `debian/rules` gate should also grep for `tt_dit_patches_dir` to assert the injects were applied) is flagged in-task with a decision instruction + report requirement, not left vague.

**Type/name consistency:** `PatchEntry` fields (`id, kind, target, anchors, dest, premise, version_ceiling`) are identical across Tasks 1/2/4. `ProbeResult(id, ok, level, message)` and the `level` filter (`"error"` for hard-fail, `"warning"` for soft) are consistent across `verify`/`require`/`main` and the tests. `_current_vendor_version` reads `vendor_root.parent / "VENDOR_VERSION"`, matching where Task 4 writes it.
