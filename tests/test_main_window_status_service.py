# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
"""
Guard tests for MainWindow owning the single ModelStatusService instance
(SP-2 Task 1: .superpowers/sdd/task-1-brief.md).

Constructing the full `MainWindow` is heavy and network/disk dependent (see
tests/test_main_window_pipelines.py's docstring) — the same reasoning that
made tests/test_main_window_create_view_mount.py a source-level guard rather
than a live construction test. These tests follow that established pattern:
assert the exact lines exist in app/main_window.py's source rather than
instantiating MainWindow.

The one non-source-level assertion (CreateView actually accepting and
storing `status_service=`) lives in tests/test_create_view.py, since
CreateView (unlike MainWindow) is cheap to construct under xvfb.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
if _SYSTEM_DIST not in sys.path:
    sys.path.insert(0, _SYSTEM_DIST)
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

_SRC = (Path(__file__).parent.parent / "app" / "main_window.py").read_text()


def test_constructs_and_starts_status_service():
    assert "from model_status import ModelStatusService" in _SRC
    assert "self._status_service = ModelStatusService(" in _SRC
    assert "self._status_service.start()" in _SRC


def test_status_service_constructed_before_create_view():
    """The service must exist before `CreateView(...)` is constructed so it
    can be injected — construction order matters here, not just presence."""
    construct_idx = _SRC.index("self._status_service = ModelStatusService(")
    create_view_idx = _SRC.index("self._create_view = CreateView(")
    assert construct_idx < create_view_idx


def test_stops_service_on_close():
    """SP-3d-6 update: the legacy `_health_stop` poller-stop event this test
    used to check alongside `_status_service.stop()` is retired (see
    tests/test_main_window_status_service_snapshot.py's
    `test_legacy_pollers_and_stop_events_are_gone` for the removal guard).
    `do_close_request` now also unsubscribes `_render_status_snapshot` from
    the service it's about to stop."""
    assert "self._status_service.stop()" in _SRC
    start = _SRC.index("def do_close_request(self) -> bool:")
    body = _SRC[start:]  # do_close_request is the last method in the class
    assert "self._status_service.stop()" in body
    assert "self._status_unsubscribe()" in body


def test_create_view_gets_status_service():
    assert "status_service=self._status_service" in _SRC


def test_start_stop_hooks_present():
    assert "self._status_service.note_starting(" in _SRC
    assert "self._status_service.note_stopping(" in _SRC


def test_on_start_stop_server_resolve_and_note_keys():
    """`_on_start_server`/`_on_stop_server` (the Video/Image tab start/stop
    buttons) resolve a server_manager key from the script they're about to
    run/have run, via the shared `_server_key_for_script` helper, and note
    it with the status service -- guarded so a bad resolution can't break
    the real start/stop flow."""
    assert "def _server_key_for_script(script_name: str)" in _SRC

    start = _SRC.index("def _on_start_server(self, model_source: str) -> None:")
    end = _SRC.index("\n    def ", start + 1)
    body = _SRC[start:end]
    assert "_server_key_for_script(script_name)" in body
    assert "self._status_service.note_starting(server_key)" in body

    start2 = _SRC.index("def _on_stop_server(self) -> None:")
    end2 = _SRC.index("\n    def ", start2 + 1)
    body2 = _SRC[start2:end2]
    assert "_server_key_for_script(" in body2
    assert "self._status_service.note_stopping(server_key)" in body2

