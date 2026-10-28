"""Ready-to-Run: pure decisions tying Create/pipeline intent to server state.

GUI-free. Given the selected model key and a way to read current server status,
decide which server must be running for a job and which currently-running server
(sharing the same Blackhole chips) would have to be stopped + reset first.

HARDWARE NOTE: backend-switch churn is risky on this hardware (a QB2 card has a
recurring ARC-NOC failure that has hard-locked the box on churn), so any switch a
plan describes MUST be user-confirmed before execution — never auto-run. This
module only DECIDES; it never touches hardware.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

import server_manager

if TYPE_CHECKING:  # no runtime import: server_registry imports this module
    from server_registry import ModelEntry

# Mutually-exclusive hardware groups today: the diffusion/media server on
# port 8000 (video | image | animate) and the artgen LLM on port 8002 (artgen).
# Switching WITHIN a group means stopping the incumbent + resetting the boards.
_MEDIA_CAPS = frozenset({"video", "image", "animate"})
_ARTGEN_CAPS = frozenset({"artgen"})


def _group_of(key: str) -> Optional[str]:
    sdef = server_manager.SERVERS.get(key)
    if sdef is None:
        return None
    caps = frozenset(sdef.capabilities)
    if caps & _MEDIA_CAPS:
        return "media"
    if caps & _ARTGEN_CAPS:
        return "artgen"
    return None


def required_server(selected_key: Optional[str]) -> Optional[str]:
    """The server that must run for `selected_key`, or None when nothing needs
    starting: a None/empty selection, or a synthetic/detected key with no
    `server_manager.SERVERS` entry (e.g. "animatediff", detected sentinels)."""
    if not selected_key:
        return None
    return selected_key if selected_key in server_manager.SERVERS else None


def conflicting_server(target_key: str, status_of: Callable[[str], str]) -> Optional[str]:
    """A READY/STARTING server sharing `target_key`'s hardware group (so it must
    be stopped + the boards reset before target starts), or None."""
    group = _group_of(target_key)
    if group is None:
        return None
    for key in server_manager.SERVERS:
        if key == target_key or _group_of(key) != group:
            continue
        # NOTE: `.lower()` directly on the status value, NOT `str(status_of(key))`.
        # `model_status.Status` is a `(str, Enum)` whose `__str__` (Python 3.11+)
        # returns "Status.READY", not "ready" — wrapping in str() first would
        # silently defeat conflict detection for every REAL caller (the live
        # `_status_service.status()` returns `Status` members), even though the
        # plain-string status_of fakes in tests/test_ready_to_run.py never
        # exercised this path. `.lower()` operates on the underlying str data on
        # both a plain str and a Status member, so this works for either.
        if status_of(key).lower() in ("ready", "starting"):
            return key
    return None


@dataclass(frozen=True)
class SwitchPlan:
    target: Optional[str]     # server to start (None -> nothing to do)
    conflict: Optional[str]   # running server to stop first (None -> none)
    needs_reset: bool         # tt-smi -r required (True iff a conflict is stopped)
    # True when the target is a registry model whose hardware group is not known:
    # `conflict` None then means "could not tell", not "nothing in the way". The
    # caller (ServerControl) must warn and ask before starting. Default False so
    # existing constructors and the registry-less path are unchanged.
    unknown: bool = False


def _plan_with_registry(selected_key: str, status_of: Callable[[str], str],
                        registry: "dict[str, ModelEntry]") -> SwitchPlan:
    entry = registry[selected_key]
    group = entry.hardware_group
    if group is None:
        # A boutique model with no group (prompt-server, CPU only) shares nothing.
        # A `tt` model with no group is unknown: say so rather than guess.
        return SwitchPlan(selected_key, None, False, unknown=(entry.backend == "tt"))
    # Candidates: SERVERS keys the registry does not describe (group from
    # _group_of), then every registry entry (group from the entry, which may be
    # overridden). A boutique entry's key is its SERVERS key, so none repeats.
    cands = [(k, _group_of(k)) for k in server_manager.SERVERS if k not in registry]
    cands += [(k, e.hardware_group) for k, e in registry.items()]
    for key, g in cands:
        if key == selected_key or g != group:
            continue
        if status_of(key).lower() in ("ready", "starting"):  # see conflicting_server
            return SwitchPlan(selected_key, key, True)
    return SwitchPlan(selected_key, None, False)


def plan_switch(selected_key: Optional[str], status_of: Callable[[str], str],
                *, registry: "Optional[dict[str, ModelEntry]]" = None) -> SwitchPlan:
    """With `registry=None` (default) this is the original SERVERS-only logic.
    With a registry, a key it holds takes its hardware group from the entry and
    conflicts are searched across SERVERS and registry entries. A key the
    registry lacks falls through to the original logic."""
    if registry and selected_key and selected_key in registry:
        return _plan_with_registry(selected_key, status_of, registry)
    target = required_server(selected_key)
    if target is None:
        return SwitchPlan(None, None, False)
    conflict = conflicting_server(target, status_of)
    return SwitchPlan(target, conflict, conflict is not None)
