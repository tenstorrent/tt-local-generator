# Running-model identity & model-specific status dots — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax. Each task ships independently (version bump + changelog folded into its final task).

**Goal:** Make the running chat model's identity first-class — the READY dot lights only the model actually loaded, and a running model matching no registered entry appears as its own selectable "detected" entry in Create's Text model list + Model door.

**Architecture:** `detect_artgen_endpoint()` already returns `(url, model_id)`; `ModelStatusService` currently discards the id and marks every artgen/prompt server READY on any chat endpoint. Add a pure matcher (detected id → specific server key), track the running model in the service, resolve readiness per-key, and surface an unknown running model as a synthetic UI entry. Spec: `docs/superpowers/specs/2026-07-17-running-model-identity-design.md`.

**Tech Stack:** Python 3, GTK4/PyGObject, pytest (xvfb).

## Global Constraints

- **Scope: artgen/text only.** Image/video/animate dots (port-8000 `runner_key` disambiguation) are untouched. Generation routing (detect-based) is untouched.
- **Invariants:** artgen param `collect()` byte-for-byte unchanged (no "model" field added for artgen mediums); `_CSS` byte literals ASCII-only (● ◌ ◐ and "(detected)" live in Python string labels); palette = tt-vscode-toolkit variant.
- System python `/usr/bin/python3`. Tests: `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q`. Deselect known flakes: `tests/test_pipeline_engine.py::test_run_plugin_loads_and_calls_real_module`, `tests/test_forge_transforms.py::test_on_transform_finished_appends_and_refreshes`.
- Per-task: version bump + changelog folded into the final task. Local commits only — DO NOT push.

---

### Task 1: `match_model_id` matcher + `ServerDef.model_id`

**Files:**
- Modify: `app/server_manager.py` (add `model_id` field to `ServerDef`; set it on the 6 artgen servers + `prompt-server`).
- Modify: `app/model_status.py` (add module-level `match_model_id`).
- Test: `tests/test_model_id_match.py` (new).

**Interfaces:**
- Produces: `match_model_id(detected_id: str | None, servers: dict) -> str | None` in `model_status.py`. `servers` is a `server_manager.SERVERS`-shaped dict; only entries whose `capabilities` include `"artgen"` or `"prompt"` are considered. Uses each entry's `model_id` when set, else `label`.

**Normalization rule (implement exactly):** take the substring after the last `/`, lowercase, remove every non-alphanumeric char. Match a candidate when normalized detected == normalized candidate, OR one is a substring of the other AND the shorter of the two is ≥ 4 chars. Return the first matching key (iteration order of `servers`); `None` if detected is falsy or nothing matches.

- [ ] **Step 1: Write failing tests** in `tests/test_model_id_match.py`:
  - `Qwen/Qwen3-8B` → `"artgen-qwen3-8b"`
  - `meta-llama/Llama-3.3-70B-Instruct` → `"artgen-llama-3.3-70b"` (containment)
  - `Qwen/Qwen3-32B` → `"artgen-qwen3-32b"` (NOT the 8b entry — distinctness)
  - `qwen3.6-27b` → `None` (unknown)
  - `""` / `None` → `None`
  - a plain id equal to a label with different case/separators matches (e.g. `"qwen3_8b"` → `"artgen-qwen3-8b"`)
  Use the real `server_manager.SERVERS`.
- [ ] **Step 2: Run tests, verify they fail** (`match_model_id` undefined). Run: `/usr/bin/python3 -m pytest tests/test_model_id_match.py -v`.
- [ ] **Step 3: Add `model_id` field** to `ServerDef` (default `None`) and set it on the 6 artgen servers + `prompt-server` to their best-known served `/v1/models` id (e.g. `artgen-qwen3-8b` → `"Qwen/Qwen3-8B"`, `artgen-llama-3.3-70b` → `"meta-llama/Llama-3.3-70B-Instruct"`, `prompt-server` → `"Qwen/Qwen3-0.6B"`; consult each `start_*` `--model` and script for the exact id).
- [ ] **Step 4: Implement `match_model_id`** in `model_status.py` per the normalization rule.
- [ ] **Step 5: Run tests, verify pass.** Then full suite (deselecting known flakes). Confirm no existing `ServerDef` construction breaks (the new field is optional).
- [ ] **Step 6: Commit** `feat(status): model-id matcher + served model_id on chat ServerDefs`.

---

### Task 2: Track the running model & make artgen readiness model-specific

**Files:**
- Modify: `app/model_status.py` (`_tick`, running-artgen state, `running_artgen_model()`, `_notify` change-detection).
- Test: `tests/test_model_status.py` (extend existing) or `tests/test_model_status_running_model.py` (new).

**Interfaces:**
- Consumes: `match_model_id` (Task 1).
- Produces: `ArtgenModelInfo = namedtuple("ArtgenModelInfo", "model_id url matched_key")`; `ModelStatusService.running_artgen_model() -> ArtgenModelInfo | None` (lock-guarded; `None` when no chat endpoint up).

- [ ] **Step 1: Write failing tests** (drive `_tick()` directly with fake `health_fn`/`detect_fn`/`clock`/`port_probe`, no threads):
  - `detect_fn` returns `("http://localhost:8003", "Qwen/Qwen3-8B")`, health all False → after `_tick`, `status("artgen-qwen3-8b") == Status.READY`, `status("artgen-qwen3-32b") == Status.OFF`, `status("artgen-llama-3.3-70b") == Status.OFF`.
  - Same but `detect_fn` returns `("http://localhost:9001", "qwen3.6-27b")` → NO artgen key READY; `running_artgen_model()` returns `ArtgenModelInfo("qwen3.6-27b", "http://localhost:9001", None)`.
  - `detect_fn` returns `(None, None)` → `running_artgen_model()` is `None`; artgen keys OFF.
  - `prompt-server` managed health True (via `health_fn`) while `detect_fn` returns `(None, None)` → `status("prompt-server") == Status.READY` (managed health independent of the sweep).
  - A subscriber added via `subscribe` is notified when the running model changes across two `_tick`s even if no per-key Status flips (e.g. unknown id A then unknown id B, both leaving all keys OFF).
- [ ] **Step 2: Run tests, verify they fail.** Run: `/usr/bin/python3 -m pytest tests/test_model_status_running_model.py -v`.
- [ ] **Step 3: Implement.** In `_tick`: keep `model_id`; compute `matched = match_model_id(model_id, SERVERS)` when `base is not None`; an artgen/prompt key is detect-healthy only if `key == matched`; managed `health.get(key)` OR that condition. Under `self._lock` store `_artgen_model_id`/`_artgen_url`/`_artgen_matched_key` (cleared when `base is None`); fold a change in that tuple into the "did anything change → `_notify`" decision (I/O outside lock, notify after release — preserve existing lock discipline). Add `ArtgenModelInfo` + `running_artgen_model()`.
- [ ] **Step 4: Run tests, verify pass.** Then full suite. Confirm existing `test_model_status.py` still green (the blanket-ready behavior it may assert must be updated to the model-specific expectation — update, don't gut).
- [ ] **Step 5: Commit** `feat(status): track running chat model; per-model artgen readiness`.

---

### Task 3: Model-specific dots + "detected" selectable entry in Create

**Files:**
- Modify: `app/create_view.py` (`_populate_model_dropdown`/`_scoped_model_keys`, `_build_model_door`, snapshot handler, auto-select).
- Test: `tests/test_create_view_*.py` (extend the create-view suite; new file if cleaner).

**Interfaces:**
- Consumes: `ModelStatusService.running_artgen_model()` (Task 2).

- [ ] **Step 1: Write failing tests** (xvfb widget-level, using the existing CreateView test harness with an injected fake/real `ModelStatusService`):
  - With a running UNKNOWN model (`running_artgen_model()` → `matched_key=None`, id `"qwen3.6-27b"`), a text/artgen medium's dropdown contains exactly one entry labeled with `qwen3.6-27b` + " (detected)" and a ● dot, AND the Model door "Text" group contains that same card.
  - With a running KNOWN model (`matched_key="artgen-qwen3-8b"`), NO synthetic entry is added; the `artgen-qwen3-8b` entry shows ● and the other artgen entries show ◌ (via `_model_dot_glyph`).
  - With `running_artgen_model()` → `None`, no synthetic entry appears.
  - `collect()` for an artgen medium is identical with and without a detected entry present (no "model" field introduced).
- [ ] **Step 2: Run tests, verify they fail.** Run under xvfb.
- [ ] **Step 3: Implement.** Inject the synthetic entry in `_scoped_model_keys`/`_populate_model_dropdown` for text/artgen mediums and in `_build_model_door`'s "Text" group when `running_artgen_model()` has `matched_key is None`; label `f"{model_id} (detected)"`, dot ●, selectable, inert for collect. Auto-select `running_or_starting("artgen")` or the synthetic entry when unknown — fresh-populate branch only (preserve a manual pick).
- [ ] **Step 4: Run tests, verify pass.** Then full suite (deselect known flakes).
- [ ] **Step 5: Version + changelog.** `VERSION` → `0.47.0`; prepend a `debian/changelog` stanza (running chat model identity: the READY dot now marks only the model actually running, and an unregistered running model appears as a selectable "detected" entry in Create's Text models + Model door). Update CLAUDE.md's "Model status" section to note model-id tracking + per-model readiness.
- [ ] **Step 6: Commit** `feat(create): model-specific dots + detected-model entry (running-model identity)`.

---

## Notes for the executor
- Order matters: Task 1 (matcher) → Task 2 (service uses it) → Task 3 (UI uses the service accessor).
- Do NOT touch `_on_generate`/workers/artgen generation routing. The detected entry is display-only; artgen already routes via `detect_artgen_endpoint()`.
- Preserve `model_status.py` lock discipline (I/O outside lock; `_notify` after release; change-only notifications).
