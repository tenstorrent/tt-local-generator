"""Tests that artgen.cli._make_call_fn stamps a model_tier onto its closure."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from artgen.cli import _make_call_fn


def _args(**kw):
    ns = argparse.Namespace()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_make_call_fn_stamps_large_tier_for_big_model():
    fn = _make_call_fn("Qwen3-32B", "http://localhost:8002", _args())
    assert fn.model_tier == "large"


def test_make_call_fn_stamps_small_tier_for_known_constrained_model():
    fn = _make_call_fn("Qwen/Qwen3.8-27B", "http://localhost:20000", _args())
    assert fn.model_tier == "small"


def test_make_call_fn_still_returns_a_working_closure():
    # Regression: the returned callable's own behavior (not just the new
    # attribute) must still be exactly the plain call_fn signature.
    fn = _make_call_fn("Qwen3-32B", "http://localhost:8002", _args())
    assert callable(fn)
    assert hasattr(fn, "model_tier")
