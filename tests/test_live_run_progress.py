# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""Pure-reducer tests for pipeline_progress.ProgressState (Task 6).

GTK-free -- no gi/Gtk import here at all, matching pipeline_progress.py's
own GTK-free construction. See app/pipeline_progress.py's docstring and
LiveRunView (app/pipeline_studio.py) for how this reducer is consumed.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import pipeline_progress as pp   # new pure module


def test_reducer_tracks_step_count_and_phase():
    st = pp.ProgressState(total=2)
    st.update("1", "running", "sampling 5/25")
    assert st.current_index == 1 and st.running_node == "1"
    assert st.phase("1") == "sampling 5/25"
    st.update("1", "done", "")
    st.update("2", "running", "encoding")
    assert st.done_count == 1 and st.current_index == 2
    assert st.phase("2") == "encoding"
    st.update("2", "done", "")
    assert st.done_count == 2 and st.running_node is None


def test_progress_state_defaults_before_any_update():
    """Nothing has happened yet: no running node, no phase, index 0."""
    st = pp.ProgressState(total=3)
    assert st.running_node is None
    assert st.current_index == 0
    assert st.done_count == 0
    assert st.phase("1") == ""
    assert st.status("1") is None


def test_completed_predicate_and_done_count():
    st = pp.ProgressState(total=3)
    st.update("1", "running", ""); st.update("1", "done", "")
    st.update("2", "running", "")
    assert st.completed("1") is True
    assert st.completed("2") is False   # running, not done
    assert st.completed("3") is False   # never seen
    assert st.done_count == 1


def test_progress_state_failed_node_is_not_counted_done_but_stops_running():
    """A failed node should clear running_node without inflating done_count
    -- done_count is specifically "done", not "no longer running"."""
    st = pp.ProgressState(total=2)
    st.update("1", "running", "sampling")
    st.update("1", "failed", "boom")
    assert st.running_node is None
    assert st.done_count == 0
    assert st.status("1") == "failed"
    # current_index still reflects "1" as the last node that ran.
    assert st.current_index == 1
