#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
artgen — Generative art via LLM structured prompts.

Each artifact type is a generator module in artgen/generators/.  All generators
share a common protocol (ArtGenerator base class) so the tt-ctl artgen command
can route to any of them uniformly.

Usage (from tt-ctl):
    tt-ctl artgen --type landscape --palette sunset
    tt-ctl artgen --type verse --form haiku --theme "winter forges"
    tt-ctl artgen --freeform "a constellation map of invented stars"
    tt-ctl artgen --type landscape --simulate
"""

from __future__ import annotations

import json
import sys
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

# ── Base class ────────────────────────────────────────────────────────────────


class ArtGenerator(ABC):
    """
    Protocol every artifact generator must implement.

    Subclass this, decorate with @register, and the generator is automatically
    available as --type <name> in tt-ctl artgen.
    """

    #: Short CLI name: "landscape", "skyline", "verse", …
    name: str
    #: One-line description shown in --help
    description: str
    #: Default output extension: ".svg", ".txt", ".ans"
    output_ext: str = ".txt"

    def add_args(self, parser: "argparse.ArgumentParser") -> None:
        """Add generator-specific flags to the shared artgen argparse parser."""

    @abstractmethod
    def build_prompt(self, args: "argparse.Namespace") -> str:
        """Return the user message to send to the LLM."""

    def parse_output(self, raw: str, args: "argparse.Namespace") -> str:
        """
        Extract the artifact from the raw LLM response.
        Default: strip markdown code fences and surrounding whitespace.
        Override for format-specific validation (e.g. SVG well-formedness check).
        """
        import re
        return re.sub(r"```\w*\s*|```", "", raw).strip()

    def post_process(self, artifact: str, args: "argparse.Namespace") -> str:
        """
        Optional in-place transforms after parsing (e.g. glitch effects).
        Default: pass-through.
        """
        return artifact

    def default_output(self) -> Path:
        """Default output path when --output is not specified."""
        return Path(f"{self.name}{self.output_ext}")


# ── Registry ──────────────────────────────────────────────────────────────────

_GENERATORS: dict[str, ArtGenerator] = {}


def register(cls: type) -> type:
    """Class decorator — instantiate and add to the generator registry."""
    g = cls()
    _GENERATORS[g.name] = g
    return cls


def get(name: str) -> ArtGenerator:
    """Return the generator for *name*, or raise KeyError."""
    return _GENERATORS[name]


def all_names() -> list[str]:
    """Sorted list of registered generator names."""
    return sorted(_GENERATORS)


def all_generators() -> list[ArtGenerator]:
    """Sorted list of registered generator instances."""
    return [_GENERATORS[n] for n in all_names()]


# ── LLM client ────────────────────────────────────────────────────────────────
# Uses server_config for the endpoint, same pattern as the rest of the app.


def detect_model(base_url: str) -> str | None:
    """Return the model ID currently loaded on the server, or None."""
    try:
        url = f"{base_url.rstrip('/')}/models"
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())["data"][0]["id"]
    except Exception:
        return None


def call_llm(
    prompt: str,
    model: str,
    base_url: str,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """
    Send *prompt* to an OpenAI-compatible endpoint and return the response text.
    Works with vLLM, Ollama, the CPU prompt server, or any compatible API.
    """
    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: openai not installed. Run: pip install openai", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(base_url=base_url, api_key="none")
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


# ── Lazy generator import ─────────────────────────────────────────────────────
# Import all generators so their @register decorators fire.  Done lazily here
# so importing artgen itself doesn't fail if a generator has a missing dep.

def _load_generators() -> None:
    from artgen.generators import (  # noqa: F401
        landscape, skyline, constellation, geometric,
        ansi, palette, verse, circuit, freeform,
    )


_load_generators()
