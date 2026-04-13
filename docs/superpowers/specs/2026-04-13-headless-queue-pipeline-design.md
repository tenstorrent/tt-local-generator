# Headless queue + prompt-generation pipeline

**Date:** 2026-04-13  
**Status:** Approved — ready for implementation

---

## Summary

Add two new CLI entry points to `tt-ctl` that let users run the full
prompt-generation → video-generation pipeline without the GUI:

| Command | Purpose |
|---|---|
| `tt-ctl queue run` | Drain and execute the persistent queue |
| `tt-ctl generate [GUIDE] [flags]` | Generate N prompts (optionally guided) and run them |

All completed records write to the same `history.json` and file layout the
GUI already reads, so opening the GUI after a headless run shows every
generated video with full metadata and thumbnails — no extra steps.

---

## Architecture

```
tt-ctl generate --count 3 "golden hour cliffs"
│
├─ guided_generate("golden hour cliffs", type="video") × 3   [generate_prompt.py]
│     ├─ Qwen up  → POST /v1/chat/completions with "generate around theme" prompt
│     └─ Qwen down → algo slug + inject guide text, used as-is
│
├─ store.save_queue(queue + new_items)                        [history_store.py]
│     queue.json gains 3 items
│
└─ (unless --queue-only) → queue-run logic
       ├─ item 0: health_check() → GenerationWorker → remove from queue → store.append(record)
       ├─ item 1: same
       └─ item 2: same → history.json has 3 new records
                          GUI reads on load → all 3 videos visible with metadata
```

```
tt-ctl queue run
│
├─ load queue.json
├─ for each item in order:
│     ├─ health_check()  ── offline → SKIP (item stays in queue, warn, continue)
│     ├─ select worker by model_source (see Worker selection)
│     ├─ run_with_callbacks() in thread, block on threading.Event
│     ├─ on success or failure: remove item from queue.json, continue
│     └─ on Ctrl+C: worker.cancel(), remove current item (submitted to server),
│                   leave remaining items in queue, suggest tt-ctl recover, exit 1
└─ print summary: N done, M failed, K skipped
```

---

## New functions / modules

### `app/generate_prompt.py` — additions only

**`_llm_guided(guide, prompt_type, timeout=45) → str | None`**

New internal helper. Sends the user's guide directly to Qwen with a
"generate around this theme" system prompt (distinct from the existing
"polish this slug" `_llm_polish` system prompt). Returns the generated
prompt string, or `None` on any error.

System prompt:
```
You are a cinematic prompt writer for AI video generation.
Write one tight, vivid prompt inspired by the theme below.
Hard limit: 25 words. No preamble, no quotes, no explanation.
Never add gore, body horror, graphic violence, or disturbing imagery.
```
User message: `{_TYPE_HINT[prompt_type]}\n\nTheme: {guide}`

(`_TYPE_HINT` is the existing dict in `generate_prompt.py` — reused here
so `_llm_guided` respects the same per-type word limits and cinematic
language guidance as `_llm_polish`.)

**`guided_generate(guide, prompt_type="video", enhance=True) → dict`**

Public function with the same return schema as `generate()`:
```json
{"prompt": "…", "type": "video", "source": "llm|algo", "slug": "…"}
```

Logic:
1. If `enhance=True` and `_llm_available()`: call `_llm_guided()`.
   - On success: return `{"prompt": polished, "source": "llm", "slug": guide}`
2. Fallback (Qwen down or `enhance=False`): build algo slug via `_ALGO_FN[prompt_type]()`,
   prepend the guide: `slug = f"{guide}; {algo_slug}"`, return with `source="algo"`.

No changes to the existing `generate()` function or its signature.

### `tt-ctl` — new functions

**`cmd_queue_run(args)`**

Loads `queue.json`. For each item (index 0 first):

1. `client.health_check()` — if False: print warning, leave item, continue to next.
2. Select worker by `item["model_source"]`:
   - `"video"`, `"mochi"`, `"skyreels"` → `GenerationWorker`
   - `"image"` → `ImageGenerationWorker`
   - `"animate"` → `AnimateGenerationWorker`
3. Run `worker.run_with_callbacks(...)` in a `threading.Thread`; block via
   `threading.Event` until `on_finished` or `on_error` fires.
4. Remove item from `queue.json` regardless of success/failure (via
   `store.save_queue(remaining)`). Print outcome.
5. On `KeyboardInterrupt`: call `worker.cancel()`, remove current item
   (it was already submitted — user can recover via `tt-ctl recover`),
   leave remaining items, print job ID hint, `sys.exit(1)`.

Print a summary line at the end: `N done, M failed, K skipped`.

**`cmd_generate(args)`**

1. For each of `args.count` iterations (index `i` from 0):
   - If `args.guide` is set: call `guided_generate(args.guide, args.type, enhance=not args.no_enhance)`
   - Else: call `generate(args.type, args.mode, enhance=not args.no_enhance)`
   - Seed handling: if `args.seed == -1` (random), each item gets `-1`.
     If an explicit seed is given, use `args.seed + i` so N items get
     `seed, seed+1, seed+2 …` — avoiding identical outputs on multi-count runs.
   - Build a queue item dict from the result + CLI flags (`steps`, `seed`, etc.)
2. Append all items to `queue.json` via `store.save_queue(existing + new_items)`.
3. Unless `--queue-only`: call `cmd_queue_run(args)` directly.

**`_build_parser()` additions**

```
queue run                       Run and drain the queue (blocking)
  [--dry-run]                   Print what would run without executing

generate [GUIDE]                Generate prompts and run them
  --count N        (default 1)
  --type           video|image|skyreels|animate  (default video)
  --mode           algo|markov  (default algo; ignored when GUIDE + Qwen up)
  --no-enhance                  Skip Qwen polish / guide even if server is up
  --steps N        (default 30)
  --seed S         (default -1)
  --queue-only                  Add to queue but do not run
  --server URL     (default http://localhost:8000)
```

**Dispatch update** — add `"generate": cmd_generate` to the top-level dispatch
dict. Inside `cmd_queue`, the existing `if/elif` chain gains a new branch:
`elif sub == "run": cmd_queue_run(args)` (inserted before the `else: list` fallback).
`cmd_queue_run` is a standalone function so it can also be called directly from
`cmd_generate`.

---

## Worker selection table

| `model_source` value | Worker class | `model` kwarg |
|---|---|---|
| `"video"` | `GenerationWorker` | `"wan2.2-t2v"` |
| `"mochi"` | `GenerationWorker` | `"mochi-1-preview"` |
| `"skyreels"` | `GenerationWorker` | `"skyreels-v2-df"` |
| `"image"` | `ImageGenerationWorker` | `"flux.1-dev"` |
| `"animate"` | `AnimateGenerationWorker` | `"wan2.2-animate-14b"` |

---

## Error handling

| Situation | Behavior |
|---|---|
| Qwen server down on `generate` | Warn once at start, proceed with algo+inject fallback |
| Inference server offline for queue item | Skip item (stays in queue), print warning, continue |
| Worker `on_error` (bad job, download fail) | Remove item from queue, print error, count as failed, continue |
| Ctrl+C during active generation | `worker.cancel()`, remove current item, leave rest, hint `tt-ctl recover`, exit 1 |
| `--queue-only` flag | Add items, print count, exit 0 — do not start any worker |
| Empty queue on `queue run` | Print "Queue is empty." exit 0 |
| Unknown `model_source` in queue item | Treat as skip (warn), leave item in queue |

---

## File changes

| File | Change type | Description |
|---|---|---|
| `app/generate_prompt.py` | Addition | `_llm_guided()`, `guided_generate()` |
| `tt-ctl` | Addition | `cmd_generate()`, `cmd_queue_run()`, parser additions, dispatch update |

No new files. No changes to `worker.py`, `history_store.py`, `api_client.py`,
or `server_manager.py` — the workers already write all metadata the GUI needs.

---

## Tests (`tests/`)

| Test | What it checks |
|---|---|
| `test_guided_generate_llm_up` | Mock Qwen endpoint returns a prompt; verify `_llm_guided` is called with guide in user message; `source == "llm"` |
| `test_guided_generate_llm_down` | Mock Qwen as unreachable; verify fallback produces a prompt that contains guide text; `source == "algo"` |
| `test_queue_run_consume_success` | Mock worker success; verify item removed from queue.json after run |
| `test_queue_run_consume_failure` | Mock worker `on_error`; verify item still removed from queue.json, counted as failed |
| `test_queue_run_skip_offline` | Mock `health_check()` → False; verify item stays in queue, skipped count incremented |
| `test_queue_run_ctrl_c` | Simulate KeyboardInterrupt mid-run; verify remaining items untouched in queue |
| `test_generate_queue_only` | `--queue-only`; verify items added to queue.json, no worker instantiated |
| `test_generate_runs_queue` | Without `--queue-only`; verify both queue add and queue run logic execute |

---

## Example sessions

```bash
# 1. Full autonomous run — 10 guided skyreels videos, no GUI needed:
tt-ctl start skyreels
tt-ctl generate --count 10 --type skyreels "erupting volcano at night, wide aerial"
# ... streams progress for each item ...
# Open GUI → all 10 videos in history with thumbnails and metadata

# 2. Stage queue from multiple sources, then drain:
tt-ctl generate --count 5 --queue-only "coastal fog rolling through redwood forest"
tt-ctl queue add "a lone lighthouse keeper watching a storm approach"
tt-ctl queue run

# 3. Interrupted run → resume:
tt-ctl queue run     # Ctrl+C after 3 of 8
tt-ctl queue run     # picks up remaining 5

# 4. Check what's queued before running:
tt-ctl queue          # list items
tt-ctl queue run --dry-run   # show what would run
tt-ctl queue run      # execute
```
