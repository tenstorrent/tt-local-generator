# Server control through the `tt` CLI (design)

Date: 2026-10-28. Branch: `tt-cli-servers` (from `main`). Status: written for the operator's review; nothing is built.

Prompt (operator): "i want the server start stop wired through the tt local generator CLI and the servers tab at the upper right. we can
show helpful stuff like this but we can also be the engine that does it ... let's shift to being tt-cli centric where we can. this can be
a separate PR too from main, not just about music."

Decisions from the conversation are marked **(decided)**. Anything marked **(my call)** was chosen while writing and is open to change.
The music PR (#30) is separate. Replacing its "Start a music model" copy-commands with real Start buttons waits until both PRs have landed. **(decided)**

## 1. Intent

Starting and stopping model servers has several routes now. `tt serve family/name` is the official one: it picks tt-inference-server,
TT-Studio or tt-model by itself. tt-local-generator also has its own start scripts for models it carries in a special form (patched
SkyReels, Wan2.2-Animate, the AnimateDiff server, the artgen chat servers). People use these together on a shared box and alone on a
dedicated one. A model may already be loaded when someone asks for it, whoever loaded it.

The app should be the engine that starts, stops and lists servers. It should lead with `tt` and keep its own scripts as a second kind
of backend for models it offers in a special form. Both the Servers menu in the upper right and the `tt-ctl` command line use the same
layer. **(decided)**

## 2. Goals, non-goals, anti-goals

Goals
- One GTK-free layer, `app/tt_backend.py`, that wraps the `tt` CLI: list servers, serve, stop, logs, catalog. **(decided: tt-centric; my call: module name)**
- One merged view of what is running, from `tt model ps`, the health checks of our script-started servers, and discovery (music and
  artgen). A server started in a terminal, by another agent, or by our scripts shows up once. **(decided)**
- A registry that says, per model, how this app prefers to start it: `tt serve`, or one of our scripts (a "boutique" model). **(decided)**
- Start on a model that is already running starts nothing; the app uses the running server. **(decided)**
- Stop on a local server this app did not start asks first. A server reached only over the network has no Stop. **(decided)**
- On a shared machine, a start that opens chips takes a gozer lease for as long as the server runs, when gozer is installed and `tt`
  does not take one. **(decided)**
- The existing confirm-before-switch dialog and chip-lease rules still apply to every start. **(decided)**
- `tt-ctl start|stop|restart|logs|servers` keep working through the layer, print a one-line deprecation note pointing to `tt` and the
  Servers menu, and never fail because of the note. They are deprecated and not removed. **(decided)**
- "Connect..." (add a server by URL) works for any kind of server, not only music. Remote servers are use-only. **(decided for remote; my call for generalising Connect)**

Non-goals
- Replacing the whole `server_manager.ServerDef` model in one step. The new layer sits beside it and feeds `ModelStatusService`; the
  `SERVERS` table stays the source for boutique models.
- Writing our own container or vLLM launcher. `tt` and our existing scripts do that.
- Changing what a "Create job" needs from a server.
- The Music card's Start buttons (follow-up after both PRs land).
- Taking leases for servers this app did not start.

Anti-goals (a failure even if it technically works)
- Stopping someone else's model without asking.
- A Start click that boots a second copy of a model that is already loaded, or that evicts a running model on the same chips without the confirm dialog.
- A lease that outlives its server, or a server with no lease on a machine where the others take them.
- A menu that is wrong because it shows what the app started instead of what is running.
- Naming any tool of ours `tt`, or installing one under that name (global rule; check `which -a tt`).

## 3. What exists (read on 2026-10-28)

`tt` 1.2.0 (`/home/ttuser/.local/bin/tt`, also in `~/.tenstorrent-venv/bin`). `--json` is available on every command.
- `tt serve [model] [--inference-server|--studio|--model-manager] [--device p300x2] [--port N] [--offline] [--force] [--dry-run]`, and for
  tt-model bundles `--profile`, `--detach`, `--print`, `--refresh`, `--no-weights`, `--yes`. The boot is a live checklist; the command
  returns when the endpoint answers (or, for a bundle with `--detach`, when the container is up).
- `tt model ps --json` returns `{"served": [{name, backend, container, container_id, image, port, base_url, status, health, started_at,
  uptime_s, profile, served_id}], "probed": bool}`.
- `tt model stop <names...> [--profile P]`, `tt model logs`, `tt model list --json` (catalog: name, kind, engines, hardware, installed,
  backends, ...), `tt model pull`, `tt model profiles`, `tt model info`. There is no top-level `tt ps` or `tt stop`.
- `tt` takes no gozer lease and has no chip-pinning flag for catalog models. tt-model has `--device-id` for container packages.

Gozer (`~/code/tt-gozer`): `gozer acquire --json --chips N --who W --reason R [--expect 45m] [--owner-pid PID]`, `gozer release <lease>` (resets the
chips), `gozer status --json`, `gozer adopt <target>`, `gozer run --chips N -- cmd` (holds only while cmd runs).

This app: `server_manager.SERVERS` (about 15 `ServerDef`s, each a `bin/start_*.sh` script with `--gui`/`--stop`), `ModelStatusService`
(one poll thread, `Status.OFF/STARTING/READY/ERROR`), `ServersControl` (Servers menu, `servers_control.py`), `ready_to_run`
(confirm-before-switch), `tt-ctl` (`start`, `stop`, `restart`, `servers`, `logs`, `status`), and music discovery from the music PR.

## 4. Design

### 4.1 `app/tt_backend.py` (stdlib only, no GTK)

A thin, typed wrapper over the `tt` executable.
- `find_tt() -> Path | None`: first `tt` on PATH that reports itself as the Tenstorrent CLI (`tt --version` matches `^tt \d`), logging a warning when
  `which -a tt` shows another binary first. Result cached, re-checked on failure.
- `ps() -> list[TtServer]`: runs `tt --json model ps`, tolerant of unknown fields and of `tt` being absent (returns `[]` and a reason).
- `catalog() -> list[TtModel]`: `tt --json model list`, cached with a TTL; works with `--offline`.
- `plan_serve(model, port=None, device=None) -> ServePlan`: runs `tt serve --dry-run --json` to learn the backend, image and command without
  touching hardware.
- `serve(model, ..., on_line=None) -> ServeResult`, `stop(names)`, `logs(name, lines=200)`. `serve` streams the boot checklist lines
  through `on_line`, runs off the GTK thread, and can be cancelled.
- All calls use argument lists (never `shell=True`), a timeout per call, and `stdin=subprocess.DEVNULL`.
- Every call returns a result object and never raises into the UI. An error carries `tt`'s own last lines.

### 4.2 The model registry and backends

A `ModelEntry` says how this app starts a model: key, display name, capabilities, `backend` in `{"tt", "boutique"}`, the `tt` model name or bundle
id when `backend == "tt"`, the `server_manager` key when `backend == "boutique"`, and how to recognise it when running (served model id,
runner key, port). It is built from three sources, in order: our boutique `SERVERS` table, the `tt` catalog, and a small hand-written
override file for models where the preferred backend is not the default (for example a model that `tt` carries but we prefer to start from our
patched script). **(my call: sources and override file.)** A boutique model whose script works and whose `tt` entry also exists defaults to
our script, because the script carries our patches. **(my call.)**

### 4.3 The merged server view

`ServerView` combines, on one background thread and one refresh (reusing `ModelStatusService`'s thread, not a second one):
- `tt_backend.ps()` entries,
- `server_manager` health results for boutique models,
- music and artgen discovery results.
Each entry becomes a `RunningServer(key, name, source, url, host_is_local, port, health, uptime_s, container, served_id, owner)` where
`source` is `"tt" | "script" | "discovered" | "saved"`, `owner` is `"app" | "external" | "unknown"`, and `host_is_local` is false for network servers.
Entries are matched across sources by port, then served model id, then runner key, so a script-started SkyReels that `tt model ps`
also lists shows once. **(decided: one list; my call: matching order.)**
Ownership: the app records what it started in `~/.local/share/tt-local-generator/servers.json` (name, port, container id, lease id, started
time). A server is `app`-owned when that record matches a live server, `external` when it is local and unrecorded, `unknown` when its host
is not local. **(my call: the record file.)**
`ModelStatusService` gains these entries as an extra source, so every existing consumer (Create's dots, the status bar segments, the Ready-to-Run
gate) sees a model started from a terminal as READY. Its public API does not change.

### 4.4 Starting

`MainWindow` and `tt-ctl` both call `ServerControl.start(model_key, port=None) -> StartOutcome`, a GTK-free orchestrator in `app/server_control.py`:
1. Look the model up in the merged view. If it is running and healthy anywhere, return `AlreadyRunning(url)`; the UI says "Running (started elsewhere)" or "Running" and
   Create uses it. A model that another session is still booting returns `Starting` and the UI waits for it.
2. Ask `ready_to_run.plan_switch` what would conflict (same chips or same port). If something would be stopped, the existing confirm dialog
   names it (the CLI asks on the terminal, or takes `--yes`). The switch runs only after an accept.
3. If gozer is installed, no lease covers the target, and `tt` does not take leases itself (detected once from `tt --help` or a config flag
   when `tt` adds one), take a lease: `gozer acquire --json --chips N --who "tt-local-generator" --reason "<model>" --expect <duration>`,
   where N comes from the model's device requirement. If the lease is queued, report "waiting for chips" and wait on the ticket; the user can cancel. **(decided: lease for the life of the server; my call: details.)**
4. Start with the registry's backend: `tt_backend.serve(...)` for `tt` models, `server_manager.start(key)` for boutique ones, with the lease's
   `TT_VISIBLE_DEVICES` in the environment when the backend honours it.
5. Poll health until ready or a timeout, streaming progress to the UI/CLI, then record the server in `servers.json` with its lease id.
6. On failure: stop what was started, release the lease (resets the chips), and show `tt`'s or the script's last lines.

### 4.5 Stopping

`ServerControl.stop(server) -> StopOutcome`:
- `owner == "app"`: stop it (`tt model stop` or the boutique `--stop`), then `gozer release <lease>` if the record holds one.
- `owner == "external"` and local: the UI asks first, naming the server and who seems to have started it (backend, container, uptime). `tt-ctl` asks
  too, or takes `--yes`. After a yes, stop it the same way. No lease release for a lease the app does not hold. **(decided)**
- not local (`host_is_local` false): no Stop control exists; the row says "Remote". **(decided)**
A lease whose server has gone (a crash, or a stop from a terminal) is released at the next refresh, after one confirmation poll. **(my call.)**

### 4.6 The Servers menu (upper right)

`ServersControl` shows sections: **Running** (merged view: name, source tag, port, health dot, uptime, Use/Stop), **Ready to start**
(registry entries, installed first, with Start; a boutique model shows its own name and benefit line), **Available** (catalog models not installed,
with "Get and start"), and **Remote** (saved network servers). A row's status dot follows the existing 3-state glyphs. "Connect..." opens the
generalised URL dialog. A long start shows the boot checklist in the existing server-log area. **(my call: layout; decided: running + startable list.)**

### 4.7 `tt-ctl`

- `tt-ctl servers [--all] [--json]`: the merged list.
- `tt-ctl start|stop|restart|logs <key>` go through `ServerControl` and `tt_backend`, with the same prompts, plus a one-line note on stderr:
  `note: server commands in tt-ctl are deprecated; use \`tt serve\` / \`tt model stop\` or the Servers menu.` The note is silenced by `TTLG_NO_DEPRECATION_NOTE=1`.
- A key that is a `tt` model name or bundle id also works (`tt-ctl start Llama-3.1-8B-Instruct`).
- Exit codes and existing output formats stay as they are for scripts that depend on them. The old keys (`wan2.2`, `prompt-server`, ...) stay valid.

## 5. Constraints

- GTK threading rules in CLAUDE.md. All calls to `tt` and gozer run off the main thread.
- `tt_backend.py` and `server_control.py` import only the standard library plus existing GTK-free modules.
- `tt` missing, too old (JSON fields absent) or offline: the layer degrades to the boutique scripts and discovery, and the menu says why. Unknown JSON fields are ignored.
- No `shell=True`; no tool of ours is named `tt`.
- The backend-switch rule stands: nothing stops a running model on the shared hardware without an accepted confirm dialog (the QB2 fragility notes).
- Tests use a fake `tt` and a fake `gozer` script on a temporary PATH that print canned JSON. No test starts a real server or touches `/dev/tenstorrent`. Read-only calls to the real `tt` (`tt --version`, `tt model ps`, `tt serve --dry-run`) are allowed by hand during development.
- Version: new user-visible feature, minor bump as its own commit on this branch's tip.

## 6. Left to the builder

- Widget layout and copy of the menu sections, and the deprecation note's wording within the stated meaning.
- The catalog cache TTL, poll interval for `ps`, and timeouts.
- The shape of the `servers.json` record, within the fields in section 4.3.
- How `tt serve`'s boot lines are parsed for progress, within what `--json` and the live checklist give.

## 7. Open facts to confirm early

1. How `tt serve` chooses chips for a catalog model, and whether `TT_VISIBLE_DEVICES` (or tt-model's `--device-id`) pins it to the leased chips.
2. Whether servers started by our `bin/start_*.sh` scripts appear in `tt model ps` (they run `run.py` in docker, not `tt-model`), and what identifies them.
3. How a lease can follow a long-lived server: `gozer acquire` with `--owner-pid` of a small supervisor, `gozer adopt` after the server is up, or a lease with no
   owner and an explicit release. The builder checks `~/code/tt-gozer` and picks the one that survives an app restart.
4. Whether `tt model stop` stops a container that `tt-model` did not start, and what it does with a server started by tt-inference-server `run.py`.
5. Which models are boutique today (the `SERVERS` table is the starting list) and which of them `tt` also carries.

## 8. Testing

- Fake `tt` and fake `gozer` on PATH: `ps` parsing with unknown fields, missing `tt`, a `tt` that is not ours, a timeout, a nonzero exit with stderr.
- Merge tests: the same server seen by `ps` and by a health check gives one row; a server on a remote host is `host_is_local` false; ownership from `servers.json`.
- `ServerControl.start`: already running gives `AlreadyRunning` with no `tt serve` call; a conflict asks first and a decline starts nothing; a queued lease waits and
  cancels cleanly; a failed start releases the lease; a boutique model uses the script path.
- `ServerControl.stop`: an `app` server stops and releases; an `external` one asks and a decline leaves it running; a remote one has no Stop.
- `tt-ctl`: the deprecation note appears on stderr and not on stdout, exit codes unchanged, `--json` output unchanged for the old verbs.
- Menu: sections render from a fake view; Stop is absent for a remote row; Start on a running model does not call `serve`.
- The pipe lesson in CLAUDE.md: a progress line from `tt_backend.serve` reaches the menu's log through every hop.
- Each new test is seen failing against the broken or absent code before it is trusted.

## 9. Calls made after the builder check (my calls, open to change)

- **Chip count, port and hardware group for a `tt` catalog model.** Sources in order: `tt serve --dry-run --json` (the resolved config), then the `hardware`
  field of `tt model list --json`, then the override file. The registry emits synthetic `ServerDef`-like entries (key, capabilities, hardware group,
  port) so `ready_to_run.plan_switch` and the lease size work unchanged. When none of the sources gives a value, the start warns and asks; it does not block.
- **`tt-ctl` output.** `servers --json` keeps its old shape. The merged view is `servers --all` and, under `--json`, an added `merged` key. Old keys and
  exit codes are untouched. `restart` on an `external` or `tt`-owned server is stop (with the same prompt), then start.
- **Leases without the app running.** No supervisor process. The app's refresh and `tt-ctl servers` both reap a lease the app recorded in `servers.json`
  when its server is gone, after one confirming poll. A lease can stay held between sweeps. `gozer reconcile` remains the backstop.
- **Matching.** A match needs the port and at least one of served model id or runner key. A port match with a different served id is a different model
  on a shared port: a conflict, not the same server. A recorded container id that no longer exists makes the record stale; that server is `external`.
- **Deprecation note.** The app never shells out to `tt-ctl` for server control; it calls the layer directly. Any internal caller that does shell out sets
  `TTLG_NO_DEPRECATION_NOTE=1`.
- **When `tt serve` cannot be pinned to the leased chips** (operator, 2026-10-28: the default is right, and it is best effort; the builder does not go further
  than this). Default: the app reads which chips are free from `gozer status --json`, starts, finds the chips the container actually opened (its `--device`
  mounts, from `docker inspect`), and runs `gozer adopt` on them. If another lease already holds any of those chips, the app stops the new server, says so, and
  does not start it. On a machine with no gozer, no lease logic runs. A boutique alternative is offered when the model has one.
