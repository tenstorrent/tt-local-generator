# Model-Capability-Aware ANSI Prompting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scale the `ansi` artgen generator's prompting strategy to the
serving model's capability tier (small/medium/large), so a heavily
quantized/speculative-decoding model gets a prompting style it can actually
follow, while every currently-working model's behavior is unchanged.

**Architecture:** A new pure module (`app/model_capability.py`) derives a
capability tier from a model id (parsed parameter size, downgraded by a
regex quantization/speculative-decoding hint or an explicit known-id
override). Both `_make_call_fn` implementations (`app/artgen/cli.py`,
`app/mcp_server.py`) stamp the tier onto the `call_fn` closure they already
build. `plugins/ansi/plugin.py` (the canonical runtime copy — mirrored into
`app/artgen/generators/ansi.py`) reads `call_fn.model_tier` and dispatches
to one of three pipelines: `large` (today's exact whole-canvas 3-pass,
untouched), `medium` (whole-canvas structure/refine + a legend-then-
mechanical-apply colorize pass), `small` (band-segmented structure + a
legend-then-per-row-mechanical-apply colorize pass).

**Tech Stack:** Python 3, stdlib only (`re`, `json`) for the new module and
generator changes — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-21-artgen-model-capability-tiering-design.md`

## Global Constraints

- No new third-party dependencies.
- `call_fn(prompt, system=None, max_tokens=None) -> str` contract is
  unchanged for every existing caller; tier is carried as an *attribute* on
  the closure (`call_fn.model_tier`), never a new positional/keyword
  parameter.
- The `large` tier must produce **byte-identical** prompts and the same
  3-call shape as today's `ansi` generator for every existing test and for
  any `call_fn` without a `.model_tier` attribute (regression safety net —
  this is the one behavior that must never change).
- `model_tier()` never raises. An unparseable model id defaults to `"large"`
  (assume capable — never punish a model we haven't characterized).
- Every LLM-facing change in this plan is pure prompt-construction /
  response-parsing logic, testable with canned fake responses — no task in
  this plan requires a live model or the QB2 board to pass its tests.
- `plugins/ansi/plugin.py` is the canonical runtime file (loaded by
  `plugin_loader`); `app/artgen/generators/ansi.py` is a stale-but-kept-in-
  sync mirror (same convention CLAUDE.md documents for `animatediff`). Every
  functional change lands in both, verbatim except for the existing
  `from artgen import ArtGenerator, register` / `@register` difference.

---

## Task 1: Capability tiering module

**Files:**
- Create: `app/model_capability.py`
- Test: `tests/test_model_capability.py`

**Interfaces:**
- Produces: `model_capability.Tier` (`Literal["small", "medium", "large"]`),
  `model_capability.parse_model_scale_b(model_id: str) -> float | None`,
  `model_capability.has_constrained_hint(model_id: str) -> bool`,
  `model_capability.model_tier(model_id: str) -> Tier`. All three functions
  are used by Task 2.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_model_capability.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_model_capability.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'model_capability'`

- [ ] **Step 3: Write `app/model_capability.py`**

```python
"""model_capability.py — a pure (no GTK, no network) heuristic for scaling
prompt strategy to what a serving LLM can actually follow.

Parses a rough parameter-count tier from a model id, downgraded when the id
(or a manually-characterized override) indicates heavy quantization or
speculative decoding — both observed live to degrade structured/repetitive
generation tasks (e.g. ANSI art) far more than raw model size alone would
predict. See docs/superpowers/specs/
2026-09-21-artgen-model-capability-tiering-design.md for the investigation.
"""
from __future__ import annotations

import re
from typing import Literal

Tier = Literal["small", "medium", "large"]

_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[Bb](?![a-zA-Z])")

_CONSTRAINED_HINTS = (
    "q4", "q8", "int4", "int8", "awq", "gptq", "fp8",
    "dflash", "speculative", "draft",
)

# Model ids we've manually characterized as behaving like a lower tier than
# their size alone would suggest. The served /v1/models id often carries no
# quantization/speculative-decoding signal at all — that information can
# live only in the serving container's own metadata (e.g. a tt-model-manager
# docker label), which this module never reads. Add entries here as they're
# found; see the design doc's Section 1 for the Qwen/Qwen3.8-27B case
# (served 2026-09-21 via profile qwen3.8-27b-dflash2-vision-p300x2-q4kv —
# q4kv quantization + dflash2 speculative decoding, invisible in the id).
_KNOWN_CONSTRAINED_IDS = frozenset({
    "Qwen/Qwen3.8-27B",
})

_TIER_ORDER: tuple[Tier, ...] = ("small", "medium", "large")


def parse_model_scale_b(model_id: str) -> float | None:
    """Extract a <number>B parameter-count token from a model id.

    Handles "Qwen3-32B", "Qwen/Qwen3.8-27B", "Llama-3.3-70B-Instruct",
    "Qwen3-0.6B". Returns None if nothing matches.
    """
    match = _SIZE_RE.search(model_id)
    if not match:
        return None
    return float(match.group(1))


def has_constrained_hint(model_id: str) -> bool:
    """True if the model id itself self-reports quantization or
    speculative decoding (e.g. a community repo with -GPTQ/-AWQ in its
    name). Does NOT see serving-container-only metadata — see
    _KNOWN_CONSTRAINED_IDS for that gap."""
    lowered = model_id.lower()
    return any(hint in lowered for hint in _CONSTRAINED_HINTS)


def _base_tier(scale_b: float | None) -> Tier:
    if scale_b is None:
        return "large"
    if scale_b >= 30:
        return "large"
    if scale_b >= 8:
        return "medium"
    return "small"


def _downgrade(tier: Tier) -> Tier:
    idx = _TIER_ORDER.index(tier)
    return _TIER_ORDER[max(0, idx - 1)]


def model_tier(model_id: str) -> Tier:
    """Capability tier for the given served model id: "small", "medium", or
    "large". Never raises — an unparseable id defaults to "large" (assume
    capable; never punish a model we haven't characterized)."""
    tier = _base_tier(parse_model_scale_b(model_id))
    if has_constrained_hint(model_id) or model_id in _KNOWN_CONSTRAINED_IDS:
        tier = _downgrade(tier)
    return tier
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/usr/bin/python3 -m pytest tests/test_model_capability.py -v`
Expected: PASS (all 13 tests)

- [ ] **Step 5: Commit**

```bash
git add app/model_capability.py tests/test_model_capability.py
git commit -m "Add model_capability tiering module for capability-aware prompting"
```

---

## Task 2: Wire tier stamping into both `_make_call_fn` implementations

**Files:**
- Modify: `app/artgen/cli.py` (the `_make_call_fn` function, ~line 281)
- Modify: `app/mcp_server.py` (the `_make_call_fn` function, ~line 88)
- Test: `tests/test_artgen_cli_call_fn.py` (new)
- Test: `tests/test_mcp_server_call_fn.py` (new)

**Interfaces:**
- Consumes: `model_capability.model_tier(model_id: str) -> Tier` (Task 1).
- Produces: both `_make_call_fn`'s returned closures now carry
  `.model_tier: Tier`. Task 3+ read this via
  `getattr(call_fn, "model_tier", "large")`.

- [ ] **Step 1: Write the failing test for `cli.py`**

Create `tests/test_artgen_cli_call_fn.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_artgen_cli_call_fn.py -v`
Expected: FAIL with `AttributeError: 'function' object has no attribute 'model_tier'`

- [ ] **Step 3: Modify `app/artgen/cli.py`**

Add the import near the top (alongside the existing `import artgen`):

```python
import artgen
import model_capability
from server_config import server_config
```

Modify `_make_call_fn` (currently ~line 281-301):

```python
def _make_call_fn(model_id: str, base_url: str, args):
    """
    Return a call_fn(prompt, system=None, max_tokens=None) -> str closure.

    Uses artgen.call_llm (stdlib urllib — safe from GTK background threads).
    The system= kwarg lets generators like VerseGenerator pass their own system
    prompt per call without mutating the args namespace.
    The max_tokens override lets multi-pass generators tune each pass budget
    independently (e.g. 1024 for ASCII structure, 8192 for colorisation).

    The returned closure also carries a `.model_tier` attribute
    (model_capability.model_tier(model_id)) so tier-aware generators (e.g.
    AnsiGenerator) can scale their prompting strategy to what this model can
    actually follow. Generators that never read the attribute are
    unaffected — this is decoration on the closure, not a new call_fn
    parameter.
    """
    def _call_fn(prompt, system=None, max_tokens=None):
        raw, _ = artgen.call_llm(
            prompt, model_id, base_url,
            max_tokens=max_tokens or getattr(args, "max_tokens", 4096),
            temperature=getattr(args, "temperature", 0.7),
            timeout=getattr(args, "timeout", 600),
            system=system,
        )
        return raw

    _call_fn.model_tier = model_capability.model_tier(model_id)
    return _call_fn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_artgen_cli_call_fn.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing test for `mcp_server.py`**

Create `tests/test_mcp_server_call_fn.py`:

```python
"""Tests that mcp_server._make_call_fn stamps a model_tier onto its closure."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed — skipping MCP server tests")


def test_make_call_fn_stamps_tier_from_detected_model(monkeypatch):
    import mcp_server
    import artgen as artgen_module

    monkeypatch.setattr(
        artgen_module, "detect_artgen_endpoint",
        lambda preferred_url=None: ("http://localhost:20000", "Qwen/Qwen3.8-27B"),
    )
    fn = mcp_server._make_call_fn()
    assert fn.model_tier == "small"


def test_make_call_fn_defaults_to_large_when_no_endpoint_detected(monkeypatch):
    import mcp_server
    import artgen as artgen_module

    monkeypatch.setattr(
        artgen_module, "detect_artgen_endpoint",
        lambda preferred_url=None: (None, None),
    )
    fn = mcp_server._make_call_fn()
    assert fn.model_tier == "large"


def test_make_call_fn_still_raises_inside_call_fn_when_no_endpoint(monkeypatch):
    # Regression: the upfront detection added for tier-stamping must not
    # swallow the existing "no server reachable" error the real call still
    # raises when actually invoked.
    import mcp_server
    import artgen as artgen_module

    monkeypatch.setattr(
        artgen_module, "detect_artgen_endpoint",
        lambda preferred_url=None: (None, None),
    )
    fn = mcp_server._make_call_fn()
    with pytest.raises(RuntimeError, match="No LLM server reachable"):
        fn("a prompt")
```

- [ ] **Step 6: Run test to verify it fails**

Run: `/usr/bin/python3 -m pytest tests/test_mcp_server_call_fn.py -v`
Expected: FAIL with `AttributeError: 'function' object has no attribute 'model_tier'`

- [ ] **Step 7: Modify `app/mcp_server.py`**

Modify `_make_call_fn` (currently ~line 88-123):

```python
def _make_call_fn(base_url: str | None = None):
    """Return a call_fn that routes to the best available LLM endpoint.

    Routing is delegated to artgen.detect_artgen_endpoint(), so MCP tool
    invocations use the same discovery as the GUI: an explicit override first
    (TTLG_LLM_URL), then the dedicated artgen port, then any OpenAI-compatible
    chat server found by sweeping local ports, and finally the tiny prompt-gen
    fallback. This means a model started outside the app on a non-standard port
    is picked up automatically — no configuration required.

    Discovery runs per-call so a server started after the MCP server is already
    running is found on the next tool invocation.

    The returned closure also carries a `.model_tier` attribute, resolved
    from ONE upfront detection call here (unlike the real per-call
    detection inside _call_fn below) — a generator reads the tier before its
    first real call, so there's no way to derive it lazily from inside
    _call_fn without changing every generator's call order. The endpoint and
    model can still change call-to-call as before; only the tier snapshot is
    fixed at construction time.
    """
    import artgen
    import model_capability

    # TTLG_LLM_URL may be a full .../v1/chat/completions URL; detect_artgen_endpoint
    # wants a base (http://host:port). Strip the known chat suffixes so the
    # override still works as a preferred endpoint.
    preferred = base_url
    if preferred:
        for suffix in ("/v1/chat/completions", "/chat/completions", "/v1"):
            if preferred.rstrip("/").endswith(suffix):
                preferred = preferred.rstrip("/")[: -len(suffix)]
                break

    def _call_fn(prompt: str, system: str | None = None,
                 max_tokens: int | None = None) -> str:
        endpoint, model = artgen.detect_artgen_endpoint(preferred_url=preferred)
        if not endpoint:
            raise RuntimeError(
                "No LLM server reachable. Start one first: "
                "tt-ctl start artgen-qwen3-8b  or  tt-ctl start prompt-server"
            )
        text, _usage = artgen.call_llm(
            prompt, model, endpoint,
            max_tokens=max_tokens or 2048,
            system=system,
        )
        return text

    _, _detected_model = artgen.detect_artgen_endpoint(preferred_url=preferred)
    _call_fn.model_tier = (
        model_capability.model_tier(_detected_model) if _detected_model else "large"
    )
    return _call_fn
```

- [ ] **Step 8: Run test to verify it passes**

Run: `/usr/bin/python3 -m pytest tests/test_mcp_server_call_fn.py -v`
Expected: PASS (3 tests). If it prints "SKIPPED" instead, fastapi isn't
installed in this environment — that's an existing, accepted skip
condition (matches `tests/test_mcp_server.py`'s own guard), not a failure.

- [ ] **Step 9: Run the full existing MCP server test file to confirm no regression**

Run: `/usr/bin/python3 -m pytest tests/test_mcp_server.py -v`
Expected: PASS (or the same skip as before this change)

- [ ] **Step 10: Commit**

```bash
git add app/artgen/cli.py app/mcp_server.py tests/test_artgen_cli_call_fn.py tests/test_mcp_server_call_fn.py
git commit -m "Stamp model_tier onto call_fn closures in both artgen entry points"
```

---

## Task 3: Refactor `ansi.py` to dispatch on tier (large path untouched)

**Files:**
- Modify: `plugins/ansi/plugin.py`
- Test: `tests/test_artgen_generators.py` (existing `TestAnsiGenerator` class,
  ~line 290-318)

**Interfaces:**
- Consumes: `getattr(call_fn, "model_tier", "large")`.
- Produces: `AnsiGenerator._generate_large(self, call_fn, subject, style,
  width, height, board_name, tagline, args) -> str` — Task 4/5 add sibling
  `_generate_small`/`_generate_medium` with the same signature (minus
  `args`, which only the large path's `self.parse_output(raw3, args)` call
  needs).

This task is a pure refactor: `generate_artifact`'s existing body moves
into `_generate_large` unchanged, and a new tier-dispatch wrapper takes its
place. No prompt text changes in this task.

- [ ] **Step 1: Write the failing regression test**

Add to `tests/test_artgen_generators.py`, inside `class TestAnsiGenerator`
(after the existing `test_generate_artifact_makes_three_llm_calls`):

```python
    def test_generate_artifact_defaults_to_large_tier_without_model_tier_attr(self):
        # A call_fn with no .model_tier attribute at all (e.g. a hand-rolled
        # test double, or any caller predating this change) must reproduce
        # today's exact 3-call whole-canvas behavior.
        calls = []

        def fn(prompt, system=None, max_tokens=None):
            calls.append(prompt)
            if len(calls) == 1:
                return "A B C\nD E F"
            if len(calls) == 2:
                return "█ ░ ▒\n▓ ▀ ▄"
            return "\033[38;5;51m█\033[0m \033[38;5;82m▒\033[0m"

        assert not hasattr(fn, "model_tier")
        args = _args(ansi_style="bbs", subject="test", width=40, height=20,
                     board_name="", tagline="")
        self.g.generate_artifact(args, fn)
        assert len(calls) == 3

    def test_generate_artifact_large_tier_explicit(self):
        calls = []

        def fn(prompt, system=None, max_tokens=None):
            calls.append(prompt)
            if len(calls) == 1:
                return "A B C\nD E F"
            if len(calls) == 2:
                return "█ ░ ▒\n▓ ▀ ▄"
            return "\033[38;5;51m█\033[0m \033[38;5;82m▒\033[0m"

        fn.model_tier = "large"
        args = _args(ansi_style="bbs", subject="test", width=40, height=20,
                     board_name="", tagline="")
        self.g.generate_artifact(args, fn)
        assert len(calls) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_artgen_generators.py::TestAnsiGenerator -v`
Expected: the two new tests currently PASS already (no dispatch exists yet,
so everything runs the one existing pipeline) — this step is a sanity check
that the OLD code already satisfies both, confirming they're valid
regression pins before you touch anything. Continue to Step 3 regardless.

- [ ] **Step 3: Refactor `plugins/ansi/plugin.py`**

Replace the existing `generate_artifact` method (currently ~line
294-325) with a dispatcher plus an extracted `_generate_large`:

```python
    def generate_artifact(self, args, call_fn) -> str:
        """Dispatch to a tier-specific pipeline based on call_fn.model_tier
        (stamped by _make_call_fn in cli.py/mcp_server.py — see
        model_capability.py). Any call_fn without the attribute (e.g. a
        hand-rolled test double, or any caller predating this change)
        defaults to "large", reproducing this generator's original
        whole-canvas 3-pass behavior byte-for-byte."""
        tier = getattr(call_fn, "model_tier", "large")

        style      = getattr(args, "ansi_style", "scene")
        subject    = getattr(args, "subject", "a mountain at sunset")
        board_name = getattr(args, "board_name", "")
        tagline    = getattr(args, "tagline", "")
        width      = getattr(args, "width", None) or (80 if style == "bbs" else 40)
        height     = 20 if style == "bbs" else max(12, width // 2)

        if tier == "small":
            return self._generate_small(
                call_fn, subject, style, width, height, board_name, tagline,
            )
        if tier == "medium":
            return self._generate_medium(
                call_fn, subject, style, width, height, board_name, tagline,
            )
        return self._generate_large(
            args, call_fn, subject, style, width, height, board_name, tagline,
        )

    def _generate_large(self, args, call_fn, subject, style, width, height,
                         board_name, tagline) -> str:
        """3-pass pipeline: ASCII structure → block refinement → colorization.
        This is the original ansi generator behavior, unchanged — the
        regression-safety default for every model this generator has ever
        worked on."""
        # Pass 1 — ASCII composition
        print("[pass 1/3: ASCII structure …]", flush=True)
        raw1 = call_fn(_build_ascii_prompt(subject, width, height, style),
                       max_tokens=1024)
        ascii_art = _normalize_grid(raw1, width, height)

        # Pass 2 — block character refinement
        print("[pass 2/3: block refinement …]", flush=True)
        raw2 = call_fn(_build_refine_prompt(ascii_art, subject, width, height),
                       max_tokens=1024)
        block_art = _normalize_grid(raw2, width, height)

        # Pass 3 — colorization (larger token budget for full ANSI output)
        print("[pass 3/3: colorization …]", flush=True)
        raw3 = call_fn(
            _build_colorize_prompt(
                block_art, subject, style, width, height, board_name, tagline,
            ),
            max_tokens=8192,
        )
        return self.parse_output(raw3, args)
```

Do not touch `build_prompt` or `parse_output` in this task — they are
unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/usr/bin/python3 -m pytest tests/test_artgen_generators.py::TestAnsiGenerator -v`
Expected: PASS (all tests in this class, including the two new ones and the
pre-existing `test_generate_artifact_makes_three_llm_calls`)

- [ ] **Step 5: Commit**

```bash
git add plugins/ansi/plugin.py tests/test_artgen_generators.py
git commit -m "Refactor AnsiGenerator to dispatch on model_tier (large path unchanged)"
```

---

## Task 4: Small-tier pipeline (band-segmented structure + per-row legend colorize)

**Files:**
- Modify: `plugins/ansi/plugin.py`
- Test: `tests/test_artgen_generators.py` (extend `TestAnsiGenerator`)

**Interfaces:**
- Consumes: nothing new from other tasks (builds directly on Task 3's
  dispatcher and the module's existing `_normalize_grid`/`_build_refine_prompt`).
- Produces: `AnsiGenerator._generate_small(self, call_fn, subject, style,
  width, height, board_name, tagline) -> str`, plus module-level helpers
  `_split_bands`, `_build_band_prompt`, `_parse_row_json`,
  `_build_legend_prompt`, `_parse_legend_json`, `_generate_legend`,
  `_build_row_colorize_prompt`, `_extract_colored_cells`,
  `_pairs_to_ansi_row`. Task 5 reuses `_build_legend_prompt`,
  `_parse_legend_json`, `_generate_legend`, `_extract_colored_cells`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_artgen_generators.py`, inside `class TestAnsiGenerator`:

```python
    def test_generate_artifact_small_tier_band_and_per_row_calls(self):
        # small tier: 3 band calls (pass 1) + 1 refine call (pass 2) +
        # 1 legend call + N per-row colorize calls (pass 3, N = height).
        calls = []

        def fn(prompt, system=None, max_tokens=None):
            calls.append(prompt)
            n = len(calls)
            if n <= 3:
                # band calls — JSON array of row-strings. Width/height for
                # this test come from the bbs style defaults (40x20); bands
                # split into [2, 16, 2] rows for bbs. Reply with a plausible
                # JSON array sized to whatever the prompt actually asked for.
                if "exactly 2 strings" in prompt:
                    return '["                                        ", "                                        "]'
                return '[' + ", ".join(
                    ['"' + ("#" * 40) + '"'] * 16
                ) + ']'
            if n == 4:
                return "\n".join(["#" * 40] * 20)  # pass 2: block refine (identity)
            if n == 5:
                return '{"#": 236, " ": 232}'  # legend
            # pass 3: one call per row — echo back a fully-wrapped row
            return "".join(f"\033[38;5;236m#" for _ in range(40)) + "\033[0m"

        args = _args(ansi_style="bbs", subject="test", width=40, height=20,
                     board_name="", tagline="")
        fn.model_tier = "small"
        result = self.g.generate_artifact(args, fn)

        # 3 band calls + 1 refine + 1 legend + 20 per-row colorize calls
        assert len(calls) == 3 + 1 + 1 + 20
        lines = result.split("\n")
        assert len(lines) == 20
        for line in lines:
            assert line.count("\033[38;5;236m#") == 40
            assert line.endswith("\033[0m")

    def test_split_bands_even_division(self):
        from ansi_plugin import _split_bands
        assert _split_bands(12, 3) == [4, 4, 4]

    def test_split_bands_uneven_division_favors_earlier_bands(self):
        from ansi_plugin import _split_bands
        assert _split_bands(20, 3) == [7, 7, 6]

    def test_parse_row_json_pads_short_rows(self):
        from ansi_plugin import _parse_row_json
        raw = '["ab", "cd"]'
        rows = _parse_row_json(raw, n_rows=3, width=4)
        assert rows == ["ab  ", "cd  ", "    "]

    def test_parse_row_json_truncates_long_rows(self):
        from ansi_plugin import _parse_row_json
        raw = '["abcdef"]'
        rows = _parse_row_json(raw, n_rows=1, width=4)
        assert rows == ["abcd"]

    def test_parse_row_json_fails_soft_on_garbage(self):
        from ansi_plugin import _parse_row_json
        rows = _parse_row_json("not json at all", n_rows=2, width=3)
        assert rows == ["   ", "   "]

    def test_extract_colored_cells_parses_escape_sequences(self):
        from ansi_plugin import _extract_colored_cells
        raw = "\033[38;5;51m█\033[38;5;82m▒"
        pairs = _extract_colored_cells(raw, expected_n=5)
        assert pairs == [("51", "█"), ("82", "▒")]

    def test_extract_colored_cells_handles_literal_backslash_notation(self):
        from ansi_plugin import _extract_colored_cells
        raw = "\\033[38;5;51m█\\033[38;5;82m▒"
        pairs = _extract_colored_cells(raw, expected_n=5)
        assert pairs == [("51", "█"), ("82", "▒")]

    def test_pairs_to_ansi_row_pads_shortfall_from_legend(self):
        from ansi_plugin import _pairs_to_ansi_row
        pairs = [("236", "#")]
        row = _pairs_to_ansi_row(pairs, original_row="##", legend={"#": 236})
        assert row == "\033[38;5;236m#\033[38;5;236m#\033[0m"
```

`self.g` in this class is created fresh per-test via the `gen` fixture, so
`import ansi_plugin` works only after `_load_plugin` has executed at least
once with that module name — the fixture already does this (`mod =
_load_plugin(_ANSI_PLUGIN, "ansi_plugin")`), which registers `"ansi_plugin"`
in `sys.modules`, so a bare `from ansi_plugin import ...` inside a test
method resolves correctly as long as the test class's `gen` fixture (which
is `autouse=True`) has already run — it has, by pytest fixture ordering.

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_artgen_generators.py::TestAnsiGenerator -v`
Expected: FAIL — `AttributeError: 'AnsiGenerator' object has no attribute
'_generate_small'` for the first test, `ImportError: cannot import name
'_split_bands'` etc. for the rest.

- [ ] **Step 3: Add `import json` and the band/legend/parsing helpers to `plugins/ansi/plugin.py`**

Add `import json` alongside the existing `import re` near the top of the
file:

```python
from __future__ import annotations

import json
import re

from artgen import ArtGenerator
```

Add the following module-level constants and functions after
`_build_colorize_prompt` (i.e. right before the `# ── Generator ──` section
divider, ~line 245):

```python
# ── Small/medium-tier helpers (band-segmented structure, legend-based color) ──
#
# A whole-canvas free-form prompt ("compose this scene, decide the colors")
# reliably collapses into repeating token loops or rambling explanation on a
# heavily quantized / speculative-decoding model — validated live against
# Qwen/Qwen3.8-27B (q4kv + dflash2) on 2026-09-21; see the design doc. The
# fix that worked: small, explicitly-anchored, mechanical sub-tasks instead
# of one big creative one. These helpers implement that for the "small" tier
# (band-segmented structure, per-row colorize) and are reused by the
# "medium" tier's colorize pass (legend generation, boundary-independent
# parsing).

# Per-style band roles for pass-1 structure, mirroring the same 3-way split
# _build_ascii_prompt already uses (bbs / landscape / everything else).
# Each style maps to exactly 3 bands: (band_label, characters_to_use,
# row_role_phrases). row_role_phrases is a short list describing what a row
# at that RELATIVE position within the band should contain — proportionally
# indexed to whatever the band's actual row count turns out to be, so the
# same table works for any canvas height.
_BAND_ROLE_PHRASES: dict[str, list[tuple[str, str, list[str]]]] = {
    "bbs": [
        ("void top", "space",
         ["all void, empty space"]),
        ("neon subject", "| / \\ o O 0 ( ) - = ^ * # @ X %",
         ["top of the neon icon/sigil, its highest point",
          "upper body of the icon",
          "widest/boldest part of the icon",
          "lower body of the icon, tapering toward its base"]),
        ("void bottom", "space",
         ["all void, empty space"]),
    ],
    "landscape": [
        ("sky", "space . , ' ` ^ - ~",
         ["highest sky, sparse stars or clouds",
          "sky with more texture, drifting clouds",
          "lower sky nearing the horizon, denser cloud texture",
          "horizon line texture, where sky meets terrain"]),
        ("horizon", "| / \\ o O 0 ( ) - = ^",
         ["silhouette breaking the horizon line",
          "main shapes along the horizon",
          "foreground shapes, larger and closer",
          "base of the foreground shapes"]),
        ("terrain", "space _ = ~ # . , :",
         ["terrain or water just past the horizon",
          "midground terrain/water texture",
          "busier foreground terrain/water texture",
          "nearest, densest terrain/water texture"]),
    ],
    "_default": [
        ("background", "space . , : ; - = + ~ / \\",
         ["sparse faint texture, mostly empty space",
          "light texture, a few scattered marks",
          "denser texture, more marks mixed together",
          "densest texture in this band, closest to the subject"]),
        ("subject", "| / \\ o O 0 ( ) - = ^ * # @ X %",
         ["top of the main subject, its highest point",
          "upper body of the subject",
          "middle body of the subject, its widest or most detailed part",
          "lower body of the subject, tapering toward its base"]),
        ("foreground", "space ~ = # X . , :",
         ["structure or ground directly beneath the subject",
          "first layer of ground/base texture",
          "a busier, more turbulent layer of ground/base texture",
          "calmest, plainest layer, farthest from the subject"]),
    ],
}


def _split_bands(height: int, n_bands: int = 3) -> list[int]:
    """Split height rows into n_bands as evenly as possible, giving any
    remainder to the earliest bands. _split_bands(12, 3) == [4, 4, 4];
    _split_bands(20, 3) == [7, 7, 6]."""
    base = height // n_bands
    rem = height % n_bands
    return [base + (1 if i < rem else 0) for i in range(n_bands)]


def _build_band_prompt(subject: str, style: str, band_label: str, chars: str,
                        row_phrases: list[str], width: int, n_rows: int) -> str:
    """Pass 1 (small tier) — one band of the canvas, with an explicit
    content instruction per row (proportionally indexed into row_phrases).
    A generic 'vary each row' instruction was validated to still collapse
    into repetition; naming what a specific row contains did not."""
    hint = _STYLE_HINTS.get(style, _STYLE_HINTS["scene"])
    lines = []
    for i in range(n_rows):
        idx = min(len(row_phrases) - 1, (i * len(row_phrases)) // n_rows)
        lines.append(f"Row {i + 1}: {row_phrases[idx]}")
    row_block = "\n".join(lines)
    return f"""\
Draw the "{band_label}" band of an ASCII art scene: "{subject}".
Style: {hint}
Respond with ONLY a JSON array of exactly {n_rows} strings, each exactly
{width} characters long, using only these characters: {chars}

Row-by-row content:
{row_block}

Output only the JSON array, nothing else.
"""


def _parse_row_json(raw: str, n_rows: int, width: int) -> list[str]:
    """Parse a JSON array of row-strings; fails soft to blank-padded rows
    on any parse error (malformed JSON, no array found, wrong element
    types) rather than raising."""
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    text = re.sub(r"```\w*\s*|```", "", text).strip()
    rows: list[str] = []
    try:
        start = text.index("[")
        end = text.rindex("]")
        arr = json.loads(text[start:end + 1])
        rows = [str(r) for r in arr]
    except Exception:
        rows = text.split("\n")
    out: list[str] = []
    for row in rows[:n_rows]:
        row = row[:width].ljust(width)
        out.append(row)
    while len(out) < n_rows:
        out.append(" " * width)
    return out


def _build_legend_prompt(distinct_chars: str, subject: str, style: str,
                          board_name: str, tagline: str) -> str:
    """One small call that decides colors ONCE, as a lookup table, rather
    than asking the model to decide colors for every cell of a whole canvas
    in one shot (the freeform version of pass 3 that was validated to make
    the model ramble through visible reasoning text instead of ever
    emitting the requested output)."""
    color_guide = _COLOR_GUIDE_BBS if style == "bbs" else _COLOR_GUIDE_SCENE
    board_ctx = ""
    if style == "bbs" and board_name:
        board_ctx = (
            f"\nBBS IDENTITY: Board name: {board_name}"
            + (f"  |  Tagline: {tagline}" if tagline else "")
            + "\nLet the name drive the color theme — neon identity.\n"
        )
    return f"""\
Decide ONE xterm-256 color index (0-255) for each distinct character below,
for a colorized drawing of "{subject}".
{board_ctx}
CHARACTERS PRESENT: {distinct_chars}

{color_guide}

Respond with ONLY a JSON object mapping each character to its color index,
e.g. {{"#": 236, " ": 232}}. Space must always be included. No explanation,
no markdown fences — only the JSON object.
"""


def _parse_legend_json(raw: str) -> dict[str, int]:
    """Parse the legend JSON object; fails soft to an empty dict on any
    parse error (the caller fills in fallback colors for every character
    the model omitted or that failed to parse)."""
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    text = re.sub(r"```\w*\s*|```", "", text).strip()
    try:
        start = text.index("{")
        end = text.rindex("}")
        obj = json.loads(text[start:end + 1])
        return {str(k)[:1]: int(v) for k, v in obj.items() if str(k)}
    except Exception:
        return {}


def _generate_legend(call_fn, block_art: str, subject: str, style: str,
                      board_name: str, tagline: str) -> dict[str, int]:
    """Build the character→color legend for block_art. Never raises: any
    character missing from the model's response (or the whole response
    being unparseable) gets a neutral gray fallback (244), and space
    always resolves to the void color (232)."""
    distinct = "".join(sorted(set(block_art) - {"\n"}))
    raw = call_fn(
        _build_legend_prompt(distinct, subject, style, board_name, tagline),
        max_tokens=400,
    )
    legend = _parse_legend_json(raw)
    for ch in distinct:
        legend.setdefault(ch, 244)
    legend.setdefault(" ", 232)
    return legend


def _build_row_colorize_prompt(row: str, legend: dict[str, int], width: int) -> str:
    """Pass 3 (small tier) — apply an ALREADY-DECIDED legend mechanically to
    one row. This is deliberately NOT 'decide the color for each character'
    (that phrasing was validated to make the model ramble instead of
    comply) — it's 'apply this lookup table', a much smaller and more
    mechanical task."""
    used = sorted(set(row))
    mapping_lines = "\n".join(f"  {ch!r} -> {legend.get(ch, 244)}" for ch in used)
    return f"""\
Wrap each character of this {width}-character row with an ANSI 256-color
foreground escape code: \\033[38;5;Nm<char>
Row: {row}
CHARACTER-TO-COLOR MAPPING (apply exactly, mechanical, do not decide creatively):
{mapping_lines}
Output ONLY the wrapped characters in order, no row-end reset, no
explanation, no markdown.
"""


_CELL_RE = re.compile(r"\x1b\[38;5;(\d+)m(.)")


def _extract_colored_cells(raw: str, expected_n: int) -> list[tuple[str, str]]:
    """Extract (color, char) pairs from a colorize response by regex,
    ignoring wherever the model actually put its line breaks. Validated
    live: a whole-canvas mechanical-apply call reliably dropped row
    boundaries under token pressure even though the (color, char) sequence
    itself stayed correct — trusting the model's own newlines would have
    silently corrupted the grid. Returns at most expected_n pairs."""
    text = raw.replace("\\033", "\x1b").replace("\\x1b", "\x1b").replace("\\e", "\x1b")
    pairs = _CELL_RE.findall(text)
    return pairs[:expected_n]


def _pairs_to_ansi_row(pairs: list[tuple[str, str]], original_row: str,
                        legend: dict[str, int]) -> str:
    """Reconstruct one fully-wrapped ANSI row from extracted (color, char)
    pairs, padding any shortfall (the model returned fewer cells than the
    row is wide) using the row's own original characters and the legend's
    color for them — never raises, never leaves a row short."""
    width = len(original_row)
    cells = list(pairs)
    if len(cells) < width:
        for ch in original_row[len(cells):]:
            cells.append((str(legend.get(ch, 244)), ch))
    cells = cells[:width]
    body = "".join(f"\033[38;5;{c}m{ch}" for c, ch in cells)
    return body + "\033[0m"
```

- [ ] **Step 4: Add `_generate_small` to the `AnsiGenerator` class**

Add this method to `plugins/ansi/plugin.py`, directly after
`_generate_large` (from Task 3):

```python
    def _generate_small(self, call_fn, subject, style, width, height,
                         board_name, tagline) -> str:
        """Band-segmented structure (pass 1) + unchanged whole-canvas block
        refinement (pass 2) + legend-then-per-row-mechanical-apply color
        (pass 3). Validated live against Qwen/Qwen3.8-27B (q4kv + dflash2)
        on 2026-09-21 — see the design doc for the full investigation."""
        print("[small-tier: band-segmented ASCII structure …]", flush=True)
        specs = _BAND_ROLE_PHRASES.get(style, _BAND_ROLE_PHRASES["_default"])
        if style == "bbs":
            band_heights = [2, max(1, height - 4), 2]
        else:
            band_heights = _split_bands(height, 3)

        rows: list[str] = []
        for (band_label, chars, phrases), n_rows in zip(specs, band_heights):
            if n_rows <= 0:
                continue
            raw = call_fn(
                _build_band_prompt(subject, style, band_label, chars,
                                    phrases, width, n_rows),
                max_tokens=max(200, n_rows * 40),
            )
            rows.extend(_parse_row_json(raw, n_rows, width))
        ascii_art = _normalize_grid("\n".join(rows), width, height)

        print("[small-tier: block refinement …]", flush=True)
        raw2 = call_fn(_build_refine_prompt(ascii_art, subject, width, height),
                       max_tokens=1024)
        block_art = _normalize_grid(raw2, width, height)

        print("[small-tier: legend + per-row colorization …]", flush=True)
        legend = _generate_legend(call_fn, block_art, subject, style,
                                   board_name, tagline)
        colored_rows = []
        for row in block_art.split("\n"):
            raw_row = call_fn(_build_row_colorize_prompt(row, legend, width),
                              max_tokens=max(150, width * 12))
            pairs = _extract_colored_cells(raw_row, width)
            colored_rows.append(_pairs_to_ansi_row(pairs, row, legend))
        return "\n".join(colored_rows)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/usr/bin/python3 -m pytest tests/test_artgen_generators.py::TestAnsiGenerator -v`
Expected: PASS (all tests, including every one from Task 3)

- [ ] **Step 6: Commit**

```bash
git add plugins/ansi/plugin.py tests/test_artgen_generators.py
git commit -m "Add small-tier band-segmented + legend-colorize ANSI pipeline"
```

---

## Task 5: Medium-tier pipeline (whole-canvas structure + legend/mechanical-apply colorize)

**Files:**
- Modify: `plugins/ansi/plugin.py`
- Test: `tests/test_artgen_generators.py` (extend `TestAnsiGenerator`)

**Interfaces:**
- Consumes: `_generate_legend`, `_build_legend_prompt`, `_parse_legend_json`,
  `_extract_colored_cells` (Task 4).
- Produces: `AnsiGenerator._generate_medium(self, call_fn, subject, style,
  width, height, board_name, tagline) -> str`, plus module-level
  `_build_mechanical_colorize_prompt`, `_pairs_to_ansi_grid`.

**Note on validation status (carry this into the code comment and do not
soften it):** this tier is a reasoned middle ground, not something tested
against a real medium-tier model — no such model was available during the
investigation behind this plan. Flag it as such in the docstring exactly as
written below.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_artgen_generators.py`, inside `class TestAnsiGenerator`:

```python
    def test_generate_artifact_medium_tier_legend_and_whole_canvas_calls(self):
        # medium tier: pass1 (whole canvas) + pass2 (whole canvas) +
        # legend + 1 mechanical-apply call = 4 calls total.
        calls = []

        def fn(prompt, system=None, max_tokens=None):
            calls.append(prompt)
            n = len(calls)
            if n == 1:
                return "\n".join(["#" * 40] * 20)  # pass 1
            if n == 2:
                return "\n".join(["#" * 40] * 20)  # pass 2
            if n == 3:
                return '{"#": 236, " ": 232}'  # legend
            # pass 3: whole-canvas mechanical apply, well-formed
            row = "".join("\033[38;5;236m#" for _ in range(40)) + "\033[0m\n"
            return row * 20

        args = _args(ansi_style="bbs", subject="test", width=40, height=20,
                     board_name="", tagline="")
        fn.model_tier = "medium"
        result = self.g.generate_artifact(args, fn)

        assert len(calls) == 4
        lines = result.split("\n")
        assert len(lines) == 20
        for line in lines:
            assert line.count("\033[38;5;236m#") == 40

    def test_generate_artifact_medium_tier_pads_shortfall_from_truncated_response(self):
        # Live-observed failure mode: a whole-canvas mechanical-apply call
        # can run out of token budget partway through and drop the
        # remaining cells entirely (no trailing newlines at all, not even a
        # partial row). The grid must still come back the right size.
        calls = []

        def fn(prompt, system=None, max_tokens=None):
            calls.append(prompt)
            n = len(calls)
            if n == 1:
                return "\n".join(["#" * 4] * 3)  # pass 1: 4x3 grid
            if n == 2:
                return "\n".join(["#" * 4] * 3)  # pass 2
            if n == 3:
                return '{"#": 236}'  # legend
            # pass 3: only the first 5 of 12 cells before running out of budget
            return "".join("\033[38;5;236m#" for _ in range(5))

        args = _args(ansi_style="scene", subject="test", width=4, height=None,
                     board_name="", tagline="")
        args.width = 4
        fn.model_tier = "medium"
        # Force a small 4x3 canvas directly via _generate_medium to keep
        # this test's expected sizes simple and explicit.
        result = self.g._generate_medium(fn, "test", "scene", 4, 3, "", "")
        lines = result.split("\n")
        assert len(lines) == 3
        for line in lines:
            assert line.count("\033[38;5;236m#") == 4

    def test_build_mechanical_colorize_prompt_lists_distinct_characters(self):
        from ansi_plugin import _build_mechanical_colorize_prompt
        prompt = _build_mechanical_colorize_prompt(
            "##\n  ", {"#": 236, " ": 232}, width=2, height=2,
        )
        assert "'#' -> 236" in prompt
        assert "' ' -> 232" in prompt

    def test_pairs_to_ansi_grid_pads_shortfall_from_legend(self):
        from ansi_plugin import _pairs_to_ansi_grid
        pairs = [("236", "#")]
        grid = _pairs_to_ansi_grid(pairs, "##\n##", {"#": 236}, width=2, height=2)
        lines = grid.split("\n")
        assert len(lines) == 2
        for line in lines:
            assert line.count("\033[38;5;236m#") == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_artgen_generators.py::TestAnsiGenerator -v`
Expected: FAIL — `AttributeError: 'AnsiGenerator' object has no attribute
'_generate_medium'` and `ImportError: cannot import name
'_build_mechanical_colorize_prompt'` / `_pairs_to_ansi_grid`.

- [ ] **Step 3: Add the medium-tier module-level helpers to `plugins/ansi/plugin.py`**

Add directly after `_pairs_to_ansi_row` (end of the block added in Task 4):

```python
def _build_mechanical_colorize_prompt(block_art: str, legend: dict[str, int],
                                       width: int, height: int) -> str:
    """Pass 3 (medium tier) — apply an already-decided legend to the WHOLE
    canvas in one call, mechanically. Still explicitly 'apply, don't
    decide' (the freeform 'creatively assign colors' phrasing was validated
    to make the model ramble through visible reasoning text instead of
    emitting output at all) — the difference from the small-tier version is
    scope (whole canvas vs. one row), which is why this tier's output is
    parsed the same boundary-independent way rather than trusted to keep
    its row breaks intact.

    UNVALIDATED AGAINST REAL HARDWARE: no medium-tier (8-30B, unquantized)
    model was available during the investigation this pipeline is based on.
    This is a reasoned middle ground between the large tier's single
    freeform colorize call and the small tier's one-call-per-row loop; if a
    medium-tier model still runs this call out of budget in practice, the
    fallback is to route medium tier through the small tier's per-row loop
    instead (see the design doc's Section 3)."""
    used = sorted({ch for ch in block_art if ch != "\n"})
    mapping_lines = "\n".join(f"  {ch!r} -> {legend.get(ch, 244)}" for ch in used)
    return f"""\
Wrap EVERY character below with an ANSI 256-color foreground escape code:
\\033[38;5;Nm<char>

CHARACTER-TO-COLOR MAPPING (apply exactly, mechanical, do not decide creatively):
{mapping_lines}

GRID ({width}x{height}):
{block_art}

Output the wrapped characters in reading order (row by row), nothing else.
No explanation, no markdown, no analysis text.
"""


def _pairs_to_ansi_grid(pairs: list[tuple[str, str]], block_art: str,
                         legend: dict[str, int], width: int, height: int) -> str:
    """Reconstruct a full width×height ANSI grid from extracted (color,
    char) pairs, ignoring the model's own row breaks entirely (they were
    validated live to be unreliable under token pressure) and padding any
    shortfall from block_art's own characters via the legend — never
    raises, never returns a short grid."""
    flat_original = block_art.replace("\n", "")
    total = width * height
    cells = list(pairs)
    if len(cells) < total:
        for ch in flat_original[len(cells):total]:
            cells.append((str(legend.get(ch, 244)), ch))
    cells = cells[:total]
    out_rows = []
    for r in range(height):
        row_cells = cells[r * width:(r + 1) * width]
        body = "".join(f"\033[38;5;{c}m{ch}" for c, ch in row_cells)
        out_rows.append(body + "\033[0m")
    return "\n".join(out_rows)
```

- [ ] **Step 4: Add `_generate_medium` to the `AnsiGenerator` class**

Add directly after `_generate_small`:

```python
    def _generate_medium(self, call_fn, subject, style, width, height,
                          board_name, tagline) -> str:
        """Unchanged whole-canvas structure/refine (passes 1-2, same
        prompts as the large tier — no evidence they fail at this tier)
        plus a legend-then-mechanical-whole-canvas-apply color pass. See
        _build_mechanical_colorize_prompt's docstring for this tier's
        validation status."""
        print("[medium-tier: ASCII structure …]", flush=True)
        raw1 = call_fn(_build_ascii_prompt(subject, width, height, style),
                       max_tokens=1024)
        ascii_art = _normalize_grid(raw1, width, height)

        print("[medium-tier: block refinement …]", flush=True)
        raw2 = call_fn(_build_refine_prompt(ascii_art, subject, width, height),
                       max_tokens=1024)
        block_art = _normalize_grid(raw2, width, height)

        print("[medium-tier: legend + mechanical colorization …]", flush=True)
        legend = _generate_legend(call_fn, block_art, subject, style,
                                   board_name, tagline)
        raw3 = call_fn(
            _build_mechanical_colorize_prompt(block_art, legend, width, height),
            max_tokens=8192,
        )
        pairs = _extract_colored_cells(raw3, width * height)
        return _pairs_to_ansi_grid(pairs, block_art, legend, width, height)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/usr/bin/python3 -m pytest tests/test_artgen_generators.py::TestAnsiGenerator -v`
Expected: PASS (all tests, including every one from Tasks 3 and 4)

- [ ] **Step 6: Commit**

```bash
git add plugins/ansi/plugin.py tests/test_artgen_generators.py
git commit -m "Add medium-tier legend + whole-canvas mechanical-apply ANSI pipeline"
```

---

## Task 6: Mirror into `app/artgen/generators/ansi.py` and run full regression

**Files:**
- Modify: `app/artgen/generators/ansi.py`

**Interfaces:**
- Consumes: nothing new — this task is a mechanical copy of Tasks 3-5's
  final `plugins/ansi/plugin.py` content.
- Produces: nothing new — no other task depends on this file.

`app/artgen/generators/ansi.py` is not the file `plugin_loader` actually
registers at runtime (confirmed: `tests/test_artgen_generators.py` loads
`plugins/ansi/plugin.py` directly, and `plugin_loader` only ever scans
`plugins/`), but CLAUDE.md documents keeping it in sync with the canonical
copy as existing practice (the same pattern already applied to
`animatediff`). This task preserves that practice rather than leaving the
two files to drift.

- [ ] **Step 1: Diff the two files to see exactly what changed**

Run: `diff app/artgen/generators/ansi.py plugins/ansi/plugin.py`
Expected: before this task, the only difference is the pre-existing one
(`from artgen import ArtGenerator, register` + `@register` in the
`app/artgen/generators/` copy vs. `from artgen import ArtGenerator` with no
decorator in `plugins/`) plus every change from Tasks 3-5 that hasn't been
mirrored yet.

- [ ] **Step 2: Mechanically copy the canonical file over, then restore the two intentional differences**

This is a deterministic copy, not a re-description of Tasks 3-5's changes —
the only two lines that must differ between the files are the import line
and the `@register` decorator, so the safest way to guarantee an exact
mirror is to copy the whole file and then re-apply just those two lines.

```bash
cp plugins/ansi/plugin.py app/artgen/generators/ansi.py
```

Then edit `app/artgen/generators/ansi.py`:

```python
from artgen import ArtGenerator
```
becomes:
```python
from artgen import ArtGenerator, register
```

And:
```python
class AnsiGenerator(ArtGenerator):
```
becomes:
```python
@register
class AnsiGenerator(ArtGenerator):
```

- [ ] **Step 3: Confirm the two files are identical apart from the known import/decorator difference**

Run: `diff app/artgen/generators/ansi.py plugins/ansi/plugin.py`
Expected: output shows only the `import` line and `@register` decorator
line as differences — nothing else.

- [ ] **Step 4: Run the full test suite**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q`

Expected: PASS, modulo the three pre-existing environment-level flakes
CLAUDE.md's "Running tests" section already documents as expected
(`test_forge_transforms::test_on_transform_finished_appends_and_refreshes`,
`test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`,
`test_role_zone_panel.py::test_prompt_field_hidden_but_still_collected_for_artgen`)
and the one documented environment skip
(`test_regression_guards` when `docs/assets/` is absent). If any *other*
test fails, stop and investigate before continuing — do not proceed to
Task 7 with an unexplained failure.

- [ ] **Step 5: Commit**

```bash
git add app/artgen/generators/ansi.py
git commit -m "Mirror model-tier-aware ANSI pipeline into app/artgen/generators/ansi.py"
```

---

## Task 7: Version bump, changelog, and CLAUDE.md log entry

**Files:**
- Modify: `VERSION`
- Modify: `debian/changelog`
- Modify: `CLAUDE.md` (project-level, at repo root)

Per this repo's own documented convention ("Version discipline" in
CLAUDE.md): this is a new, user-visible behavior change (ANSI generation
quality on constrained models) — a minor bump.

- [ ] **Step 1: Bump `VERSION`**

Change the file's contents from `0.100.2` to `0.101.0` (single line, no
prefix, no trailing newline changes beyond what the file already has).

- [ ] **Step 2: Prepend a changelog entry to `debian/changelog`**

Add this stanza above the existing `tt-local-generator (0.100.2) noble;
...` entry (match the existing format exactly — run `date -R` if you need
the current RFC-2822 timestamp instead of guessing it):

```
tt-local-generator (0.101.0) noble; urgency=medium

  * feat(artgen): the `ansi` generator now scales its prompting strategy to
    the serving model's capability tier (small/medium/large), derived from
    parsed parameter size and known quantization/speculative-decoding
    markers (app/model_capability.py). A heavily quantized model (validated
    live against a q4kv + dflash2 27B) collapsed the previous whole-canvas
    prompts into repeating token loops or rambling explanation instead of
    ANSI output; band-segmented structure plus legend-then-mechanical-apply
    colorization fixes this without changing behavior for any
    already-working model (the "large" tier is byte-identical to the prior
    single pipeline).

 -- Taylor Singletary <tsingletary@tenstorrent.com>  <RFC-2822 timestamp>

```

- [ ] **Step 3: Append a "what happened" entry to `CLAUDE.md`**

Add a new section near the top of the file's changelog-style narrative
(above the most recent existing dated section, matching this file's
existing house style of one section per shipped change):

```markdown
## Model-capability-aware ANSI prompting (v0.101.0)

Bringing up `Qwen/Qwen3.8-27B` via tt-model-manager (profile
`qwen3.8-27b-dflash2-vision-p300x2-q4kv`) exposed that the `ansi`
generator's whole-canvas 3-pass prompts assume a level of instruction-
following and output diversity a heavily quantized + speculative-decoding
model doesn't have — live testing showed pass 1 collapsing into a repeating
token loop regardless of temperature/penalty tuning, and pass 3's freeform
"creatively assign colors" prompt making the model ramble through visible
reasoning text instead of ever emitting the requested output.

- **`app/model_capability.py`** (new, pure/stdlib) derives a `small` /
  `medium` / `large` tier from a model id: parsed `<N>B` parameter count,
  downgraded one tier by a regex quantization/speculative-decoding hint
  (`q4`/`awq`/`gptq`/`dflash`/etc.) OR an explicit
  `_KNOWN_CONSTRAINED_IDS` override. The override list exists because the
  served `/v1/models` id for this exact case (`Qwen/Qwen3.8-27B`) carries
  **no** quantization signal at all — that lives only in the serving
  container's docker label, which the app doesn't read. Unparseable ids
  default to `large` (never punish a model we haven't characterized).
- **Both `_make_call_fn` implementations** (`app/artgen/cli.py`,
  `app/mcp_server.py`) stamp `.model_tier` onto the `call_fn` closure they
  already build — no change to the `call_fn(prompt, system=,
  max_tokens=)` contract every generator depends on. The MCP path needed a
  different fix than a copy-paste of the CLI one: it discovers
  `(endpoint, model)` lazily inside `_call_fn` on every real call (so a
  server started after the MCP server launches is still picked up), so
  there's no upfront model id to stamp a tier from — fixed with one extra
  upfront detection call at `_make_call_fn` construction time.
- **`plugins/ansi/plugin.py`** (mirrored into
  `app/artgen/generators/ansi.py`, per the existing dual-copy convention)
  reads `getattr(call_fn, "model_tier", "large")` and dispatches to
  `_generate_large` (today's exact pipeline, byte-identical — the
  regression safety net for every currently-working model),
  `_generate_medium` (whole-canvas structure/refine unchanged + a
  legend-then-whole-canvas-mechanical-apply color pass — **not yet
  hardware-validated**, no medium-tier model was available during this
  work), or `_generate_small` (band-segmented structure + a
  legend-then-per-row-mechanical-apply color pass — validated live against
  the real Qwen/Qwen3.8-27B endpoint).
- **The technique that actually worked, validated live:** small,
  explicitly-anchored, mechanical sub-tasks instead of one big creative
  one — band-segmented structure with an explicit per-row content
  instruction (not "vary each row," which still collapsed), a JSON array
  of row-strings instead of a raw grid block, and a fixed
  character→color legend applied mechanically rather than decided
  creatively. Colorize output is parsed by regex-extracting `(color,
  char)` pairs rather than trusting the model's own line breaks, which
  the medium/whole-canvas path was validated to drop under token
  pressure even while the pair sequence itself stayed correct.
- **Audit of the other 9 LLM-backed generators** (not implemented this
  pass — see the design doc's Section 6 for the full ranking): `skyline`
  and `landscape` are the highest-risk for the same repetition-collapse
  failure (skyline asks for up to 28-38 buildings × 2-8 windows each in
  one shot; landscape asks for 35-50 background stars + several cloud/
  mountain layers with exact-coordinate closure rules), `constellation` a
  smaller-scale version of the same risk. `verse`/`emoji-storyteller`/
  `palette`/`circuit`/`codeart`/`geometric`/`freeform` are low-risk or
  risk-neutral by design.
- Spec: `docs/superpowers/specs/2026-09-21-artgen-model-capability-tiering-design.md`.
  Plan: `docs/superpowers/plans/2026-09-22-artgen-model-capability-tiering.md`.
```

- [ ] **Step 4: Commit**

```bash
git add VERSION debian/changelog CLAUDE.md
git commit -m "Bump version to 0.101.0 for model-capability-aware ANSI prompting"
```

---

## Post-plan validation (manual, not part of the automated task steps)

Once hardware is available again, run a live end-to-end check against the
real `Qwen/Qwen3.8-27B` endpoint (the exact scenario that motivated this
plan) via `tt-ctl artgen ansi --subject "..." --ansi-style scene`, and
separately against a known-`large`-tier model (e.g. whatever the box has
running as `artgen-qwen3-8b` or larger) to confirm the `large` path is
still producing output indistinguishable from before this change. This is
deliberately NOT a task with its own checkbox — it requires live hardware
and a gozer lease, which this plan's automated tests don't and shouldn't
depend on.
