"""At-a-glance status-bar segments, resolved by FUNCTION rather than by model.

The bottom status bar used to show ONE aggregate dot whose state was
``READY > STARTING > ERROR > OFF`` folded across every key in
``server_manager.SERVERS``.  That aggregation was actively misleading: the
CPU-only ``prompt-server`` is auto-started on app launch (see
``MainWindow._autostart_prompt_server``) and goes READY within seconds, so the
aggregate read "ready" forever — while Video was off, while Image was mid-load,
while anything at all was happening.  It also stomped the elapsed "starting…"
timer, because the very next poll folded prompt-server's READY back over it.

Four independent segments have nothing to aggregate, so no segment can ever
speak for another one.  This module is the GTK-free half: the segment table and
the pure state resolver.  ``main_window._StatusBar`` owns the widgets.

Keep this module GTK-free (stdlib + ``server_manager``/``model_status`` only)
so it stays unit-testable without a display, in the same spirit as
``ready_to_run.py`` and ``model_status.py``.
"""

from __future__ import annotations

from model_status import Status

import server_manager as _sm


# ── The segment table ────────────────────────────────────────────────────────
#
# (segment key, display name, capabilities folded into this segment)
#
# "animate" folds into Video deliberately: Wan2.2-Animate is a video model, and
# the Create surface has presented it as one since the v0.61.0 "Video is Video"
# merge.  A separate Animate segment here would contradict the picker.
#
# "animatediff" is NOT a segment.  It has no `server_manager.SERVERS` entry —
# it's a local Blackhole hardware capability with nothing to start — so there is
# no live service state for the bar to report (`_check_animatediff_hardware`
# still fills its row in the detail popover).
SEGMENTS: tuple = (
    ("prompt", "Prompt",  ("prompt",)),
    ("image",  "Image",   ("image",)),
    ("video",  "Video",   ("video", "animate")),
    ("artgen", "Art LLM", ("artgen",)),
)

#: Segment keys in display order — the order they appear left-to-right.
SEGMENT_KEYS: tuple = tuple(key for key, _label, _caps in SEGMENTS)

#: State glyph.  Mirrors CreateView's own ``◌/◐/●`` model-dot convention so a
#: segment and a model picker entry never disagree about what a shape means.
#: The glyph carries the state as well as the colour does, so the bar stays
#: readable without relying on hue.
GLYPHS: dict = {
    Status.READY:    "●",   # ● filled
    Status.STARTING: "◐",   # ◐ half
    Status.ERROR:    "✕",   # ✕ cross
    Status.OFF:      "○",   # ○ hollow
}

#: CSS class suffix per state — consumed as ``tt-statusbar-dot-<suffix>`` /
#: ``tt-statusbar-segname-<suffix>`` by ``_StatusBar``.  The four dot classes
#: already exist in the app stylesheet; nothing new is invented here.
CSS_STATES: dict = {
    Status.READY:    "ready",
    Status.STARTING: "starting",
    Status.ERROR:    "error",
    Status.OFF:      "offline",
}

# Resolution precedence WITHIN one segment.  A segment is READY if any server
# backing it is ready, else STARTING if any is launching, else ERROR if any
# failed, else OFF.  This is the same policy the old aggregate used — the fix is
# that it now applies only across servers that actually serve the SAME
# function, never across unrelated ones.
_PRECEDENCE: tuple = (Status.READY, Status.STARTING, Status.ERROR)


def segment_states(
    snap: dict,
    *,
    artgen_detected: bool = False,
    servers_for_capability=None,
) -> dict:
    """Resolve one :class:`~model_status.Status` per segment.

    Args:
        snap: a ``ModelStatusService.snapshot()`` — ``{server key: Status}``.
        artgen_detected: True when ``ModelStatusService.running_artgen_model()``
            resolved a live chat endpoint.  Folded into the "artgen" segment so
            a chat model started outside this app — one that matches no
            registered ``ServerDef``, and therefore leaves every artgen key
            legitimately OFF in ``snap`` (see ``model_status._tick``) — still
            reads as on.  CreateView already surfaces exactly this case as a
            selectable "(detected)" entry; the bar must not disagree with it.
        servers_for_capability: injection seam for tests; defaults to
            ``server_manager.servers_for_capability``.

    Returns:
        ``{segment key: Status}`` with an entry for every key in
        :data:`SEGMENT_KEYS` — always all four, so the bar never has to hide or
        reflow a segment.
    """
    lookup = servers_for_capability or _sm.servers_for_capability

    out: dict = {}
    for key, _label, caps in SEGMENTS:
        statuses = [
            snap.get(sdef.key, Status.OFF)
            for cap in caps
            for sdef in lookup(cap)
        ]
        state = Status.OFF
        for candidate in _PRECEDENCE:
            if candidate in statuses:
                state = candidate
                break
        if key == "artgen" and artgen_detected and state != Status.READY:
            state = Status.READY
        out[key] = state
    return out


def glyph_for(state: "Status") -> str:
    """Display glyph for a segment state (``○`` for anything unrecognised)."""
    return GLYPHS.get(state, GLYPHS[Status.OFF])


def css_state_for(state: "Status") -> str:
    """CSS class suffix for a segment state (``offline`` for unrecognised)."""
    return CSS_STATES.get(state, CSS_STATES[Status.OFF])


def segment_for_server_key(key: str, servers_for_capability=None) -> "str | None":
    """Which segment a ``server_manager.SERVERS`` key belongs to.

    Used for optimistic, click-time feedback: the start/stop handlers know the
    key they are acting on and can light that one segment immediately, without
    waiting a poll interval for ``ModelStatusService`` to catch up.

    Returns ``None`` for a key that backs no segment (or an unresolvable
    script name), in which case the caller should simply skip the optimistic
    update and let the next snapshot speak.
    """
    lookup = servers_for_capability or _sm.servers_for_capability
    for seg_key, _label, caps in SEGMENTS:
        for cap in caps:
            if any(sdef.key == key for sdef in lookup(cap)):
                return seg_key
    return None
