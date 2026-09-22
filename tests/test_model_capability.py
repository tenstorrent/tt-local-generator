"""Pure unit tests for model_capability.py — no live model needed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import model_capability as mc


def test_parse_model_scale_b_simple():
    assert mc.parse_model_scale_b("Qwen3-32B") == 32.0


def test_parse_model_scale_b_with_repo_prefix():
    assert mc.parse_model_scale_b("Qwen/Qwen3.8-27B") == 27.0


def test_parse_model_scale_b_decimal():
    assert mc.parse_model_scale_b("Qwen3-0.6B") == 0.6


def test_parse_model_scale_b_with_suffix():
    assert mc.parse_model_scale_b("Llama-3.3-70B-Instruct") == 70.0


def test_parse_model_scale_b_unparseable_returns_none():
    assert mc.parse_model_scale_b("some-custom-model-name") is None


def test_has_constrained_hint_detects_known_markers():
    assert mc.has_constrained_hint("meta-llama/Llama-2-70B-GPTQ") is True
    assert mc.has_constrained_hint("TheBloke/Mixtral-8x7B-AWQ") is True
    assert mc.has_constrained_hint("some-model-int4") is True


def test_has_constrained_hint_clean_id_is_false():
    assert mc.has_constrained_hint("Qwen3-32B") is False
    assert mc.has_constrained_hint("Qwen/Qwen3.8-27B") is False


def test_model_tier_large_for_big_clean_model():
    assert mc.model_tier("Qwen3-32B") == "large"
    assert mc.model_tier("Llama-3.3-70B-Instruct") == "large"


def test_model_tier_medium_for_mid_clean_model():
    assert mc.model_tier("Qwen3-8B") == "medium"


def test_model_tier_small_for_tiny_model():
    assert mc.model_tier("Qwen3-0.6B") == "small"


def test_model_tier_downgrades_on_regex_hint():
    # 70B would be "large" on size alone, but a self-reported GPTQ marker
    # downgrades it one tier.
    assert mc.model_tier("meta-llama/Llama-2-70B-GPTQ") == "medium"


def test_model_tier_unparseable_defaults_to_large():
    assert mc.model_tier("some-custom-model-name") == "large"


def test_model_tier_known_override_wins_even_though_id_looks_clean():
    # Qwen/Qwen3.8-27B parses to a clean 27B (base tier "medium"), but is
    # served (as of 2026-09-21) via a q4kv + dflash2 profile invisible in
    # the id itself — the explicit override list forces it down one more
    # tier. See the design doc's Section 1 for the live-tested evidence.
    assert mc.model_tier("Qwen/Qwen3.8-27B") == "small"


def test_model_tier_never_raises_on_garbage_input():
    assert mc.model_tier("") == "large"
    assert mc.model_tier("???...") == "large"


def test_model_tier_never_raises_on_non_string_input():
    # model_tier is documented as "never raises" — a caller that couldn't
    # resolve a served id at all (None) or passed a non-string by mistake
    # must fall back to "large" like any other unparseable input, not
    # raise TypeError deep inside the regex search.
    assert mc.model_tier(None) == "large"
    assert mc.model_tier(123) == "large"


def test_model_tier_known_override_matches_case_insensitively():
    # The override list exists specifically to catch a served id whose
    # quantization/speculative-decoding profile is invisible in the id
    # itself — it must not silently stop working just because the served
    # id differs in case from how we characterized it.
    assert mc.model_tier("qwen/qwen3.8-27b") == "small"
