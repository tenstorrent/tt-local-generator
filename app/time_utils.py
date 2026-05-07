#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
time_utils — UTC storage helpers and local-time display formatters.

All timestamps written to the database or record files are UTC ISO 8601.
All timestamps shown in the UI are converted to the user's local timezone
and rendered in 12-hour format (e.g. "May 6  3:42 PM").

Rule: always call utc_now_iso() when creating a timestamp; always call one
of the fmt_local_* functions when displaying a timestamp.
"""

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string (with +00:00 offset)."""
    return datetime.now(timezone.utc).isoformat()


def utc_now_file_ts() -> str:
    """
    Return the current UTC time as a compact string suitable for filenames.
    Format: YYYYMMDD_HHMMSS  (UTC, no timezone suffix so filenames stay simple).
    """
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _parse_utc(iso: str) -> datetime | None:
    """
    Parse an ISO 8601 timestamp string, returning a timezone-aware UTC datetime.

    Handles:
      - Strings with a UTC offset: "2026-05-06T14:42:00+00:00"
      - Strings with a Z suffix:   "2026-05-06T14:42:00Z"
      - Naive strings (no offset): assumed to be UTC for backward compatibility
        with records written before this module was introduced.
    """
    if not iso:
        return None
    try:
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def fmt_local_12h(iso: str) -> str:
    """
    Full date + 12-hour time in the user's local timezone.
    Example: "May 6  3:42 PM"
    Falls back to the raw string on parse error.
    """
    dt = _parse_utc(iso)
    if dt is None:
        return iso[:16] if iso else "—"
    local = dt.astimezone()
    return local.strftime("%b %-d  %-I:%M %p")


def fmt_local_date(iso: str) -> str:
    """
    Date only in the user's local timezone.
    Example: "May 6"
    Falls back to the first 10 characters of the raw string on parse error.
    """
    dt = _parse_utc(iso)
    if dt is None:
        return iso[:10] if iso else "—"
    local = dt.astimezone()
    return local.strftime("%b %-d")


def fmt_local_time(iso: str) -> str:
    """
    Time only in the user's local timezone, 12-hour format.
    Example: "3:42 PM"
    Falls back to empty string on parse error.
    """
    dt = _parse_utc(iso)
    if dt is None:
        return ""
    local = dt.astimezone()
    return local.strftime("%-I:%M %p")
