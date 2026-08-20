# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""Pure reducer for live pipeline-run progress (Pipeline UX overhaul Task 6).

GTK-free by construction — no `gi`/GLib import, nothing display-server
dependent — so it can be unit tested without a display (`tests/
test_live_run_progress.py`) and so `LiveRunView` (`app/pipeline_studio.py`)
has exactly one place that decides what "Step N of M", a step's live phase
text, and its done/running bookkeeping mean. `LiveRunView.on_node_update`
folds each `(node_id, status, detail)` event from `PipelineRunner`'s
callback through `ProgressState.update()` and re-reads the reduced fields
below to drive widgets — it never derives progress by parsing rendered
widget text back out.
"""
from __future__ import annotations


class ProgressState:
    """Tracks per-node status + latest `detail` across one live pipeline run.

    `total` is the step count (normally `len(RunView.steps)`) — used only so
    callers can render "Step {current_index} of {total}"; this class does no
    bounds-checking against it, so a runner reporting more distinct nodes
    than `total` just grows `current_index` past it (rendering that edge
    case, if it's ever reached, is the view's call, not this reducer's).
    """

    def __init__(self, total: int) -> None:
        self.total = total
        # node_id -> latest status string ("pending"/"running"/"done"/"failed").
        self._status: "dict[str, str]" = {}
        # node_id -> latest detail string handed to update() (the "phase").
        self._phase: "dict[str, str]" = {}
        # Node ids in the order they FIRST transitioned to "running" — this,
        # not declaration/insertion order, is what current_index counts: a
        # step's position in the sequence of steps that have actually started
        # is what "Step N of M" means to someone watching the run happen.
        self._started_order: "list[str]" = []

    def update(self, node_id: str, status: str, detail: str) -> None:
        """Fold one `(node_id, status, detail)` event into the state."""
        self._status[node_id] = status
        self._phase[node_id] = detail
        if status == "running" and node_id not in self._started_order:
            self._started_order.append(node_id)

    def phase(self, node_id: str) -> str:
        """The latest `detail` text reported for *node_id*, or "" if none."""
        return self._phase.get(node_id, "")

    def status(self, node_id: str) -> "str | None":
        """The latest status reported for *node_id*, or None if never updated."""
        return self._status.get(node_id)

    def completed(self, node_id: str) -> bool:
        """True iff this node has finished successfully (status == 'done')."""
        return self._status.get(node_id) == "done"

    @property
    def done_count(self) -> int:
        return sum(1 for s in self._status.values() if s == "done")

    @property
    def running_node(self) -> "str | None":
        """The node_id currently "running", or None if nothing is."""
        for node_id, status in self._status.items():
            if status == "running":
                return node_id
        return None

    @property
    def current_index(self) -> int:
        """1-based position of the running (or most-recently-run) node.

        0 before any node has ever started running.
        """
        return len(self._started_order)
