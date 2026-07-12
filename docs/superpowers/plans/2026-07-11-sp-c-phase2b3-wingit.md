# SP-C Phase 2b-3: Wing-it (free-form → step) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** The imagination-first escape hatch in the composer: a "describe what you want next" free-form input that maps a natural-language request → a concrete pipeline step (a capability + params), inserted into the working spec. LLM-assisted (via whatever chat model is up — ideally the always-on CPU prompt server), with a deterministic heuristic fallback so it never fails hard.

**Architecture:** A pure-ish **wing-it mapper** takes the free-form text + the prior step's output kind + the discovered capabilities and (via an injected `llm_fn`) returns a `(class_type, params)` decision; a heuristic fallback covers no-LLM / bad-output. The composer's add-a-step area gains a free-form entry that runs the mapper (real `llm_fn` = detect+call an available chat LLM) and adds the resulting step through the existing kind-safe `add_step`.

**Tech Stack:** Python 3.12 / GTK4, pytest (+xvfb). Reuses `app/capability_discovery.py` (`Capability`, `default_capabilities`), `app/spec_remix.py` (`add_step`), `app/intent_vocab.py`, `app/artgen/__init__.py` (`detect_artgen_endpoint`, `call_llm`), `app/pipeline_studio.py` (RemixView composer).

## Global Constraints

- The mapper core has **no GTK imports** and takes an injected `llm_fn` (and the capability list) — unit-tested with a fake `llm_fn`, no real network.
- **Never fail hard:** if `llm_fn` is None/raises/returns unparseable output, fall back to a deterministic heuristic that still produces a sensible step (or returns None → the UI shows "couldn't compose that, try rephrasing" — never a crash/traceback).
- **Kind-safe:** the mapper only ever chooses a capability whose input can consume the prior step's output kind (constrain the LLM to the *provided* contextual capability list; validate the choice; the composer's `add_step` guard is the final backstop).
- **Reuse, don't fork:** adding the resulting step goes through the SAME `add_step` path as the picker (native → `add_step(class_type)`; plugin → `add_step("TTLGArtgenGenerate", params={"plugin":…, ...})`). Free-form text lands in the chosen capability's primary text param (e.g. `prompt`/`theme`) unless the LLM assigns params.
- Intent language in any UI copy; model a quiet detail. No regression to Phase-1/2a/2b-1/2b-2. RemixView repeat-safe; keep the picker (2b-2) working alongside.
- Mockup: `.superpowers/brainstorm/988333-1783804257/content/add-step-wingit.html` (the free-form "say it however you like" box on top; capabilities below).

## File Structure

- Create `app/wingit.py` — pure: `map_freeform_to_step(text, prior_output_kind, capabilities, *, llm_fn) -> WingitResult|None` + `default_llm_fn(prompt) -> str|None` (thin real wrapper over detect+call_llm).
- Modify `app/pipeline_studio.py` — add a free-form entry + "Compose it" to the add-a-step area (via a `wingit_fn` seam defaulting to a real mapper), adding the composed step through `add_step`.
- Tests: `tests/test_wingit.py`, extend `tests/test_pipeline_studio.py`.

---

### Task 1: Wing-it mapper (pure, injected llm_fn)

**Files:** Create `app/wingit.py`, `tests/test_wingit.py`.

**Interfaces produced:**
- `@dataclass class WingitResult: class_type:str; params:dict; capability_id:str; via:str` (`via` ∈ "llm"|"fallback").
- `map_freeform_to_step(text:str, prior_output_kind:str|None, capabilities:list[Capability], *, llm_fn) -> WingitResult|None`:
  - Build a compact prompt listing the LIVE candidate capabilities (id, label, kind_in/out, key params) and the user's `text`; ask `llm_fn` for JSON `{"capability_id": ..., "params": {...}}`. Parse leniently (strip fences/think-blocks; tolerate extra prose — extract the first JSON object).
  - Validate: `capability_id` must be one of the provided LIVE capabilities; drop unknown params. On success → `WingitResult(cap.class_type, params(+plugin for plugin caps), cap.id, "llm")`.
  - **Fallback** (llm_fn None / raises / unparseable / invalid id): pick a deterministic default capability from the LIVE list that can consume `prior_output_kind` (prefer a text-producing or the first compatible one), put `text` into its primary text param (prompt/theme), return `WingitResult(..., via="fallback")`. If NO live capability fits → return None.
- `default_llm_fn(prompt) -> str|None`: `detect_artgen_endpoint()`; if a base is found, `call_llm(prompt, model, base_url=base, max_tokens=...)`; else None. (Keep thin; the mapper core stays pure via the injected `llm_fn`.)

- [ ] **Step 1: failing tests** (fake `llm_fn` + a small `Capability` list incl. a plugin cap): LLM returns valid JSON choosing a live cap + params → `WingitResult(via="llm")` with that class_type/params (+plugin for a plugin cap); LLM returns prose-wrapped JSON → still parsed; LLM returns an INVALID capability_id → falls back (not a crash); `llm_fn=None` → fallback puts `text` into the default cap's prompt param (`via="fallback"`); no compatible live cap → returns None. All pure (no network).
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** `app/wingit.py` (pure core + `default_llm_fn` wrapper). Lenient JSON extraction; kind-constrained to the provided caps.
- [ ] **Step 4: run → pass.**
- [ ] **Step 5: commit** `feat(sp-c): wing-it mapper (free-form text -> step, llm + fallback)`.

---

### Task 2: Wing-it UI in the composer

**Files:** Modify `app/pipeline_studio.py`, extend `tests/test_pipeline_studio.py`.

- [ ] **Step 1: xvfb tests** — `RemixView` gains a `wingit_fn` seam (defaults to a real mapper closure using `wingit.map_freeform_to_step` + `wingit.default_llm_fn` + `default_capabilities`). The add-a-step area shows a free-form entry + "Compose it" (per the mockup, imagination-first). With an injected fake `wingit_fn(text, after_node_id) -> WingitResult`: typing text + Compose after a step calls it and adds the composed step via `add_step` (native → class_type; plugin → `TTLGArtgenGenerate` + plugin param) — assert the working spec grew with the composed node (via `current_spec()`); a `None` result shows a gentle "couldn't compose that" message and adds nothing (no crash); the kind-safe `add_step` guard still applies. → fail.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** — free-form entry + Compose button in the add-a-step area (alongside the 2b-2 capability list); wire to `wingit_fn`; run the mapper OFF the main thread if the real llm_fn is used (it may hit the network) → `GLib.idle_add` to apply the result (GTK threading rule); the injected-fake path in tests can be synchronous. Gentle failure message; repeat-safe.
- [ ] **Step 4: run → pass; full suite (xvfb) — no NEW failures.**
- [ ] **Step 5: commit** `feat(sp-c): composer wing-it (describe a step in your own words)`.

---

### Task 3: Changelog + version

- [ ] Bump `VERSION` (minor) + changelog stanza: "Pipeline Studio Phase 2b-3 — 'wing it': describe the next step in your own words and the composer turns it into a real, kind-safe step (LLM-assisted via any running chat model, with a heuristic fallback). Completes the free-form composer." Commit.

---

## Self-Review

**Spec coverage (Phase 2b-3 slice):** free-form→step mapping (LLM + fallback, kind-constrained) → Task 1; the composer wing-it entry running it off-thread + adding via `add_step` → Task 2; version → Task 3. Phase 3 (in-app showcase) remains separate. ✓
**Placeholder scan:** pure Task 1 has full interface + injected-`llm_fn` TDD incl. lenient parse + fallback + None cases; GTK task specs the `wingit_fn` seam, off-thread mapping, add path, gentle failure, xvfb tests; real `default_llm_fn` via detect+call_llm with graceful no-LLM. No TBDs. ✓
**Type consistency:** `WingitResult`, `map_freeform_to_step`, `default_llm_fn`, the `wingit_fn` seam, and the native/plugin `add_step` path all consistent with 2b-1/2b-2; kind constraint reuses `Capability`/`intent_vocab`; threading follows the RemixView/PipelineRunner GLib pattern. ✓
