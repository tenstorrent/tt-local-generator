"""
Code-as-art generator — source code written as an art form.

Produces aesthetically striking yet real source code, steered by a free-text
inspiration and an optional style. `should_compile` is a prompt directive (an
earnest attempt at runnable code), not an enforced gate. Python output is
additionally checked with a safe, parse-only validator whose result is recorded
as metadata — the generated code is NEVER executed.
"""

from __future__ import annotations

import argparse
import ast
import re

from artgen import ArtGenerator

# ── Style presets ──────────────────────────────────────────────────────────
# "auto" = no bias (empty hint). Every other style appends a short, concrete
# instruction to the system prompt.
_STYLES: dict[str, str] = {
    "auto": "",
    "quine": (
        "Write a quine: a program whose only behaviour is to print its own "
        "complete source code exactly."
    ),
    "ascii": (
        "Lay the source out so its visual shape forms ASCII art related to the "
        "theme, while remaining valid code."
    ),
    "poem": (
        "Make the code read like a poem — identifiers, string literals, and "
        "structure should carry rhythm and imagery — while remaining valid code."
    ),
    "oneliner": (
        "Express the whole idea as a single dense, elegant line of code."
    ),
    "glitch": (
        "Make the code cryptic and obfuscated yet beautiful — surprising and "
        "dense, and still valid."
    ),
    "unusually_verbose": (
        "Be deliberately, expressively over-verbose: long descriptive names, "
        "explicit intermediate variables, and narrative comments. Verbosity is "
        "the aesthetic. Keep it valid."
    ),
    "function_oriented": (
        "Decompose the solution into many tiny, single-purpose, well-named "
        "functions so that the top-level sequence of calls reads as the art — "
        "the composition of calls is the poem."
    ),
}

_DEFAULT_LANGUAGE = "python"
_DEFAULT_INSPIRATION = "the nature of recursion"


def _build_messages(args: argparse.Namespace) -> "tuple[str, str]":
    """Return (system_prompt, user_message) for a code-art generation."""
    language = getattr(args, "language", _DEFAULT_LANGUAGE) or _DEFAULT_LANGUAGE
    inspiration = (
        getattr(args, "inspiration", _DEFAULT_INSPIRATION) or _DEFAULT_INSPIRATION
    )
    style = getattr(args, "style", "auto") or "auto"
    should_compile = getattr(args, "should_compile", True)

    system_parts = [
        "You write source code as an art form: aesthetically striking yet real "
        f"{language} code. Output only the code — no prose, no explanation, and "
        "no markdown fences.",
    ]
    style_hint = _STYLES.get(style, "")
    if style_hint:
        system_parts.append(style_hint)
    if should_compile:
        system_parts.append(
            f"The code must be valid, complete {language} that compiles and runs "
            "as-is — make an earnest, correct attempt."
        )
    else:
        system_parts.append("Aesthetics may take precedence over strict correctness.")
    system = " ".join(system_parts)

    user = f"Write {language} code as art on the theme: {inspiration}."
    return system, user


def validate_python(src: str) -> "tuple[bool, str | None]":
    """Safely check whether *src* is syntactically valid Python.

    Uses ast.parse ONLY — the code is never executed, imported, or compiled to a
    runnable object. Returns (ok, error_message).
    """
    try:
        ast.parse(src)
        return True, None
    except SyntaxError as e:
        return False, str(e)
    except Exception as e:  # pragma: no cover - defensive; never fail generation
        return False, str(e)
