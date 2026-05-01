#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
artgen — Generative art via LLM structured prompts.

Each artifact type is a generator module in artgen/generators/.  All generators
share a common protocol (ArtGenerator base class) so the tt-ctl artgen command
can route to any of them uniformly.

Usage (from tt-ctl):
    tt-ctl artgen landscape --palette sunset
    tt-ctl artgen verse --form haiku --theme "winter forges"
    tt-ctl artgen freeform --freeform "a constellation map of invented stars"
    tt-ctl artgen landscape --simulate

Requires a chat/text LLM on port 8002 (separate from the diffusion server on
port 8000). Start one with: python3 app/prompt_server.py --port 8002
or override per-run: tt-ctl artgen landscape --base-url http://localhost:8000/v1
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
    base = base_url.rstrip("/")
    # Try OpenAI-style /v1/models first; fall back to bare /models.
    for url in (f"{base}/v1/models", f"{base}/models"):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return json.loads(r.read())["data"][0]["id"]
        except Exception:
            continue
    return None


def call_llm(
    prompt: str,
    model: str,
    base_url: str,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    on_token=None,  # unused — kept for API compatibility
    system: str | None = None,
) -> str:
    """
    Send *prompt* to an OpenAI-compatible chat endpoint and return the response.

    Uses urllib.request (stdlib only) so it is safe to call from GTK background
    threads — the openai/httpx client interacts poorly with GLib's event loop.

    Qwen3 models default to extended thinking which silently consumes thousands
    of tokens before any real output.  chat_template_kwargs disables it so the
    full token budget goes to the artifact.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if "qwen3" in model.lower():
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    base = base_url.rstrip("/")
    # Accept both http://host:port and http://host:port/v1 as base_url.
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    url = f"{base}/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer none"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        body = json.loads(r.read())
    return body["choices"][0]["message"]["content"] or ""


# ── Lazy generator import ─────────────────────────────────────────────────────
# Import all generators so their @register decorators fire.  Done lazily here
# so importing artgen itself doesn't fail if a generator has a missing dep.

def _load_generators() -> None:
    from artgen.generators import (  # noqa: F401
        landscape, skyline, constellation, geometric,
        ansi, palette, verse, circuit, freeform,
    )


_load_generators()
