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
from typing import Callable, Optional

import server_manager

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
        if str(status_of(key)).lower() in ("ready", "starting"):
            return key
    return None


@dataclass(frozen=True)
class SwitchPlan:
    target: Optional[str]     # server to start (None -> nothing to do)
    conflict: Optional[str]   # running server to stop first (None -> none)
    needs_reset: bool         # tt-smi -r required (True iff a conflict is stopped)


def plan_switch(selected_key: Optional[str], status_of: Callable[[str], str]) -> SwitchPlan:
    target = required_server(selected_key)
    if target is None:
        return SwitchPlan(None, None, False)
    conflict = conflicting_server(target, status_of)
    return SwitchPlan(target, conflict, conflict is not None)
