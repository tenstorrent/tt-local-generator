import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import intent_vocab as iv


def test_flow_line_takes_and_makes():
    assert iv.flow_line(iv.intent_for("TTLGAnimateDiff")) == "Takes a prompt → makes a looping GIF"
    assert iv.flow_line(iv.intent_for("TTLGTextToImage")) == "Takes a prompt → makes an image"


def test_flow_line_source_node_has_no_takes():
    # TTLGArtgenGenerate is a source node (input_kind is None).
    assert iv.flow_line(iv.intent_for("TTLGArtgenGenerate")).startswith("Makes ")


def test_capability_for_intent():
    assert iv.capability_for_intent("TTLGTextToImage") == "image"
    assert iv.capability_for_intent("TTLGImageToVideo") == "video"
    assert iv.capability_for_intent("TTLGAnimateDiff") == "animatediff"
    assert iv.capability_for_intent("TTLGGenerateText") == "artgen"
    assert iv.capability_for_intent("TTLGCaptionImage") is None  # no model dimension


def test_capability_for_intent_montage_is_none():
    """Whole-branch review Finding 1: TTLGMontage is an ffmpeg slideshow node
    with no model dimension at all (`pipeline_engine._backend_for` has no
    branch for it) — it must not map to a capability, or Pipeline Studio
    renders a video-model picker that does nothing when touched."""
    assert iv.capability_for_intent("TTLGMontage") is None


def test_summary_field_optional_and_present_for_key_intents():
    assert iv.intent_for("TTLGCaptionImage").summary is None or isinstance(
        iv.intent_for("TTLGCaptionImage").summary, str)
    # At least the marquee generative intents carry a summary.
    assert iv.intent_for("TTLGAnimateDiff").summary
    assert iv.intent_for("TTLGTextToImage").summary
