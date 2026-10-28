# Music servers: wire notes

Status: **from demo clients, live spike pending.** Read on 2026-10-28 from
`~/code/tt-minimax-music-blackhole/demo/music_client.py`, `.../tests/fake_music_server.py`, `.../serve/serve_music.py`,
`~/code/tt-stable-audio-3-small-music/demo/audio_client.py` and `.../serve/serve_audio.py`. No job was submitted. The only live
call was a read-only `GET http://127.0.0.1:8795/v1/health`.

## What differs from section 3 of the design spec

1. **MiniMax wire names are not `caption` and `lyrics`.** The demo client sends
   `{"input": <lyrics>, "instructions": <caption>, "audio_duration": <s>, "response_format": "flac", "seed": n, "num_inference_steps": n}`.
   `MusicProfile.text_fields` keeps the logical names `("caption", "lyrics")`; `music_runtime.WIRE` maps them to the wire names.
2. Duration is `audio_duration` on MiniMax and `duration` on Stable Audio. Steps are `num_inference_steps` on MiniMax and `steps` on Stable Audio.
3. Stable Audio's `serve_audio.py` now builds an OpenAPI 3.1 document (paths, `JobRequest`, `Health`), but the running server on :8795
   still answers 404 for `/openapi.json`, so it has not restarted with it. Field names below come from the source.

## MiniMax-Music3 (`/v1/music`)

- `POST /v1/music/jobs` -> `{"id", "status": "queued", "queue_position"}`; 422 for a missing `input` or `instructions`; 429 when the queue is full.
- `GET /v1/music/jobs/{id}` -> `status` one of `queued|running|done|error|cancelled`; while running: `frames_done`, `frames_max`
  (25 frames per second of audio), `stage`; on error: `error`; on done: `audio_url` and
  `stats = {audio_s, ended, total_s, llm_s, depth_s, denoise_s, vocoder_s, seed}`.
  **Seconds received is `stats.audio_s`; `stats.ended` is true when the song stopped because the lyrics ran out.**
- `GET /v1/music/jobs/{id}/audio` -> FLAC, 32 kHz stereo. `DELETE /v1/music/jobs/{id}` cancels.
- `GET /v1/health` (from the demo fake; the real reply was not read live): `{status, model, mesh, device_name, busy, queue_depth, error}`.
  **It carries no `limits` and no `defaults`.** The server's own code is in the tt-metal container image, not in this repo, so this is
  the best evidence available. `model` is the served display name (`fiona-crapple-merged` by default in `serve_music.py`, `--name`).
  The profile table must declare limits: 5 to 60 s (client constants `MIN_SECONDS`, `MAX_SECONDS`), steps 1 to 100.
- Instrumental: **unknown, not confirmed.** The client refuses empty lyrics (`"lyrics must not be empty"`) and the fake returns 422 for an
  empty `input`. Neither repo mentions an instrumental tag or placeholder. `serve_music.py` is only a container launcher and does not
  validate requests. Until a live check, Task 4 should treat the Instrumental switch as unconfirmed: the runtime sends the
  placeholder `[Instrumental]` (named constant `INSTRUMENTAL_PLACEHOLDER`), and the switch is dropped if the live server rejects it.
- Port: `serve_music.py --port` defaults to 20000.
- v6 package: no evidence about a different job API. Not checked.

## Stable Audio 3 small-music (`/v1/audio`)

- `POST /v1/audio/jobs` body `{prompt, duration?, steps?, cfg_scale?, seed?}` -> 202 `{id, status: "queued"}`; 400 bad input
  (`additionalProperties` false); 429 queue full; 503 model loading or failed to load.
- `GET /v1/audio/jobs/{id}` -> `{id, status, queue_position, stage, settings, error, cancel_requested, stats}`. `settings` echoes
  `{prompt, duration, steps, cfg_scale, seed}` (a seed not asked for is chosen by the server and returned here).
  `stats = {audio_s, total_s, audio_format, stages}` when done. **There is no `ended` field and no `frames_done`.**
  Seconds received is `stats.audio_s`.
- `GET /v1/audio/jobs/{id}/audio` -> FLAC or WAV (`stats.audio_format`). 409 if not done.
- `DELETE /v1/audio/jobs/{id}`: a queued job is cancelled; **a running job cannot be stopped** (`cancel_requested: true`, the chip
  keeps running and the clip is discarded).
- Live `GET /v1/health` on 2026-10-28:
  `{"status":"ok","model":"stable-audio-3 small-music, diffusion transformer on one Blackhole chip","device_name":"one Blackhole chip","queue_depth":0,"queue_max":2,"busy":false,"limits":{"min_seconds":1,"max_seconds":120,"max_steps":100,"min_cfg_scale":1.0,"max_cfg_scale":20.0},"defaults":{"duration":10,"steps":8,"cfg_scale":1.0}}`
  Limits use `min_seconds`, `max_seconds`, `max_steps`, `min_cfg_scale`, `max_cfg_scale`. Defaults use `duration`, `steps`, `cfg_scale`.
- Prompt limit 2000 characters. The server is bound to loopback and refuses a Host header that is not the loopback address.
- Neither server demands a bearer token in the demo clients; token support is ours.

## To confirm in the live spike

- Stable Audio `/openapi.json` has `paths` and the field names above.
- MiniMax real `/v1/health` body, and whether `[Instrumental]` or empty `input` is accepted.
- Whether the v6 MiniMax package serves the same API.
- The fake server's field names live in one dict, `FIELDS`, at the top of `tests/fake_music_server.py`, and the client's in `WIRE` in `app/music_runtime.py`.
