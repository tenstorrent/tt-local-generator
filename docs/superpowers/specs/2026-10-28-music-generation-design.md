# Music generation in Create (design)

Date: 2026-10-28. Branch: `generate_music`. Status: written for the operator's review; nothing is built.

Prompt (operator): "build a UI for generating music using tt-model-manager community models like minimax-music3 or
stabilityai/stable-audio-3-small-music". Decisions from the conversation are marked **(decided)**. Anything marked **(my call)** is
a choice made while writing and is open to change.

A second, separate PR updates the vendored tt-inference-server (pinned v0.19.0, upstream is at v0.25.0). It is not part of this
design. The two PRs are released together later.

## 1. Intent

Someone types an idea and gets a song. Helpers build the prompt with them: chips for genre, mood, instruments, tempo and "in the
style of ...", a lyrics box with its own writing helper, and a duration control. The panel must work with several music models, so
it learns what each model accepts instead of hard-coding one. Later, tt-local-generator will combine and remix generations, for
example music for a video, so a generated song has to land in the Library with enough provenance to be used that way.

## 2. Goals, non-goals, anti-goals

Goals
- A **Music** medium in Create, next to Image and Video, with the existing result panel, a player, and a Library entry. **(decided)**
- Prompt-first. Assistive prompting: chip pills and an Inspire helper on the prompt. **(decided)**
- A **dedicated Lyrics field** with its own helper that uses the same prompt generator. **(decided)**
- Controls for duration, seed, and the model's own settings (steps, guidance), shown only when the selected model supports them. **(decided: duration, tempo, style; my call: the rest)**
- Model-agnostic: MiniMax-Music3, `episod/minimax-music3-fiona-crapple`, `stabilityai/stable-audio-3-small-music`, and models added later. **(decided)**
- Reference music **in words** ("in the style of ..."). **(decided)**
- Be ready for models that take audio in (condition on a clip, or edit one). The profile and the panel have a slot for it; no shipped model uses it yet. **(decided)**
- Find servers automatically, and let the user point the app at any server by URL. A model may be on the network or on this machine. **(decided)**

Non-goals
- Combining or remixing music with video. A later project; this one must not block it. **(decided)**
- Starting, stopping or leasing music servers. They are started elsewhere (tt-model, the orchard agent). The app only finds them and talks to them. **(my call)**
- Uploading audio to a model. The slot exists; the upload path is not wired to a model.
- The vendor bump.

Anti-goals (a failure even if it technically works)
- A panel that offers a control the selected model ignores. A control that does nothing is the same kind of lie as the old status dot.
- A request that reports success but dropped a field. If a value cannot be sent, the panel says so before generating.
- Opening the app on a machine with no music server and finding that Music is missing. It shows, with a hint on how to start a model.

## 3. What the two known servers accept (read on 2026-10-28)

| | MiniMax-Music3 (`tt-minimax-music-blackhole`) | Stable Audio 3 small-music (`tt-stable-audio-3-small-music`) |
|---|---|---|
| Jobs | `POST /v1/music/jobs`, `GET /v1/music/jobs/{id}`, `GET .../{id}/audio`, `DELETE .../{id}` | same with `/v1/audio/jobs` |
| Health | `GET /v1/health` | `GET /v1/health`, which also returns `limits`, `defaults`, `queue_depth`, `queue_max`, `busy` |
| Text | `caption` and `lyrics`, both required | `prompt` only, up to `MAX_PROMPT_CHARS` |
| Duration | 5 to 60 s asked; about 3.2 s of compute per second of audio | 1 to 120 s; a 5 s clip took 16 s including a model reload |
| Settings | `seed`, `steps` (up to 100) | `seed`, `steps` (1 to 100, default 8), `cfg_scale` (1 to 20, default 1) |
| Output | FLAC, 32 kHz stereo | FLAC or WAV |
| Busy | HTTP 429 | busy; queue of 2 |
| Default port | 20000 (tt-model) | 8795 (loopback only) |

MiniMax ends a song when the lyrics run out, so the song can be shorter than the duration asked (`ended: true`). Lyrics use
structure tags (`[Verse 1]`, `[Chorus]`) on their own lines. The fiona-crapple model answers to the trigger phrase `fiona crapple`
and has no quality evaluation. The MiniMax community license requires products built on it to display "MiniMax-Music3".

Neither server takes audio in. Stable Audio's server has no usable `/openapi.json` (the reply has no `paths`), so the
table above comes from the demo client in `~/code/tt-stable-audio-3-small-music/demo/audio_client.py`. The builder should confirm
each field against that client and against a live job before relying on it.

## 4. Design

### 4.1 A model profile, learned and declared

A **MusicProfile** describes what one model accepts: job path prefix (`/v1/music` or `/v1/audio`), which text fields it has
(`caption`+`lyrics`, or `prompt`), duration min and max, steps and guidance ranges and defaults, output formats, whether it takes
a reference clip or an edit clip, trigger phrases, a credit line, and a one-line "what this model is good at".

Two sources fill it, in this order **(my call)**:
1. What the server reports at `GET /v1/health` (`limits`, `defaults`, `model`). Stable Audio reports these; MiniMax's health reply
   should be checked, and where it lacks limits the profile table supplies them.
2. A built-in table keyed by a model-name match (same approach as `model_status.match_model_id`), carrying what a server cannot
   say: the lyrics field, trigger phrases, the credit line, the fields' wire names. Unknown servers that answer `/v1/health`
   with a jobs API get a generic profile (`prompt`, duration, seed) and still work.

### 4.2 `app/music_runtime.py` (stdlib only, no GTK)

- `MusicClient`: the shared job client. Submit, poll, fetch audio, cancel on abandon, busy handling. Ported from the two demo
  clients, which are nearly identical. Fields are mapped through the profile's wire names.
- `discover_music_servers()`: probes a known set of ports on localhost (20000, 8795, and a short sweep range set by
  `TTLG_MUSIC_SCAN_PORTS`), plus tt-model docker-label ports (reuse `artgen._tt_model_host_ports`), plus every saved URL. A
  server counts as a music server when `/v1/health` answers and a jobs path under `/v1/music` or `/v1/audio` responds.
  Returns `(url, profile, health)`. Probes run on a background thread with short timeouts.
- Saved servers: a list of user-entered URLs in `app_settings` (`music_servers`). A saved URL that is down is shown as down,
  never dropped.

### 4.3 The panel

`MusicParamPanel` in `create_param_panels.py`, following the existing `CreateParamPanel` / `field_specs()` pattern so
`RoleZonePanel` zones it the same way. Brief zone: Prompt (caption) and Lyrics. Direction zone: chips. Controls zone: duration,
seed, steps, guidance.

- **Prompt** has the ✨ Inspire button, wired through the existing `attach_inspire_button`.
- **Lyrics** is a multi-line field with its own helper through the same `inspire_fn`. Three actions, **(my call)**: write lyrics
  from the prompt, polish what is typed, and an "Instrumental" switch that hides the field and sends the model's placeholder (the
  builder checks what MiniMax accepts for an empty song). A structure-tag insert menu ([Verse], [Chorus], [Bridge]) is a small
  convenience, **(my call)**.
- **Chips** come from a new `music` bank in `config/prompt_chips.yaml`: Genre, Mood, Instruments, Tempo, Era, and a "In the style
  of" category that takes free text. Tempo is written into the prompt text ("slow, 70 bpm") because no model has a tempo field.
  The model's trigger phrase appears as an add-chip (`fiona crapple`). Uses the existing `load_chips_for_artgen`-style loader and
  a `Surprise` chip, as in the artgen banks.
- **Duration** is a spin or slider bounded by the profile's min and max. With a lyrics model, the hint says the song ends when the
  lyrics do.
- **Steps, guidance, seed** appear only if the profile has them. The seed control keeps and shows the seed the server used, so a
  clip can be asked for again.
- **Reference audio slot**: a "Reference or edit clip" row, hidden unless the profile declares audio-in. Built, tested for
  visibility, and not wired to a shipped model.
- **Model line**: picker of discovered and saved servers with an up/down dot (as in the model picker), and a "Connect to a model..."
  entry that takes a URL, probes it, and saves it.
- `collect()` returns only fields the profile accepts. A control that is hidden is never collected.

### 4.4 Generating

`MusicGenerationWorker` in `worker.py` (no GUI imports), the same callbacks as the other workers (`on_progress`, `on_finished`,
`on_error`), wrapped in `GLib.idle_add` by `MainWindow`. It builds a `MusicClient` from the chosen server, submits, polls, saves
the audio into the Library storage directory, and returns a record. Progress text comes from the server's stage times.
A busy or full queue gives a plain message. Cancel sends `DELETE` on the job.

Create's pending state, the result panel, the recents strip and the Watch viz all keep working. The Watch viz mode for music is
`inference` **(my call)**; the app reads chip activity locally and a remote server will not show any.

### 4.5 Library and result

- New record shape: `media_type="audio"`, `generator_type="music"`, `model_id`, `file_path` (FLAC or WAV), `thumbnail_path`, and
  `params` holding prompt, lyrics, duration asked, duration got, seed, steps, guidance, server URL, credit line, and
  `source_clip` (null for now). **(my call: `media_type="audio"`; the builder checks `media_store` filters and `library_convert` for anything that enumerates types.)**
- Thumbnail: a waveform PNG drawn with Pillow, in the app's palette. **(my call)** The `.ans`/`.json` thumbnail work in
  `artgen_thumb` is the model for failing soft to an honest placeholder.
- `CreateResultPanel` gets an audio branch: a player with play/pause, a seek bar and the waveform, the lyrics shown beneath, and
  the credit line. Playback uses `Gtk.MediaFile` (the same GStreamer path as video; audio needs no sink for video). On a machine
  where GStreamer playback fails, the panel offers "Open externally" and does not show a dead player.
- A Library gallery for audio, a Discover tile on the Start-something wall, and a curated-pool entry in `possibilities`.
  Remix: the card's Remix button works like other kinds only if a matching seed kind exists; otherwise it is hidden for audio.

### 4.6 Status bar and servers popover

A fifth segment "Music", resolved from discovery and not from `SERVERS`, because these servers are not `ServerDef`s. It follows the
rule from v0.97.0: one segment speaks only for its own function. **(my call)**

## 5. Constraints

- GTK threading rules in CLAUDE.md apply. Discovery and generation never touch widgets from a thread.
- `worker.py`-style modules stay GTK-free. `music_runtime.py` imports only the standard library.
- `_CSS` byte literals stay ASCII.
- `collect()` for every other medium stays byte-identical. Guard with the existing collect-equality tests.
- Version bump and `debian/changelog` per CLAUDE.md. This is a new user-visible feature, so a minor bump. VERSION is on its own commit on a `bump/` branch per project rules.
- The app never opens `/dev/tenstorrent/*` for music. The servers hold the chips; this app has no lease to take. If the builder finds otherwise, stop and ask.
- Test against fake servers; never against the operator's live servers in automated tests. Manual checks against the live Stable Audio server on `127.0.0.1:8795` are fine for one short clip, with the operator's say-so, because it holds a lease the operator set up.

## 6. Left to the builder

- Exact widget layout, copy, and CSS.
- The exact discovery port list and timeouts.
- The chip bank contents (start from the existing `config/prompt_chips.yaml` voice).
- How `Inspire` composes its prompt for lyrics versus the music prompt, within `prompt_client`'s existing contract.
- Waveform rendering details.

## 7. Open facts to confirm early

1. MiniMax's `/v1/health` reply: does it include limits and defaults? (Read `serve/serve_music.py` and a live reply when a server is up.)
2. What MiniMax accepts for instrumental songs.
3. The `episod/minimax-music3-fiona-crapple-blackhole` package is a v5.1 container; the operator's own v6 package for MiniMax may serve differently. Confirm that a v6 server exposes the same job API, or record the difference.
4. Whether `stabilityai/stable-audio-3-small-music` has trigger phrases or prompt conventions worth surfacing as chips.

## 8. Testing

- `tests/test_music_runtime.py`: profile from health, profile from the table, a generic profile, a fake server for submit/poll/fetch/cancel, busy and 429, a down server.
- Discovery against two fake servers on ephemeral ports, plus a saved URL that is down.
- Panel tests: controls follow the profile; Stable Audio hides Lyrics; the instrumental switch; `collect()` carries only accepted fields; audio-in row hidden by default and shown for a profile that declares it.
- Worker tests with callbacks; cancel sends `DELETE`.
- A pipe test per the CLAUDE.md lesson: a progress line passes through every hop to the result panel. A test that asserts only the arithmetic is not enough.
- Each new test is seen failing against the broken or absent code before it is trusted.
- A real check with `GDK_BACKEND=x11` under Xvfb that the panel renders, with a screenshot.

## 9. Calls made after the builder check (my calls, open to change)

- **Wire contract.** The two demo clients (`tt-minimax-music-blackhole/demo/music_client.py`, `tt-stable-audio-3-small-music/demo/audio_client.py`) are the contract. They are the projects' own clients, and the servers' tests use them. The first task is a spike: with the operator's approval, one 5 s clip against the live Stable Audio server, checking the profile and field names before the runtime and fake servers are written. The fake servers copy what the demo clients send, and the spike is what shows that copy is right.
- **Library type.** New `media_type="audio"`. The PR includes a Library gallery page for audio, so a song is findable after the session. The thumbnail falls back to a plain placeholder.
- **PR size.** One PR, built and committed in three stages: (1) runtime, panel, worker and result panel; (2) Library gallery and thumbnail; (3) Start-something tile and the Music status segment. One minor version bump, on its own `bump/` branch.
- **Lyrics and prompt helpers.** `prompt_client` may be extended with `music` and `lyrics` prompt types. Qwen3-0.6B on port 8001 is too small to rely on for lyrics, so the helper prefers the artgen chat endpoint (`detect_artgen_endpoint`) and falls back to the prompt server. With no LLM reachable, the lyrics helper is disabled with a message, and the prompt chips still work.
- **Server choice.** The Music panel has its own server dropdown. Default is the last server used (saved in settings), else the first one that is up. Music bypasses `ready_to_run`, since the app never starts these servers.
- **Credit line.** "MiniMax-Music3" shows in the result panel and in the Library detail view for MiniMax-derived models. It is stored in the record. It is not added to the exported file.
- **Fallbacks.** If a section 7 fact comes back the wrong way, the builder falls back and does not stop: declare MiniMax's limits in the table, hide the Instrumental switch if MiniMax rejects an empty song, add a version key if v6 differs. Each fallback is recorded in the PR description.
- **Live check approved; contract may change (operator, 2026-10-28).** The operator approved one short clip against the Stable Audio server and said it may restart with a fixed OpenAPI definition (my reading of "openai definition"). The spike therefore runs after that restart. If `/openapi.json` then has `paths` and schemas, the profile reads field names, limits and defaults from it, and the demo clients become the fallback. Wire names are never hard-coded where the schema can supply them. The spike also checks whether the job paths moved.
- **Optional token (operator: not needed now, a user's server might).** A saved server entry has an optional bearer token, sent as `Authorization: Bearer <token>` on every call to that server, including discovery probes of it. Empty by default, so nothing changes for servers without one. My call: it is stored in the app settings file as plain text, and the "Connect to a model..." dialog says so. Moving it to the system keyring is a later change. The token is never written to the Library record, logs or error messages. Non-loopback plain-HTTP URLs get a one-line warning in that dialog.
