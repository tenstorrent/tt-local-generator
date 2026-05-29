# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Vale prose-style plugin — example plugin for tt-local-generator.

Runs the Vale prose linter (https://vale.sh) on text artifacts in three modes:
  annotate  — show Vale suggestions inline in the original text
  suggest   — return only the list of issues
  rewrite   — LLM applies the suggestions automatically (requires a running server)

Install Vale first:
  snap install vale        # Ubuntu
  brew install vale        # macOS

Install this plugin:
  cp -r docs/examples/plugins/vale-style/ ~/.config/tt-local-gen/plugins/

Then launch tt-local-generator — the plugin appears in the Generative Art tab
and the MCP tool registry automatically.

Remix graph:
  verse, freeform → vale-style → video, image, verse

Example (Remix → Vale tighten → use as video seed):
  verse record → Remix → vale-style (rewrite) → output text → Remix → video
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from artgen import ArtGenerator


class ValeStyleGenerator(ArtGenerator):
    name = "vale-style"
    description = "Apply Vale prose style guide to text — tighten, lint, or reformat"
    output_ext = ".txt"

    def add_args(self, parser) -> None:
        parser.add_argument("--text", default="", help="Text to process")
        parser.add_argument(
            "--style", default="Chicago",
            choices=["Chicago", "Microsoft", "Google"],
            help="Vale style guide",
        )
        parser.add_argument(
            "--mode", default="annotate",
            choices=["annotate", "suggest", "rewrite"],
            help="annotate / suggest / rewrite",
        )

    def build_prompt(self, args) -> str:
        """Rewrite prompt — used only in 'rewrite' mode."""
        return (
            f"Rewrite the following text applying {getattr(args, 'style', 'Chicago')} "
            f"style guide rules. Fix passive voice, wordiness, and hedging language. "
            f"Preserve all meaning and intentional line breaks.\n\n"
            f"{getattr(args, 'text', '')}"
        )

    def parse_output(self, raw: str, args) -> str:
        return raw.strip()

    def generate_artifact(self, args, call_fn) -> str:
        text = getattr(args, "text", "") or ""
        style = getattr(args, "style", "Chicago")
        mode = getattr(args, "mode", "annotate")

        if mode == "rewrite":
            raw = call_fn(self.build_prompt(args), max_tokens=512)
            return self.parse_output(raw, args)

        vale = _find_vale()
        if vale is None:
            return (
                "[Vale not installed — install from https://vale.sh]\n\n"
                + text
            )

        tmp_path = None
        cfg_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(text)
                tmp_path = tmp.name

            cfg_path = _write_config(style)
            result = subprocess.run(
                [vale, "--output=JSON", f"--config={cfg_path}", tmp_path],
                capture_output=True, text=True, timeout=30,
                stdin=subprocess.DEVNULL,
            )
            if mode == "suggest":
                return _format_suggestions(result.stdout)
            return _format_annotations(result.stdout, text)

        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return f"[Vale error: {e}]\n\n{text}"
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)
            if cfg_path:
                Path(cfg_path).unlink(missing_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_vale() -> "str | None":
    import shutil
    return shutil.which("vale")


def _write_config(style: str) -> str:
    """Write a minimal Vale config for the given style and return its path.

    Vale styles are installed by the user via `vale sync` and live in
    ~/.local/share/vale/styles (Linux) or ~/Library/Application Support/vale
    (macOS). We tell Vale to look there rather than bundling styles.
    """
    import platform
    if platform.system() == "Darwin":
        styles_dir = Path.home() / "Library" / "Application Support" / "vale" / "styles"
    else:
        styles_dir = Path.home() / ".local" / "share" / "vale" / "styles"

    cfg = (
        f"StylesPath = {styles_dir}\n"
        f"MinAlertLevel = suggestion\n\n"
        f"[*.txt]\n"
        f"BasedOnStyles = {style}\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ini", delete=False, encoding="utf-8"
    ) as f:
        f.write(cfg)
        return f.name


def _format_annotations(vale_json: str, original: str) -> str:
    """Inline Vale suggestions as a comment block appended to the original text."""
    try:
        data = json.loads(vale_json) if vale_json.strip() else {}
    except json.JSONDecodeError:
        return original

    annotations = []
    for _file, issues in data.items():
        for issue in sorted(issues, key=lambda x: x.get("Line", 0)):
            line = issue.get("Line", "?")
            msg = issue.get("Message", "")
            level = issue.get("Severity", "suggestion")
            annotations.append(f"  Line {line} [{level}]: {msg}")

    if not annotations:
        return original + "\n\n[Vale: no style issues found]"
    return original + "\n\n[Vale suggestions]\n" + "\n".join(annotations)


def _format_suggestions(vale_json: str) -> str:
    """Return only the Vale suggestion list, not the original text."""
    try:
        data = json.loads(vale_json) if vale_json.strip() else {}
    except json.JSONDecodeError:
        return "[No output from Vale]"

    lines = []
    for _file, issues in data.items():
        for issue in sorted(issues, key=lambda x: x.get("Line", 0)):
            lines.append(
                f"Line {issue.get('Line', '?')}: "
                f"[{issue.get('Severity', 'info')}] {issue.get('Message', '')}"
            )
    return "\n".join(lines) if lines else "[Vale: no style issues found]"
