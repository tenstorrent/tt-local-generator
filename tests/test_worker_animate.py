"""
Regression tests for AnimateGenerationWorker.

Primary regression: poll_status() returns a 3-tuple (status, err, data),
not a 2-tuple. The worker must unpack all three values; failing to do so
raised ValueError on every completed animate job.
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

# repo root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from worker import AnimateGenerationWorker


def _make_worker(client, store=None):
    if store is None:
        store = MagicMock()
    return AnimateGenerationWorker(
        client=client,
        store=store,
        reference_video_path="/tmp/motion.mp4",
        reference_image_path="/tmp/char.png",
        prompt="test animate prompt",
        num_inference_steps=10,
        seed=42,
        animate_mode="animation",
        model="wan2.2-animate-14b",
    )


def test_poll_status_3tuple_does_not_raise():
    """poll_status returns 3-tuple — worker must unpack without ValueError."""
    client = MagicMock()
    client.submit_animate.return_value = "job-abc12345"
    # Return a 3-tuple as the real api_client.poll_status() does
    client.poll_status.return_value = ("completed", None, {"output_resolution": "720p"})
    client.download.return_value = None

    store = MagicMock()
    store.append = MagicMock()

    worker = _make_worker(client, store)

    errors = []
    finished = []

    # Patch _extract_thumbnail and _write_prompt_sidecar to avoid file I/O
    with (
        patch.object(worker, "_extract_thumbnail"),
        patch.object(worker, "_write_prompt_sidecar"),
    ):
        worker.run_with_callbacks(
            on_progress=lambda msg: None,
            on_finished=lambda rec: finished.append(rec),
            on_error=lambda msg: errors.append(msg),
        )

    assert errors == [], f"Expected no errors, got: {errors}"
    assert len(finished) == 1, "Expected on_finished to be called once"


def test_poll_status_failed_job_calls_on_error():
    """A failed poll status triggers on_error, not ValueError."""
    client = MagicMock()
    client.submit_animate.return_value = "job-fail0001"
    client.poll_status.return_value = ("failed", "OOM error", {})

    worker = _make_worker(client)

    errors = []
    finished = []
    worker.run_with_callbacks(
        on_progress=lambda msg: None,
        on_finished=lambda rec: finished.append(rec),
        on_error=lambda msg: errors.append(msg),
    )

    assert finished == [], "on_finished should not be called on failed job"
    assert len(errors) == 1
    assert "failed" in errors[0] or "OOM" in errors[0]


def test_submit_failure_calls_on_error():
    """A network error during submit calls on_error, not on_finished."""
    client = MagicMock()
    client.submit_animate.side_effect = ConnectionError("server unreachable")

    worker = _make_worker(client)

    errors = []
    finished = []
    worker.run_with_callbacks(
        on_progress=lambda msg: None,
        on_finished=lambda rec: finished.append(rec),
        on_error=lambda msg: errors.append(msg),
    )

    assert finished == []
    assert len(errors) == 1
    assert "Submit failed" in errors[0]
