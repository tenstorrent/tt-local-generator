import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import field_roles as fr
from dataclasses import dataclass

@dataclass
class _Spec:  # stand-in for create_param_panels._ArgSpec
    dest: str; kind: str; default: object

def test_native_prompt_is_brief_words():
    assert fr.classify_native("prompt") == fr.FieldRole(fr.ROLE_BRIEF, fr.MARK_WORDS)

def test_native_negative_is_brief_words():
    assert fr.classify_native("negative_prompt") == fr.FieldRole(fr.ROLE_BRIEF, fr.MARK_WORDS)

def test_native_numeric_knobs_are_control_exact():
    for k in ("num_inference_steps", "seed", "guidance_scale", "num_frames"):
        assert fr.classify_native(k) == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)

def test_native_unknown_defaults_control_exact():
    assert fr.classify_native("wat").role == fr.ROLE_CONTROL

def test_artgen_subject_is_brief_words():
    assert fr.classify_artgen(_Spec("subject", "str", "a mountain")) == fr.FieldRole(fr.ROLE_BRIEF, fr.MARK_WORDS)

def test_artgen_numeric_is_control_exact():
    assert fr.classify_artgen(_Spec("width", "int", None)) == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)

def test_artgen_bool_is_direction_exact():
    assert fr.classify_artgen(_Spec("mountains", "bool", True)) == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_EXACT)

def test_artgen_random_default_choice_is_interpreted():
    assert fr.classify_artgen(_Spec("palette", "choice", "random")) == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_INTERPRETED)

def test_artgen_none_default_choice_is_interpreted():
    assert fr.classify_artgen(_Spec("ansi_style", "choice", None)).marker == fr.MARK_INTERPRETED

def test_artgen_fixed_choice_is_direction_exact():
    assert fr.classify_artgen(_Spec("colors", "choice", "256")) == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_EXACT)

def test_glyphs_present():
    assert fr.MARKER_GLYPH[fr.MARK_INTERPRETED] == "✨"

def test_pipeline_brief_key_is_brief_words():
    for k in ("prompt", "text", "negative_prompt", "subject", "theme", "caption", "description", "lore"):
        assert fr.classify_pipeline_field("text", "whatever", k) == fr.FieldRole(fr.ROLE_BRIEF, fr.MARK_WORDS)

def test_pipeline_number_is_control_exact():
    assert fr.classify_pipeline_field("number", 20, "steps") == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)

def test_pipeline_bool_is_direction_exact():
    assert fr.classify_pipeline_field("bool", True, "tiled") == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_EXACT)

def test_pipeline_text_random_default_is_interpreted():
    assert fr.classify_pipeline_field("text", "random", "sampler") == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_INTERPRETED)
    assert fr.classify_pipeline_field("choice", None, "scheduler").marker == fr.MARK_INTERPRETED

def test_pipeline_plain_text_is_direction_exact():
    assert fr.classify_pipeline_field("text", "euler", "sampler") == fr.FieldRole(fr.ROLE_DIRECTION, fr.MARK_EXACT)

def test_pipeline_unknown_kind_is_control_exact():
    assert fr.classify_pipeline_field("weird", "x", "k") == fr.FieldRole(fr.ROLE_CONTROL, fr.MARK_EXACT)

def test_marker_prefix():
    assert fr.marker_prefix(fr.MARK_INTERPRETED) == "✨ "
    assert fr.marker_prefix("nonsense") == ""
