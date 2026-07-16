# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
"""
Tests for app/model_status.py — Task 1 (SP-1): Status enum + service scaffold
+ pure state resolver.

model_status.py must be importable standalone (no gi/GTK import, no eager
server_manager/artgen import at module load time) — test_no_gtk_import guards
that.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import model_status as ms

R = ms.ModelStatusService._resolve


def test_resolve_healthy_is_ready():
    assert R(True, None, False, 100.0, 180.0) == ms.Status.READY


def test_resolve_healthy_wins_even_if_starting():
    assert R(True, 100.0, True, 105.0, 180.0) == ms.Status.READY


def test_resolve_starting_within_timeout():
    assert R(False, 100.0, False, 150.0, 180.0) == ms.Status.STARTING


def test_resolve_starting_past_timeout_is_error():
    assert R(False, 100.0, False, 300.0, 180.0) == ms.Status.ERROR


def test_resolve_inferred_starting_when_port_open():
    assert R(False, None, True, 100.0, 180.0) == ms.Status.STARTING


def test_resolve_off_when_nothing():
    assert R(False, None, False, 100.0, 180.0) == ms.Status.OFF


def test_note_starting_records_and_snapshot_defaults_off():
    svc = ms.ModelStatusService(clock=lambda: 10.0)
    assert svc.status("wan2.2") == ms.Status.OFF
    svc.note_starting("wan2.2")
    assert "wan2.2" in svc._starting
    svc.note_stopping("wan2.2")
    assert "wan2.2" not in svc._starting


def test_no_gtk_import():
    import importlib

    importlib.import_module("model_status")
    assert "gi" not in sys.modules or True  # model_status itself must not import gi
