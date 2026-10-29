# Music Generation in Create Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Music medium to Create that makes songs on any discovered or saved music server (MiniMax-Music3, Stable Audio 3 small-music, others), with a lyrics field, assistive prompting, and a Library entry.

**Architecture:** A stdlib `music_runtime.py` holds the job client, per-model profiles and server discovery. A `MusicParamPanel` shows only the controls the selected model's profile accepts. A `MusicGenerationWorker` runs the job and writes a `media_type="audio"` record that the result panel, a new Library gallery and the Start-something wall display.

**Tech Stack:** Python 3 stdlib, GTK4/PyGObject, pytest (xvfb-run), ffmpeg (already a dependency) for waveform decode, Pillow for the thumbnail.

**Spec:** `docs/superpowers/specs/2026-10-28-music-generation-design.md` (section 9 holds the calls made after review).

## Global Constraints

- Work on branch `generate_music`. The vendor bump is a separate PR and is out of scope.
- `music_runtime.py` imports only the standard library. `worker.py`-style code has no GUI imports.
- GTK calls only on the main thread: background work reports through `GLib.idle_add`.
- `_CSS` byte literals stay ASCII-only.
- `collect()` for every non-music medium stays byte-identical (the collect-equality tests keep passing).
- The app never opens `/dev/tenstorrent/*` for music and takes no gozer lease.
- A control the selected model ignores is never shown, and a hidden control is never collected.
- Automated tests use fake servers only. The one live clip against `127.0.0.1:8795` happens in Task 1 and only after the operator restarts the server.
- Version: new user-visible feature, minor bump in `VERSION` and `debian/changelog`, on its own `bump/` branch (Task 9).
- Wire contract: the two demo clients (`~/code/tt-minimax-music-blackhole/demo/music_client.py`, `~/code/tt-stable-audio-3-small-music/demo/audio_client.py`) are the fallback. `/openapi.json` from a restarted server wins where it has `paths`.
- Credit line "MiniMax-Music3" appears in the result panel and the Library detail view for MiniMax-derived models; it is stored in `params["credit"]`, not written into the audio file.
- Optional bearer token per saved server, sent as `Authorization: Bearer <token>`. It never appears in a Library record, a log line or an error message.

## Review Focus

1. MiniMax ends the song when the lyrics run out (`ended: true`): the record stores the duration actually received, and the panel says why it is shorter. Pinned in Task 5.
2. Empty lyrics with a lyrics model and Instrumental off: the panel refuses before submitting and says what to add, and does not send a request that will return 400. Pinned in Task 4.
3. Server full (429 or `busy`), down, or restarting mid-job: a plain message appears, the result panel leaves "pending", and the job is cancelled with `DELETE` where possible. Pinned in Tasks 1 and 5.
4. A saved URL with a trailing slash, a path, uppercase host or no scheme: it normalizes to one entry and does not duplicate a discovered server. Pinned in Task 2.
5. A bearer token appears in no error text, log line or record, including when the server echoes request headers. Pinned in Tasks 1 and 5.
6. The audio file is deleted or unreadable when the user opens the record: the panel shows "file not found" with Open-externally disabled, and does not crash. Pinned in Task 6.

---

### Task 1: Spike and wire notes, then the profile and client

**Files:**
- Create: `docs/superpowers/specs/2026-10-28-music-wire-notes.md` (spike output)
- Create: `app/music_runtime.py`
- Create: `tests/fake_music_server.py`, `tests/test_music_runtime.py`

**Interfaces:**
- Produces (all in `music_runtime.py`):
  - `@dataclass(frozen=True) MusicProfile(model_id: str, label: str, jobs_prefix: str, text_fields: tuple[str, ...], has_lyrics: bool, min_seconds: float, max_seconds: float, steps_default: int | None, steps_max: int | None, cfg_default: float | None, cfg_min: float | None, cfg_max: float | None, formats: tuple[str, ...], audio_in: str, triggers: tuple[str, ...], credit: str, blurb: str)` where `audio_in` is `"none" | "reference" | "edit" | "both"`.
  - `profile_for(health: dict, schema: dict | None = None) -> MusicProfile`. Reads `limits`/`defaults`/`model` from health, field names from `schema` when it has `paths`, then fills what a server cannot say from a built-in table matched on the health `model` string (MiniMax: `caption`+`lyrics`, `/v1/music`, credit, trigger `fiona crapple` for the fiona model; Stable Audio: `prompt`, `/v1/audio`, cfg). An unknown server with a jobs API gets a generic profile (`prompt`, duration, seed).
  - `@dataclass MusicRequest(prompt: str, lyrics: str | None, seconds: float, seed: int | None, steps: int | None, cfg_scale: float | None, instrumental: bool = False)`.
  - `@dataclass MusicResult(audio: bytes, audio_format: str, job_id: str, stats: dict, seconds_got: float | None, ended: bool, seed: int)`.
  - `class MusicServerError(Exception)`, `class ServerBusy(MusicServerError)`, `class ServerDown(MusicServerError)`.
  - `class MusicClient(base_url: str, profile: MusicProfile, token: str | None = None, timeout: float = 30.0)` with `health() -> dict`, `submit(req: MusicRequest) -> str`, `poll(job_id: str) -> dict`, `fetch(job_id: str) -> bytes`, `cancel(job_id: str) -> None`, `generate(req: MusicRequest, on_progress: Callable[[str], None] | None = None, poll_s: float = 2.0, should_cancel: Callable[[], bool] | None = None) -> MusicResult`.

- [ ] **Step 1: Spike (needs the operator).** Ask the operator to confirm the Stable Audio server has restarted. Fetch `http://127.0.0.1:8795/openapi.json` and `/v1/health`. With the operator's approval, submit one 5 s clip through the demo client and record in `music-wire-notes.md`: whether the schema has `paths`, whether the job paths moved, request and job-status field names (including the field that carries seconds received and `ended`), and the audio format. If the schema has `paths`, record it. Also read `~/code/tt-minimax-music-blackhole/serve/serve_music.py` and, if a MiniMax server is up, its `/v1/health`, and record whether health carries limits and what the server accepts for an instrumental song (empty lyrics or a placeholder). If it rejects both, Task 4 drops the Instrumental switch and the PR description says so. If the operator's v6 MiniMax package serves a different job API, record the difference and add a version key to the profile table. Decision gate: if the paths or field names differ from section 3 of the spec, update the spec table and this plan's Interfaces before Step 2.
- [ ] **Step 2: Write `tests/fake_music_server.py`**: a `ThreadingHTTPServer` on an ephemeral port with `/v1/health`, `{prefix}/jobs` (POST, GET, DELETE), `{prefix}/jobs/{id}/audio`, configurable `prefix`, health body, field names and behaviours (`busy`, `down_after_submit`, `ended_early`, `require_token`). Field names come from the spike notes.
- [ ] **Step 3: Write failing tests** in `tests/test_music_runtime.py`: `test_profile_from_health_reads_limits` (Stable Audio health gives `min_seconds == 1`, `max_seconds == 120`, `cfg_default == 1.0`), `test_profile_table_supplies_lyrics_for_minimax` (`has_lyrics` and `text_fields == ("caption", "lyrics")`), `test_unknown_server_gets_generic_profile`, `test_generate_round_trip_returns_audio_and_seed`, `test_busy_raises_serverbusy_with_plain_message`, `test_down_raises_serverdown`, `test_abandoned_job_is_cancelled` (`should_cancel` returns True, fake records a `DELETE`), `test_ended_early_reports_seconds_got_and_ended`, `test_token_sent_as_bearer_and_absent_from_errors` (fake echoes headers in a 500 body; assert the token string is in no exception message), `test_request_refused_before_submit_when_out_of_range` (duration outside limits raises `ValueError`).
- [ ] **Step 4: Run** `/usr/bin/python3 -m pytest tests/test_music_runtime.py -q`. Expected: FAIL (module missing).
- [ ] **Step 5: Implement** `music_runtime.py` per the Interfaces block, porting the polling, cancel and 429 handling from the two demo clients. Field names are mapped through the profile.
- [ ] **Step 6: Run the tests.** Expected: PASS. Then disable the cancel call and confirm `test_abandoned_job_is_cancelled` fails, and restore it.
- [ ] **Step 7: Commit** `git commit -m "feat(music): profiles and job client"`.

### Task 2: Discovery and saved servers

**Files:**
- Modify: `app/music_runtime.py`, `app/app_settings.py` (`DEFAULTS` gains `music_servers: []`, `music_last_server: ""`)
- Test: `tests/test_music_discovery.py`

**Interfaces:**
- Consumes: `MusicProfile`, `profile_for`, `MusicClient.health` from Task 1.
- Produces:
  - `normalize_url(url: str) -> str` (adds `http://` if missing, lowercases the host, strips path and trailing slash).
  - `@dataclass(frozen=True) MusicServer(url: str, profile: MusicProfile | None, up: bool, saved: bool, token: str | None, label: str)`.
  - `discover_music_servers(saved: list[dict], ports: tuple[int, ...] = DEFAULT_PORTS, probe: Callable = _probe, docker_ports: Callable[[], list[int]] = _docker_ports) -> list[MusicServer]` where `DEFAULT_PORTS` is `(20000, 8795)` plus `TTLG_MUSIC_SCAN_PORTS`, and `_docker_ports` calls `artgen._tt_model_host_ports`. A saved entry that is down is returned with `up=False`.
  - `save_server(url: str, token: str | None = None, label: str = "") -> None` and `remove_server(url: str) -> None`, over `app_settings`.

- [ ] **Step 1: Write failing tests**: `test_discovers_two_fake_servers_and_builds_profiles`, `test_saved_down_server_is_listed_not_dropped`, `test_trailing_slash_and_case_normalise_to_one_entry` (`"HTTP://Host:8795/"` and `"http://host:8795"` give one server; a saved URL equal to a discovered one does not duplicate it), `test_non_music_http_server_is_ignored` (answers `/v1/health` but no jobs path), `test_probe_timeout_does_not_block` (a hung fake server returns within 3 s), `test_saved_token_reaches_the_probe_header`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** the functions above. Probes run in a thread pool with a 1.5 s timeout each. A server counts as music when `/v1/health` answers and `{prefix}/jobs/none` returns 404 or a JSON "no such job" for `/v1/music` or `/v1/audio`.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(music): server discovery and saved servers"`.

### Task 3: Prompt helpers and the music chip bank

**Files:**
- Modify: `app/prompt_client.py`, `config/prompt_chips.yaml`, `app/chip_config.py` only if the loader needs it
- Test: `tests/test_music_prompts.py`

**Interfaces:**
- Produces: `prompt_client.generate_prompt(source, ...)` accepts `source in {"music", "lyrics"}`. For `"lyrics"` it returns lyrics with `[Verse]` and `[Chorus]` tags on their own lines. `prompt_client.music_llm_available() -> bool` is true when `artgen.detect_artgen_endpoint()` finds a chat model, else falls back to the prompt server (8001). With no LLM reachable `llm_polish_or_none("lyrics", ...)` returns `None`.
- Produces: chip banks under tab `music` with categories Genre, Mood, Instruments, Tempo, Era and "In the style of" (the last takes free text appended as `in the style of <text>`), plus a Surprise chip. Loaded with `chip_config.load_chips_for_artgen("music")`.

- [ ] **Step 1: Write failing tests**: `test_music_bank_has_expected_categories`, `test_music_bank_not_offered_to_native_tabs` (`load_chips("video")` contains none of them), `test_lyrics_polish_returns_none_without_llm`, `test_lyrics_prompt_asks_for_structure_tags` (the system prompt string passed to the fake LLM names `[Verse]` and `[Chorus]`), `test_lyrics_prefers_artgen_endpoint_over_prompt_server`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Chip YAML uses `for: [music]` on every music category, following the artgen-bank convention in the file.
- [ ] **Step 4: Run the tests plus `tests/test_chip_config.py`.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(music): lyrics/music prompt types and chip bank"`.

### Task 4: `MusicParamPanel`

**Files:**
- Modify: `app/create_param_panels.py`
- Test: `tests/test_music_param_panel.py` (xvfb-run)

**Interfaces:**
- Consumes: `MusicServer`, `MusicProfile`, `MusicRequest`, `attach_inspire_button`, `ModifierPills(kind, artgen=True)`.
- Produces: `class MusicParamPanel(CreateParamPanel)` with `__init__(self, inspire_fn=None, prompt_type_getter=None)`, `set_server(server: MusicServer | None) -> None` (rebuilds visibility from the profile), `servers_changed(servers: list[MusicServer]) -> None`, and `collect() -> dict` returning only the keys the profile accepts from `{"prompt", "lyrics", "instrumental", "seconds", "seed", "steps", "cfg_scale", "server_url", "reference_clip"}`. `field_specs()` lists the same keys. Roles: prompt and lyrics are `ROLE_BRIEF`, `seconds` is `ROLE_DIRECTION`, the rest `ROLE_CONTROL` (add a `classify_native`-style entry in `field_roles.py` if needed).
- Produces: `MusicParamPanel.validate() -> str | None` returns a plain message when the request cannot be sent (empty prompt; empty lyrics with `has_lyrics` and Instrumental off; no server up).

- [ ] **Step 1: Write failing tests**: `test_stable_audio_profile_hides_lyrics_and_shows_cfg`, `test_minimax_profile_shows_lyrics_hides_cfg`, `test_collect_omits_hidden_fields` (collect on the Stable Audio profile has no `lyrics` key), `test_duration_bounded_by_profile`, `test_instrumental_switch_hides_lyrics_and_sets_flag`, `test_validate_refuses_empty_lyrics_for_minimax` (Review Focus 2), `test_audio_in_row_hidden_by_default_and_shown_for_edit_profile`, `test_trigger_phrase_chip_offered_for_fiona_profile`, `test_server_dropdown_lists_down_servers_with_down_dot`, `test_connect_dialog_normalises_and_warns_on_non_local_http`, `test_lyrics_helper_disabled_message_when_no_llm`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** the panel in the existing `ArtgenParamPanel` style. The Lyrics row gets its own `attach_inspire_button` with prompt type `"lyrics"` and a tag-insert menu button. The Prompt row uses prompt type `"music"`. The Connect dialog calls `save_server` after a successful probe.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(music): Music param panel"`.

### Task 5: Medium, worker and dispatch (stage 1 core)

**Files:**
- Modify: `app/create_mediums.py`, `app/worker.py`, `app/create_view.py`, `app/main_window.py`, `app/media_store.py` (docstring and filters only)
- Test: `tests/test_music_worker.py`, `tests/test_create_view_music.py`

**Interfaces:**
- Consumes: `MusicClient`, `MusicRequest`, `MusicServer`, `MusicParamPanel.collect()`.
- Produces: `Medium(id="music", label="Music", icon="🎵", kind="audio", source="music", generator=None, uses_llm=False)` returned by `default_mediums()` and `discover_mediums()`; `sort_mediums_visual_first` places it last.
- Produces: `worker.MusicGenerationWorker(store, server: MusicServer, request: MusicRequest, out_dir: Path, token: str | None = None)` with `run_with_callbacks(on_progress, on_finished, on_error)` and `cancel()`. On success it saves the audio to `out_dir`, builds a `MediaRecord(media_type="audio", generator_type="music", model_id=profile.model_id, prompt=request.prompt, ...)` whose `params` JSON holds `prompt, lyrics, seconds_asked, seconds_got, ended, seed, steps, cfg_scale, server_url, credit, source_clip: null`, adds it to the store, and calls `on_finished(record)`. The token is never in `params`.
- `MainWindow._launch_create_job` routes `medium.source == "music"` to a new `_create_generate_music(medium, params)` that skips `_ensure_server_ready_then`, creates the worker on a thread, forwards callbacks through `GLib.idle_add`, and calls `_fail_create_job(reason)` on every terminal failure path.

- [ ] **Step 1: Write failing tests**: `test_music_medium_present_and_last`, `test_worker_saves_record_with_audio_type_and_params` (assert `media_type == "audio"`, `params["seconds_got"]` equals the fake's, `"credit"` set for a MiniMax profile), `test_worker_records_ended_early_duration` (Review Focus 1), `test_worker_busy_calls_on_error_with_plain_message`, `test_worker_server_dies_mid_job_cancels_and_errors` (Review Focus 3), `test_record_and_error_text_never_contain_token` (Review Focus 5), `test_create_view_music_collect_equals_panel_collect`, `test_other_mediums_collect_unchanged` (existing equality tests untouched), `test_launch_create_job_music_bypasses_server_gate`, `test_progress_line_reaches_result_panel` (a progress message from the worker arrives at `CreateResultPanel.show_progress`, through the real `GLib.idle_add` wrapper, per the pipe lesson in CLAUDE.md).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** per the Interfaces block. Persist `music_last_server` when a job is launched, and have the panel preselect it, else the first `up` server. A synthetic status for Music is added in Task 8, not here.
- [ ] **Step 4: Run the new tests and `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/test_create_view.py tests/test_role_zone_panel.py -q`.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(music): Music medium, worker and dispatch"`.

### Task 6: Result panel audio branch and waveform thumbnail (end of stage 1)

**Files:**
- Create: `app/audio_thumb.py`
- Modify: `app/create_view.py` (`CreateResultPanel._build_artifact_widget`), `app/artgen_thumb.py` only if it dispatches by type
- Test: `tests/test_audio_thumb.py`, `tests/test_create_result_panel_audio.py`

**Interfaces:**
- Produces: `audio_thumb.make_waveform_thumbnail(audio_path: str, out_path: str, size: tuple[int, int] = (320, 180)) -> str | None` decodes with `ffmpeg -f s16le -ac 1 -ar 8000 -` into `array.array`, draws a bar waveform in `#4FD1C5` on `#0F2A35` with Pillow, and returns `out_path`, or `None` on any failure (the caller uses the placeholder).
- Produces: in `CreateResultPanel`, an audio branch showing a `Gtk.MediaControls` over `Gtk.MediaFile.new_for_filename(path)`, the waveform picture, the lyrics in a read-only `Gtk.TextView`, and the credit line from `params["credit"]`. A missing file shows "file not found" and disables Open externally.

- [ ] **Step 1: Write failing tests**: `test_waveform_thumbnail_is_nonblank_for_a_tone` (generate a 1 s sine with ffmpeg in a fixture; assert the PNG has both palette colours), `test_waveform_returns_none_for_garbage_file`, `test_audio_record_renders_player_lyrics_and_credit`, `test_missing_audio_file_shows_not_found_and_does_not_raise` (Review Focus 6), `test_swap_result_releases_media_stream` (mirrors `_release_video`).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Wire the thumbnail into `MusicGenerationWorker` so `thumbnail_path` is set at save time. Release the media stream in `_clear_current()` as the video branch does.
- [ ] **Step 4: Run the new tests plus `tests/test_create_result_panel.py`.** Expected: PASS.
- [ ] **Step 5: Real check.** Under `GDK_BACKEND=x11 env -u WAYLAND_DISPLAY xvfb-run`, render the real Music panel and result panel against a fake server, take screenshots for a Stable Audio profile and a MiniMax profile, and read them for overflow and a missing control.
- [ ] **Step 6: Commit** `git commit -m "feat(music): audio result panel and waveform thumbnails"`.

### Task 7: Library gallery for audio (stage 2)

**Files:**
- Modify: `app/main_window.py` (gallery stack, loop nav, filters), `app/library_convert.py` (only if it enumerates types), `app/gallery` code that routes by `media_type`
- Test: `tests/test_audio_gallery.py`

**Interfaces:**
- Consumes: audio `MediaRecord`s from Task 5.
- Produces: an "Audio" Library page listing `media_type="audio"` records newest first, a card with the waveform thumbnail, a single-click detail with the player, lyrics and credit, star and delete, and no Remix button (no seed kind exists for audio).

- [ ] **Step 1: Write failing tests**: `test_audio_records_route_to_audio_gallery_only`, `test_audio_card_has_no_remix_button`, `test_audio_never_reaches_video_or_image_gallery`, `test_existing_library_filters_unchanged` (counts of video/image/artgen unchanged with an audio row present), `test_convert_library_leaves_audio_rows_alone`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** following how `_artgen_gallery` is wired, grep every `media_type ==` and `media_type in` site first and list the ones touched in the commit message.
- [ ] **Step 4: Run the new tests and the full suite.** Expected: PASS apart from the three known environment flakes listed in CLAUDE.md.
- [ ] **Step 5: Commit** `git commit -m "feat(music): Library audio gallery"`.

### Task 8: Start-something tile and Music status segment (stage 3)

**Files:**
- Modify: `app/possibilities.py`, `app/status_segments.py`, `app/main_window.py` (`_StatusBar`)
- Test: `tests/test_possibilities_music.py`, `tests/test_status_segments_music.py`

**Interfaces:**
- Produces: `possibilities._EXAMPLE_POOLS_BY_ID["music"]` (at least four distinct lines in the house voice, one naming a lyrics idea); the tile art uses the latest audio waveform thumbnail, else the gradient fallback.
- Produces: `status_segments.segment_states(snap, artgen_model=None, music_servers=())` gains a `music` entry: `ready` when any discovered or saved server is up, `off` otherwise. It speaks only for music servers.

- [ ] **Step 1: Write failing tests**: `test_music_tile_present_with_distinct_example_lines`, `test_music_tile_art_prefers_latest_waveform`, `test_music_segment_ready_only_for_music_servers` (a ready video server leaves Music off), `test_music_segment_off_with_no_servers`, `test_segments_for_other_functions_unchanged`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Discovery results come from a background refresh every 10 s in `MainWindow`, delivered through `GLib.idle_add`, and shared with the panel's dropdown.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(music): wall tile and status segment"`.

### Task 9: Docs, version bump and final check

**Files:**
- Modify: `CLAUDE.md` (a "Music generation" section: the original prompt, key decisions, and what was verified), `README.md` (one paragraph), `VERSION`, `debian/changelog`

- [ ] **Step 1:** Add the CLAUDE.md section in the file's existing style, covering the prompt, decisions from the spec, the fallbacks taken, and the spike result. Keep claims to what was run.
- [ ] **Step 2:** Create branch `bump/<next minor>` from `generate_music`, edit `VERSION` and prepend a `debian/changelog` stanza, commit them together with `git commit -m "chore: bump version"`.
- [ ] **Step 3: Verify.** Run `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q` and report the exact counts and any failures, deselecting only the three known flakes by name. With the operator's approval, make one short live clip from the real Create panel against the Stable Audio server and confirm the saved FLAC plays.
- [ ] **Step 4:** Push nothing and open no PR until the operator asks.
