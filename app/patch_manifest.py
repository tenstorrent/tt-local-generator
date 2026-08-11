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
