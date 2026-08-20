# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""
test_model_id_match.py — coverage for `model_status.match_model_id`.

`match_model_id` maps a detected `/v1/models` model-id string (as returned by
`artgen.detect_artgen_endpoint()`) to the `server_manager.SERVERS` key whose
chat model it actually is, so the status dot can mark only the model that is
really running instead of lighting up every artgen entry together.

Uses the REAL `server_manager.SERVERS` dict (not a fake) so this test breaks
if the served `model_id`s and `server_manager` drift apart.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from server_manager import SERVERS
from model_status import match_model_id


def test_matches_qwen3_8b():
    assert match_model_id("Qwen/Qwen3-8B", SERVERS) == "artgen-qwen3-8b"


def test_matches_llama_3_3_70b_by_containment():
    assert (
        match_model_id("meta-llama/Llama-3.3-70B-Instruct", SERVERS)
        == "artgen-llama-3.3-70b"
    )


def test_qwen3_32b_does_not_collide_with_qwen3_8b():
    # Distinctness guard: normalized "qwen332b" vs "qwen38b" -- neither is a
    # substring of the other, so this must resolve to the 32B key only.
    assert match_model_id("Qwen/Qwen3-32B", SERVERS) == "artgen-qwen3-32b"


def test_unknown_model_id_returns_none():
    assert match_model_id("qwen3.6-27b", SERVERS) is None


def test_empty_string_returns_none():
    assert match_model_id("", SERVERS) is None


def test_none_returns_none():
    assert match_model_id(None, SERVERS) is None


def test_matches_despite_case_and_separator_differences():
    # "qwen3_8b" normalizes to "qwen38b", same as the Qwen3-8B label/model_id.
    assert match_model_id("qwen3_8b", SERVERS) == "artgen-qwen3-8b"


def test_matches_prompt_server():
    assert match_model_id("Qwen/Qwen3-0.6B", SERVERS) == "prompt-server"


def test_non_artgen_non_prompt_servers_are_not_considered():
    # A video/image server's label should never be reachable via this matcher,
    # even if someone passed a detected id that happened to equal one.
    assert match_model_id("Wan2.2-T2V-A14B  (P300X2)", SERVERS) is None


def test_capability_filter_excludes_video_server_even_on_exact_match():
    # Synthetic dict: a "video" capability server whose label would otherwise
    # match exactly. The capability gate must exclude it, leaving the artgen
    # entry (or nothing) as the only eligible candidate.
    fake_servers = {
        "wan2.2": SERVERS["wan2.2"].__class__(
            key="wan2.2",
            label="SharedName",
            script="start_wan_qb2.sh",
            health_url="http://localhost:8000/tt-liveness",
            capabilities=("video",),
        ),
        "artgen-fake": SERVERS["artgen-qwen3-8b"].__class__(
            key="artgen-fake",
            label="SharedName",
            script="start_artgen.sh",
            health_url="http://localhost:8002/v1/models",
            capabilities=("artgen",),
        ),
    }
    assert match_model_id("SharedName", fake_servers) == "artgen-fake"
