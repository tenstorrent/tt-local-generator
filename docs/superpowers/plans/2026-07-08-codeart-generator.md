# codeart Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `codeart` artgen plugin that generates source code as art, steered by `language`, `inspiration`, an optional `style`, and a `should_compile` prompt directive, with safe Python-only validation reported as metadata.

**Architecture:** A standard local artgen plugin — `plugins/codeart/` with `plugin.py` (a `CodeArtGenerator(ArtGenerator)` subclass) and `mcp.json` (manifest + tool schema). It overrides `generate_artifact` to pass a system prompt directly to `call_fn` (the `verse` pattern), validates Python output with `ast.parse` (never executed), and stashes the result on `args` so the panel persists it automatically. Auto-discovered into the GUI artgen panel and MCP surface — no wiring.

**Tech Stack:** Python 3.12 (system `/usr/bin/python3`), stdlib only (`argparse`, `ast`, `re`, `json`); `ArtGenerator` base from `app/artgen/__init__.py`; pytest.

## Global Constraints

- Python source lives in `plugins/<name>/`; tests in `tests/` at repo root (each test file does `sys.path.insert(0, str(Path(__file__).parent.parent / "app"))` — and, to load a plugin module, appends the plugin dir too).
- Use the **system** python: run tests with `/usr/bin/python3 -m pytest`. GTK-touching tests run under `xvfb-run --auto-servernum`; these codeart tests do **not** import GTK, so plain `/usr/bin/python3 -m pytest` is fine.
- Never execute, `exec`, `eval`, `compile`-to-run, or import generated code. Validation is `ast.parse` only.
- Follow the existing plugin conventions exactly (compare `plugins/verse/plugin.py` and `plugins/verse/mcp.json`).
- Version discipline: this is a new user-visible feature → **minor bump** `0.9.2` → `0.10.0` (edit `VERSION` and prepend a `debian/changelog` stanza).
- Style set (exact, order matters for the schema enum): `auto`, `quine`, `ascii`, `poem`, `oneliner`, `glitch`, `unusually_verbose`, `function_oriented`.
- Defaults: `--language` = `python`, `--inspiration` = `"the nature of recursion"`, `--style` = `auto`, `--should-compile` default **True** (via `argparse.BooleanOptionalAction`).

---

## File Structure

- Create: `plugins/codeart/plugin.py` — the generator (module helpers + `CodeArtGenerator`).
- Create: `plugins/codeart/mcp.json` — plugin manifest + `codeart` tool schema.
- Create: `tests/test_codeart_generator.py` — all unit tests for the plugin.
- Modify: `VERSION` — bump to `0.10.0`.
- Modify: `debian/changelog` — new `0.10.0` stanza.

---

### Task 1: Module helpers — styles, prompt builder, Python validator

**Files:**
- Create: `plugins/codeart/plugin.py` (module-level portion only in this task)
- Test: `tests/test_codeart_generator.py`

**Interfaces:**
- Produces:
  - `_STYLES: dict[str, str]` — keys are the 8 style names; `_STYLES["auto"] == ""`.
  - `_DEFAULT_LANGUAGE = "python"`, `_DEFAULT_INSPIRATION = "the nature of recursion"`.
  - `_build_messages(args) -> tuple[str, str]` returning `(system, user)`.
  - `validate_python(src: str) -> tuple[bool, str | None]` returning `(ok, error)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_codeart_generator.py`:

```python
"""Unit tests for the codeart (code-as-art) artgen plugin."""
import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
_CODEART_PLUGIN = Path(__file__).parent.parent / "plugins" / "codeart" / "plugin.py"


def _load():
    """Load plugins/codeart/plugin.py fresh, bypassing the sys.modules cache."""
    spec = importlib.util.spec_from_file_location("codeart_plugin", _CODEART_PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**kw):
    ns = argparse.Namespace()
    # sensible defaults so getattr() in the plugin always resolves
    ns.language = "python"
    ns.inspiration = "the nature of recursion"
    ns.style = "auto"
    ns.should_compile = True
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class TestHelpers:
    @pytest.fixture(autouse=True)
    def mod(self):
        self.m = _load()

    def test_styles_has_eight_including_auto(self):
        assert set(self.m._STYLES) == {
            "auto", "quine", "ascii", "poem", "oneliner",
            "glitch", "unusually_verbose", "function_oriented",
        }
        assert self.m._STYLES["auto"] == ""

    def test_build_messages_user_has_language_and_inspiration(self):
        system, user = self.m._build_messages(_args(language="rust", inspiration="the tide"))
        assert "rust" in user
        assert "the tide" in user

    def test_should_compile_directive_present_when_true(self):
        system, _ = self.m._build_messages(_args(should_compile=True))
        assert "compiles" in system.lower()

    def test_should_compile_directive_absent_when_false(self):
        system, _ = self.m._build_messages(_args(should_compile=False))
        assert "compiles and runs as-is" not in system.lower()

    def test_style_auto_adds_no_style_hint(self):
        system_auto, _ = self.m._build_messages(_args(style="auto"))
        system_quine, _ = self.m._build_messages(_args(style="quine"))
        assert len(system_quine) > len(system_auto)
        assert "quine" in system_quine.lower()

    def test_validate_python_accepts_valid(self):
        ok, err = self.m.validate_python("def f(x):\n    return x * 2\n")
        assert ok is True
        assert err is None

    def test_validate_python_rejects_invalid(self):
        ok, err = self.m.validate_python("def f(:\n  pass")
        assert ok is False
        assert isinstance(err, str) and err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_codeart_generator.py::TestHelpers -q`
Expected: FAIL — `plugins/codeart/plugin.py` does not exist (spec load error / FileNotFoundError).

- [ ] **Step 3: Write minimal implementation**

Create `plugins/codeart/plugin.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/usr/bin/python3 -m pytest tests/test_codeart_generator.py::TestHelpers -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add plugins/codeart/plugin.py tests/test_codeart_generator.py
git commit -m "feat(codeart): style presets, prompt builder, safe Python validator"
```

---

### Task 2: CodeArtGenerator class

**Files:**
- Modify: `plugins/codeart/plugin.py` (append the class)
- Test: `tests/test_codeart_generator.py` (add a `TestCodeArtGenerator` class)

**Interfaces:**
- Consumes: `_build_messages`, `validate_python`, `_STYLES`, `_DEFAULT_LANGUAGE` from Task 1.
- Produces: `CodeArtGenerator(ArtGenerator)` with:
  - `name = "codeart"`, `output_ext = ".py"`
  - `add_args(parser)` adds `--language`, `--inspiration`, `--style`, `--should-compile/--no-should-compile`
  - `build_prompt(args) -> str` (the user message)
  - `parse_output(raw, args) -> str` (strips `<think>` blocks and ``` fences)
  - `post_process(artifact, args) -> str` (sets `args._codeart_compiles` bool/None and `args._codeart_error` str/None; returns artifact unchanged)
  - `generate_artifact(args, call_fn) -> str` (calls `call_fn(user, system=system)`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_codeart_generator.py`:

```python
from unittest.mock import MagicMock


class TestCodeArtGenerator:
    @pytest.fixture(autouse=True)
    def gen(self):
        self.g = _load().CodeArtGenerator()

    def test_identity(self):
        assert self.g.name == "codeart"
        assert self.g.output_ext == ".py"

    def test_add_args_wires_defaults(self):
        p = argparse.ArgumentParser()
        self.g.add_args(p)
        ns = p.parse_args([])
        assert ns.language == "python"
        assert ns.inspiration == "the nature of recursion"
        assert ns.style == "auto"
        assert ns.should_compile is True

    def test_add_args_no_should_compile_flag(self):
        p = argparse.ArgumentParser()
        self.g.add_args(p)
        ns = p.parse_args(["--no-should-compile"])
        assert ns.should_compile is False

    def test_build_prompt_returns_user_message(self):
        out = self.g.build_prompt(_args(language="c", inspiration="entropy"))
        assert isinstance(out, str)
        assert "c" in out and "entropy" in out

    def test_parse_output_strips_fences_and_think(self):
        raw = "<think>plan</think>\n```python\nprint('hi')\n```"
        out = self.g.parse_output(raw, _args())
        assert "```" not in out
        assert "<think>" not in out
        assert "print('hi')" in out

    def test_post_process_flags_valid_python(self):
        args = _args(language="python")
        out = self.g.post_process("x = 1\n", args)
        assert out == "x = 1\n"                    # unchanged
        assert args._codeart_compiles is True
        assert args._codeart_error is None

    def test_post_process_flags_invalid_python(self):
        args = _args(language="python")
        self.g.post_process("def (:\n", args)
        assert args._codeart_compiles is False
        assert isinstance(args._codeart_error, str)

    def test_post_process_non_python_unvalidated(self):
        args = _args(language="rust")
        self.g.post_process("fn main() {}", args)
        assert args._codeart_compiles is None
        assert args._codeart_error is None

    def test_generate_artifact_end_to_end(self):
        args = _args(language="python", inspiration="mirrors", style="quine")
        call_fn = MagicMock(return_value="```python\nprint(open(__file__).read())\n```")
        artifact = self.g.generate_artifact(args, call_fn)
        # system prompt was passed through
        _, kwargs = call_fn.call_args
        assert "system" in kwargs and "quine" in kwargs["system"].lower()
        # cleaned + validated
        assert "```" not in artifact
        assert args._codeart_compiles is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_codeart_generator.py::TestCodeArtGenerator -q`
Expected: FAIL — `module 'codeart_plugin' has no attribute 'CodeArtGenerator'`.

- [ ] **Step 3: Write minimal implementation**

Append to `plugins/codeart/plugin.py`:

```python
class CodeArtGenerator(ArtGenerator):
    name = "codeart"
    description = (
        "Source code as art: quines, code-poems, ascii-shaped source, and more"
    )
    output_ext = ".py"

    def add_args(self, parser: "argparse.ArgumentParser") -> None:
        parser.add_argument(
            "--language", default=_DEFAULT_LANGUAGE,
            help=f"Target language (default: {_DEFAULT_LANGUAGE}). "
                 "Validation is Python-only.",
        )
        parser.add_argument(
            "--inspiration", default=_DEFAULT_INSPIRATION,
            help=f'Thematic seed (default: "{_DEFAULT_INSPIRATION}")',
        )
        parser.add_argument(
            "--style", choices=list(_STYLES), default="auto",
            help="Art style bias (default: auto = open prompt)",
        )
        parser.add_argument(
            "--should-compile", dest="should_compile",
            action=argparse.BooleanOptionalAction, default=True,
            help="Directive: ask for code that compiles/runs (default: on)",
        )

    def build_prompt(self, args: "argparse.Namespace") -> str:
        """Return the user message (also used by --simulate)."""
        _, user = _build_messages(args)
        return user

    def parse_output(self, raw: str, args: "argparse.Namespace") -> str:
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        cleaned = re.sub(r"```\w*\s*|```", "", cleaned).strip()
        return cleaned

    def post_process(self, artifact: str, args: "argparse.Namespace") -> str:
        """Validate Python output (parse-only) and stash the result on args.

        Non-Python languages are left unvalidated (None). Never raises, never
        executes the code. Returns the artifact unchanged.
        """
        language = (getattr(args, "language", _DEFAULT_LANGUAGE) or "").strip().lower()
        if language.startswith("python"):
            ok, err = validate_python(artifact)
            args._codeart_compiles = ok
            args._codeart_error = err
        else:
            args._codeart_compiles = None
            args._codeart_error = None
        return artifact

    def generate_artifact(self, args: "argparse.Namespace", call_fn) -> str:
        system, user = _build_messages(args)
        raw = call_fn(user, system=system)
        return self.post_process(self.parse_output(raw, args), args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/usr/bin/python3 -m pytest tests/test_codeart_generator.py -q`
Expected: PASS (all TestHelpers + TestCodeArtGenerator)

- [ ] **Step 5: Commit**

```bash
git add plugins/codeart/plugin.py tests/test_codeart_generator.py
git commit -m "feat(codeart): CodeArtGenerator (args, parse, validate, generate)"
```

---

### Task 3: Plugin manifest + loader discovery

**Files:**
- Create: `plugins/codeart/mcp.json`
- Test: `tests/test_codeart_generator.py` (add a `TestManifestAndDiscovery` class)

**Interfaces:**
- Consumes: `CodeArtGenerator` (Task 2); `plugin_loader` from `app/`.
- Produces: a valid `mcp.json` so `plugin_loader.load_plugins()` registers `codeart` and exposes its tool schema (used by the GUI panel and MCP server).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_codeart_generator.py`:

```python
import json


class TestManifestAndDiscovery:
    _MANIFEST = Path(__file__).parent.parent / "plugins" / "codeart" / "mcp.json"

    def test_manifest_is_valid_generator_manifest(self):
        m = json.loads(self._MANIFEST.read_text())
        assert m["x-ttlg"]["output_ext"] == ".py"
        assert m["x-ttlg"]["media_type"] == "text"
        assert m["x-ttlg"]["tab"] == "generative-art"
        tool = m["tools"][0]
        assert tool["name"] == "codeart"
        assert tool["x-ttlg"]["artifact_tool"] is True
        props = tool["inputSchema"]["properties"]
        assert set(props) == {"language", "inspiration", "style", "should_compile"}
        assert props["style"]["enum"] == [
            "auto", "quine", "ascii", "poem", "oneliner",
            "glitch", "unusually_verbose", "function_oriented",
        ]
        assert props["should_compile"]["default"] is True

    def test_loader_discovers_codeart(self):
        import plugin_loader
        # Load from the real repo plugins/ dir only.
        repo_plugins = Path(__file__).parent.parent / "plugins"
        plugin_loader._SEARCH_PATHS[:] = [repo_plugins]
        try:
            plugin_loader.load_plugins()
            assert "codeart" in plugin_loader._PLUGINS
            pdef = plugin_loader._PLUGINS["codeart"]
            assert pdef.generator.name == "codeart"
            assert pdef.runnable is True
        finally:
            plugin_loader._PLUGINS.clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/usr/bin/python3 -m pytest tests/test_codeart_generator.py::TestManifestAndDiscovery -q`
Expected: FAIL — `plugins/codeart/mcp.json` does not exist (FileNotFoundError / codeart missing from `_PLUGINS`).

- [ ] **Step 3: Write minimal implementation**

Create `plugins/codeart/mcp.json`:

```json
{
  "x-ttlg": {
    "output_ext": ".py",
    "media_type": "text",
    "accepts_remix_from": [],
    "can_remix_to": ["image", "video"],
    "tab": "generative-art",
    "hardware": null
  },
  "tools": [
    {
      "name": "codeart",
      "description": "Source code as art: quines, code-poems, ascii-shaped source, verbose or function-oriented compositions",
      "inputSchema": {
        "type": "object",
        "properties": {
          "language": {
            "type": "string",
            "default": "python",
            "description": "Target language (validation is Python-only)"
          },
          "inspiration": {
            "type": "string",
            "default": "the nature of recursion",
            "description": "Thematic seed"
          },
          "style": {
            "type": "string",
            "enum": ["auto", "quine", "ascii", "poem", "oneliner", "glitch", "unusually_verbose", "function_oriented"],
            "default": "auto",
            "description": "Art style bias"
          },
          "should_compile": {
            "type": "boolean",
            "default": true,
            "description": "Directive: ask for code that compiles/runs"
          }
        },
        "required": []
      },
      "examples": [
        {"style": "quine", "inspiration": "a mirror"},
        {"style": "poem", "language": "python", "inspiration": "the tide"},
        {"style": "function_oriented", "inspiration": "making a pot of tea"}
      ],
      "x-ttlg": {"artifact_tool": true}
    }
  ]
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/usr/bin/python3 -m pytest tests/test_codeart_generator.py -q`
Expected: PASS (all classes)

- [ ] **Step 5: Verify it also passes the existing plugin-invariant suite**

Run: `/usr/bin/python3 -m pytest tests/test_plugin_loader.py tests/test_forge_plugins.py tests/test_mcp_server.py -q`
Expected: PASS (no regressions; codeart is a generator plugin, not a utility plugin, so the utility-only assertions do not apply to it)

- [ ] **Step 6: Commit**

```bash
git add plugins/codeart/mcp.json tests/test_codeart_generator.py
git commit -m "feat(codeart): mcp.json manifest + loader discovery"
```

---

### Task 4: End-to-end smoke, version bump, changelog

**Files:**
- Modify: `VERSION`
- Modify: `debian/changelog`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Smoke-test generation via the CLI (mocked LLM not required — use --simulate)**

Run: `/usr/bin/python3 app/artgen/cli.py codeart --simulate --style quine --inspiration "a mirror"`
Expected: prints the pass-1 (user) prompt containing "a mirror" and exits 0 (no server needed).
(If `cli.py` is not directly runnable, invoke via the project's artgen entry point: `./tt-ctl artgen codeart --simulate --style quine`. Use whichever the repo exposes — confirm by reading `app/artgen/cli.py`'s `__main__`/argparse wiring first.)

- [ ] **Step 2: Run the full suite (GTK tests need xvfb)**

Run: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q`
Expected: all pass except the known environment skip; no new failures. Confirm the codeart tests are included in the count.

- [ ] **Step 3: Bump the version**

Set `VERSION` to a single line: `0.10.0`

- [ ] **Step 4: Prepend a changelog stanza**

Prepend to `debian/changelog`:

```
tt-local-generator (0.10.0) noble; urgency=medium

  * artgen: new "codeart" generator — source code as art. Args: --language,
    --inspiration, --style (auto/quine/ascii/poem/oneliner/glitch/
    unusually_verbose/function_oriented), and --should-compile (a prompt
    directive, on by default). Python output is checked with a safe parse-only
    validator (ast.parse, never executed); the result is recorded in the
    artifact's params. Appears automatically in the GUI artgen panel and over
    MCP.

 -- Taylor Singletary <tsingletary@tenstorrent.com>  Wed, 08 Jul 2026 00:00:00 +0000

```

- [ ] **Step 5: Commit**

```bash
git add VERSION debian/changelog
git commit -m "chore: release 0.10.0 — codeart generator"
```

---

## Self-Review

**Spec coverage:**
- Args (language/inspiration/style/should_compile) → Task 2 `add_args` + Task 1 `_build_messages`. ✓
- Default open prompt + optional style (8 presets) → Task 1 `_STYLES`, Task 2 schema. ✓
- `should_compile` as directive only, no retry → Task 1 `_build_messages` (prompt line only); no loop anywhere. ✓
- Python-only, parse-only, never-executed validation → Task 1 `validate_python` (ast.parse) + Task 2 `post_process`. ✓
- Result persisted as metadata, no panel change → Task 2 stashes `_codeart_compiles`/`_codeart_error` on args; relies on existing `params = vars(args)` serialization (verified in `app/artgen_panel.py`). ✓
- System prompt across GUI/CLI/MCP → Task 2 `generate_artifact` passes `system=` to `call_fn` (verse pattern). ✓
- Auto-discovery in GUI + MCP → Task 3 `mcp.json` + loader test. ✓
- `output_ext = ".py"` v1 → Task 2. ✓
- Testing mirrors `test_artgen_generators.py` → Task 1–3 tests. ✓
- Future work (per-language ext, compiled-lang validation, retry loop, highlighting) → explicitly out of scope; not implemented. ✓

**Placeholder scan:** No TBD/TODO; every code step contains full code; every command has expected output. Task 4 Step 1 notes to confirm the exact CLI entry point by reading `app/artgen/cli.py` — this is a real read, not a placeholder (the fallback command is given).

**Type consistency:** `_build_messages(args) -> (system, user)`, `validate_python(src) -> (bool, str|None)`, `_codeart_compiles` (bool|None), `_codeart_error` (str|None), `CodeArtGenerator.name == "codeart"`, `output_ext == ".py"`, style enum identical in `_STYLES`, `add_args` choices, and `mcp.json` — all consistent across tasks.
