# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Tests for wingit.py — the pure "wing it" free-form-text -> step mapper
(SP-C Phase 2b-3 Task 1).

Everything here is pure: `llm_fn` is a plain fake (no network), and the
Capability list is hand-built rather than discovered. Three fixture
capabilities exercise the three shapes the mapper has to handle:

  cap_video  — native, image -> video (kind_in="image", not a text input)
  cap_text   — native, text -> text (kind_in="text")
  cap_verse  — plugin (source="plugin", plugin="verse"), seed capability
               (kind_in=None) that always runs through TTLGArtgenGenerate

A fourth, non-live capability (cap_latent) is used to prove that a
capability needing a backend that isn't up is never offered to the LLM and
never picked by the fallback, even when it would otherwise be the best kind
match.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from capability_discovery import Capability
from wingit import WingitResult, map_freeform_to_step, map_freeform_to_pipeline


def _cap(id, kind_out, kind_in, source, class_type, plugin=None, live=True):
    return Capability(
        id=id, label=f"label-{id}", kind_out=kind_out, kind_in=kind_in,
        source=source, class_type=class_type, plugin=plugin,
        hardware=None, live=live, reason=None,
    )


cap_video = _cap("TTLGImageToVideo", "video", "image", "native", "TTLGImageToVideo")
cap_text = _cap("TTLGGenerateText", "text", "text", "native", "TTLGGenerateText")
cap_verse = _cap("verse", "text", None, "plugin", "TTLGArtgenGenerate", plugin="verse")
cap_latent = _cap(
    "TTLGAnimateDiff", "gif", "text", "native", "TTLGAnimateDiff", live=False,
)

ALL_CAPS = [cap_video, cap_text, cap_verse, cap_latent]


# ── llm path ───────────────────────────────────────────────────────────────────


def test_llm_valid_json_picks_named_capability_with_params():
    def llm_fn(prompt):
        assert "verse" in prompt  # the live plugin cap must be in the listing
        assert "make it a poem" in prompt
        return '{"capability_id": "verse", "params": {"theme": "cyberpunk city"}}'

    result = map_freeform_to_step(
        "make it a poem", None, ALL_CAPS, llm_fn=llm_fn,
    )

    assert result == WingitResult(
        class_type="TTLGArtgenGenerate",
        params={"theme": "cyberpunk city", "plugin": "verse"},
        capability_id="verse",
        via="llm",
    )


def test_llm_prose_and_fenced_json_still_parses():
    def llm_fn(prompt):
        return (
            "Sure, here you go:\n"
            "```json\n"
            '{"capability_id": "TTLGImageToVideo", "params": {"strength": 0.5}}\n'
            "```\n"
            "Hope that helps!"
        )

    result = map_freeform_to_step(
        "turn this into a clip", "image", ALL_CAPS, llm_fn=llm_fn,
    )

    assert result == WingitResult(
        class_type="TTLGImageToVideo",
        params={"strength": 0.5},
        capability_id="TTLGImageToVideo",
        via="llm",
    )


def test_llm_think_block_wrapped_json_still_parses():
    def llm_fn(prompt):
        return (
            "<think>the user wants text, so cap_text fits best</think>"
            '{"capability_id": "TTLGGenerateText", "params": {}}'
        )

    result = map_freeform_to_step(
        "write a caption", "text", ALL_CAPS, llm_fn=llm_fn,
    )

    assert result.via == "llm"
    assert result.capability_id == "TTLGGenerateText"
    assert result.class_type == "TTLGGenerateText"


# ── invalid/unknown LLM answers fall back rather than crash ──────────────────


def test_llm_unknown_capability_id_falls_back_instead_of_crashing():
    def llm_fn(prompt):
        return '{"capability_id": "NoSuchCapability", "params": {}}'

    result = map_freeform_to_step(
        "surprise me", None, ALL_CAPS, llm_fn=llm_fn,
    )

    assert result.via == "fallback"
    assert result.capability_id == "verse"
    assert result.class_type == "TTLGArtgenGenerate"
    assert result.params == {"prompt": "surprise me", "plugin": "verse"}


def test_llm_unparseable_garbage_falls_back_instead_of_crashing():
    def llm_fn(prompt):
        return "I'm not sure what you mean, could you clarify?"

    result = map_freeform_to_step("uh???", None, ALL_CAPS, llm_fn=llm_fn)

    assert result.via == "fallback"
    assert result.capability_id == "verse"


def test_llm_fn_raising_falls_back_instead_of_crashing():
    def llm_fn(prompt):
        raise RuntimeError("connection refused")

    result = map_freeform_to_step("uh???", None, ALL_CAPS, llm_fn=llm_fn)

    assert result.via == "fallback"
    assert result.capability_id == "verse"


# ── llm_fn=None -> deterministic fallback ─────────────────────────────────────


def test_llm_fn_none_falls_back_and_fills_prompt_param():
    result = map_freeform_to_step("a lonely lighthouse", None, ALL_CAPS, llm_fn=None)

    assert result == WingitResult(
        class_type="TTLGArtgenGenerate",
        params={"prompt": "a lonely lighthouse", "plugin": "verse"},
        capability_id="verse",
        via="fallback",
    )


def test_fallback_prefers_text_primary_input_over_seed_capability():
    # prior_output_kind="text" makes BOTH cap_text (kind_in="text") and
    # cap_verse (kind_in=None, always-compatible seed) candidates; the
    # text-primary-input one should win.
    result = map_freeform_to_step(
        "make it more dramatic", "text", ALL_CAPS, llm_fn=None,
    )

    assert result.via == "fallback"
    assert result.capability_id == "TTLGGenerateText"
    assert result.params == {"caption": "make it more dramatic"}


def test_fallback_non_text_primary_input_uses_prompt_key():
    # Only cap_video (kind_in="image") is compatible with prior_output_kind
    # "image" among the non-seed caps; its own input_key ("image") isn't a
    # text input, so the generic "prompt" key is used instead.
    result = map_freeform_to_step(
        "make it feel like a dream", "image", [cap_video], llm_fn=None,
    )

    assert result == WingitResult(
        class_type="TTLGImageToVideo",
        params={"prompt": "make it feel like a dream"},
        capability_id="TTLGImageToVideo",
        via="fallback",
    )


# ── no compatible live capability at all -> None ─────────────────────────────


def test_no_live_compatible_capability_returns_none():
    # cap_latent matches kind "text" but isn't live; cap_video/cap_text don't
    # match "gif"; there is no seed/plugin cap in this list at all.
    result = map_freeform_to_step(
        "anything goes", "gif", [cap_video, cap_text, cap_latent], llm_fn=None,
    )

    assert result is None


def test_no_live_compatible_capability_returns_none_even_with_llm_fn():
    def llm_fn(prompt):
        return '{"capability_id": "TTLGImageToVideo", "params": {}}'

    # cap_video is NOT live here, so even though the LLM names a real
    # capability id, it isn't in the live set and must be rejected -> and
    # there's nothing left to fall back to either.
    result = map_freeform_to_step(
        "anything goes", "gif", [_cap(
            "TTLGImageToVideo", "video", "gif", "native", "TTLGImageToVideo",
            live=False,
        )], llm_fn=llm_fn,
    )

    assert result is None


# ── default_llm_fn: real-deps wrapper (SP-C Phase 2b-3 final review Fix 1) ────
#
# The wing-it mapping call is a tiny JSON reply, not a full artifact — it
# must not inherit `call_llm`'s 600s default, or a stalled/slow chat server
# hangs the compose worker for up to 10 minutes. `default_llm_fn` is a thin
# wrapper (see its docstring); these tests monkeypatch the `artgen` module it
# imports internally so no real network/subprocess I/O happens.


def test_default_llm_fn_passes_short_timeout_to_call_llm(monkeypatch):
    import artgen
    import wingit

    calls = []

    def fake_call_llm(prompt, model, base_url, max_tokens=2048, timeout=600, **kw):
        calls.append({"timeout": timeout, "max_tokens": max_tokens})
        return "reply", {}

    monkeypatch.setattr(artgen, "detect_artgen_endpoint", lambda: ("http://x", "some-model"))
    monkeypatch.setattr(artgen, "call_llm", fake_call_llm)

    result = wingit.default_llm_fn("prompt text")

    assert result == "reply"
    assert len(calls) == 1
    assert calls[0]["timeout"] == 45
    assert calls[0]["timeout"] < 600  # must not fall through to call_llm's default


def test_default_llm_fn_returns_none_when_no_endpoint(monkeypatch):
    import artgen
    import wingit

    monkeypatch.setattr(artgen, "detect_artgen_endpoint", lambda: (None, None))

    assert wingit.default_llm_fn("prompt text") is None


# ── map_freeform_to_pipeline: free text -> multi-step draft ─────────────────
#
# A separate small live-capability list exercising a real chain: text -> image
# -> video, plus an image -> text side branch (caption), so kind-chaining
# rules have something to actually validate against.

PIPE_CAPS = [
    _cap("TTLGTextToImage", "image", "text", "native", "TTLGTextToImage"),
    _cap("TTLGImageToVideo", "video", "image", "native", "TTLGImageToVideo"),
    _cap("TTLGCaptionImage", "text", "image", "native", "TTLGCaptionImage"),
]


def test_pipeline_llm_valid_chain():
    def fn(prompt):
        return (
            '[{"capability_id":"TTLGTextToImage","params":{"prompt":"koi"}},'
            '{"capability_id":"TTLGImageToVideo","params":{}}]'
        )

    steps = map_freeform_to_pipeline(
        "koi looping", seed_output_kind=None, capabilities=PIPE_CAPS, llm_fn=fn,
    )

    assert [ct for ct, _ in steps] == ["TTLGTextToImage", "TTLGImageToVideo"]


def test_pipeline_drops_kind_broken_tail():
    def fn(prompt):
        return (
            '[{"capability_id":"TTLGTextToImage","params":{}},'
            '{"capability_id":"TTLGCaptionImage","params":{}}]'
        )

    steps = map_freeform_to_pipeline(
        "x", seed_output_kind=None, capabilities=PIPE_CAPS, llm_fn=fn,
    )

    assert steps[0][0] == "TTLGTextToImage"


def test_pipeline_drops_invalid_tail_keeps_valid_prefix():
    # A genuine kind-broken tail: TextToImage (->image) then ImageToVideo
    # (image->video) is a valid 2-step prefix; the 3rd step CaptionImage
    # needs an IMAGE but the prior step produced a VIDEO, so it can't chain
    # and must be dropped along with everything after it -- leaving exactly
    # the 2-item valid prefix (NOT [] and NOT the full 3). This test FAILS
    # if the drop-invalid-tail logic is removed (a full-list return would be
    # len 3; a fail-whole-draft return would fall back to a single step).
    def fn(prompt):
        return (
            '[{"capability_id":"TTLGTextToImage","params":{}},'
            '{"capability_id":"TTLGImageToVideo","params":{}},'
            '{"capability_id":"TTLGCaptionImage","params":{}}]'
        )

    steps = map_freeform_to_pipeline(
        "x", seed_output_kind=None, capabilities=PIPE_CAPS, llm_fn=fn,
    )

    assert len(steps) == 2
    assert [ct for ct, _ in steps] == ["TTLGTextToImage", "TTLGImageToVideo"]


def test_pipeline_fallback_when_no_llm():
    steps = map_freeform_to_pipeline(
        "a fox", seed_output_kind=None, capabilities=PIPE_CAPS, llm_fn=None,
    )

    assert steps and steps[0][0] == "TTLGTextToImage"
    assert "a fox" in str(steps[0][1].values())


def test_pipeline_none_when_nothing_fits():
    only_image_consumers = [
        _cap("TTLGImageToVideo", "video", "image", "native", "TTLGImageToVideo"),
    ]
    assert map_freeform_to_pipeline(
        "x", seed_output_kind="text", capabilities=only_image_consumers, llm_fn=None,
    ) is None


def test_pipeline_caps_at_max_steps():
    # image -> image so the chain stays valid indefinitely after the seed
    # step; only the first `max_steps` entries should survive even though
    # the LLM returned more.
    loop_caps = [_cap("TTLGRemoveBackground", "image", "image", "native", "TTLGRemoveBackground")]
    seed_cap = [_cap("TTLGTextToImage", "image", "text", "native", "TTLGTextToImage")]

    def fn(prompt):
        return (
            '[{"capability_id":"TTLGTextToImage","params":{}},'
            '{"capability_id":"TTLGRemoveBackground","params":{}},'
            '{"capability_id":"TTLGRemoveBackground","params":{}},'
            '{"capability_id":"TTLGRemoveBackground","params":{}},'
            '{"capability_id":"TTLGRemoveBackground","params":{}},'
            '{"capability_id":"TTLGRemoveBackground","params":{}}]'
        )

    steps = map_freeform_to_pipeline(
        "x", seed_output_kind=None, capabilities=seed_cap + loop_caps,
        llm_fn=fn, max_steps=3,
    )

    assert len(steps) == 3


def test_pipeline_llm_none_and_no_fallback_returns_none():
    def fn(prompt):
        return '[{"capability_id":"NoSuchCapability","params":{}}]'

    only_image_consumers = [
        _cap("TTLGImageToVideo", "video", "image", "native", "TTLGImageToVideo"),
    ]
    assert map_freeform_to_pipeline(
        "x", seed_output_kind="text", capabilities=only_image_consumers, llm_fn=fn,
    ) is None
