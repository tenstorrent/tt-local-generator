# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
Log viewer -- pure-Python helpers (testable) + LogViewerWindow (GTK).

Pure helpers: detect_log_path, shorten_error, parse_run_log_name,
parse_server_log_name, is_error_log, collect_log_files.

LogViewerWindow: sidebar tree (ListBox) + content pane (TextView).
Added in the GTK section below.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# ── Repo and log locations ─────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOGS_DIR  = _REPO_ROOT / "logs"
_ANIMATEDIFF_LOG_DIR = _LOGS_DIR / "animatediff"
_PROMPT_LOG = Path("/tmp/tt_prompt_gen.log")


# ── Pure helpers ───────────────────────────────────────────────────────────────

def detect_log_path(message: str) -> Optional[str]:
    """Extract an absolute log file path from an error message string.

    Looks for lines starting with "Full log:" or "Log:" (case-insensitive).
    Returns None if no path is found.
    """
    for line in message.splitlines():
        stripped = line.strip()
        for prefix in ("Full log:", "Log:"):
            if stripped.lower().startswith(prefix.lower()):
                path = stripped[len(prefix):].strip()
                if path:
                    return path
    return None


def shorten_error(message: str) -> str:
    """Return the first non-path line of *message*, truncated to 80 characters.

    Removes any embedded log path lines so long path strings never reach the
    status label. If the first line exceeds 80 chars, it is truncated with
    the ellipsis character (U+2026).
    """
    lines = [
        ln for ln in message.splitlines()
        if not ln.strip().lower().startswith(("full log:", "log:"))
    ]
    first = (lines[0] if lines else message).strip()
    if len(first) > 80:
        return first[:80] + "…"
    return first


def parse_run_log_name(filename: str) -> tuple[str, str]:
    """Parse an AnimateDiff run log filename into (display_name, date_str).

    Expected format: run_YYYYMMDD_HHMMSS_YYYYMMDD_HHMMSS_<jobid8>.log
    Returns ("run_HH:MM", "Mon D") on success, or (stem, "") on mismatch.
    """
    stem = Path(filename).stem
    m = re.match(r"run_(\d{8})_(\d{6})_\d{8}_\d{6}_[0-9a-f]+$", stem)
    if not m:
        return stem, ""
    date_part, time_part = m.group(1), m.group(2)
    hh, mm = time_part[:2], time_part[2:4]
    try:
        from datetime import datetime
        dt = datetime.strptime(date_part, "%Y%m%d")
        date_str = dt.strftime("%b %-d")
    except ValueError:
        date_str = date_part
    return f"run_{hh}:{mm}", date_str


def parse_server_log_name(filename: str) -> tuple[str, str]:
    """Parse a server log filename into (model_name, date_str).

    Expected format: media_YYYY-MM-DD_HH-MM-SS_<ModelName>_<device>_server.log
    Returns (model_name, date_str) on success, or (stem, "") on mismatch.
    """
    stem = Path(filename).stem
    if stem.endswith("_server"):
        stem = stem[:-7]
    m = re.match(r"media_(\d{4}-\d{2}-\d{2})_\d{2}-\d{2}-\d{2}_(.+)$", stem)
    if not m:
        return stem, ""
    date_raw, rest = m.group(1), m.group(2)
    parts = rest.rsplit("_", 1)
    model = parts[0] if len(parts) == 2 else rest
    try:
        from datetime import datetime
        dt = datetime.strptime(date_raw, "%Y-%m-%d")
        date_str = dt.strftime("%b %-d")
    except ValueError:
        date_str = date_raw
    return model, date_str


def is_error_log(content: str) -> bool:
    """Return True if *content* looks like a failed run log."""
    indicators = ("rc=1", "Traceback (most recent", "FAILED", "exited with rc=")
    return any(ind in content for ind in indicators)


def collect_log_files(
    repo_root: Path = _REPO_ROOT,
    prompt_log: Optional[Path] = _PROMPT_LOG,
) -> list[dict]:
    """Return a list of section dicts for the log tree.

    Each section dict: {"section": str, "files": list[dict]}
    Each file dict:    {"path": str, "name": str, "date": str, "is_error": bool}

    Sections (in order): ANIMATEDIFF, SERVERS, PROMPT, APP
    Missing directories/files are silently skipped.
    """
    sections = []

    # ANIMATEDIFF run logs (exclude the app-level animatediff.log)
    ad_dir = repo_root / "logs" / "animatediff"
    run_logs = sorted(ad_dir.glob("run_*.log"), reverse=True) if ad_dir.exists() else []
    if run_logs:
        files = []
        for p in run_logs:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            name, date = parse_run_log_name(p.name)
            files.append({"path": str(p), "name": name, "date": date,
                          "is_error": is_error_log(content)})
        sections.append({"section": "ANIMATEDIFF", "files": files})

    # SERVERS — media_*_server.log files in repo root
    server_logs = sorted(repo_root.glob("media_*_server.log"), reverse=True)
    if server_logs:
        files = []
        for p in server_logs:
            model, date = parse_server_log_name(p.name)
            files.append({"path": str(p), "name": model, "date": date,
                          "is_error": False})
        sections.append({"section": "SERVERS", "files": files})

    # PROMPT — /tmp/tt_prompt_gen.log (optional, may not exist)
    if prompt_log and prompt_log.exists():
        sections.append({"section": "PROMPT", "files": [
            {"path": str(prompt_log), "name": prompt_log.stem,
             "date": "", "is_error": False}
        ]})

    # APP — the animatediff module log (distinct from run logs)
    app_log = repo_root / "logs" / "animatediff" / "animatediff.log"
    if app_log.exists():
        sections.append({"section": "APP", "files": [
            {"path": str(app_log), "name": "animatediff",
             "date": "", "is_error": False}
        ]})

    return sections
