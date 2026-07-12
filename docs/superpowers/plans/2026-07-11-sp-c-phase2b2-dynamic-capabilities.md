# SP-C Phase 2b-2: Dynamic (MCP-driven) capabilities — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Replace the composer's STATIC add-a-step list (Phase 2b-1: `compatible_intents` from `intent_vocab`) with a **dynamic, contextual, live-vs-latent** capability list built from what's actually available — the plugin **`mcp.json`** definitions (the "MCP capability" source), the loaded plugin registry, and live server/hardware health — so the options reflect the running system and grow as models start.

**Architecture:** A pure-ish **capability-discovery** layer aggregates capabilities from (a) native engine intents (`intent_vocab`) and (b) plugin `mcp.json` `x-ttlg`/`tools` metadata, filters by the prior step's output kind, and partitions each into **live** (usable now) vs **latent** (needs a model started → "start X"), using injected registry/health lookups. The composer's add picker renders that list.

**Tech Stack:** Python 3.12 / GTK4, pytest (+xvfb). Reuses `app/intent_vocab.py`, `app/spec_remix.py`, `app/pipeline_studio.py` (RemixView composer), `app/artgen/__init__.py` (`all_names`, `detect_artgen_endpoint`), `app/server_manager.py` (`is_healthy`/`status_all`/`SERVERS`), and `plugins/*/mcp.json`.

## Global Constraints

- The discovery layer has **no GTK imports** and is unit-tested with **injected** registry/health/mcp-reader functions (no real disk/network/hardware in unit tests).
- Capability source of truth for plugins = each plugin's `plugins/<name>/mcp.json` `x-ttlg` block (`media_type`, `accepts_remix_from`, `can_remix_to`, `hardware`) + `tools[].name`/`description`. Native (engine) capabilities come from `intent_vocab` (kinds). Do NOT hardcode a capability list.
- **Contextual filter:** given the prior step's output kind, offer only capabilities that can consume it (native: `input_kind == output_kind`; plugin: kind derivable from its metadata / a text-seeded plugin consumes text). Never offer a kind-incompatible capability as *live*.
- **Live vs latent:** a capability is **live** iff it can run right now — CPU/native-with-no-hardware and loaded plugins are live; a capability needing a backend/model (`x-ttlg.hardware` set, or a media/LLM node type) is live only if that backend is healthy/startable-and-up, else **latent** with a human reason ("start a video model", "start an LLM"). Availability is computed from injected `is_plugin_loaded`/`is_backend_up` callables (real impls wrap `artgen.all_names` + `server_manager.is_healthy`/`detect_artgen_endpoint`).
- Backward-safe: if `mcp.json` reads fail or a source is empty, degrade to the Phase-2b-1 static `compatible_intents` (never crash / never an empty picker for a valid kind).
- No regression to Phase-1/2a/2b-1. Intent language in all labels; model a quiet detail.
- Mockup: `.superpowers/brainstorm/988333-1783804257/content/add-step-wingit.html` (the live-badged, greyed-latent capability grid).

## File Structure

- Create `app/capability_discovery.py` — pure: read/parse plugin mcp.json (`load_plugin_capabilities`), aggregate with native intents, `discover_capabilities(output_kind, *, is_plugin_loaded, is_backend_up, mcp_reader=…) -> list[Capability]`.
- Modify `app/pipeline_studio.py` — the add-a-step picker uses `discover_capabilities` (live enabled + latent greyed) instead of static `compatible_intents`; choosing a live one adds the step (native → existing `add_step`; plugin → a `TTLGArtgenGenerate` step with `plugin=<name>`).
- Modify `app/capability_discovery.py` real wrappers use `artgen.all_names`, `server_manager`, `detect_artgen_endpoint`.
- Tests: `tests/test_capability_discovery.py`, extend `tests/test_pipeline_studio.py`, `tests/fixtures/` (a couple of tiny mcp.json).

---

### Task 1: Capability discovery layer (pure, injected deps)

**Files:** Create `app/capability_discovery.py`, `tests/test_capability_discovery.py`, fixtures.

**Interfaces produced:**
- `@dataclass(frozen=True) class Capability: id:str; label:str; kind_out:str; kind_in:str|None; source:str; class_type:str; plugin:str|None; hardware:str|None; live:bool; reason:str|None` (source ∈ "native"|"plugin"; for a plugin capability `class_type="TTLGArtgenGenerate"`, `plugin=<name>`; label from the plugin's tool description or the native intent label).
- `load_plugin_capabilities(mcp_reader) -> list[<raw plugin cap>]` — parse each plugin `mcp.json` (`mcp_reader() -> {plugin: mcp_dict}`), reading `x-ttlg.media_type` (→ kind_out), `x-ttlg.hardware`, `tools[0].name`/`description`.
- `discover_capabilities(output_kind, *, is_plugin_loaded, is_backend_up, mcp_reader, native=INTENTS) -> list[Capability]` — native intents kind-filtered (Phase-2b-1 semantics) + plugin caps whose input kind can consume `output_kind`; each marked `live`/`reason` via `is_plugin_loaded`(plugin) and `is_backend_up`(the backend the cap needs) — CPU/no-hardware caps live when loaded; hardware/media/LLM caps latent unless their backend is up. Deterministic order: live first, then latent.
- `default_capabilities(output_kind)` — a thin real-deps wrapper (imports artgen/server_manager) used by the UI; kept out of the pure core.

- [ ] **Step 1: failing tests** (fixtures: 2 tiny mcp.json — a CPU text plugin `hardware:null media_type:text`, and a hardware one `hardware:"blackhole" media_type:video`; injected `is_plugin_loaded`/`is_backend_up`/`mcp_reader` as fakes): `discover_capabilities("image", ...)` includes native image-consumers (Caption/RemoveBackground/ImageToVideo) as live; a loaded CPU plugin is **live**; a hardware plugin whose backend `is_backend_up` returns False is **latent** with a reason; an unloaded plugin is latent ("start"/"install"); live-before-latent ordering; empty/failed mcp_reader → falls back to native `compatible_intents` (non-empty). All pure (no real disk/hardware).
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** `app/capability_discovery.py` (pure core + the injected-deps signature; `default_capabilities` wraps `artgen.all_names`/`server_manager.is_healthy`/`detect_artgen_endpoint`).
- [ ] **Step 4: run → pass.**
- [ ] **Step 5: commit** `feat(sp-c): dynamic capability discovery (mcp.json + registry + health)`.

---

### Task 2: Composer picker uses dynamic capabilities

**Files:** Modify `app/pipeline_studio.py`, extend `tests/test_pipeline_studio.py`.

- [ ] **Step 1: xvfb tests** — the add-after picker now lists `discover_capabilities(<this step's output_kind>, …)` (inject fakes in the test via a seam — e.g. `RemixView` takes an optional `capability_fn` defaulting to `capability_discovery.default_capabilities`): live capabilities are enabled and, when chosen, add the right step (a native intent → `add_step(class_type)`; a plugin capability → `add_step("TTLGArtgenGenerate", params={"plugin": name})`); latent capabilities render greyed/disabled with their reason ("start a video model") and do NOT add a step when clicked. Assert a latent one is present-but-disabled and a live one adds. → fail.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** — swap the picker's source from `compatible_intents` to the injected `capability_fn`; render live vs latent (reuse the `add-step-wingit` mockup styling: live badge, greyed latent + reason). Plugin caps add a `TTLGArtgenGenerate` node (its `plugin` param); native caps unchanged. Keep the kind-safe `add_step` guard.
- [ ] **Step 4: run → pass; full suite (xvfb) — no NEW failures.**
- [ ] **Step 5: commit** `feat(sp-c): composer add-a-step uses live MCP-driven capabilities`.

---

### Task 3: Changelog + version

- [ ] Bump `VERSION` (minor) + changelog stanza: "Pipeline Studio Phase 2b-2 — the composer's add-a-step list is now dynamic: generated from your plugins' MCP definitions + live server health, contextual to the prior step's output, with capabilities that need a model shown as 'start it' rather than hidden." Commit.

---

## Self-Review

**Spec coverage (Phase 2b-2 slice):** dynamic discovery from mcp.json + registry + health → Task 1; composer picker consumes it (live/latent) → Task 2; version → Task 3. Phase 2b-3 (free-form wing-it) + Phase 3 (showcase) remain separate. ✓
**Placeholder scan:** pure Task 1 has full interface + injected-deps TDD; GTK task specs the seam (`capability_fn`), live/latent rendering, and native-vs-plugin add behaviour with xvfb tests; mcp.json is the real capability source (fixtures for tests). Degradation-to-static fallback specified (no empty picker). No TBDs. ✓
**Type consistency:** `Capability`, `load_plugin_capabilities`, `discover_capabilities`, `default_capabilities`, the `capability_fn` seam, and plugin-cap→`TTLGArtgenGenerate(plugin=…)` add path are consistent; kind filter reuses Phase-2b-1 `intent_vocab` semantics; availability via injected `is_plugin_loaded`/`is_backend_up` wrapping `artgen`/`server_manager`. ✓
