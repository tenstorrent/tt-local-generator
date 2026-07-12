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
