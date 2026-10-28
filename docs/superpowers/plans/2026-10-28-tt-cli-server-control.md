# Server Control Through the `tt` CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the app the engine that lists, starts and stops model servers through the `tt` CLI (with our boutique scripts as a second backend), for both the Servers menu and `tt-ctl`.

**Architecture:** A GTK-free `tt_backend` wraps `tt`; a `gozer_client` wraps gozer; a registry says how each model is started; a merged view says what is running and who owns it; `ServerControl` orchestrates start and stop (confirm, lease, health). The Servers menu and `tt-ctl` call `ServerControl`. `ModelStatusService` gets the merged view as an extra source so every existing status consumer sees servers started elsewhere.

**Tech Stack:** Python 3 stdlib, GTK4/PyGObject, pytest (xvfb-run), the `tt` 1.2.0 CLI and gozer as external executables (faked in tests).

**Spec:** `docs/superpowers/specs/2026-10-28-tt-cli-server-control-design.md` (section 9 holds the calls made after review).

## Global Constraints

- Branch `tt-cli-servers`, from `main`. The music PR (#30) is separate; both touch `main_window.py` and `create_view.py` areas, so keep hunks small and expect a merge fix-up later.
- `tt_backend.py`, `gozer_client.py`, `server_registry.py`, `server_view.py` and `server_control.py` import only the standard library plus existing GTK-free modules. No GTK in them.
- GTK calls only on the main thread; every call to `tt` or gozer runs off it and reports back through `GLib.idle_add`.
- No `shell=True`; argument lists only; a timeout on every subprocess; `stdin=subprocess.DEVNULL`.
- No tool of ours is named `tt`; `find_tt()` accepts only a binary whose `--version` output matches `^tt \d`.
- Nothing stops a running model on shared hardware without an accepted confirm dialog (QB2 fragility). A server started elsewhere on this machine is stopped only after an accepted prompt. A server on a non-local host has no Stop.
- On a machine with gozer installed and where `tt` takes no lease itself, a start that opens chips takes a lease first when it can be pinned to chosen chips (acquire, then start pinned) and adopts the chips the container opened when it cannot (no pre-acquire); a failed or stopped server releases it. Chip pinning is best effort and goes no further than spec section 9.
- `tt-ctl start|stop|restart|logs|servers`: old keys (`wan2.2`, `prompt-server`, ...), exit codes and stdout stay as they are. The deprecation note goes to stderr only and is silenced by `TTLG_NO_DEPRECATION_NOTE=1`. The note text: `note: server commands in tt-ctl are deprecated; use \`tt serve\` / \`tt model stop\` or the Servers menu.`
- `server_manager.SERVERS`, `ready_to_run.plan_switch(selected_key, status_of)` and `ModelStatusService`'s public API stay backward compatible (new parameters default to today's behaviour).
- Tests use a fake `tt` and a fake `gozer` script on a temporary PATH. No automated test starts a real server or touches `/dev/tenstorrent`. By hand, only read-only `tt --version`, `tt --json model ps`, `tt model list`, `tt serve --dry-run`.
- `_CSS` byte literals stay ASCII-only. Palette: app main scheme `#4FD1C5` on `#0F2A35`.
- Version: new user-visible feature, minor bump (`VERSION` and `debian/changelog`) as the branch tip's own commit (Task 9).

## Review Focus

1. A model already running (started in a terminal, by another agent, or by our script) and healthy: Start calls no `tt serve` and returns the running URL; a model that `tt model ps` shows still booting returns "starting elsewhere" and waits. Pinned in Task 6.
2. Stop on a local server the app did not start asks first and a "no" leaves it running; a non-local server has no Stop control at all; an app-owned server stops without asking and releases its lease. Pinned in Tasks 6 and 8.
3. `tt` missing, a different `tt` first on PATH, an older `tt` whose JSON lacks fields, a hung `tt`: the layer degrades to scripts and discovery with a stated reason and raises nothing into the UI. Pinned in Task 2.
4. Lease leaks: a failed start releases its lease; a stop releases it; a lease whose server is gone is reaped after one confirming poll; a start where another lease holds the target chips does not start. Pinned in Tasks 3 and 6.
5. Shared-port mismatch: a `tt model ps` entry on the same port as a registry model but with a different served model id is a conflict, never the same server. A recorded container id that no longer exists makes that server `external`. Pinned in Task 5.
6. `tt-ctl` keeps old exit codes and stdout; the note is on stderr only; `--json` on `servers` is new and the text output is byte-identical. Pinned in Task 7.

---

### Task 1: Spike the open facts and capture fixtures

**Files:**
- Create: `docs/superpowers/specs/2026-10-28-tt-cli-wire-notes.md`
- Create: `tests/fixtures/tt/ps.json`, `tests/fixtures/tt/list.json`, `tests/fixtures/tt/serve_dry_run.json`, `tests/fixtures/gozer/status.json`

**Interfaces:**
- Produces: the fixtures and notes every later task's fake `tt` / fake `gozer` reads. A fixture is real output with hostnames and paths left as they are but no tokens.

- [ ] **Step 1: Capture (read-only).** Save `tt --json model ps`, `tt --json model list` (trim to about 12 models covering kinds `container` and bundle and engines), `tt --json serve <an installed catalog model> --dry-run` (never without `--dry-run`), and `gozer status --json`. If `tt serve --dry-run --json` does not emit JSON, record the plain output and what can be parsed.
- [ ] **Step 2: Answer spec section 7 in the notes file** with evidence: (1) how `tt serve` chooses chips and whether `TT_VISIBLE_DEVICES` or tt-model `--device-id` pins them (read `tt serve --help`, the dry-run command, and `tt-model` source/help; do not start anything); (2) whether a server started by our `bin/start_*.sh` appears in `tt model ps` (read what `tt model ps` reads: docker labels or tt-model state; check `docker ps` labels on a script-started container if one is running, else say unverified); (3) which gozer mechanism fits a long-lived server (`acquire --owner-pid`, `adopt`, or an explicit release; read `~/code/tt-gozer` docs); (4) what `tt model stop` does to a container it did not start (read the help and code only; do not stop anything); (5) which `SERVERS` models `tt model list` also carries.
- [ ] **Step 3: Decision gate.** If any answer contradicts spec sections 4.3 to 4.5 or 9, write the correction into the spec and this plan's Interfaces blocks before Task 2, and say so in the commit.
- [ ] **Step 4: Commit** `git commit -m "docs: tt CLI wire notes and fixtures"`.

### Task 2: `tt_backend`

**Files:**
- Create: `app/tt_backend.py`, `tests/fake_tt.py` (writes a fake `tt` executable script into a temp dir driven by a JSON behaviour file), `tests/test_tt_backend.py`

**Interfaces:**
- Produces (all in `tt_backend.py`):
  - `@dataclass(frozen=True) TtServer(name: str, backend: str, container: str | None, container_id: str | None, image: str | None, port: int | None, base_url: str | None, status: str, health: str, started_at: str | None, uptime_s: int | None, profile: str | None, served_id: str | None)`.
  - `@dataclass(frozen=True) TtModel(name: str, kind: str | None, engines: tuple[str, ...], hardware: tuple[str, ...], installed: bool, backends: tuple[str, ...])`.
  - `@dataclass(frozen=True) ServePlan(model: str, backend: str | None, command: tuple[str, ...], device: str | None, port: int | None, chips: int | None)`. Built from `tt --json serve <model> --dry-run --offline`: `command` = `argv`; `backend` = `backend` (bundle) or `"inference-server"` (catalog); `device` = `device_requested` (catalog) or `bundle.default_profile`; `chips` = `bundle.device_count` (bundle) or `None` (catalog). Fixtures: `tests/fixtures/tt/serve_dry_run.json` (bundle), `serve_dry_run_catalog.json`.
  - `@dataclass(frozen=True) TtResult(ok: bool, reason: str, lines: tuple[str, ...])`.
  - `find_tt(which: Callable = shutil.which, run: Callable = _run) -> Path | None` (cached, `reset_cache()` for tests).
  - `ps(timeout: float = 20.0) -> tuple[list[TtServer], str]` (second item is the reason `[]` was returned, or `""`).
  - `catalog(ttl: float = 300.0, offline: bool = True) -> list[TtModel]`.
  - `plan_serve(model: str, *, port: int | None = None, device: str | None = None) -> ServePlan | None`.
  - `serve(model: str, *, port: int | None = None, device: str | None = None, detach: bool = False, on_line: Callable[[str], None] | None = None, cancel: Callable[[], bool] | None = None, env: dict | None = None, timeout: float = 3600.0) -> TtResult`.
  - `stop(names: Sequence[str]) -> TtResult`, `logs(name: str, lines: int = 200) -> str`.

- [ ] **Step 1: Write failing tests** with the fake `tt` and the Task 1 fixtures: `test_find_tt_accepts_the_real_cli`, `test_find_tt_rejects_a_binary_that_is_not_tt` (version output without the `tt \d` form; also when `which -a tt` has another binary first, the first matching ours wins and a warning is logged), `test_ps_parses_fixture_and_ignores_unknown_fields`, `test_ps_missing_tt_returns_empty_with_reason`, `test_ps_hung_tt_times_out_without_raising` (fake sleeps; timeout 0.5), `test_ps_old_tt_without_served_key_degrades`, `test_catalog_caches_within_ttl_and_passes_offline`, `test_plan_serve_reads_dry_run`, `test_serve_streams_lines_to_on_line_and_returns_ok`, `test_serve_nonzero_exit_returns_last_lines_in_reason`, `test_serve_cancel_terminates_the_process`, `test_stop_passes_names_as_separate_args` (a name containing spaces and `;` stays one argument), `test_no_shell_true_anywhere` (grep the module source for `shell=True`).
- [ ] **Step 2: Run** `/usr/bin/python3 -m pytest tests/test_tt_backend.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** per the Interfaces block. Every function returns a result and never raises. Parse JSON defensively: missing keys become `None`.
- [ ] **Step 4: Run the tests.** Expected: PASS. Then break the timeout handling and watch `test_ps_hung_tt_times_out_without_raising` fail; restore.
- [ ] **Step 5: Commit** `git commit -m "feat(servers): tt_backend wrapper over the tt CLI"`.

### Task 3: `gozer_client`

**Files:**
- Create: `app/gozer_client.py`, `tests/fake_gozer.py`, `tests/test_gozer_client.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) Lease(id: str, chips: tuple[str, ...])`; `available() -> bool`; `status() -> dict`; `holders(chips: Sequence[str]) -> list[str]` (who holds any of them); `free_chips() -> list[str]`; `acquire(chips: int, who: str, reason: str, expect: str = "2h", queue: bool = True) -> Lease | Queued | None` where `Queued(ticket: str)`; `wait(ticket: str, timeout: float, cancel: Callable[[], bool] | None = None) -> Lease | None`; `release(lease_id: str) -> bool`; `adopt(target: str, who: str, reason: str) -> Lease | None`. All run `gozer ... --json` through an argument list with a timeout and never raise.

- [ ] **Step 1: Failing tests** against a fake `gozer`: `test_available_false_without_gozer`, `test_acquire_returns_lease_with_chip_ids`, `test_acquire_queued_returns_ticket_and_wait_grants`, `test_wait_cancel_stops_waiting`, `test_release_returns_true_and_false_on_error`, `test_holders_reports_other_agents_leases`, `test_adopt_wraps_untracked_chip`, `test_hung_gozer_times_out_without_raising`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** from the real `gozer` JSON in the Task 1 fixture.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(servers): gozer_client"`.

### Task 4: Registry and `plan_switch` for registry models

**Files:**
- Create: `app/server_registry.py`, `config/server_overrides.yaml` (a header comment and an empty `models: {}`), `tests/test_server_registry.py`
- Modify: `app/ready_to_run.py`, `tests/test_ready_to_run.py`

**Interfaces:**
- Consumes: `tt_backend.TtModel`, `tt_backend.plan_serve`, `server_manager.SERVERS`.
- Produces: `@dataclass(frozen=True) ModelEntry(key: str, name: str, capabilities: tuple[str, ...], backend: str, tt_name: str | None, sm_key: str | None, hardware_group: str | None, chips: int | None, port: int | None, served_id: str | None, runner_key: str | None, installed: bool, benefit: str)` with `backend in {"tt", "boutique"}`; `build_registry(catalog: Sequence[TtModel] | None = None, overrides_path: Path | None = None) -> dict[str, ModelEntry]`; `chips_for(entry: ModelEntry, plan: ServePlan | None) -> int | None` (dry-run first, then override, then catalog `hardware`, else `None`).
- Modifies: `ready_to_run.plan_switch(selected_key, status_of, *, registry: dict[str, ModelEntry] | None = None) -> SwitchPlan`. With `registry=None` the behaviour and results are byte-identical to today. With a registry, a `tt` entry's hardware group and port come from the entry, and conflicts are found against both `SERVERS` and registry entries.

- [ ] **Step 1: Failing tests**: `test_boutique_models_come_from_servers_table`, `test_catalog_model_becomes_tt_entry`, `test_override_prefers_boutique_script_when_tt_also_carries_it` (the rule from spec 4.2), `test_unknown_chip_count_is_none_not_a_guess`, `test_plan_switch_without_registry_unchanged` (reuse the existing assertions), `test_plan_switch_finds_conflict_with_a_tt_entry_on_the_same_group`, `test_plan_switch_missing_hardware_group_returns_no_conflict_and_flags_unknown`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** The override file is YAML read with the project's existing YAML loader (as `chip_config` does); a bad file is logged and ignored.
- [ ] **Step 4: Run these tests and `tests/test_ready_to_run.py`.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(servers): model registry and registry-aware plan_switch"`.

### Task 5: Merged server view, ownership records, status-service hook

**Files:**
- Create: `app/server_view.py`, `tests/test_server_view.py`
- Modify: `app/model_status.py`, `tests/test_model_status.py`

**Interfaces:**
- Consumes: `TtServer`, `ModelEntry`, music/artgen discovery results (as plain `(url, model_id)` tuples passed in).
- Produces: `@dataclass(frozen=True) RunningServer(key: str, name: str, source: str, url: str | None, host_is_local: bool, port: int | None, health: str, uptime_s: int | None, container: str | None, container_id: str | None, served_id: str | None, owner: str)` with `source in {"tt","script","discovered","saved"}` and `owner in {"app","external","unknown"}`.
- Produces: `class ServerRecords(path: Path | None = None)` with `load() -> list[dict]`, `add(name, port, container_id, lease_id, started_at)`, `remove(name)`, `prune(live_container_ids: set[str]) -> list[dict]` (returns the dropped records so their leases can be reaped). Default path `~/.local/share/tt-local-generator/servers.json`, written atomically.
- Produces: `merge(tt_servers, script_health: dict[str, bool], discovered, saved, records, registry) -> list[RunningServer]`.
- Modifies: `ModelStatusService.__init__` gains `extra_ready_fn: Callable[[], set[str]] | None = None`; keys it returns count as READY in `_tick`. With `None`, nothing changes.

- [ ] **Step 1: Failing tests**: `test_same_server_in_ps_and_health_check_is_one_row`, `test_port_and_served_id_must_both_match_else_conflict` (Review Focus 5: same port 8000, different served id gives two entries, and a `conflicts` helper reports it), `test_runner_key_can_stand_in_for_served_id`, `test_remote_host_is_not_local_and_owner_unknown`, `test_record_with_live_container_is_app_owned`, `test_record_with_vanished_container_makes_the_server_external` (Review Focus 5), `test_prune_returns_dropped_records`, `test_records_written_atomically_and_survive_a_corrupt_file`, `test_model_status_extra_ready_marks_a_terminal_started_server_ready`, `test_model_status_unchanged_without_extra_ready_fn`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Matching order is port, then served id, then container id or HF repo name; `tt model ps` carries no runner key, so the runner key only comes from the script health check (wire notes). Local means host in `{127.0.0.1, localhost, ::1}` or an address on this machine.
- [ ] **Step 4: Run these tests and `tests/test_model_status.py`.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(servers): merged server view, ownership records, status hook"`.

### Task 6: `ServerControl` (start, stop, restart)

**Files:**
- Create: `app/server_control.py`, `tests/test_server_control.py`

**Interfaces:**
- Consumes: Tasks 2 to 5 (`tt_backend`, `gozer_client`, `build_registry`, `plan_switch`, `merge`, `ServerRecords`) and `server_manager.start/stop` for boutique entries. All are injected through the constructor so tests pass fakes.
- Produces: `@dataclass(frozen=True) StartOutcome(kind: str, url: str | None, message: str, lease_id: str | None)` with `kind in {"started","already_running","starting_elsewhere","declined","waiting_for_chips","chips_held","failed"}`; `@dataclass(frozen=True) StopOutcome(kind: str, message: str)` with `kind in {"stopped","declined","not_allowed","failed"}`.
- Produces: `class ServerControl(*, registry_fn, view_fn, tt, gozer, records, boutique, status_of, confirm_fn, clock=time.monotonic)` with `start(model_key: str, *, port: int | None = None, on_progress: Callable[[str], None] | None = None, cancel: Callable[[], bool] | None = None) -> StartOutcome`, `stop(server: RunningServer) -> StopOutcome`, `restart(server_or_key, ...) -> StartOutcome`. `confirm_fn(kind: str, text: str) -> bool` is called from the worker thread; the GUI bridges it to a dialog, the CLI to a prompt or `--yes`.

- [ ] **Step 1: Failing tests** (fakes for every dependency): `test_already_running_anywhere_calls_no_serve` (Review Focus 1; assert the fake `tt.serve` was never called, and that the outcome URL is the running one), `test_still_booting_elsewhere_waits_then_reports_started`, `test_conflict_asks_and_decline_starts_nothing`, `test_accepted_switch_stops_the_incumbent_then_starts`, `test_pinnable_bundle_acquires_first_then_starts_pinned`, `test_pinnable_flow_checks_lease_status_after_healthy` (`CLAIMED` logs a reap warning, `HELD-FOREIGN` does not), `test_unpinnable_start_does_not_acquire_first`, `test_no_gozer_means_no_lease_logic`, `test_queued_lease_reports_waiting_and_cancel_cleans_up`, `test_failed_start_stops_and_releases_the_lease` (Review Focus 4), `test_unpinnable_tt_start_adopts_the_chips_the_container_opened` (fake `docker inspect` shows devices; the adopt call is recorded), `test_adopt_finds_another_lease_on_those_chips_stops_the_new_server_and_reports_chips_held` (Review Focus 4), `test_boutique_entry_uses_the_script_path`, `test_stop_app_owned_does_not_ask_and_releases_lease`, `test_stop_external_local_asks_and_decline_leaves_it_running` (Review Focus 2), `test_stop_remote_is_not_allowed`, `test_restart_external_asks_then_stops_then_starts`, `test_stale_lease_reaped_after_one_confirming_poll` (a record whose container is gone is released only on the second poll), `test_unknown_chip_count_warns_and_asks_instead_of_blocking`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the six-step start flow and the stop rules from spec 4.4 and 4.5. Corrections from the wire notes: pin a tt-model bundle with `tt serve <bundle> -- --device-id <ids>` (no pin for a catalog model, no `TT_VISIBLE_DEVICES`); a `ps` name that is not a catalog name or bundle id is stopped by the boutique `--stop` or `docker stop <container_id>`, not `tt model stop`; lease flow, one per case (spec 4.4 step 3): PINNABLE (bundle with `-- --device-id`, or a script that pins): `gozer acquire` (queue and wait), start pinned to the leased chips, wait healthy, then check `gozer status --json` (`CLAIMED` = 900 s reap risk, log it; `HELD-FOREIGN` = normal). UNPINNABLE (catalog model, unpinnable bundle, unpinned script): no pre-acquire; read free chips from `gozer status --json`, start, read the chips the container opened from `docker inspect`, `gozer adopt` them; if adopt finds a lease held by someone else, stop the new server and return `chips_held`, offering a boutique alternative. No gozer, or `tt` takes its own lease: no lease logic. Progress lines from `tt_backend.serve` go straight to `on_progress`. Record the started server in `ServerRecords` with its lease id.
- [ ] **Step 4: Run the tests**, then disable the lease release on failure and watch `test_failed_start_stops_and_releases_the_lease` fail; restore.
- [ ] **Step 5: Commit** `git commit -m "feat(servers): ServerControl start/stop/restart"`.

### Task 7: `tt-ctl` through `ServerControl`

**Files:**
- Modify: `tt-ctl`
- Test: `tests/test_tt_ctl_servers.py` (runs `tt-ctl` as a subprocess with the fake `tt`/`gozer` on PATH and a temp HOME)

**Interfaces:**
- Consumes: `ServerControl`, `merge`, `build_registry`.
- Produces: `tt-ctl servers [--all] [--json]` (no flags: the current text output, byte-identical; `--all`: the merged list; `--json`: the old services as `{"services": [...]}` plus, with `--all`, a `merged` list), `tt-ctl start|stop|restart <key|tt model name|bundle id> [--yes] [--blocking] [--single-chip]` going through `ServerControl` with a terminal prompt for confirms, the stderr note on every start/stop/restart/logs call (not on `servers`), and `TTLG_NO_DEPRECATION_NOTE=1` to silence it. A `tt` model name or bundle id is accepted wherever a key is. Old exit codes stay: 0 on success, 1 on failure or unknown key.

- [ ] **Step 1: Failing tests**: `test_servers_text_output_is_byte_identical_to_main` (capture from `git show main:tt-ctl` run in a temp copy, or a stored golden for the same fake state), `test_servers_all_lists_a_terminal_started_model`, `test_servers_json_keeps_services_and_adds_merged_only_with_all`, `test_start_old_key_still_works_and_note_goes_to_stderr_only` (stdout has no "deprecated"), `test_note_silenced_by_env`, `test_stop_external_prompts_and_no_leaves_it_running`, `test_stop_yes_flag_skips_the_prompt`, `test_unknown_key_exits_one_with_the_old_message`, `test_tt_model_name_accepted_as_a_key`, `test_exit_codes_unchanged_for_failed_start`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Old per-service output lines (`✓  <key> started`, `✗ ...`) stay as they are for old keys.
- [ ] **Step 4: Run the tests plus `tests/test_tt_ctl*.py` if present.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(servers): tt-ctl uses ServerControl, deprecation note"`.

### Task 8: Servers menu

**Files:**
- Modify: `app/servers_control.py`, `app/main_window.py` (construction of `ServersControl`, `_on_start_server`, `_on_stop_server`, a background refresh), `app/create_param_panels.py` (generalise the Connect dialog if the music branch is not present; otherwise leave a documented hook)
- Test: `tests/test_servers_control_sections.py` (xvfb)

**Interfaces:**
- Consumes: `ServerControl`, `merge`, `build_registry`.
- Produces: `ServersControl` sections **Running**, **Ready to start**, **Available**, **Remote**, built from a `ServerView` snapshot passed through `set_view(running: list[RunningServer], registry: dict[str, ModelEntry])`. Row actions call `on_start(key)`, `on_stop(server)`, `on_use(server)`. Existing constructor keyword arguments stay valid (`on_start`, `on_stop`, `on_restart`), so `SERVERS`-based rows keep working with `set_view` never called.
- Produces: `MainWindow` runs one background refresh every 10 s (reusing the `ModelStatusService` thread where possible) that calls `tt_backend.ps()`, `merge(...)`, then `GLib.idle_add(servers_control.set_view, ...)`. The `confirm_fn` for `ServerControl` shows the existing confirm dialog from the worker thread through `GLib.idle_add` plus a `threading.Event`.
- A "Connect..." entry opens a URL dialog that saves a remote server (URL, optional bearer token, label) to `app_settings`; the token never appears in a label, tooltip, log line or error.

- [ ] **Step 1: Failing tests** (fake view, real widgets): `test_running_section_lists_a_terminal_started_model_with_source_tag`, `test_stop_button_absent_for_a_remote_row` (Review Focus 2), `test_stop_on_external_row_goes_through_confirm`, `test_start_on_a_running_model_calls_on_use_not_on_start` (Review Focus 1), `test_ready_to_start_shows_boutique_name_and_benefit`, `test_available_section_offers_get_and_start`, `test_tt_missing_shows_the_reason_and_keeps_script_rows` (Review Focus 3), `test_existing_server_rows_unchanged_when_set_view_never_called`, `test_confirm_bridge_blocks_worker_until_main_thread_answers`, `test_progress_line_reaches_the_server_log` (the pipe lesson: a line from `tt_backend.serve` through `ServerControl.on_progress` and `GLib.idle_add` into `append_server_log`), `test_connect_dialog_token_never_shown_in_row_text`.
- [ ] **Step 2: Run under xvfb.** Expected: FAIL.
- [ ] **Step 3: Implement.** Keep the existing 3-state dot glyphs and CSS conventions. Take a real screenshot of the open menu with a fake view under `GDK_BACKEND=x11 env -u WAYLAND_DISPLAY xvfb-run --auto-servernum`, read it, and fix clipping or legibility problems within this task.
- [ ] **Step 4: Run the new tests, `tests/test_servers_control*.py`, `tests/test_main_window*status*`.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(servers): Servers menu sections, stop-asks-first, remote rows"`.

### Task 9: Docs, version bump and final check

**Files:**
- Modify: `CLAUDE.md` (a "Server control through tt (v0.NNN.0)" section in the file's style), `README.md` (a short paragraph), `VERSION`, `debian/changelog`, `docs/superpowers/specs/2026-10-28-tt-cli-wire-notes.md` if facts changed

- [ ] **Step 1:** Add the CLAUDE.md section: the original prompt, the architecture, the decisions from the spec (tt-centric, boutique backend, ask-first, remote use-only, best-effort chip pinning, deprecate not remove), what review and the spike found, and what was NOT verified (no real `tt serve`/stop run, leases not exercised against real gozer, script-server visibility in `tt model ps` per the wire notes). Plain-English rules from `~/CLAUDE.md` apply; sweep for `, not `, `rather than`, `instead of`, `honest`.
- [ ] **Step 2:** Run `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q` and report exact counts and every failure by name (known flakes in CLAUDE.md are the only expected failures).
- [ ] **Step 3:** By hand, read-only: `./tt-ctl servers --all` against the real machine; confirm the real `tt model ps` servers show with `owner` external and a Stop that would ask. Do not stop or start anything without the operator's say-so.
- [ ] **Step 4:** Bump `VERSION` (next minor) and prepend a `debian/changelog` stanza as one commit `chore: bump version to X.Y.Z`. Commit docs first as `docs: server control through tt`. Push nothing and open no PR until the operator asks.
