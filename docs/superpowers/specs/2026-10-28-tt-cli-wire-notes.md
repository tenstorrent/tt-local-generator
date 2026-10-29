# `tt` CLI and gozer wire notes (read-only spike, 2026-10-28)

Companion to `2026-10-28-tt-cli-server-control-design.md` section 7. Everything below was read from help output, source, or read-only commands
(`tt --version`, `tt --json model ps|list`, `tt --json serve <m> --dry-run --offline`, `gozer status --json`, `docker ps|inspect`).
Nothing was started, stopped, pulled or reset. Items that could only be settled by a forbidden command are marked **unverified**.

Source of the running `tt`: `/home/ttuser/.local/bin/tt` -> uv tool `tenstorrent` 1.2.0, an editable install of `~/code/tt-cli/src/tenstorrent`.
`~/.tenstorrent-venv/.../site-packages/tenstorrent` is an older copy (its `tt model stop` takes one name); read the editable copy for behaviour.
tt-model is `~/.local/share/tenstorrent/bin/tt-model`, from the `tt_kernel` package (`container.py`).

## Fixtures

| File | Command | Notes |
|---|---|---|
| `tests/fixtures/tt/ps.json` | `tt --json model ps` | One live row: tt-model container `tt-model-qwen3-coder-next-p300`, port 8011. |
| `tests/fixtures/tt/list.json` | `tt --json model list` (scope `all` has 140 rows; default has 86 for device p300x2) | Trimmed to 16 rows (14 names): container bundles (`vllm-plugin`, `tt-dit-server`), catalog models (`vLLM`, `media`, `forge` engines). |
| `tests/fixtures/tt/serve_dry_run.json` | `tt --json serve raahemnabeel/qwen3-coder-next --dry-run --offline` | tt-model bundle shape. |
| `tests/fixtures/tt/serve_dry_run_catalog.json` | `tt --json serve Qwen3.8-27B --dry-run --offline` | tt-inference-server catalog shape. |
| `tests/fixtures/gozer/status.json` | `gozer status --json` | Two boards, 4 chips; chips 0-1 `HELD-FOREIGN` (another agent's lease), chips 2-3 `FREE` at capture time. Chip state changes within minutes (the first capture showed chips 2-3 `BUSY-UNTRACKED`, a later one `CLAIMED`), so tests must not depend on it. |

No token or secret is in any of them (grep for `hf_`, `bearer`, `password`, `token` found only the field name `hf_token_source: "env"`).

Shapes the parser must handle:
- `model list` rows come in two shapes. Container bundles: `name, source, kind:"container", engine, engines[], arch[], hardware[], installed, backends:["tt-model"], cached, ...`.
  Catalog models: `name, hf_repo, model_type, engines[], tt_model_id, hardware[], devices{<hw>:{status, docker_image, max_context, ...}}, backends:["inference-server","studio"]`, and no `kind` or `installed` key.
  A bundle name can appear twice with different `hardware` (one row per profile). `scope` and `device` are top-level keys.
- `tt --json serve ... --dry-run` emits JSON (put `--json` before or after `serve`; both work). Two shapes:
  - bundle: `{bundle:{engine,image,arch,device_count,hardware,port,max_model_len,profiles[],default_profile,...}, backend:"tt-model", model, offline, port, serve_flags[], extra_args[], installed, argv[]}`.
    `device_count` is for the default profile (4 for `p300x2`) and `bundle.port` is the manifest port (8000), which tt-model ignores (it publishes 20000, walking up). `port` at top level is the requested `--port` (null).
  - catalog: `{model, workflow, device_requested, device_sent, served_through, engines[], status, spec_docker_image, default_port:"20000", argv[], ...}`. No chip count, only the device string (`p300x2`).
  Both carry the exact `argv`. Errors come as JSON `{why, next_step, exit_code, code, ...}` with exit code 2 for usage errors.

## Section 7, item 1: how `tt serve` picks chips; does anything pin it?

- Catalog model (tt-inference-server `run.py`): no chip choice at all. With no `--device-id`, `run_docker_server.py` (v0.25.0, lines ~511-517) mounts the whole `/dev/tenstorrent` directory.
  `tt serve` exposes `--device p300x2`, which names a board configuration, not chip indices. `tt serve Qwen3.8-27B --dry-run -- --device-id 0` fails with
  `Unrecognized arguments for a catalog model` (`commands/serve.py` line ~209, exit 2). Our own scripts can pin because they call `run.py --device-id` directly (`bin/start_artgen.sh` line ~210).
- tt-model bundle: tt-model picks chips itself. `pick_free_devices(count)` (`tt_kernel/container.py`) takes the lowest `count` chip indices not claimed by any running container (`docker inspect` `--device` mounts) or by any host process holding `/dev/tenstorrent/N` open (`/proc/*/fd`). It does not read gozer leases.
  `tt serve <bundle> -- --device-id 0,1` pins them: the dry-run argv ends `... --detach --local-only --device-id 0,1`. The count must match the profile. Evidence: `tt-model serve --help` text and `tt --json serve raahemnabeel/qwen3-coder-next --offline --dry-run -- --device-id 0,1`.
  A running example: `docker inspect tt-model-qwen3-coder-next-p300` shows `HostConfig.Devices` = `/dev/tenstorrent/0` and `/1`, and label `org.tenstorrent.tt-model.devices=0,1`.
- `TT_VISIBLE_DEVICES`: not read by `tt` or tt-model (grep of `tenstorrent/` and `tt_kernel/` finds only a progress-log regex in `phases.py`). Setting it on a `--dry-run` leaves `argv` unchanged. It is a runner variable inside tt-media-server only.
- Answer: catalog models cannot be pinned through `tt`; bundles can, with `-- --device-id`. `TT_VISIBLE_DEVICES` pins nothing on the `tt` path.

## Item 2: do servers from our `bin/start_*.sh` appear in `tt model ps`?

- `tt model ps` (`backends/serving/ps.py`) runs `docker ps` plus one `docker inspect`, then classifies each container: name prefix `tt-inference-server-` (backend `inference-server`), label `org.tenstorrent.tt-model` (`model-manager`), or the TT-Studio name/image rule. It also probes `GET /v1/models` on each published port and on the default endpoint (port 20000); an answering server no container explains shows as backend `unknown`.
- Our scripts run the vendored `run.py`, which names containers `tt-inference-server-<uuid>` (`vendor/tt-inference-server/workflows/run_docker_server.py` line 308) and our scripts pass `--host-hf-cache`, so the weights mount identifies the model. Conclusion: they should list as `backend: "inference-server"`, name = HF repo from the mount (or the matching catalog name).
- **Unverified:** no script-started container is running now (`docker ps` shows only `tt-model-qwen3-coder-next-p300` and `tt_studio_docker_control`). Open questions for the first live run: whether `health` reaches `healthy` for tt-media-server images (the probe needs `/v1/models`; our own check uses `/tt-liveness`), and what `served_id` is for them.
- What identifies a row: `container`, `container_id`, `port` (lowest published host port), `served_id` (probe only). There is **no runner key** in `ps`.

## Item 3: which gozer mechanism fits a long-lived server?

Read `~/code/tt-gozer/README.md` ("How it decides what's free", `--owner-pid`) and `gozer/cli.py` `cmd_adopt`.
- Bare `acquire` is a detached lease: `CLAIMED` for a 900 s grace window, `HELD` once an fd is open on the chip, reaped as `STALE` if 900 s pass with no visible fd. A root-owned container's fds are invisible to an unprivileged gozer, so such a lease is reaped about 15 minutes in while the server runs (README and design doc say so).
  Here the coder-next container runs as uid 1000 (`docker inspect` `Config.User` 1000:1000; `/proc/<pid>/fd` owned by ttuser), and `gozer status` lists its `pids_holding`, so the invisibility case did not occur. **Unverified** for a root image such as a tt-inference-server container run as root.
- `acquire --owner-pid N`: non-detached, lives while pid N lives. Needs a supervisor that outlives the server. The app can close, so this does not fit a server that must survive an app restart.
- `adopt <target> --who W`: needs a process already holding the chip (reads `/proc` fds), creates a non-detached lease with `pid` = the first holder (`state:"adopted"`, `reset_on_release:true`). It is judged by that pid's liveness, so it survives an app restart and goes `STALE` when the container exits. It fails with "has no process holding it" when no fd is visible, and with "already leased" when another lease holds the unit.
- `gozer status` shows `HELD-FOREIGN` for a leased chip whose fd is held by a process outside the owner's tree. `chips 0,1` in the fixture are exactly that: the lease owner is `sleep 86400` (pid 1466136), the container's `VLLM::EngineCore` holds the fds. HELD-FOREIGN is normal for a container served under a lease and is not a fault by itself.
- Answer: `adopt` after the server is up (as spec section 9 already says) is the one that survives an app restart and needs no supervisor. A pre-start bare `acquire` is acceptable for reserving and queueing, but it is only safe while a visible fd opens within 900 s; see the correction in spec 4.4 step 3.

## Item 4: what does `tt model stop` do to a container it did not start?

Read `commands/model.py` `stop_model`/`_stop_catalog_model` and `backends/serving/*.py`.
- It takes one or more names (the editable source; older copy takes one). Every name is resolved first, so a typo stops nothing. A bundle id (`namespace/name`) goes to `tt-model stop <id> [--profile P]`: SIGTERM first, and if docker has to SIGKILL, tt-model resets that container's own chips (read from its devices label) in a throwaway container. It acts on tt-model's own container for that bundle and profile.
- A catalog model name goes to `_stop_catalog_model` (or TT-Studio's own stop if studio serves it). It lists containers named `tt-inference-server-*` and identifies each by its HF snapshot mount or `volume_id_*` volume. Matched ones get a plain `docker stop <id>` (default 10 s timeout, then SIGKILL; no chip reset).
  If none match but some are unidentifiable, it refuses with `Cannot tell which container serves X` and names them. If none match and all are identified as other models: `Nothing running for X.` (not an error).
- So a script-started container is stoppable by `tt model stop <catalog name>` only when its mount names a model in the catalog. For boutique models `tt` does not carry (SkyReels, Wan2.2-Animate, Wan2.2-I2V, Mochi variants, AnimateDiff) a `ps` row's `name` is a raw HF repo that `_dispatch` rejects as an unknown model. Stop for those has to use the script's `--stop` or `docker stop <container_id>`.
- **Unverified** (needs a real stop): exit codes and JSON of a successful stop, and whether a 10 s `docker stop` is long enough for a 14B diffusion server to close the mesh.

## Item 5: which `SERVERS` models does `tt model list` carry?

From `server_manager.SERVERS` and `tt --json model list --all` (the default listing is filtered to this box's device, p300x2):

| SERVERS key | In `tt model list`? | Catalog name (backends) |
|---|---|---|
| wan2.2 | yes | Wan2.2-T2V-A14B-Diffusers (inference-server, studio; p300x2) |
| flux | yes | FLUX.1-schnell (inference-server, studio; p300x2) |
| flux-dev | yes | FLUX.1-dev (inference-server, studio; p300x2) |
| mochi | yes, not for p300x2 | mochi-1-preview (galaxy, p150x8, t3k only) |
| sdxl | partly | stable-diffusion-xl-base-1.0 (n150, t3k, galaxy; no p300x2); ours is the cpp_server variant |
| z-image-turbo | yes | Z-Image-Turbo (inference-server) |
| motif | yes, studio only | Motif-Image-6B-Preview (studio) |
| artgen-qwen3-8b | yes, not for p300x2 | Qwen3-8B (galaxy, n150, n300, t3k) |
| artgen-qwen3-32b | yes | Qwen3-32B (p300x2 included) |
| artgen-llama-3.1-8b | yes | Llama-3.1-8B-Instruct (p300x2 included) |
| artgen-llama-3.3-70b | yes | Llama-3.3-70B-Instruct (p300x2 included) |
| artgen-deepseek-r1-70b | yes, not for p300x2 | DeepSeek-R1-Distill-Llama-70B (galaxy, t3k, p150x8) |
| artgen-qwen2.5-7b | studio only | Qwen2.5-7B-Instruct |
| skyreels, wan2.2-i2v, animate | no | not in the catalog (we patch them in) |
| prompt-server (Qwen3-0.6B, CPU) | no | not served by `tt` |
| animatediff | no | boutique; tt-animatediff is not in this catalog |

Boutique today: everything we patch or that `tt` lacks (skyreels, wan2.2-i2v, animate, prompt-server, animatediff, mochi on this board, qwen3-8b and r1-70b on this board, sdxl cpp_server). A model that `tt` carries for this board and that we also script (wan2.2, flux, flux-dev, qwen3-32b, llama) defaults to our script per spec 4.2.

## Contradictions with the spec (corrected in the spec and plan in the same commit)

1. `plan_serve` form and shape (spec 4.1, section 9, plan Task 2): the command is `tt --json serve <model> --dry-run [--offline]`, and the JSON has two shapes. Neither gives the effective chip count: bundles give `device_count` for the default profile, catalog models give only a device string. Chip count has to come from the running profile, the `hardware` string, or an override.
2. Matching by runner key (spec 4.3, section 9, plan Task 5): `ps` has no runner key. Match on port, then `served_id`, then container id or HF repo name. The runner key comes from our own health check of script servers, not from `ps`.
3. `TT_VISIBLE_DEVICES` (spec 4.4 step 4): nothing on the `tt` path honours it. Bundles pin with `-- --device-id`; catalog models cannot be pinned through `tt`.
4. Leases (spec 4.4 step 3, section 9 "Leases without the app running"): gozer itself reaps a detached lease at 900 s if no visible fd opens, whatever the app thinks, and a `HELD-FOREIGN` reading is normal for a container under a lease. Adopt is the mechanism whose lifetime follows the container.
5. Stop (spec 4.5, plan Task 6/7): "stop it the same way" does not hold for servers whose `ps` name is not a catalog name or bundle id; `tt model stop` rejects them. Fall back to `docker stop <container_id>` with a long timeout, or the boutique `--stop`.
