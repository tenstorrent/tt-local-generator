"""Fail-loud, host-side verification of the tt-inference-server patch surface.

Borrows the philosophy (and the two pure, dependency-free helpers PatchError +
version_at_most) of tt-vscode-toolkit's tt_patches.py, but does NOT do in-process
attribute patching — our patches stay on bind-mounts. This module only ASSERTS
that each patch's premise still holds against a vendored tree, so drift fails
loud instead of shipping silently. Pure/stdlib-only.
"""
from __future__ import annotations

import argparse
import py_compile
import sys
from dataclasses import dataclass
from pathlib import Path

import patch_manifest as pm

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
