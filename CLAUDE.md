# tt-local-generator — developer notes

## artgen LLM endpoint discovery

`artgen.detect_artgen_endpoint()` (`app/artgen/__init__.py`) picks the chat
server for generative art. Hardcoded ports (artgen=8002, prompt-server=8001)
matter only for servers the *app* starts. For models started any other way it
sweeps local ports (`_SCAN_PORT_RANGE`, override via `TTLG_ARTGEN_SCAN_PORTS`)
for any OpenAI-compatible `/v1/models` responder.

Resolution order: `preferred_url` → artgen (8002) → swept ports → prompt-gen
(8001, tiny Qwen3-0.6B) **last**. The prompt-gen fallback is deliberately last
so a real chat model always beats it — the original bug was a vLLM Llama-3.3-70B
on 8003 losing to Qwen3-0.6B on 8001 because 8003 was never probed. The known
diffusion port (8000) and the two explicit ports are excluded from the sweep.
`mcp_server._make_call_fn` routes through the same function for consistency.

**Single source of truth for "is a model on".** The artgen panel's health dot
(`ArtgenPanel._check_health_bg`) also calls `detect_artgen_endpoint()`, so the
indicator can never disagree with where generation requests actually go. It
caches the last-found base URL and re-pings only that on each 5 s poll (via
`detect_model`), re-sweeping the full port range only when the endpoint drops.
Regression: previously the dot pinged the fixed configured port (8002) for the
dropdown's model key, so a model on any other port read "offline" while
generation worked fine.

## ANSI art — 3-pass pipeline

`AnsiGenerator` in `app/artgen/generators/ansi.py` overrides `generate_artifact`
to run three sequential LLM calls instead of one. This is the first implementation
of the multi-pass remix pattern that will be generalised in remix-mode.

**Why three passes:** A single 40×20 canvas already asks the model to manage 800
decisions. Planning spatial composition and color simultaneously causes models to
output palette strips instead of imagery — horizontal bands of one color per zone.
Separating concerns gives each pass a task the model handles reliably.

**Pass flow:**

1. `_build_ascii_prompt()` → `call_fn(..., max_tokens=1024)` → `_normalize_grid()`
   - Plain ASCII chars only; no color, no block chars
   - Style-specific spatial hints (BBS: void rows top/bottom, neon subject center;
     landscape: sky top / terrain bottom; scene: foreground/midground/background)

2. `_build_refine_prompt(ascii_art)` → `call_fn(..., max_tokens=1024)` → `_normalize_grid()`
   - Replaces dense chars with `█ ▀ ▄ ▌ ▐ ░ ▒ ▓`; exact layout preserved
   - `_normalize_grid` strips think-blocks, fences, pads/truncates to exact width×height

3. `_build_colorize_prompt(block_art)` → `call_fn(..., max_tokens=8192)`
   - Wraps every character as `\033[38;5;Nm█` (foreground + block char)
   - BBS color guide: neon-on-void (`_COLOR_GUIDE_BBS`); scene/landscape: `_COLOR_GUIDE_SCENE`
   - Space chars use `\033[38;5;232m\033[0m` (near-black foreground, remain invisible)

**`generate_artifact` hook:** `ArtGenerator` base class defines a single-pass default
(`build_prompt` → `call_fn` → `parse_output` → `post_process`). Override `generate_artifact`
to implement multi-pass. The `call_fn` closure is built by `_make_call_fn` in `cli.py`
and by the artgen panel; it accepts `max_tokens=` per-call so each pass can have its own
token budget.

**`--simulate`:** `build_prompt()` returns the pass-1 ASCII prompt, so dry-run still works.

**`--ansi-style bbs`:** BBS canvas is fixed at 40×20. Color guide specifies electric cyan
(51, 87), toxic green (46, 82), hot magenta (201, 199), gold (226, 220). Zone rules
constrain rows 1-2 and 18-20 to near-black void (232–234), rows 3-17 to the neon subject.

## Create surface (role zones, scoped models, modifier pills)

The **Create** loop-nav verb opens `CreateView` (`app/create_view.py`), the
role-grouped generation surface (v0.28.0). Three key ideas, each backed by a
small unit — deliberately shared so pipeline field configuration can adopt them
later:

- **`app/field_roles.py`** — a pure (no-GTK) taxonomy. Every field has a **role**
  (`ROLE_BRIEF` / `ROLE_DIRECTION` / `ROLE_CONTROL` → the three zones) and a
  **marker** (`MARK_WORDS` ✎ raw text the model renders · `MARK_INTERPRETED` ✨
  a value the model/LLM decides from · `MARK_EXACT` ⚙ deterministic, never read
  by the model). `classify_native`, `classify_artgen`, `classify_pipeline_field`
  are the single source of truth. Glyphs live in `MARKER_GLYPH` — Python strings
  only, never inside a `b"""` CSS literal.
- **`RoleZonePanel`** (in `app/create_param_panels.py`) — wraps any
  `CreateParamPanel`, reads its `field_specs()`, and **re-parents** the panel's
  already-built field widgets into the brief / Direction / collapsed-Controls
  zones. It never rebuilds widgets, so `RoleZonePanel.collect()` is a verbatim
  passthrough to `panel.collect()`. **Migration invariant:** that dict must stay
  byte-for-byte compatible with what generation consumes — guarded by
  `test_role_zone_panel.py`'s collect-equality tests. The `kind=="model"` field
  is excluded here; CreateView's scoped dropdown owns model selection.
- **`ModifierPills`** (same file) — the Direction zone's chip palette. Banks come
  from `chip_config.load_chips(medium.kind)`; tapping an add-chip creates a
  removable pill (the add-chip hides until removed), and `applied_text()` is
  appended to the brief at generate time.

**Models:** no persistent full-width strip (it overflowed — retired in 0.28.0).
Within a medium, a scoped `Gtk.DropDown` lists only that medium's models. The
"Start with a model" door is a grouped, wrapping grid (Image / Video / Animate /
Text) classified by each `ServerDef.capabilities` via
`_CAPABILITY_TO_MODEL_DOOR_GROUP` — **not** by `_server_key_to_medium_id` (that
"first artgen medium" heuristic mis-files the chat-LLM backends under Animate;
regression-guarded). Text cards return to the Idea door without changing the
active medium.

**Width discipline:** the whole surface is wrapped in
`gtk_layout.wrap_centered` (`MaxWidthBin`, extracted from `pipeline_studio`), and
every multi-item row is a wrapping `Gtk.FlowBox` — width overflow is structurally
impossible. Palette stays the tt-vscode-toolkit variant (`#4FD1C5`/`#0F2A35`).

The legacy per-model tabs / ControlPanel / ArtgenPanel remain the reachable
fallback until a real-generation smoke test on hardware; deleting them is a
separate step.

**Pipeline editor adoption (sub-project 2, v0.29.0).** The same vocabulary now
drives `RemixView`'s node field editing (`app/pipeline_studio.py`), so a field
means the same thing in Create and in Remix/Compose. `field_roles` gained a
deepened `classify_pipeline_field` (classifies a `spec_remix.ParamField` by
kind/value/key) and a pure `marker_prefix(marker)` label formatter shared by both
surfaces. In each step card, fields are classified, ordered brief -> direction ->
control, marker-prefixed (✎/✨/⚙ + tooltip), and the control fields sit under a
per-card collapsed `Gtk.Expander` "Controls (N)". Brief text fields get a
contextual `ModifierPills` (imported from `create_param_panels`; bank chosen by
`intent_for(class_type).output_kind` via `_bank_kind_for_output` -- image/video/
gif->animate, text/None->no bank), and `_collect_edits` folds each field's
`applied_text()` into its value at Run time. **Edit-contract invariant preserved:**
`_field_widgets`/`_field_meta` are populated for every field regardless of zone,
and with no retype + no applied pill a field stays out of the edit diff (untouched
run reproduces exactly). Known follow-up: `ModifierPills` re-reads
`config/prompt_chips.yaml` per render (fail-soft, uncached) on both surfaces --
cache `load_chips_for_kind` if render latency ever matters.

**In-place results (v0.31.0).** Create is a two-pane surface: the form beside a
`CreateResultPanel` (`app/create_view.py`), laid out in a `Gtk.FlowBox`
(min1/max2 per line) so it's side-by-side when wide and stacked when narrow,
inside `wrap_centered` at `_TWO_PANE_MAX_WIDTH` (1440, a true ceiling -> no
overflow). Hitting Create shows a live pending state in the panel (spinner +
elapsed), resolving in place to the finished image/video/text the instant it's
done, and prepending to a session recents strip (cap 6). This is the
[[project-see-result-immediately]] principle. Wiring: `main_window` marks a
Create-launched job with `self._create_job_active` and forwards the lifecycle to
`self._create_view._result_panel` -- native jobs via `_on_generate`'s
progress/finished/error callbacks (the gallery pending card is SKIPPED for
Create jobs, but the finished record still lands in the gallery/store, so
Discover is unchanged -- the panel is additive), artgen jobs via
`_on_create_artgen_finished`/`_fail_create_job` on the `tt-ctl` worker thread.
**Every terminal path clears the flag** -- `_fail_create_job(reason)` is called
on all `_on_generate` early returns (server busy / low disk / AnimateDiff-busy)
and on artgen failure, so the panel never stays stuck on "pending" and the
window-global flag never bleeds into an unrelated next job. Non-Create jobs
(attractor/TT-TV/queue) never touch the panel and keep their gallery pending
card. Note: the artgen `MediaRecord` gets a `media_file_path` alias set so the
panel's renderer (which reads that name, matching `GenerationRecord`) resolves
the artifact; `MediaStore.add` reads only declared fields, so it's inert for
persistence.

## Model status (single source of truth)

`app/model_status.py` — `ModelStatusService`, a **GUI-free** single source of
truth for server/model state (v0.32.0, SP-1 of the coherent-shell program).
One poll thread merges managed-server health (`server_manager.status_all`) with
the artgen port-sweep (`artgen.detect_artgen_endpoint` -> any `artgen`/`prompt`
capability server reads ready when a chat endpoint is up on any port), tracks a
`starting` state (app-initiated via `note_starting()`, plus inferred-starting
when a server's `health_url` port is open but health hasn't passed), and resolves
each `server_manager.SERVERS` key to `Status.OFF/STARTING/READY/ERROR` via the
pure `_resolve(...)`. Design notes:
- **GUI-free**: no `gi` import; `server_manager`/`artgen` imports are LAZY (inside
  the default `health_fn`/`detect_fn` callables) so the module imports standalone.
- **Injectable**: `health_fn`/`detect_fn`/`clock`/`port_probe`/`poll_interval`/
  `start_timeout` are constructor params -> tests drive `_tick()` directly with
  fakes, no threads/sleeps/sockets.
- **Lock discipline**: `_tick` does all I/O (health/detect/port probes) OUTSIDE
  `self._lock`, takes the lock only to read/mutate `_starting`/`_ready_at` and
  swap `_statuses`, and calls `_notify()` AFTER releasing (since `_notify` ->
  `snapshot()` re-acquires the non-reentrant lock). Subscribers get change-only
  notifications; a raising subscriber never breaks the loop.
- **Consumers**: `snapshot()`/`status(key)`/`subscribe(cb)` and capability helpers
  `ready_keys(cap)` (most-recently-ready first) / `starting_keys(cap)` /
  `running_or_starting(cap)`.
- **SP-2 wiring (v0.33.0):** `MainWindow` constructs + `start()`s the service on
  open, `stop()`s it in `do_close_request`, hooks `note_starting`/`note_stopping`
  at the server start/stop sites, and injects it into `CreateView`
  (`status_service=`). CreateView subscribes (poll-thread callback -> `GLib.idle_add`
  -> `_on_status_snapshot`), renders 3-state dots (◌/◐/●) via `_status_glyph` +
  `_model_dot_glyph` on both the scoped dropdown and the Model door, and
  auto-selects `running_or_starting(cap)` in `_populate_model_dropdown` (cap keyed
  by `medium.id` -- the Animate medium's `kind` is "gif"; only in the fresh-populate
  branch so a manual pick is preserved per the v0.28.1 fix). `status_service=None`
  keeps CreateView's old boolean `status_all` fallback (tests/standalone).
- **Still on their own pollers until SP-3 deletes them:** `MainWindow._health_loop`
  (footer row + statusbar), `_refresh_servers_popover` (Servers popover),
  `artgen_panel._check_health_bg`. SP-3 retires the vestiges and stands up one
  surviving status control on the service.

## Retiring the vestiges (SP-3, staged migration)

The app is consolidating onto the Create / Discover / Remix shell; the old
per-medium tabs + ControlPanel + ArtgenPanel + Generative-Art tab + duplicate
server UI are being retired in stages (delete last, only once every capability
has a new home). Decisions: server control -> a compact top-bar `Servers ▾`
wired to `ModelStatusService`; migrate (not drop) seed-image/i2i, "Inspire me"
prompt-gen, attractor/TT-TV launch, the generation queue, and the status
bar/server-log.

- **SP-3a done (v0.34.0): `_on_generate` decoupled from ControlPanel.** It takes
  `video_model_key`/`image_model_key`/`animatediff_args` params and reads NO
  `self._controls.get_*` for model selection; module defaults `_DEFAULT_VIDEO_KEY`
  /`_DEFAULT_IMAGE_KEY`/`_ANIMATEDIFF_DEFAULTS` mirror ControlPanel's old defaults.
  All callers (legacy generate/enqueue, Create `_create_generate_native`, queue,
  attractor) pass the model explicitly; the Create `_controls._video_model` sync
  hack (v0.27.1) is gone. The legacy generate call site + the attractor path still
  read `_controls` (legitimately — those ARE ControlPanel-driven); they go with
  ControlPanel in SP-3d, where the attractor also needs a new model source.
- **Open decision for SP-3c/3d:** native AnimateDiff (ControlPanel video-model
  `"animatediff"` + `get_animatediff_args`) is distinct from the artgen
  `animatediff` plugin — migrate into Create or drop when ControlPanel is deleted?
- **Remaining:** SP-3b top-bar Servers control + status bar + log; SP-3c migrate
  seed-image/Inspire-me/attractor/queue into Create; SP-3d delete the vestiges +
  the 3 legacy pollers (`_health_loop`, `_refresh_servers_popover`,
  `artgen_panel._check_health_bg`).

## Version discipline

**Always increment the version when landing changes.** The version in `VERSION`
(at repo root) is the single source of truth — it drives the `.deb` package
version and `tt-ctl --version`. Without a bump, the CI build produces a `.deb`
with the same version string as the previous release, making releases
indistinguishable and `apt` upgrades silent no-ops.

- Patch bump (`0.2.1` → `0.2.2`): bug fixes, docs, word bank additions, any
  non-breaking change.
- Minor bump (`0.2.x` → `0.3.0`): new user-visible feature or UI change.
- Major bump: breaking change to config, API, or install layout.

When bumping:
1. Edit `VERSION` (single line, no prefix).
2. Prepend a new stanza to `debian/changelog` (use `dch` or edit manually).
3. Commit both files together on a dedicated `bump/version-X.Y.Z` branch and
   open a PR — version bumps should be their own commit so the git log is
   unambiguous about what shipped when.

## Running the app

```bash
./tt-gen                                            # recommended launcher
/usr/bin/python3 app/main.py [--server http://localhost:8000]  # direct
```

Use the **system** python3 (`/usr/bin/python3`), not a venv. GTK4 bindings
(`python3-gi`) are installed as system packages and are invisible inside venvs.

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0  # if missing
```

## Starting / stopping the inference server

From the GUI, use the **Servers ▾** toolbar dropdown or the **▶ Start** / **■ Stop**
buttons in the server control row. Start is context-aware: Video tab starts
`start_wan_qb2.sh` (QB2/P300x2), `start_mochi.sh`, or `start_skyreels.sh` depending
on the selected video model; Animate tab starts `start_animate.sh`; Image tab starts
`start_flux.sh`. Script output streams into a collapsible log panel that closes when
the health check confirms the server is ready.

From the terminal (all scripts are in `bin/`):

```bash
cd ~/code/tt-local-generator
./bin/start_wan_qb2.sh                       # Wan2.2-T2V on QB2 (P300x2)
./bin/start_wan_qb2.sh --stop                # stop the running server container
./bin/start_wan.sh                           # Wan2.2-T2V on P150x4
./bin/start_skyreels_i2v.sh                  # SkyReels-V2-I2V-14B-540P on QB2
./bin/start_animate.sh                       # Wan2.2-Animate-14B on QB2 (P300x2)
./bin/start_mochi.sh                         # Mochi-1 on QB2 (weights needed)
./bin/start_flux.sh                          # FLUX.1-schnell image server on QB2
./bin/start_sdxl.sh                          # SDXL via cpp_server backend on QB2
./bin/start_artgen.sh                        # Artgen LLM (Qwen3-8B default, port 8002)
./bin/start_artgen.sh --model Llama-3.3-70B-Instruct   # 70B artgen LLM
./bin/start_artgen.sh --model Qwen3-32B                # 32B artgen LLM
./bin/start_prompt_gen.sh                    # Qwen3-0.6B prompt server (CPU, port 8001)
```

Or via the CLI:

```bash
./tt-ctl start wan2.2          # non-blocking; same as start_wan_qb2.sh --gui
./tt-ctl stop  wan2.2
./tt-ctl start all             # wan2.2 + prompt-server (QB2 / P300X2 recommended set)
./tt-ctl start --single-chip    # artgen-qwen3-8b + prompt-server (single Blackhole card or CPU-only)
./tt-ctl servers               # live health of every managed service
```

All scripts accept `--gui` (non-blocking, skips the interactive tail).
The server is ready when the log prints `Application startup complete`.

### Animate mode (Wan2.2-Animate-14B)

The **💃 Animate** source toggle activates Wan2.2-Animate-14B, a video-to-video
character animation model. Unlike the text-to-video T2V mode, it requires:

- **Motion video** — an MP4 supplying the motion pattern
- **Character image** — PNG/JPG of the character to animate
- **Mode** — `animation` (character mimics the motion) or `replacement` (character
  replaces the person in the video)

The text prompt is optional (style guidance only). `start_animate.sh` binds the
modified `tt-media-server` files from `~/code/tt-inference-server/tt-media-server/`
into the container and upgrades `diffusers>=0.34.0` before starting uvicorn
(Phase 1: Diffusers CPU/CUDA path — TT hardware support pending).

### SkyReels mode (SkyReels-V2-DF-1.3B-540P)

The **SkyReels** video model button selects SkyReels-V2-DF-1.3B-540P, a fast
diffusion transformer that runs on **Blackhole** hardware (P150X4 or P300X2).
Key parameters:

- **Resolution** — 480×272 (540P) native
- **Frame count** — configurable: 9 / 33 / 65 / 97 frames (Preferences → SkyReels)
  Valid counts follow `(N-1) % 4 == 0`. Default: 33 frames (~1.4 s at 24 fps).
- **`skyreels_num_frames`** setting in `app_settings.py` / Preferences dialog.
- `GenerationWorker` accepts `num_frames=` and forwards it to `api_client`.
- `start_skyreels.sh` requires `apply_patches.sh` to be run first (Step 6 appends
  the SkyReels T2V/I2V entries to the 0.18.0 YAML catalog,
  `workflows/model_specs/dev/video.yaml`, and copies runner patches). Prior to
  0.18.0 this injected a `ModelSpecTemplate(...)` into `model_spec.py` directly;
  that file is no longer the registry's source of truth — see the "Vendored
  tt-inference-server" section below.

## Directory layout

All Python source lives in `app/`, shell scripts in `bin/`.

```
tt-local-generator/
  app/                   ← Python source
  bin/                   ← shell scripts (start_*.sh, apply_patches.sh)
  patches/               ← hotpatch files applied by bin/apply_patches.sh
  vendor/                ← shallow clone of tt-inference-server (gitignored)
  docker/                ← Docker image archive (Git LFS, ~7.4 GB)
  tests/                 ← pytest test suite (107 tests)
  tt-gen                 ← GUI launcher
  tt-ctl                 ← CLI (status, history, start/stop services)
```

## Architecture

| File | Purpose |
|---|---|
| `app/main.py` | `Gtk.Application` entry point |
| `app/main_window.py` | All GTK4 widgets and `MainWindow` |
| `app/worker.py` | `GenerationWorker` — pure Python, no GUI imports |
| `app/api_client.py` | HTTP client for the inference server |
| `app/server_manager.py` | Start/stop/health for all managed services (no GTK) |
| `app/history_store.py` | Persistent JSON history + file path management |

`worker.py`, `api_client.py`, `server_manager.py`, and `history_store.py` have
**zero GUI dependencies** — keep them that way.

## Server management (`server_manager.py`)

`app/server_manager.py` is the single source of truth for all managed services.
It is imported by both `tt-ctl` and `main_window.py`. Add new services there by
adding a `ServerDef` to `SERVERS`. Current services: `wan2.2`, `mochi`, `skyreels`,
`flux`, `animate`, `prompt-server`. The key `"all"` starts the recommended set
(`wan2.2` + `prompt-server`).

```python
from server_manager import start, stop, restart, health, status_all, SERVERS

start("wan2.2")           # launch Wan2.2 server (non-blocking --gui mode)
stop("prompt-server")     # send --stop to the prompt-gen script
health("wan2.2")          # {"wan2.2": True/False}
status_all()              # {"wan2.2": True, "prompt-server": False, ...}
```

Path resolution: `_REPO_ROOT = Path(__file__).resolve().parent.parent` (app/ → repo root).
All script paths are `_BIN / sdef.script` where `_BIN = _REPO_ROOT / "bin"`.

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

## Assets

`app/assets/` contains:
- `tenstorrent.png` — 32×32 app icon (pulled from tenstorrent.com/favicon.ico)
- `ai.tenstorrent.tt-video-gen.desktop` — XDG desktop entry for GNOME/KDE launchers

`setup_ubuntu.sh` copies both into the correct XDG locations automatically.
To install manually:
```bash
cp app/assets/tenstorrent.png ~/.local/share/icons/hicolor/32x32/apps/ai.tenstorrent.tt-video-gen.png
cp app/assets/ai.tenstorrent.tt-video-gen.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications
```

## Video hover / looping

`Gtk.Video.set_loop(True)` is unreliable when playback is driven by calling
`get_media_stream().play()` directly — it bypasses GTK's internal
`notify::ended` → seek(0) → play() loop restart logic.

**Fix in place**: `GenerationCard._play_hover_stream()` lazily connects a
`notify::ended` handler (`_on_stream_ended`) the first time a stream is played,
then manually seeks to 0 and restarts. `_loop_connected` guards against double-
connecting.

The stream itself is created lazily by GStreamer and `get_media_stream()` returns
`None` until the `Gtk.Video` widget has been realized. `_play_hover_stream()`
retries via `GLib.timeout_add(100, ...)` if the stream is not yet available.

## GTK Application single-instance behaviour

`Gtk.Application` uses D-Bus to enforce a single instance per `application_id`
by default. If any process has already registered `ai.tenstorrent.tt-video-gen`
on the session bus, a second `./tt-gen` invocation silently exits (code 0)
without ever firing `activate`.

**Fix in place**: `main.py` calls `app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)`
so every launch is independent. If the app is not opening, also check for a
stale process: `pgrep -a python3 | grep main.py`.

## Stale .pyc cache

If the app crashes with a traceback pointing to a line number that doesn't
match the source, the bytecode cache is stale (e.g. from an earlier version).
Clear it with:
```bash
find ~/code/tt-local-generator/app -name "*.pyc" -delete
find ~/code/tt-local-generator/app -name "__pycache__" -type d -exec rm -rf {} +
```

## Running tests

```bash
# Full suite — xvfb-run provides a virtual X11 display so GTK widget tests run
xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q

# Headless fallback (no display available) — GTK widget tests are skipped
/usr/bin/python3 -m pytest tests/ -q
```

`xvfb-run` is pre-installed on Ubuntu 24.04 (`apt install xvfb` if missing).
Two pre-existing, environment-level flakes are expected and should be deselected
in full-suite runs (both pass in isolation / are unrelated to app code):
`test_forge_transforms::test_on_transform_finished_appends_and_refreshes`, and
`test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module` (a
`cffi`/`cairosvg` version-mismatch that only surfaces under full-suite import
ordering). Plus one environment skip (`test_regression_guards` when
`docs/assets/` is absent).

Tests are in `tests/` at repo root. Each file does `sys.path.insert(0, str(Path(__file__).parent.parent / "app"))` to import from `app/`. Tests mock all subprocess and network calls.

## Vendored `tt-inference-server`

`vendor/tt-inference-server/` is a shallow git clone of the upstream repo (gitignored due to 143 GB working tree). The pinned commit SHA is in `vendor/VENDOR_SHA`.

```bash
cat vendor/VENDOR_SHA            # see what's pinned
./bin/apply_patches.sh           # apply patches/ to vendor/
```

The `.env` file at `vendor/tt-inference-server/.env` is passed to Docker containers via `--env-file`. Key variables:
- `TT_DIT_CACHE_DIR` — caches compiled TT weights across container restarts (~66 GB after first run)
- `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1` — prevents HF network access during startup (weights are bind-mounted from host cache)

The `patches/` directory contains:
- `patches/media_server_config/config/constants.py` — overrides P300X2 device config, request timeouts, adds missing v0.17.0 symbols (CANARY_TASK_IDS etc.)
- `patches/media_server_config/tt_model_runners/dit_runners.py` — adds TTWan22AnimateRunner, SkyReels log-map entries, Mochi cache-dir env, Flux trace-region bump
- `patches/media_server_config/tt_model_runners/runner_fabric.py` — routes SkyReels and Animate model runners
- `patches/media_server_config/tt_model_runners/skyreels_runner.py` / `skyreels_i2v_runner.py` — SkyReels T2V and I2V runners
- `patches/media_server_config/domain/video_generate_request.py` — request-model extensions
- `patches/tt_dit/` — pipeline fixes (bind-mounted only in dev_mode)

### Model registry migrated to YAML in 0.18.0

0.18.0 replaced the inline-Python `ModelSpecTemplate(...)` list in
`workflows/model_spec.py` with YAML catalogs under
`workflows/model_specs/{prod,dev}/*.yaml`, loaded by `load_templates_from_yaml()`.
`--dev-mode` (used by all `start_*.sh` scripts) sets `MODEL_SPECS_ENV=dev`, so the
catalog actually consulted is `dev/*.yaml` (video models → `dev/video.yaml`).

**Consequence:** `apply_patches.sh`'s old text-injection anchor in `model_spec.py`
(`"]\n\n# ... image_templates"`) no longer exists — `model_spec.py` isn't read
for video models anymore. This broke SkyReels registration after the 0.18.0
upgrade: the old Step 6/7 printed "ERROR: could not find insertion anchor" and
SkyReels never registered, so `run.py --model SkyReels-V2-I2V-14B-540P` said
"invalid choice". The fix (Step 6 in `apply_patches.sh`) now appends
the SkyReels T2V/I2V entries directly to `dev/video.yaml` as YAML text — same
idempotent pattern (skip if the weights string is already present), just a
different target file and format. `MODEL_SPEC_YAML` points at that file.

**Not yet migrated:** Steps 7-9 (Animate `ModelSpecTemplate` injection, DeepSeek
and SDXL version bumps) still target the legacy `model_spec.py` anchor. Step 7
(Animate) is *already* failing with the same "could not find insertion anchor"
error as of 0.18.0 — it was previously masked because Step 6 died first. If a
model needs re-registering there, apply the same YAML-catalog treatment used for
SkyReels rather than trying to fix the `model_spec.py` anchor.

### Patch philosophy — minimize divergence from upstream

**Goal: always use the latest and greatest features in each tt-inference-server release.**
Patches are a compatibility shim, not a fork. Keep the surface area as small as possible:

- **Rebase patches onto each new image version.** When upgrading the Docker image,
  diff the new image's files against the current patch and drop any lines that are
  now in upstream. `docker create <new-image> && docker cp … /tmp/` to extract files.
- **Never copy-and-modify upstream runners whole-cloth.** Add only what is missing
  (new runner class, log-map entry, env var, constant override). Everything else
  stays as the image shipped it.
- **The canonical check:** `diff <image-extracted-file> patches/…/<file>` should
  show only the lines we intentionally added. Anything else is drift that should be
  removed.
- **Sync patches/ → vendor/ after every edit.** `apply_patches.sh` does this, but
  if you edit a patch file by hand, also `cp patches/… vendor/tt-inference-server/patches/…`
  immediately — the bind-mount uses the *vendor* copy, not the *patches/* copy.

## Prompt generator

A three-tier algorithmic prompt generator lives alongside the UI. It runs
independently of the video server and works even when no TT hardware is
available.

### Files

| File | Purpose |
|---|---|
| `app/generate_prompt.py` | CLI generator — algo → Markov → LLM polish |
| `app/word_banks.py` | All word banks as Python lists + sampling helpers |
| `app/prompt_server.py` | FastAPI server exposing Qwen3-0.6B on port 8001 |
| `bin/start_prompt_gen.sh` | Start/stop the prompt server |
| `app/prompts/prompt_generator.md` | System prompt for interactive LLM use |
| `app/prompts/markov_seed.txt` | Seed corpus for the Markov chain (tagged by type) |
| `app/prompts/markov_output.txt` | Accumulate good outputs here to grow the corpus |

### Three-tier design

**Tier 1 — Algorithmic** (`--mode algo`, always available):
`word_banks.py` contains every category as a Python list. `generate_prompt.py`
calls `random.choice()` on each slot independently. Selection happens in code,
not by the LLM, so diversity is guaranteed regardless of model size.

**Tier 2 — Markov** (`--mode markov`, requires `markovify`):
Trained on `prompts/markov_seed.txt` (and `markov_output.txt` if it exists).
Produces novel sentence-level recombinations — useful for unexpected register
collisions. Falls back to algo if the corpus is too small or markovify isn't
installed.

**Tier 3 — LLM polish** (`--enhance`, default on):
Sends the tier-1/2 slug to Qwen3-0.6B (port 8001) with a short polishing
prompt. The LLM only makes the output flow naturally — it does not re-select
elements. Falls back gracefully (returns the raw slug) if the server is down.

### CLI usage

```bash
# Default: algo + LLM polish, video type
python3 app/generate_prompt.py

# Markov mode, image type
python3 app/generate_prompt.py --type image --mode markov

# Algo only, no LLM, five prompts
python3 app/generate_prompt.py --count 5 --no-enhance

# Plain text output (no JSON wrapper)
python3 app/generate_prompt.py --raw

# All types
python3 app/generate_prompt.py --type video      # for Wan2.2 / Mochi
python3 app/generate_prompt.py --type image      # for FLUX / SD
python3 app/generate_prompt.py --type animate    # for Wan2.2-Animate
python3 app/generate_prompt.py --type skyreels   # for SkyReels-V2
```

### JSON output schema

```json
{
  "prompt": "Final polished prompt string",
  "type":   "video" | "image" | "animate" | "skyreels",
  "source": "llm" | "markov" | "algo",
  "slug":   "Raw pre-polish slug (always present)"
}
```

### Starting the prompt server

```bash
./bin/start_prompt_gen.sh          # start in background, wait for ready
./bin/start_prompt_gen.sh --stop   # stop
./bin/start_prompt_gen.sh --gui    # start silently (no tail, for GUI use)
# Or: ./tt-ctl start prompt-server
```

The server loads Qwen3-0.6B on CPU (~2.9 GB RSS, ~19 tok/s on Ryzen 7 9700X).
It runs on port 8001 and does not touch the TT chips, so it coexists with any
video generation server on port 8000.

Health check: `curl -s http://localhost:8001/health`
→ `{"status":"ok","model_ready":true}`

### Wiring into the UI

The generator is a standalone subprocess — the UI calls it and parses JSON.

**Minimal integration** (one prompt on demand):

```python
import subprocess, json

def generate_prompt(prompt_type="video", mode="markov"):
    result = subprocess.run(
        [
            "python3",
            "/home/ttuser/code/tt-local-generator/app/generate_prompt.py",
            "--type", prompt_type,
            "--mode", mode,
        ],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)["prompt"]
```

**Threading** — run the subprocess in a background thread (not the GTK main
thread). Post the result back with `GLib.idle_add` per the GTK threading rule
above:

```python
import threading
from gi.repository import GLib

def _fetch_prompt_async(prompt_entry, prompt_type="video"):
    def worker():
        prompt = generate_prompt(prompt_type)
        if prompt:
            GLib.idle_add(prompt_entry.set_text, prompt)
    threading.Thread(target=worker, daemon=True).start()
```

**Auto-start the server** (optional): call `start_prompt_gen.sh --gui` from the
app startup sequence (same pattern as the video server). Poll `/health` until
`model_ready` is true before enabling the "✨ Generate prompt" button.

**Prompt type mapping**:

| UI tab / source | `--type` |
|---|---|
| Video (Wan2.2, Mochi) | `video` |
| Video (SkyReels) | `skyreels` |
| Image (FLUX, SD) | `image` |
| Animate (Wan2.2-Animate) | `animate` |

### Growing the Markov corpus

Append good generated prompts to `prompts/markov_output.txt` in the same
tagged format (`video|...`, `image|...`, `animate|...`). The model is rebuilt
fresh on each `generate_prompt.py` run, so additions take effect immediately.
This file is intentionally gitignored — it accumulates machine-specific history.

### Extending the word banks

Edit `word_banks.py` directly — add entries to any list. More unusual / specific
entries outperform common ones (the model anchors to surprising items). After
editing, the changes take effect on the next `generate_prompt.py` call with no
restart needed. The `prompts/prompt_generator.md` system prompt is separate and
used only for interactive LLM chat (not by `generate_prompt.py`).

---

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

- **Prompt server shows "algo only" from remote Mac**: `start_prompt_gen.sh`
  was hardcoding `--host 127.0.0.1`, binding the server to loopback only.
  Connections from a Mac client via `--server http://quietbox:8000` were
  refused at the network level. Fixed by changing to `--host 0.0.0.0`
  (configurable via `PROMPT_HOST` env var). Restart the server on quietbox
  after pulling this change.

---

## .deb packaging (Ubuntu 24.04)

**What happened:** Analysed dependency taxonomy and implemented full `debian/`
packaging infrastructure for Ubuntu 24.04 (Noble).

**Original prompt:** "Analyse what it would take to package tt-local-generator
as a .deb for Ubuntu 24.04, with embedded tt-inference-server. Identify which
deps fit dpkg, which can reference tt-installer, and how to communicate deps
outside both ecosystems."

### Files added

| File | Purpose |
|---|---|
| `debian/control` | Package metadata, Depends/Recommends/Suggests |
| `debian/rules` | debhelper build rules (dh 13) |
| `debian/postinst` | Docker CE apt setup, pip extras, .env seed, image pull, checklist |
| `debian/prerm` | Stop managed services before removal |
| `debian/conffiles` | Mark vendor .env as user-editable (preserved on upgrade) |
| `debian/changelog` | Debian changelog (version 0.1.0, noble) |
| `debian/compat` | debhelper compat level 13 |
| `debian/copyright` | Apache-2.0 copyright declaration |
| `bin/snapshot_vendor.sh` | Snapshot Python-only files from tt-inference-server into vendor/ |

### Files modified

- `app/assets/ai.tenstorrent.tt-video-gen.desktop` — `Exec=` updated from
  hardcoded `~/code/…/tt-gen` to `/usr/bin/tt-local-gen`

### Dependency taxonomy summary

- **Tier 1 (dpkg):** python3-gi, python3-requests, ffmpeg, GStreamer stack, gir1.2-gtk-4.0
- **Tier 2 (external apt):** docker-ce — added by postinst; `Recommends: docker-ce | docker.io`
- **Tier 3 (pip-only):** markovify — installed by postinst via `pip --break-system-packages`
- **Tier 4 (tt-installer):** torch, transformers, ttkmd — `Recommends: tt-installer`; prompt-server warns if absent
- **Tier 5 (out-of-band):** Docker image (~15 GB, pulled by postinst), Wan2.2 weights (~118 GB, checklist item)

### Build command (run on QB2 target)

```bash
# 1. Snapshot the vendor Python files
./bin/snapshot_vendor.sh --src ~/code/tt-inference-server

# 2. Build the .deb
dpkg-buildpackage -us -uc -b

# 3. Lint
lintian ../tt-local-generator_0.1.0_amd64.deb

# 4. Install
sudo apt install ../tt-local-generator_0.1.0_amd64.deb
```

### Known issues / next steps

- **`snapshot_vendor.sh` placeholder SHA:** `DEFAULT_SHA` in `bin/snapshot_vendor.sh`
  is a placeholder. Replace with the real git SHA of the `0.15.0-25891d3` image's
  source commit before building for distribution.
- **`vendor/VENDOR_SHA`:** The `vendor/` directory is gitignored. Either remove
  the gitignore entry before a release build, or run `snapshot_vendor.sh` as part
  of the CI pipeline.
- **`debian/compat` vs `debhelper-compat` in control:** Both declare compat 13.
  debhelper ≥ 12 recommends using only the `Build-Depends: debhelper-compat (= 13)`
  form; the `debian/compat` file is kept for compatibility with older toolchains.
- **Testing:** Active install testing must happen on QB2 (Ubuntu 24.04 amd64).
  The Mac dev machine cannot run `dpkg-buildpackage` natively.

---

## .deb model packages (0.2.0)

**What happened:** Added four binary model-download packages (`tt-model-wan2-t2v`,
`tt-model-flux`, `tt-model-mochi`, `tt-model-qwen3`) that download HuggingFace
weights at install time, with a shared debconf HF token question.

**Original prompt:** "Create virtual/meta .deb packages — one per inference mode —
that download the required HuggingFace model weights after collecting/sourcing a
HF_TOKEN via debconf when not already present."

### New files (13)

| File | Purpose |
|---|---|
| `bin/download_model.sh` | Shared HF downloader: `--repo`, `--token`, `--skip-if-exists`, `--check-only` |
| `debian/tt-model-wan2-t2v.templates` | debconf password question (`tt-local-generator/hf-token`) |
| `debian/tt-model-wan2-t2v.config` | Token discovery → pre-set or prompt |
| `debian/tt-model-wan2-t2v.postinst` | Download `Wan-AI/Wan2.2-T2V-A14B-Diffusers` (~118 GB) |
| `debian/tt-model-flux.templates` | Same debconf question (gated-model notice in description) |
| `debian/tt-model-flux.config` | Same token discovery pattern |
| `debian/tt-model-flux.postinst` | Download `black-forest-labs/FLUX.1-dev` (~34 GB) |
| `debian/tt-model-mochi.templates` | Same debconf question |
| `debian/tt-model-mochi.config` | Same token discovery pattern |
| `debian/tt-model-mochi.postinst` | Download `genmo/mochi-1-preview` (~20 GB) |
| `debian/tt-model-qwen3.templates` | Same debconf question (prompt always suppressed) |
| `debian/tt-model-qwen3.config` | Token optional; `db_fset seen true` so no prompt |
| `debian/tt-model-qwen3.postinst` | Download `Qwen/Qwen3-0.6B` (~1.2 GB) |

### Modified files (3)

| File | Change |
|---|---|
| `debian/control` | Four new `Package:` stanzas (Architecture: all) |
| `debian/rules` | Symlink `download_model.sh` → `/usr/bin/tt-local-gen-download-model` |
| `debian/changelog` | Bump to 0.2.0 |

### Design decisions

- **Shared debconf key:** All four `.templates` files declare the same key
  (`tt-local-generator/hf-token`). debconf merges by name, so a single `apt install`
  of multiple packages prompts once.
- **Immediate wipe:** Each postinst calls `db_reset` right after `db_get` — the
  token lives in `passwords.dat` for seconds only.
- **`runuser`:** postinst runs as root; the download script is invoked as
  `$SUDO_USER` so weights land in the correct user's `~/.cache/huggingface/hub/`.
- **Non-fatal downloads:** If the download fails, postinst prints retry instructions
  and exits 0 — the package stays installed and other packages aren't rolled back.
- **Qwen3 special case:** `tt-model-qwen3.config` always sets `seen=true` because
  the model is fully public. A token found in the environment is still forwarded
  for rate-limit avoidance.

### Build (same as 0.1.0, produces five .deb files)

```bash
./bin/snapshot_vendor.sh --src ~/code/tt-inference-server
dpkg-buildpackage -us -uc -b
# Produces: tt-local-generator_0.2.0_amd64.deb
#           tt-model-wan2-t2v_0.2.0_all.deb
#           tt-model-flux_0.2.0_all.deb
#           tt-model-mochi_0.2.0_all.deb
#           tt-model-qwen3_0.2.0_all.deb
```

### Manual re-download helper

```bash
# Re-run a failed download without reinstalling the package:
tt-local-gen-download-model --repo Wan-AI/Wan2.2-T2V-A14B-Diffusers
tt-local-gen-download-model --repo black-forest-labs/FLUX.1-dev --token hf_xxxx
tt-local-gen-download-model --repo Qwen/Qwen3-0.6B --skip-if-exists

# Check whether a model is already cached:
tt-local-gen-download-model --repo genmo/mochi-1-preview --check-only
```

---

## macOS remote-client video playback (in progress — 2026-04-14)

**Symptom:** Gtk.Video shows ⊘ (broken-media icon), ▶ Play does nothing, hover
preview is blank. "Open externally" and "Export" both work (files are valid MP4s).

**Root cause hypothesis:** `libmedia-gstreamer.dylib` — the GTK4↔GStreamer bridge —
is absent from the Homebrew `gtk4` bottle. Without it `get_media_stream()` returns a
`GtkMediaStream` already in error state; `stream.play()` silently no-ops.

**Diagnostics added:**
- `bin/test_macos.sh` — comprehensive check: GStreamer elements, `libmedia-gstreamer`
  presence, `GST_PLUGIN_PATH`, gst-launch smoke test against a real MP4.
- `DetailPanel._toggle_play` now calls `stream.get_error()` before `stream.play()`
  and prints the GLib error message + hint to stderr when the stream is errored.
- `DetailPanel.show_record` registers a `notify::error` handler via a 200 ms
  `GLib.timeout_add` so async pipeline errors also appear on stderr.

**Key check — run on the Mac:**
```bash
./bin/test_macos.sh        # look at section [ 6 ] for libmedia-gstreamer
```

**Likely fix if `libmedia-gstreamer` is missing:**
```bash
brew install --build-from-source gtk4   # rebuilds gtk4 with GStreamer backend enabled
```
GTK4 Homebrew bottles are pre-built before GStreamer is present, so the backend is
compiled out. Building from source after `brew install gstreamer gst-plugins-*` picks
it up.

**`_llm_available()` timeout** raised 2 s → 3 s (`app/generate_prompt.py`) so remote
Qwen servers on LAN don't get false-negative health checks.

**Gallery ordering** fixed: `_load_history` now sorts merged local+remote records by
`created_at` descending so downloaded records appear chronologically, not at the top.
