"""Tests for `app/status_segments.py` — the by-function status-bar resolver.

The load-bearing case is `test_prompt_ready_alone_leaves_every_other_segment_off`:
that is the exact bug this module exists to kill.  The old status bar folded
every server into ONE aggregate (`READY > STARTING > ERROR > OFF` across all
keys), and `prompt-server` is auto-started on launch — so the bar read "ready"
permanently, no matter what Video/Image/Art LLM were actually doing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import status_segments as ss  # noqa: E402
from model_status import Status  # noqa: E402


class _FakeDef:
    """Minimal stand-in for `server_manager.ServerDef` (only `.key` is read)."""

    def __init__(self, key: str) -> None:
        self.key = key


# A deliberately small, fixed capability map so these tests never drift when a
# real ServerDef is added to `server_manager.SERVERS`.
_FAKE_CAPS = {
    "prompt":  [_FakeDef("prompt-server")],
    "image":   [_FakeDef("flux"), _FakeDef("flux-dev")],
    "video":   [_FakeDef("wan2.2"), _FakeDef("skyreels")],
    "animate": [_FakeDef("animate")],
    "artgen":  [_FakeDef("artgen-qwen3-8b"), _FakeDef("artgen-qwen3-32b")],
}


def _states(snap, **kw):
    return ss.segment_states(
        snap, servers_for_capability=lambda cap: _FAKE_CAPS.get(cap, []), **kw
    )


# ── The regression this module exists for ────────────────────────────────────

def test_prompt_ready_alone_leaves_every_other_segment_off():
    """A ready prompt-server must NOT make the bar claim anything else is on.

    The auto-started CPU prompt server made the old single aggregate dot read
    "ready" for the whole session.  Four independent segments cannot do that.
    """
    states = _states({"prompt-server": Status.READY})
    assert states["prompt"] == Status.READY
    assert states["image"] == Status.OFF
    assert states["video"] == Status.OFF
    assert states["artgen"] == Status.OFF


def test_prompt_ready_does_not_mask_a_starting_video_server():
    """The other half of the same bug: a launch in flight stayed visible.

    `update_starting()`'s elapsed timer used to be overwritten by the next
    poll, because the aggregate folded prompt-server's READY back over it.
    """
    states = _states(
        {"prompt-server": Status.READY, "wan2.2": Status.STARTING}
    )
    assert states["prompt"] == Status.READY
    assert states["video"] == Status.STARTING


# ── Per-segment resolution ───────────────────────────────────────────────────

def test_every_segment_key_is_always_present():
    """All four segments resolve on an empty snapshot — the bar never reflows."""
    states = _states({})
    assert set(states) == set(ss.SEGMENT_KEYS)
    assert all(v == Status.OFF for v in states.values())


def test_segment_order_is_prompt_image_video_artgen():
    assert ss.SEGMENT_KEYS == ("prompt", "image", "video", "artgen")


def test_ready_wins_over_starting_within_one_segment():
    states = _states({"flux": Status.READY, "flux-dev": Status.STARTING})
    assert states["image"] == Status.READY


def test_starting_wins_over_error_within_one_segment():
    states = _states({"wan2.2": Status.ERROR, "skyreels": Status.STARTING})
    assert states["video"] == Status.STARTING


def test_error_surfaces_when_nothing_is_ready_or_starting():
    states = _states({"flux": Status.ERROR})
    assert states["image"] == Status.ERROR


def test_animate_capability_folds_into_the_video_segment():
    """Wan2.2-Animate is a video model (v0.61.0 "Video is Video")."""
    states = _states({"animate": Status.READY})
    assert states["video"] == Status.READY
    # ...and it does not leak into any other segment.
    assert states["image"] == Status.OFF
    assert states["prompt"] == Status.OFF


# ── The detected-chat-model case ─────────────────────────────────────────────

class _Info:
    """Stand-in for `model_status.ArtgenModelInfo`."""

    def __init__(self, model_id, url, matched_key):
        self.model_id = model_id
        self.url = url
        self.matched_key = matched_key


def test_detected_unregistered_chat_model_lights_art_llm_only():
    """A chat LLM started outside the app matches no ServerDef, so every artgen
    key is legitimately OFF in the snapshot — the segment must still read on,
    matching the "(detected)" entry CreateView already offers.
    """
    info = _Info("some-org/some-70b", "http://localhost:8009", None)
    states = _states({}, artgen_model=info)
    assert states["artgen"] == Status.READY
    assert states["video"] == Status.OFF
    assert states["image"] == Status.OFF
    assert states["prompt"] == Status.OFF


def test_prompt_gen_fallback_does_not_light_art_llm():
    """THE bug this parameter had to grow a brain for.

    `artgen.detect_artgen_endpoint()` deliberately falls back to the tiny
    prompt-gen server (Qwen3-0.6B on :8001) as a LAST RESORT when no real chat
    model is up. So `running_artgen_model()` is non-None whenever the auto-
    started prompt server is alive — and treating that as "a chat model is
    running" lit Art LLM off the Prompt model, which is the exact complaint
    ("it reports a model being ready, but it's just the prompt gen") one layer
    up from where it was first fixed.
    """
    info = _Info("Qwen/Qwen3-0.6B", "http://localhost:8001", "prompt-server")
    states = _states({"prompt-server": Status.READY}, artgen_model=info)
    assert states["prompt"] == Status.READY
    assert states["artgen"] == Status.OFF, (
        "the prompt-gen model belongs to the Prompt segment, not Art LLM"
    )


def test_prompt_gen_endpoint_ignored_even_if_its_id_matches_nothing():
    """Belt and braces: if the prompt server ever reports an id `match_model_id`
    doesn't recognise, `matched_key` is None — but the URL still says :8001, so
    it must not be mistaken for a foreign chat model."""
    info = _Info("weird-unreleased-id", "http://localhost:8001", None)
    states = _states({}, artgen_model=info)
    assert states["artgen"] == Status.OFF


def test_detected_model_matching_a_real_artgen_server_lights_art_llm():
    info = _Info("Qwen/Qwen3-8B", "http://localhost:8002", "artgen-qwen3-8b")
    states = _states({"artgen-qwen3-8b": Status.READY}, artgen_model=info)
    assert states["artgen"] == Status.READY


def test_detected_flag_never_downgrades_a_real_starting_artgen_server():
    info = _Info("Qwen/Qwen3-8B", "http://localhost:8002", "artgen-qwen3-8b")
    states = _states({"artgen-qwen3-8b": Status.STARTING}, artgen_model=info)
    assert states["artgen"] == Status.READY  # detected endpoint answers now


def test_no_detected_model_leaves_artgen_off():
    states = _states({}, artgen_model=None)
    assert states["artgen"] == Status.OFF


def test_detected_model_is_artgen_helper():
    """The policy is exposed on its own so main_window can paint the bar AND
    the capability popover row from ONE decision (they must never disagree)."""
    assert ss.detected_model_is_artgen(None) is False
    assert ss.detected_model_is_artgen(
        _Info("Qwen/Qwen3-0.6B", "http://localhost:8001", "prompt-server")
    ) is False
    assert ss.detected_model_is_artgen(
        _Info("Qwen/Qwen3-8B", "http://localhost:8002", "artgen-qwen3-8b")
    ) is True
    assert ss.detected_model_is_artgen(
        _Info("some-org/some-70b", "http://localhost:8009", None)
    ) is True


# ── Glyph / CSS maps ─────────────────────────────────────────────────────────

def test_glyphs_are_distinct_per_state():
    glyphs = [ss.glyph_for(s) for s in
              (Status.READY, Status.STARTING, Status.ERROR, Status.OFF)]
    assert len(set(glyphs)) == 4, "each state needs its own shape, not just a colour"


def test_glyph_and_css_fall_back_to_off_for_an_unknown_state():
    assert ss.glyph_for("nonsense") == "○"
    assert ss.css_state_for("nonsense") == "offline"


def test_css_states_map_onto_the_existing_stylesheet_classes():
    """These suffixes must match the `.tt-statusbar-dot-*` rules already in
    main_window's CSS — no new colour vocabulary is invented here."""
    assert set(ss.CSS_STATES.values()) == {"ready", "starting", "error", "offline"}


def test_default_capability_lookup_is_server_manager():
    """Called without the injection seam, it reads the real SERVERS table."""
    import server_manager as sm

    states = ss.segment_states({"prompt-server": Status.READY})
    assert states["prompt"] == Status.READY
    assert sm.servers_for_capability("video"), "sanity: real table is populated"
