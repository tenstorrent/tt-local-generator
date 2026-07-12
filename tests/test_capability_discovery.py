# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for capability_discovery.py — the pure capability-discovery core of the
Pipeline Studio composer's "add a step" list (SP-C Phase 2b-2 Task 1).

Everything here is pure: `is_plugin_loaded`, `is_backend_up`, and `mcp_reader`
are injected fakes.  No real disk/hardware/network access.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import capability_discovery as cd


# ── Fixtures: two tiny fake plugin manifests ──────────────────────────────────
#
#   textcpu — CPU-only text plugin (hardware: null, media_type: text)
#   vidhw   — hardware-backed video plugin (hardware: "blackhole", media_type: video)

def _fake_mcp_reader():
    return {
        "textcpu": {
            "x-ttlg": {
                "output_ext": ".txt",
                "media_type": "text",
                "accepts_remix_from": [],
                "can_remix_to": ["image"],
                "tab": "generative-art",
                "hardware": None,
            },
            "tools": [
                {
                    "name": "textcpu",
                    "description": "CPU text plugin: no hardware required",
                    "inputSchema": {"type": "object", "properties": {}, "required": []},
                }
            ],
        },
        "vidhw": {
            "x-ttlg": {
                "output_ext": ".mp4",
                "media_type": "video",
                "accepts_remix_from": ["image"],
                "can_remix_to": [],
                "tab": "generative-art",
                "hardware": "blackhole",
            },
            "tools": [
                {
                    "name": "vidhw",
                    "description": "Blackhole-accelerated video plugin",
                    "inputSchema": {"type": "object", "properties": {}, "required": []},
                }
            ],
        },
    }


def _raising_mcp_reader():
    raise RuntimeError("disk unavailable")


def _empty_mcp_reader():
    return {}


# ── Fixture: normal generator plugin + a utility plugin (ffmpeg-like) ────────
#
# Utility plugins (blip, depth, ffmpeg, rmbg) are not standalone generators —
# their functionality is already exposed as native intents (TTLGCaptionImage,
# TTLGEstimateDepth, TTLGRemoveBackground). They declare `x-ttlg.utility: true`
# and must never surface as a Capability.

def _fake_mcp_reader_with_utility():
    return {
        "textcpu": {
            "x-ttlg": {
                "output_ext": ".txt",
                "media_type": "text",
                "accepts_remix_from": [],
                "can_remix_to": ["image"],
                "tab": "generative-art",
                "hardware": None,
            },
            "tools": [
                {
                    "name": "textcpu",
                    "description": "CPU text plugin: no hardware required",
                    "inputSchema": {"type": "object", "properties": {}, "required": []},
                }
            ],
        },
        "ffmpeg": {
            "x-ttlg": {
                "utility": True,
                "output_ext": ".mp4",
                "media_type": "video",
                "accepts_remix_from": ["video"],
                "can_remix_to": [],
                "tab": "generative-art",
                "hardware": None,
            },
            "tools": [
                {
                    "name": "ffmpeg",
                    "description": "ffmpeg utility: not a standalone generator",
                    "inputSchema": {"type": "object", "properties": {}, "required": []},
                }
            ],
        },
    }


# ── load_plugin_capabilities ──────────────────────────────────────────────────


def test_load_plugin_capabilities_parses_both_fixtures():
    caps = cd.load_plugin_capabilities(_fake_mcp_reader)
    names = {c["plugin"] for c in caps}
    assert names == {"textcpu", "vidhw"}
    textcpu = next(c for c in caps if c["plugin"] == "textcpu")
    assert textcpu["kind_out"] == "text"
    assert textcpu["hardware"] is None
    assert "CPU text plugin" in textcpu["label"]
    vidhw = next(c for c in caps if c["plugin"] == "vidhw")
    assert vidhw["kind_out"] == "video"
    assert vidhw["hardware"] == "blackhole"


def test_load_plugin_capabilities_raising_reader_returns_empty():
    assert cd.load_plugin_capabilities(_raising_mcp_reader) == []


def test_load_plugin_capabilities_empty_reader_returns_empty():
    assert cd.load_plugin_capabilities(_empty_mcp_reader) == []


def test_load_plugin_capabilities_skips_utility_plugins():
    # Utility plugins (x-ttlg.utility: true) are not standalone generators —
    # their functionality is already exposed as native intents — so they must
    # never become a plugin-capability dict, mirroring plugin_loader.py's skip.
    caps = cd.load_plugin_capabilities(_fake_mcp_reader_with_utility)
    names = {c["plugin"] for c in caps}
    assert names == {"textcpu"}
    assert "ffmpeg" not in names


# ── Fixture: plugin directory name differs from its primary TOOL name ────────
#
# Real case (SP-C Phase-2b-2 final review, Fix 1): plugins/midi/ ships a tool
# named "generate_midi". plugin_loader.py registers the plugin under its
# PRIMARY TOOL name (the first tool with x-ttlg.artifact_tool=True, else
# tools[0]["name"]) — NOT the directory name. artgen.all_names() and
# `tt-ctl artgen <name>` resolve by that same tool name. A capability whose
# `plugin` field is the directory name ("midi") can never match
# is_plugin_loaded("midi") once the real plugin registry only knows
# "generate_midi", so it shows up permanently latent even though it's loaded.

def _fake_mcp_reader_dir_neq_tool():
    return {
        "midi": {
            "x-ttlg": {
                "output_ext": ".mid",
                "media_type": "text",
                "accepts_remix_from": [],
                "can_remix_to": [],
                "tab": "generative-art",
                "hardware": None,
            },
            "tools": [
                {
                    "name": "generate_midi",
                    "description": "Generate a MIDI composition",
                    "inputSchema": {"type": "object", "properties": {}, "required": []},
                }
            ],
        },
    }


def _fake_mcp_reader_multi_tool_artifact_tool():
    # Plugin declares a non-artifact helper tool FIRST and the real generator
    # tool (marked artifact_tool=True) second — plugin_loader.py's primary
    # selection must still pick the artifact_tool one, not tools[0].
    return {
        "midi": {
            "x-ttlg": {
                "output_ext": ".mid",
                "media_type": "text",
                "accepts_remix_from": [],
                "can_remix_to": [],
                "tab": "generative-art",
                "hardware": None,
            },
            "tools": [
                {
                    "name": "list_midi_presets",
                    "description": "List available MIDI presets",
                    "inputSchema": {"type": "object", "properties": {}, "required": []},
                },
                {
                    "name": "generate_midi",
                    "description": "Generate a MIDI composition",
                    "inputSchema": {"type": "object", "properties": {}, "required": []},
                    "x-ttlg": {"artifact_tool": True},
                },
            ],
        },
    }


def test_load_plugin_capabilities_uses_tool_name_not_dir_name_as_plugin_id():
    caps = cd.load_plugin_capabilities(_fake_mcp_reader_dir_neq_tool)
    assert len(caps) == 1
    cap = caps[0]
    assert cap["plugin"] == "generate_midi"
    assert cap["id"] == "generate_midi"


def test_load_plugin_capabilities_picks_artifact_tool_over_first_tool():
    caps = cd.load_plugin_capabilities(_fake_mcp_reader_multi_tool_artifact_tool)
    assert len(caps) == 1
    cap = caps[0]
    assert cap["plugin"] == "generate_midi"
    assert cap["id"] == "generate_midi"


def test_discover_capabilities_dir_neq_tool_name_is_live_when_tool_name_loaded():
    # The end-to-end regression: is_plugin_loaded is keyed by the TOOL name
    # (as artgen.all_names() really is) — the capability must come back LIVE,
    # not latent, and its `plugin` param must be the runnable tool name.
    caps = cd.discover_capabilities(
        "text",
        is_plugin_loaded=lambda n: n == "generate_midi",
        is_backend_up=lambda f: True,
        mcp_reader=_fake_mcp_reader_dir_neq_tool,
    )
    midi = next(c for c in caps if c.source == "plugin")
    assert midi.plugin == "generate_midi"
    assert midi.id == "generate_midi"
    assert midi.live is True
    assert midi.reason is None


def test_discover_capabilities_skips_utility_plugins():
    # Regression: previously utility plugins (blip/depth/ffmpeg/rmbg) surfaced
    # in the composer's add-a-step picker as permanently-latent "plugin not
    # available" duplicates of native intents. They must be filtered out
    # entirely — never appear as a Capability, live or latent.
    caps = cd.discover_capabilities(
        "video",
        is_plugin_loaded=lambda n: False,   # even "not loaded" must not matter
        is_backend_up=lambda f: False,
        mcp_reader=_fake_mcp_reader_with_utility,
    )
    plugin_names = {c.plugin for c in caps if c.source == "plugin"}
    assert "ffmpeg" not in plugin_names


# ── discover_capabilities: native intents ─────────────────────────────────────


def test_native_image_consumers_present():
    caps = cd.discover_capabilities(
        "image",
        is_plugin_loaded=lambda n: True,
        is_backend_up=lambda f: False,
        mcp_reader=_fake_mcp_reader,
    )
    native_class_types = {c.class_type for c in caps if c.source == "native"}
    # compatible_intents("image") includes these input_kind == "image" natives.
    assert "TTLGCaptionImage" in native_class_types
    assert "TTLGRemoveBackground" in native_class_types
    assert "TTLGImageToVideo" in native_class_types


def test_native_cpu_intents_always_live_regardless_of_backend():
    caps = cd.discover_capabilities(
        "image",
        is_plugin_loaded=lambda n: True,
        is_backend_up=lambda f: False,   # every backend down
        mcp_reader=_fake_mcp_reader,
    )
    caption = next(c for c in caps if c.class_type == "TTLGCaptionImage")
    rmbg = next(c for c in caps if c.class_type == "TTLGRemoveBackground")
    assert caption.live is True
    assert rmbg.live is True
    assert caption.reason is None


def test_native_media_backed_intent_latent_when_backend_down():
    caps = cd.discover_capabilities(
        "image",
        is_plugin_loaded=lambda n: True,
        is_backend_up=lambda f: False,
        mcp_reader=_fake_mcp_reader,
    )
    i2v = next(c for c in caps if c.class_type == "TTLGImageToVideo")
    assert i2v.live is False
    assert i2v.reason


def test_native_media_backed_intent_live_when_backend_up():
    caps = cd.discover_capabilities(
        "image",
        is_plugin_loaded=lambda n: True,
        is_backend_up=lambda f: True,
        mcp_reader=_fake_mcp_reader,
    )
    i2v = next(c for c in caps if c.class_type == "TTLGImageToVideo")
    assert i2v.live is True
    assert i2v.reason is None


# ── discover_capabilities: plugin capabilities ────────────────────────────────


def test_cpu_plugin_loaded_is_live():
    caps = cd.discover_capabilities(
        "image",
        is_plugin_loaded=lambda n: True,
        is_backend_up=lambda f: False,
        mcp_reader=_fake_mcp_reader,
    )
    textcpu = next(c for c in caps if c.source == "plugin" and c.plugin == "textcpu")
    assert textcpu.live is True
    assert textcpu.reason is None
    assert textcpu.hardware is None
    assert textcpu.class_type == "TTLGArtgenGenerate"


def test_hardware_plugin_backend_down_is_latent_with_reason():
    caps = cd.discover_capabilities(
        "image",
        is_plugin_loaded=lambda n: True,
        is_backend_up=lambda f: False,
        mcp_reader=_fake_mcp_reader,
    )
    vidhw = next(c for c in caps if c.source == "plugin" and c.plugin == "vidhw")
    assert vidhw.live is False
    assert vidhw.reason
    assert "start" in vidhw.reason.lower()
    assert "blackhole" in vidhw.reason.lower()


def test_hardware_plugin_backend_up_is_live():
    caps = cd.discover_capabilities(
        "image",
        is_plugin_loaded=lambda n: True,
        is_backend_up=lambda f: True,
        mcp_reader=_fake_mcp_reader,
    )
    vidhw = next(c for c in caps if c.source == "plugin" and c.plugin == "vidhw")
    assert vidhw.live is True
    assert vidhw.reason is None


def test_unloaded_plugin_is_latent_with_reason():
    caps = cd.discover_capabilities(
        "image",
        is_plugin_loaded=lambda n: False,
        is_backend_up=lambda f: True,
        mcp_reader=_fake_mcp_reader,
    )
    textcpu = next(c for c in caps if c.source == "plugin" and c.plugin == "textcpu")
    vidhw = next(c for c in caps if c.source == "plugin" and c.plugin == "vidhw")
    assert textcpu.live is False
    assert vidhw.live is False
    assert textcpu.reason and "not available" in textcpu.reason.lower()


# ── ordering + robustness ─────────────────────────────────────────────────────


def test_live_before_latent_ordering():
    caps = cd.discover_capabilities(
        "image",
        is_plugin_loaded=lambda n: True,
        is_backend_up=lambda f: False,
        mcp_reader=_fake_mcp_reader,
    )
    seen_latent = False
    for c in caps:
        if not c.live:
            seen_latent = True
        else:
            assert not seen_latent, f"live capability {c.id!r} appeared after a latent one"


def test_raising_mcp_reader_falls_back_to_native_only_non_empty():
    caps = cd.discover_capabilities(
        "image",
        is_plugin_loaded=lambda n: True,
        is_backend_up=lambda f: True,
        mcp_reader=_raising_mcp_reader,
    )
    assert caps
    assert all(c.source == "native" for c in caps)


def test_empty_mcp_reader_falls_back_to_native_only_non_empty():
    caps = cd.discover_capabilities(
        "image",
        is_plugin_loaded=lambda n: True,
        is_backend_up=lambda f: True,
        mcp_reader=_empty_mcp_reader,
    )
    assert caps
    assert all(c.source == "native" for c in caps)


def test_output_kind_with_no_native_consumers_still_returns_plugin_caps():
    # "playlist" has no native input_kind consumers among INTENTS, but plugin
    # caps are loose (kind_in=None) so they still show up as candidates.
    caps = cd.discover_capabilities(
        "playlist",
        is_plugin_loaded=lambda n: True,
        is_backend_up=lambda f: True,
        mcp_reader=_fake_mcp_reader,
    )
    assert any(c.source == "plugin" for c in caps)


# ── default_capabilities: thin real-deps wrapper ──────────────────────────────


def test_default_capabilities_is_thin_wrapper_and_returns_native_caps():
    with patch("server_manager.status_all", return_value={}), \
         patch("artgen.detect_artgen_endpoint", return_value=(None, None)):
        caps = cd.default_capabilities("image")
    assert isinstance(caps, list)
    assert caps
    assert any(c.source == "native" for c in caps)
