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
    available as a subcommand in tt-ctl artgen (e.g. `tt-ctl artgen landscape`).
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

    def generate_artifact(self, args: "argparse.Namespace", call_fn) -> str:
        """
        Run the full generation pipeline and return the artifact string.

        call_fn(prompt, system=None, max_tokens=None) -> raw LLM text.

        Default is single-pass: build_prompt → call → parse_output → post_process.
        Override to implement multi-pass pipelines (see AnsiGenerator for an example
        of the structure → refinement → colorization pattern).
        """
        raw = call_fn(self.build_prompt(args))
        return self.post_process(self.parse_output(raw, args), args)

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
    # Normalize: accept both http://host:port and http://host:port/v1 as base_url.
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    # Try OpenAI-compatible /v1/models first; fall back to bare /models.
    for url in (f"{base}/v1/models", f"{base}/models"):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return json.loads(r.read())["data"][0]["id"]
        except Exception:
            continue
    return None


def detect_artgen_endpoint(
    preferred_url: str | None = None,
) -> "tuple[str, str] | tuple[None, None]":
    """Return (base_url, model_id) for the best available artgen LLM.

    Resolution order:
      1. preferred_url  — explicit CLI/UI override
      2. port 8002      — dedicated artgen LLM (Qwen3-8B, Llama, etc.)
      3. port 8001      — prompt-gen server (Qwen3-0.6B; limited but functional)

    Returning port 8001 as fallback means the tool has day-one value even before
    a full artgen server is configured, and automatically upgrades to the best
    model once one is started — no configuration required.

    Returns (None, None) if nothing responds.
    """
    from server_config import server_config as _sc
    seen: set = set()
    candidates: list = []
    for url in filter(None, [
        preferred_url,
        _sc.base_url("artgen"),         # http://localhost:8002
        _sc.base_url("prompt-server"),  # http://localhost:8001
    ]):
        if url not in seen:
            seen.add(url)
            candidates.append(url)
    for url in candidates:
        m = detect_model(url)
        if m:
            return url, m
    return None, None


def call_llm(
    prompt: str,
    model: str,
    base_url: str,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    on_token=None,  # unused — kept for API compatibility
    system: str | None = None,
    timeout: int = 300,
) -> tuple[str, dict]:
    """
    Send *prompt* to an OpenAI-compatible chat endpoint.

    Returns (text, usage) where usage is the API usage dict:
        {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}

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
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read())
    text = body["choices"][0]["message"]["content"] or ""
    usage = body.get("usage", {})
    return text, usage


# ── SVG repair ────────────────────────────────────────────────────────────────


def repair_svg(raw: str) -> str:
    """
    Attempt to recover a truncated SVG.

    Strategy:
    1. Drop the last line if it doesn't end with '>' (cut mid-attribute).
    2. Close unclosed <g> groups.
    3. Append </svg> if missing.

    Returns the (possibly repaired) SVG string, or the original if no <svg>
    opener is found.
    """
    import re as _re

    m = _re.search(r"<svg\b", raw, _re.IGNORECASE)
    if not m:
        return raw

    body = raw[m.start():]

    # Already complete
    if _re.search(r"</svg\s*>", body, _re.IGNORECASE):
        return body

    # Drop incomplete last line (ends mid-token/mid-attribute)
    lines = body.rstrip().splitlines()
    while lines and not lines[-1].rstrip().endswith(">"):
        lines.pop()
    body = "\n".join(lines)

    # Close unclosed <g> groups (self-closing <g/> don't count)
    open_g  = len(_re.findall(r"<g(?:\s[^>]*)?>", body, _re.IGNORECASE))
    close_g = len(_re.findall(r"</g\s*>",          body, _re.IGNORECASE))
    body += "\n" + "\n".join("</g>" for _ in range(max(0, open_g - close_g)))
    body += "\n</svg>"
    return body


# ── Remix support ─────────────────────────────────────────────────────────────

from dataclasses import dataclass as _dataclass


@_dataclass
class RemixContext:
    """
    Carries all context needed to drive a remix operation from one artifact
    type to another.

    Attributes:
        source_record : the originating history record dict (may contain id,
                        prompt, media_type, artifact_path, etc.)
        source_type   : the media/artifact type of the source (e.g. "verse",
                        "palette", "landscape")
        target_type   : the media/artifact type the remix will produce (e.g.
                        "video", "image")
        hint          : pre-extracted text hint forwarded to the target generator
                        as its seed prompt (may be empty string)
    """

    source_record: dict
    source_type: str
    target_type: str
    hint: str


def remix_targets_for(source_type: str) -> list:
    """
    Return a list of PluginDef objects for all loaded plugins that accept
    *source_type* as a remix input.

    Delegates to plugin_loader.all_plugins() so the result always reflects the
    current plugin registry — no caching.  Returns an empty list when no plugins
    accept the given source type or when the plugin registry is empty.

    Args:
        source_type: the artifact/media type produced by the source generator
                     (e.g. "verse", "palette", "landscape").

    Returns:
        Sorted (by name) list of PluginDef whose accepts_remix_from tuple
        contains source_type.
    """
    import plugin_loader
    return [
        p for p in plugin_loader.all_plugins()
        if source_type in p.accepts_remix_from
    ]


def extract_remix_hint(record: dict) -> str:
    """
    Default remix hint extractor — returns the prompt text from a history record.

    Plugin generators may override this by implementing their own extraction
    logic, but the default works for any record that stores a plain-text prompt
    in the "prompt" key (which all built-in generators do).

    Args:
        record: a history record dict, typically loaded from history_store.

    Returns:
        The string value of record["prompt"], or "" if the key is absent.
    """
    return record.get("prompt", "")


# ── Plugin-driven generator loading ──────────────────────────────────────────
# plugin_loader scans plugins/ and ~/.config/tt-local-gen/plugins/.
# _GENERATORS is back-filled so existing code using artgen.get() and
# artgen.all_names() continues to work.


def _load_generators() -> None:
    import plugin_loader
    plugin_loader.load_plugins()
    for name, pdef in plugin_loader._PLUGINS.items():
        _GENERATORS[name] = pdef.generator


_load_generators()
