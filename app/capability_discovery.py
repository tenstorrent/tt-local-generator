# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Capability discovery — the pure core behind Pipeline Studio's dynamic
"add a step" list (SP-C Phase 2b-2 Task 1).

Where intent_vocab.py answers "what does this native class_type do?", this
module answers the composer's real question: "given the artifact kind the
previous step just produced, what steps can plausibly come next — right now,
on this machine?" That list is built from three sources:

  1. Native engine intents      — intent_vocab.compatible_intents(output_kind)
  2. Plugin capabilities        — parsed from each plugins/<name>/mcp.json
  3. Live server/hardware health — is a capability's backend actually up?

This module has NO GTK imports and does no real disk/network/subprocess I/O
itself — every external dependency (`mcp_reader`, `is_plugin_loaded`,
`is_backend_up`) is injected, so `discover_capabilities` is unit-testable
with plain fakes. `default_capabilities()` at the bottom is the thin
real-deps wrapper the UI actually calls; it is intentionally the only
function here that imports artgen/server_manager or touches the filesystem.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from intent_vocab import INTENTS, Intent, compatible_intents, label as _label


@dataclass(frozen=True)
class Capability:
    """One candidate "add a step" entry offered by the composer.

    id         — stable identifier: the class_type for native capabilities,
                 the plugin's tool name for plugin capabilities.
    label      — human-facing text: the intent's verb+noun label for native
                 capabilities, the plugin tool's `description` for plugins.
    kind_out   — the artifact KIND this capability's step would produce.
    kind_in    — the artifact KIND this capability consumes as its primary
                 input, or None for source/seed capabilities that don't wire
                 an upstream artifact in (e.g. TTLGAddToPlaylist, and every
                 artgen-style plugin, which takes a theme/text seed rather
                 than a piped-in artifact).
    source     — "native" (engine intent) or "plugin" (mcp.json-declared).
    class_type — the engine class_type that would actually run this step.
                 Always "TTLGArtgenGenerate" for plugin capabilities (every
                 plugin runs through the generic artgen-generate node).
    plugin     — the plugin name, or None for native capabilities.
    hardware   — the hardware/backend requirement, or None when the
                 capability needs no dedicated backend (CPU-only natives,
                 CPU-only plugins). Native media-backed intents (image/video
                 output) report their backend family here too ("media") so
                 the UI can render one consistent "needs X" affordance
                 regardless of source.
    live       — True if this capability can be used right now.
    reason     — human-readable explanation when `live` is False (e.g.
                 "start a blackhole model", "plugin not available"); None
                 when live.
    """

    id: str
    label: str
    kind_out: Optional[str]
    kind_in: Optional[str]
    source: str  # "native" | "plugin"
    class_type: str
    plugin: Optional[str]
    hardware: Optional[str]
    live: bool
    reason: Optional[str]


# ── Native intent → backend family ────────────────────────────────────────────
#
# Which server family (if any) a native class_type needs before it can
# actually run. Intents absent from this table are CPU-only (or otherwise
# always-available) and are always live: TTLGCaptionImage, TTLGRemoveBackground,
# TTLGEstimateDepth, TTLGPromptCompose, TTLGAddToPlaylist, TTLGComposite,
# TTLGSVGRender.
_NATIVE_BACKEND_FAMILY: dict = {
    "TTLGTextToImage": "media",     # image server (FLUX/SDXL/...)
    "TTLGImageToVideo": "media",    # video server (Wan2.2/SkyReels/Mochi)
    "TTLGAnimateDiff": "media",     # hardware-accelerated animate backend
    "TTLGGenerateText": "llm",      # chat/text LLM (artgen/prompt-gen)
    "TTLGArtgenGenerate": "llm",    # artgen is LLM-backed by default
}

_PLUGIN_CLASS_TYPE = "TTLGArtgenGenerate"


def _native_reason(family: str) -> str:
    return f"start a {family} model"


def _plugin_reason(*, loaded: bool, hardware: Optional[str]) -> str:
    if not loaded:
        return "plugin not available"
    return _native_reason(hardware)


# ── Plugin manifest parsing ───────────────────────────────────────────────────


def load_plugin_capabilities(mcp_reader: Callable[[], dict]) -> list[dict]:
    """Parse every plugin's mcp.json into a raw plugin-capability dict.

    `mcp_reader()` -> {plugin_name: mcp_dict}, exactly the shape produced by
    reading plugins/<name>/mcp.json for every plugin directory.

    Returns [] (never raises) if `mcp_reader` raises or a given manifest is
    malformed — callers fall back to native-only capabilities in that case.

    Each returned dict has keys: id, label, kind_out, kind_in, plugin,
    hardware. `kind_in` defaults to None (a "seed" capability — it takes a
    theme/text prompt, not a piped-in artifact) which is the case for every
    artgen-style plugin today; `accepts_remix_from` is where a future task
    could derive a real kind_in for plugins that *do* consume a specific
    artifact kind.
    """
    try:
        raw_map = mcp_reader() or {}
    except Exception:
        return []

    caps: list[dict] = []
    for plugin_name, manifest in raw_map.items():
        try:
            xt = manifest.get("x-ttlg", {}) or {}
            tools = manifest.get("tools") or []
            tool0 = tools[0] if tools else {}

            tool_name = tool0.get("name") or plugin_name
            tool_label = tool0.get("description") or tool_name
            kind_out = xt.get("media_type")
            hardware = xt.get("hardware")
            accepts_remix_from = tuple(xt.get("accepts_remix_from") or ())

            caps.append({
                "id": tool_name,
                "label": tool_label,
                "kind_out": kind_out,
                "kind_in": None,  # loose/seed input — see docstring
                "plugin": plugin_name,
                "hardware": hardware,
                "accepts_remix_from": accepts_remix_from,
            })
        except Exception:
            # One malformed manifest must not take down the whole list.
            continue
    return caps


# ── Discovery core ─────────────────────────────────────────────────────────────


def _native_capabilities(
    output_kind: str,
    *,
    is_backend_up: Callable[[str], bool],
    native,
) -> list[Capability]:
    if native is None:
        intents = compatible_intents(output_kind)
    elif isinstance(native, dict):
        intents = [i for i in native.values() if i.input_kind == output_kind]
    else:
        intents = [i for i in native if i.input_kind == output_kind]

    caps: list[Capability] = []
    for intent in intents:
        family = _NATIVE_BACKEND_FAMILY.get(intent.class_type)
        if family is None:
            live, reason = True, None
        else:
            try:
                up = bool(is_backend_up(family))
            except Exception:
                up = False
            live = up
            reason = None if up else _native_reason(family)

        caps.append(Capability(
            id=intent.class_type,
            label=_label(intent.class_type),
            kind_out=intent.output_kind,
            kind_in=intent.input_kind,
            source="native",
            class_type=intent.class_type,
            plugin=None,
            hardware="media" if family == "media" else None,
            live=live,
            reason=reason,
        ))
    return caps


def _plugin_capabilities(
    output_kind: str,
    *,
    is_plugin_loaded: Callable[[str], bool],
    is_backend_up: Callable[[str], bool],
    mcp_reader: Callable[[], dict],
) -> list[Capability]:
    raw_caps = load_plugin_capabilities(mcp_reader)

    caps: list[Capability] = []
    for raw in raw_caps:
        kind_in = raw.get("kind_in")
        # Offer a plugin cap when it's a loose/seed capability (kind_in is
        # None) or when it explicitly consumes this output_kind.
        if not (kind_in is None or kind_in == output_kind):
            continue

        plugin_name = raw["plugin"]
        hardware = raw.get("hardware")

        try:
            loaded = bool(is_plugin_loaded(plugin_name))
        except Exception:
            loaded = False

        if not loaded:
            live, reason = False, _plugin_reason(loaded=False, hardware=hardware)
        elif hardware is None:
            live, reason = True, None
        else:
            try:
                up = bool(is_backend_up(hardware))
            except Exception:
                up = False
            live = up
            reason = None if up else _plugin_reason(loaded=True, hardware=hardware)

        caps.append(Capability(
            id=raw["id"],
            label=raw["label"],
            kind_out=raw.get("kind_out"),
            kind_in=kind_in,
            source="plugin",
            class_type=_PLUGIN_CLASS_TYPE,
            plugin=plugin_name,
            hardware=hardware,
            live=live,
            reason=reason,
        ))
    return caps


def discover_capabilities(
    output_kind: str,
    *,
    is_plugin_loaded: Callable[[str], bool],
    is_backend_up: Callable[[str], bool],
    mcp_reader: Callable[[], dict],
    native=None,
) -> list[Capability]:
    """Return every capability that can follow a step producing `output_kind`.

    Pure core: all external state (which plugins are loaded, whether a
    backend is up, and the plugin manifest set) is injected so this function
    never touches real disk/hardware/network and is fully unit-testable.

    Order is deterministic: live capabilities first, then latent ones; each
    group preserves the underlying native/plugin discovery order (stable
    sort) so repeated calls with the same inputs return the same sequence.

    Robustness: a raising or empty `mcp_reader` degrades gracefully to
    native-only capabilities (compatible_intents is never empty for a valid
    output_kind that has at least one native consumer) rather than crashing
    or returning nothing.
    """
    caps = _native_capabilities(output_kind, is_backend_up=is_backend_up, native=native)
    caps += _plugin_capabilities(
        output_kind,
        is_plugin_loaded=is_plugin_loaded,
        is_backend_up=is_backend_up,
        mcp_reader=mcp_reader,
    )
    # Stable sort: live (False -> "not latent") before latent (True).
    caps.sort(key=lambda c: not c.live)
    return caps


# ── Real-deps wrapper ──────────────────────────────────────────────────────────


def _read_all_plugin_mcp() -> dict:
    """Real `mcp_reader`: read every plugins/<name>/mcp.json from disk."""
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    plugins_dir = repo_root / "plugins"
    out: dict = {}
    if not plugins_dir.is_dir():
        return out
    for plugin_dir in sorted(plugins_dir.iterdir()):
        if not plugin_dir.is_dir():
            continue
        manifest_path = plugin_dir / "mcp.json"
        if not manifest_path.exists():
            continue
        try:
            out[plugin_dir.name] = json.loads(manifest_path.read_text())
        except Exception:
            continue
    return out


def _real_is_plugin_loaded(name: str) -> bool:
    import artgen
    return name in artgen.all_names()


def _real_is_backend_up(family: str) -> bool:
    """Real `is_backend_up`: map a backend family/hardware tag to live health.

    "llm"   -> any chat model discoverable via artgen.detect_artgen_endpoint().
    "media" -> any image/video/animate server_manager service is healthy.
    anything else (a specific hardware tag like "blackhole") -> best-effort:
      any managed server is healthy (there's no per-chip health probe yet;
      refine this once server_manager exposes hardware-level status).
    """
    import artgen
    import server_manager as sm

    if family == "llm":
        base_url, _model = artgen.detect_artgen_endpoint()
        return bool(base_url)

    status = sm.status_all()
    if family == "media":
        media_keys = [
            key for key, sdef in sm.SERVERS.items()
            if set(sdef.capabilities) & {"image", "video", "animate"}
        ]
        return any(status.get(key) for key in media_keys)

    # Unknown/specific hardware tag — best-effort until per-hardware health
    # exists: consider it up if anything managed is currently healthy.
    return any(status.values())


def default_capabilities(output_kind: str) -> list[Capability]:
    """The real-deps wrapper `MainWindow`/composer UI calls.

    Thin by design — all real logic lives in `discover_capabilities`; this
    function only wires up the three real dependencies (mcp.json files on
    disk, artgen's plugin registry, server_manager's health checks).
    """
    return discover_capabilities(
        output_kind,
        is_plugin_loaded=_real_is_plugin_loaded,
        is_backend_up=_real_is_backend_up,
        mcp_reader=_read_all_plugin_mcp,
    )
