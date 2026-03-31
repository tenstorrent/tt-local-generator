# tt-local-generator — developer notes

## Running the app

```bash
/usr/bin/python3 main.py [--server http://localhost:8000]
```

Use the **system** python3 (`/usr/bin/python3`), not a venv. GTK4 bindings
(`python3-gi`) are installed as system packages and are invisible inside venvs.

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0  # if missing
```

## Starting the inference server

```bash
cd ~/code/tt-local-generator
./start_wan.sh
```

The script reads `JWT_SECRET` from `~/code/tt-inference-server/.env`, sets
`MODEL_SOURCE=huggingface` to skip interactive prompts, launches the Docker
container, then tails the log. The server is ready when the log prints
`Application startup complete` (takes ~5 min on P150x4).

## Architecture

| File | Purpose |
|---|---|
| `main.py` | `Gtk.Application` entry point |
| `main_window.py` | All GTK4 widgets and `MainWindow` |
| `worker.py` | `GenerationWorker` — pure Python, no GUI imports |
| `api_client.py` | HTTP client for the inference server |
| `history_store.py` | Persistent JSON history + file path management |

`worker.py`, `api_client.py`, and `history_store.py` have **zero GUI
dependencies** — keep them that way.

## GTK threading discipline (CRITICAL)

GTK is strictly single-threaded. **Never call any GTK method from a background
thread.** Doing so causes silent data corruption or hard crashes that are
difficult to debug.

### The rule

Every UI update from a worker thread must be posted to the main thread via:

```python
GLib.idle_add(callback, *args)
```

`idle_add` schedules `callback(*args)` to run on the GLib main loop (main
thread) at the next idle moment. The callback **must return `False`** (or
`GLib.SOURCE_REMOVE`) to run once; return `True` to keep repeating.

### Pattern used in this app

`GenerationWorker.run_with_callbacks()` takes three plain Python callables.
`MainWindow` wraps each one in `GLib.idle_add` when it passes them in:

```python
gen.run_with_callbacks(
    on_progress=lambda msg: GLib.idle_add(self._on_progress, msg, pending),
    on_finished=lambda rec: GLib.idle_add(self._on_finished, rec),
    on_error=lambda msg:    GLib.idle_add(self._on_error, msg),
)
```

The `_on_progress`, `_on_finished`, `_on_error` methods then touch widgets
freely because they run on the main thread.

### GLib.timeout_add

`PendingCard` uses `GLib.timeout_add(1000, self._tick)` for the elapsed-time
counter. This fires on the main thread — no `idle_add` needed inside `_tick`.
Cancel it with `GLib.source_remove(timer_id)` when the card is replaced.

### Health worker

The health-check loop uses `threading.Thread` + `daemon=True`. It posts results
via `GLib.idle_add(self._on_health_result, ready)`. The `_health_stop` event
lets `do_close_request` cleanly signal the thread to exit.

## FileDialog (GTK4 async API)

GTK4's `Gtk.FileDialog` is async — it takes a callback, not a return value:

```python
dlg = Gtk.FileDialog()
dlg.open(parent_window, cancellable, callback)  # returns immediately

def callback(dlg, result):
    try:
        gfile = dlg.open_finish(result)
    except Exception:
        return   # user cancelled
    path = gfile.get_path()
```

Always wrap `open_finish` / `save_finish` in try/except — they raise if the
user cancels.

## Queue system

`MainWindow._queue` is a `list[_QueueItem]`. After `_on_finished` runs,
`_start_next_queued()` pops the front item and calls `_on_generate()` directly.
`ControlPanel.update_queue_display()` rebuilds the visible list; call it from
the main thread only (always safe since queue mutations happen in response to
button clicks or `_on_finished`).

## PyGObject gotchas

- **No `set_data`/`get_data` on widgets**: PyGObject deliberately blocks GObject's
  C-level data methods. Store arbitrary Python values as plain attributes instead:
  ```python
  cb.job = job_dict   # yes
  cb.set_data("job", job_dict)  # RuntimeError
  ```

## Known issues / history

- **ffmpeg stdin hang**: ffmpeg inherited terminal stdin from the process and
  blocked waiting for `[q]`. Fixed by passing `stdin=subprocess.DEVNULL` in
  `_extract_thumbnail`. Also add `-update 1` to avoid image-sequence warnings.

- **Inference server interactive prompt**: `setup_host.py` globs snapshot root
  for `model*.safetensors`; Wan2.2 weights live in subdirectories so the check
  always fails and prompts interactively. Fixed in `start_wan.sh` by setting
  `MODEL_SOURCE=huggingface` and `JWT_SECRET` env vars.

- **Wrong entry point**: the correct entry is `python3 run.py` in the
  `tt-inference-server` repo, not `python3 -m workflows.run_workflows`
  (that module imports `benchmarking` which isn't on the path).
