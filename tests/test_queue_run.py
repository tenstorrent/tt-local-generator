"""
Tests for cmd_queue_run() in tt-ctl.

Imports tt-ctl via importlib (it has no .py extension).
All HistoryStore I/O is redirected to tmp_path.
All network calls are mocked.
"""
import importlib.util
import json
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ── Load tt-ctl as a module ───────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

_TTCTL_PATH = Path(__file__).parent.parent / "tt-ctl"
# importlib.util.spec_from_file_location cannot infer the loader for files
# without a .py extension.  Provide SourceFileLoader explicitly.
import importlib.machinery
_loader = importlib.machinery.SourceFileLoader("tt_ctl", str(_TTCTL_PATH))
_spec = importlib.util.spec_from_loader("tt_ctl", _loader)
tt_ctl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tt_ctl)

# ── Fixtures ──────────────────────────────────────────────────────────────────
import history_store as hs


def _patch_store(monkeypatch, tmp_path):
    """Redirect all HistoryStore paths to tmp_path."""
    monkeypatch.setattr(hs, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(hs, "VIDEOS_DIR", tmp_path)
    monkeypatch.setattr(hs, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(hs, "THUMBNAILS_DIR", tmp_path)
    monkeypatch.setattr(hs, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(hs.HistoryStore, "_QUEUE_FILE", tmp_path / "queue.json")


def _make_item(prompt: str, model_source: str = "video") -> dict:
    return {
        "prompt": prompt,
        "negative_prompt": "",
        "steps": 20,
        "seed": -1,
        "seed_image_path": "",
        "model_source": model_source,
        "guidance_scale": 5.0,
        "ref_video_path": "",
        "ref_char_path": "",
        "animate_mode": "animation",
        "model_id": "",
        "job_id_override": "",
    }


def _make_args(server="http://localhost:8000", dry_run=False):
    args = MagicMock()
    args.server = server
    args.dry_run = dry_run
    return args


# ── Empty queue ───────────────────────────────────────────────────────────────

def test_queue_run_empty_queue_exits_cleanly(monkeypatch, tmp_path):
    """queue run on an empty queue prints a message and returns without error."""
    _patch_store(monkeypatch, tmp_path)
    mock_client = MagicMock()
    with patch.object(tt_ctl, "_make_client", return_value=mock_client):
        tt_ctl.cmd_queue_run(_make_args())
    mock_client.health_check.assert_not_called()


# ── Dry run ───────────────────────────────────────────────────────────────────

def test_queue_run_dry_run_does_not_modify_queue(monkeypatch, tmp_path):
    """--dry-run prints items but does not remove them from queue.json."""
    _patch_store(monkeypatch, tmp_path)
    queue_file = tmp_path / "queue.json"
    items = [_make_item("a red fox"), _make_item("a blue whale")]
    queue_file.write_text(json.dumps(items))

    mock_client = MagicMock()
    with patch.object(tt_ctl, "_make_client", return_value=mock_client):
        tt_ctl.cmd_queue_run(_make_args(dry_run=True))

    # Queue unchanged
    remaining = json.loads(queue_file.read_text())
    assert len(remaining) == 2
    mock_client.health_check.assert_not_called()


# ── Success path ──────────────────────────────────────────────────────────────

def test_queue_run_success_removes_item_from_queue(monkeypatch, tmp_path):
    """A successful generation removes the item from queue.json."""
    _patch_store(monkeypatch, tmp_path)
    queue_file = tmp_path / "queue.json"
    items = [_make_item("a cat sitting")]
    queue_file.write_text(json.dumps(items))

    mock_client = MagicMock()
    mock_client.health_check.return_value = True

    fake_record = MagicMock()
    fake_record.media_file_path = "/tmp/fake.mp4"

    def fake_run_with_callbacks(on_progress, on_finished, on_error):
        on_finished(fake_record)

    mock_worker = MagicMock()
    mock_worker.run_with_callbacks.side_effect = fake_run_with_callbacks

    with patch.object(tt_ctl, "_make_client", return_value=mock_client), \
         patch.object(tt_ctl, "_make_worker_for_item", return_value=mock_worker):
        tt_ctl.cmd_queue_run(_make_args())

    remaining = json.loads(queue_file.read_text())
    assert remaining == []


def test_queue_run_processes_multiple_items_in_order(monkeypatch, tmp_path):
    """Multiple items are consumed in order; all are removed on success."""
    _patch_store(monkeypatch, tmp_path)
    queue_file = tmp_path / "queue.json"
    items = [_make_item("first"), _make_item("second"), _make_item("third")]
    queue_file.write_text(json.dumps(items))

    mock_client = MagicMock()
    mock_client.health_check.return_value = True

    processed_prompts = []

    def fake_make_worker(client, store, item):
        worker = MagicMock()
        _prompt = item["prompt"]

        def run(on_progress, on_finished, on_error):
            processed_prompts.append(_prompt)
            rec = MagicMock()
            rec.media_file_path = f"/tmp/{_prompt}.mp4"
            on_finished(rec)

        worker.run_with_callbacks.side_effect = run
        return worker

    with patch.object(tt_ctl, "_make_client", return_value=mock_client), \
         patch.object(tt_ctl, "_make_worker_for_item", side_effect=fake_make_worker):
        tt_ctl.cmd_queue_run(_make_args())

    assert processed_prompts == ["first", "second", "third"]
    remaining = json.loads(queue_file.read_text())
    assert remaining == []


# ── Failure path ──────────────────────────────────────────────────────────────

def test_queue_run_failure_removes_item_from_queue(monkeypatch, tmp_path):
    """A failed generation (on_error) still removes the item from queue.json."""
    _patch_store(monkeypatch, tmp_path)
    queue_file = tmp_path / "queue.json"
    queue_file.write_text(json.dumps([_make_item("bad prompt")]))

    mock_client = MagicMock()
    mock_client.health_check.return_value = True

    def fail_run(on_progress, on_finished, on_error):
        on_error("Download failed: connection reset")

    mock_worker = MagicMock()
    mock_worker.run_with_callbacks.side_effect = fail_run

    with patch.object(tt_ctl, "_make_client", return_value=mock_client), \
         patch.object(tt_ctl, "_make_worker_for_item", return_value=mock_worker):
        tt_ctl.cmd_queue_run(_make_args())

    remaining = json.loads(queue_file.read_text())
    assert remaining == []


# ── Server offline (skip) ─────────────────────────────────────────────────────

def test_queue_run_offline_skips_item_leaves_in_queue(monkeypatch, tmp_path):
    """If health_check() fails, the item stays in queue.json and is not run."""
    _patch_store(monkeypatch, tmp_path)
    queue_file = tmp_path / "queue.json"
    items = [_make_item("skip me"), _make_item("skip me too")]
    queue_file.write_text(json.dumps(items))

    mock_client = MagicMock()
    mock_client.health_check.return_value = False

    with patch.object(tt_ctl, "_make_client", return_value=mock_client), \
         patch.object(tt_ctl, "_make_worker_for_item") as mock_make:
        tt_ctl.cmd_queue_run(_make_args())

    mock_make.assert_not_called()
    remaining = json.loads(queue_file.read_text())
    assert len(remaining) == 2


def test_queue_run_partial_skip_processes_online_items(monkeypatch, tmp_path):
    """If first item's server is offline but second is online, second runs."""
    _patch_store(monkeypatch, tmp_path)
    queue_file = tmp_path / "queue.json"
    items = [_make_item("skip"), _make_item("run me")]
    queue_file.write_text(json.dumps(items))

    call_count = [0]

    def health_side_effect():
        # First call: offline; subsequent calls: online
        call_count[0] += 1
        return call_count[0] > 1

    mock_client = MagicMock()
    mock_client.health_check.side_effect = health_side_effect

    processed = []

    def fake_make_worker(client, store, item):
        worker = MagicMock()

        def run(on_progress, on_finished, on_error):
            processed.append(item["prompt"])
            rec = MagicMock()
            rec.media_file_path = "/tmp/out.mp4"
            on_finished(rec)

        worker.run_with_callbacks.side_effect = run
        return worker

    with patch.object(tt_ctl, "_make_client", return_value=mock_client), \
         patch.object(tt_ctl, "_make_worker_for_item", side_effect=fake_make_worker):
        tt_ctl.cmd_queue_run(_make_args())

    # Only "run me" was processed
    assert processed == ["run me"]
    # "skip" item stays in queue
    remaining = json.loads(queue_file.read_text())
    assert len(remaining) == 1
    assert remaining[0]["prompt"] == "skip"


# ── Ctrl+C ────────────────────────────────────────────────────────────────────

def test_queue_run_ctrl_c_removes_current_item_leaves_rest(monkeypatch, tmp_path):
    """Ctrl+C removes the in-flight item (submitted to server) and leaves the rest."""
    _patch_store(monkeypatch, tmp_path)
    queue_file = tmp_path / "queue.json"
    items = [_make_item("in-flight"), _make_item("not-yet")]
    queue_file.write_text(json.dumps(items))

    mock_client = MagicMock()
    mock_client.health_check.return_value = True

    mock_worker = MagicMock()
    mock_worker._current_job_id = "deadbeef1234"

    # Patch threading.Event so that the `done` event's .wait() raises
    # KeyboardInterrupt, simulating Ctrl+C while waiting for the in-flight item.
    # We avoid patching threading.Event globally (which would break threading.Thread
    # internals).  Instead we patch threading.Thread so no real thread is started,
    # and we provide a fake Event whose .wait() raises KeyboardInterrupt.
    import threading as _threading
    _real_Event = _threading.Event

    _call_count = [0]

    def _event_factory():
        """First call returns the KI-raising mock; subsequent calls are real Events."""
        _call_count[0] += 1
        if _call_count[0] == 1:
            ev = MagicMock()
            ev.wait.side_effect = KeyboardInterrupt
            return ev
        return _real_Event()

    # Also stub threading.Thread so the background thread does not actually
    # run (the mock worker never sets the event anyway).
    mock_thread = MagicMock()

    with patch.object(tt_ctl, "_make_client", return_value=mock_client), \
         patch.object(tt_ctl, "_make_worker_for_item", return_value=mock_worker), \
         patch.object(tt_ctl.threading, "Event", side_effect=_event_factory), \
         patch.object(tt_ctl.threading, "Thread", return_value=mock_thread):
        with pytest.raises(SystemExit) as exc:
            tt_ctl.cmd_queue_run(_make_args())

    assert exc.value.code == 1
    remaining = json.loads(queue_file.read_text())
    assert len(remaining) == 1
    assert remaining[0]["prompt"] == "not-yet"


# ── Worker selection ──────────────────────────────────────────────────────────

def test_make_worker_for_item_video_returns_generation_worker():
    """model_source='video' creates a GenerationWorker."""
    client = MagicMock()
    store = MagicMock()
    item = _make_item("a horse galloping", model_source="video")
    worker = tt_ctl._make_worker_for_item(client, store, item)
    assert isinstance(worker, tt_ctl.GenerationWorker)


def test_make_worker_for_item_image_returns_image_worker():
    """model_source='image' creates an ImageGenerationWorker."""
    client = MagicMock()
    store = MagicMock()
    item = _make_item("a red rose", model_source="image")
    worker = tt_ctl._make_worker_for_item(client, store, item)
    assert isinstance(worker, tt_ctl.ImageGenerationWorker)


def test_make_worker_for_item_animate_returns_animate_worker():
    """model_source='animate' creates an AnimateGenerationWorker."""
    client = MagicMock()
    store = MagicMock()
    item = {**_make_item("dance move", model_source="animate"),
            "ref_video_path": "/tmp/motion.mp4",
            "ref_char_path": "/tmp/char.png"}
    worker = tt_ctl._make_worker_for_item(client, store, item)
    assert isinstance(worker, tt_ctl.AnimateGenerationWorker)


def test_make_worker_for_item_skyreels_returns_generation_worker():
    """model_source='skyreels' creates a GenerationWorker (same base class as video)."""
    client = MagicMock()
    store = MagicMock()
    item = _make_item("erupting volcano", model_source="skyreels")
    worker = tt_ctl._make_worker_for_item(client, store, item)
    assert isinstance(worker, tt_ctl.GenerationWorker)
    assert worker._model == "skyreels-v2-df"
