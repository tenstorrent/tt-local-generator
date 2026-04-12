# Create Zone Redesign — Design Spec
*2026-04-12*

## Problem

The current UI has three separate places that deal with model selection (toolbar model buttons, Servers dropdown, status bar), creating confusion between two distinct concerns:

- **"What am I trying to accomplish?"** — source type (video / image), clip length, quality
- **"Is the server up?"** — which model is running, health status

In addition, the "Advanced Settings" accordion buries the most frequently tuned controls (quality, seed), while exposing raw technical labels (steps, guidance scale, seed integer) that communicate nothing to a non-ML user. The time labels shown next to quality presets are ambiguous between *render time* and *generated clip duration*.

## Workflow model

**Create → Curate → Watch** (Configure occasionally)

The left panel is the Create zone. It should stay in that lane — not double as a server management panel. Server health lives in the status bar and Servers menu, not in the prompt pane.

## Design decisions

### 1. Source tabs — toolbar (keep, simplify)

**Keep:** `🎬 Video` / `🖼 Image` source tabs in the toolbar. These represent distinct creative modes — different gallery views, different generation pipelines, future distinct capabilities (Text, etc.).

**Remove:** All model-specific toggle buttons from the toolbar — `Wan2.2 / Mochi / SkyReels` (video) and `FLUX.1-dev` (image). Model selection moves entirely to the Shot panel.

**Hide:** `💃 Animate` tab. No working TT-hardware model exists yet. Tab remains in code but hidden (`set_visible(False)`) until a model ships. Not removed — the plumbing is nearly done and the source tab itself is a useful concept.

**Future:** `📝 Text` tab is a natural addition. Architecture should not assume only Video and Image exist.

### 2. Toolbar after cleanup

```
[TT logo]  [Video] [Image]        …spacer…    [⚙ Generation ▾]  [Playlists ▾]  [📺 Watch]
```

`Servers ▾` moves into the `⚙ Generation` menu (or remains as a standalone menu item — see §6). Model buttons are gone entirely.

### 3. Control panel — Create zone layout (C+A hybrid)

The left pane becomes three named zones separated by a divider:

```
┌─────────────────────────────────┐
│ PROMPT                          │
│ ┌─────────────────────────────┐ │
│ │ textarea (placeholder)      │ │
│ └─────────────────────────────┘ │
│ [🖼 seed drop] [✨ Inspire me]  │
│               [🎬 Theme Set]    │
│ [chip] [chip] [chip] [chip] … │
├─────────────────────────────────┤  ← divider
│ CLIP LENGTH — output video is   │
│ [Short 2s·49f] [Std 5s·121f✓] [Long 8s·193f] │
│                                 │
│ QUALITY — render detail & time  │
│ [Fast ~3min] [Standard ~6min✓] [Cinematic ~9min] │
│                                 │
│ SHOT                            │
│ ┌─────────────────────────────┐ │
│ │ ● Wan2.2 · 720p             │ │
│ │              SkyReels ready›│ │
│ │ [🎲 New idea] [🔁 Repeat]  │ │
│ │              [📌 Keep this] │ │
│ └─────────────────────────────┘ │
├─────────────────────────────────┤
│ [▶ Generate]                    │
│ [✕ Cancel] (when running)       │
└─────────────────────────────────┘
```

#### PROMPT section (unchanged structure)
- Prompt textarea (existing)
- Seed image drop target — **moved here** from the Advanced Settings accordion, lives beside Inspire me as a small thumbnail well. Drag any gallery frame onto it to use as seed. Clear button appears when filled.
- Inspire me + Theme Set buttons (existing)
- Style chips (existing)

#### CLIP LENGTH section (new)
Named buttons controlling **output video duration** (what you get back). Label always shows both the human time and frame count, so the distinction from render time is unambiguous.

| Button label | Sublabel | Wan2.2 frames | SkyReels frames |
|---|---|---|---|
| Short | 2 s · 49 f | 49 | 9 |
| Standard | 5 s · 121 f | 121 | 33 |
| Long | 8 s · 193 f | 193 | 65 |
| Extended *(optional)* | 10 s · 257 f | 257 | 97 |

**Wan2.2** valid frame counts follow `4k+1` (33, 49, 65, 81, 97, …). Default is 81. At 24 fps:

| Button label | Sublabel | Wan2.2 frames |
|---|---|---|
| Short | 2 s · 49 f | 49 |
| Standard | 3.4 s · 81 f *(current default)* | 81 |
| Long | 5 s · 121 f | 121 |
| Extended | 8 s · 193 f | 193 |

**SkyReels** uses the same `4k+1` formula with a smaller default (33 frames = 1.4 s). Its button labels use its own frame counts at the same named slots:

| Button label | SkyReels frames | Duration |
|---|---|---|
| Short | 9 f | 0.4 s |
| Standard | 33 f | 1.4 s |
| Long | 65 f | 2.7 s |
| Extended | 97 f | 4.0 s |

When the active model changes (via the Shot panel switcher), CLIP LENGTH buttons update their sublabels to reflect the new model's frame counts. If the currently selected slot has no equivalent (unlikely — both models share the same slot names), snap to Standard.

**Mochi** runner has `num_frames` hard-coded to 168 (7 s at 24 fps) — the pipeline doesn't accept `num_frames` from the request yet. Show the CLIP LENGTH row for Mochi but render it as a single locked button: `"7 s · 168 f  (fixed)"` — greyed toggle, not interactive. When Mochi is parameterised in future, this unlocks automatically.

Wan2.2 clip length (`num_frames` pass-through) is already implemented in `TTWan22Runner.run()` via `getattr(request, "num_frames", None) or 81` and the patched `VideoGenerateRequest.num_frames` field. The UI just needs to send the value.

For **FLUX (image)**, this section is hidden. Image generation has no duration.

#### QUALITY section (new)
Named buttons controlling **render detail and compute time**. The sublabel always says "to render" — never just a bare time — to prevent any confusion with clip length.

| Button | Sublabel |
|---|---|
| Fast | ~3 min to render |
| Standard | ~6 min to render |
| Cinematic | ~9 min to render |

Render time estimates are rough and multiply with clip length — the sublabels can be computed dynamically: `estimated_render_time(steps, num_frames, model)`. Until that function exists, use static approximations and mark them as approximate with `~`.

Maps to existing `quality_preset` setting (10 / 30 / 40 steps). The Preferences quality radio becomes secondary — or removed from Preferences now that it's surfaced here.

#### SHOT section (new)
A lightly bordered panel grouping model context and seed variation.

**Model row:**
- Live badge showing auto-detected active model (from `status_all()` + `/v1/models`) with green dot
- Resolution + approximate output size as context text (e.g., `720p · ~5 s`)
- If a second compatible model is also running: low-lift switcher text on the right — `"SkyReels also ready ›"`. Clicking it switches the active model for the next generation (does not restart anything). This selection persists in `app_settings` as `preferred_video_model`.
- If no server is running: badge shows `○ No server · Start one` which opens the Servers popover on click.

**Seed variation row:**
Three named buttons replacing the raw seed integer.

| Button | Behaviour |
|---|---|
| 🎲 New idea | seed = −1 (fully random each time) |
| 🔁 Repeat last | seed = last completed job's seed (read from history) |
| 📌 Keep this | seed = currently pinned value (sticky across runs) |

"Repeat last" is greyed out when history is empty (no past generations exist). It reads the seed from `history_store.all_records()` sorted by date — the most recent completed job.

### 4. Advanced Settings — moved to Generation menu

The current Advanced Settings accordion (negative prompt, steps SpinButton, seed SpinButton, guidance scale) moves out of the ControlPanel entirely and into a `Generation → Advanced…` dialog (or inline expander under the Generation menubar item).

Users who need raw access — a specific seed integer, exact step count, guidance scale, negative prompt — reach them there.

**Bidirectional sync with named controls:** The named buttons are a friendly view onto the same underlying values. If a user sets something in Advanced, the panel reflects it:

- **Seed:** If Advanced sets seed = 42 → "Keep this" button becomes active; badge shows the value (e.g. "📌 42"). "New idea" (−1) and "Repeat last" map to their own states naturally.
- **Steps:** If Advanced sets steps = 30 → "Standard" highlights (30 is the Standard preset). If steps = 25 (no named match) → a fourth "Custom" button appears, showing the raw value ("Custom · 25"). This way the panel is always an accurate readout, not a disconnected override.
- **Guidance scale / negative prompt:** No named representation — they only appear in the Advanced dialog. No indication shown in the panel.

The `PreferencesDialog` quality preset radio is redundant once Quality is in the panel — remove it from Preferences. The SkyReels frames dropdown in Preferences is also redundant — remove it.

### 5. Animate tab

`set_visible(False)` on the Animate toggle button. The `_source_animate` code path remains intact. When a working model ships, flip visibility.

### 6. Servers dropdown

`Servers ▾` stays in the toolbar for now (it's also a start/stop control, not just status). It does **not** merge into Generation menu — its function is operational, not creative. Revisit when server management is further simplified. Status bar popover stays as-is.

## What does not change

- Gallery / Curate zone — no changes in this spec
- TT-TV / Watch — no changes
- Playlists — no changes
- Status bar chip telemetry — no changes
- History store, worker, api_client — no structural changes (only num_frames threading through)
- `tt-ctl` CLI — no changes

## Implementation scope

| Area | Change |
|---|---|
| `main_window.py` — toolbar | Remove `_mdl_wan2_btn`, `_mdl_mochi_btn`, `_mdl_skyreels_btn`, `_img_model_sel_row`; hide Animate tab |
| `main_window.py` — ControlPanel | Add CLIP LENGTH buttons; add QUALITY buttons; add SHOT panel (model badge + switcher + seed variation); move thumbnail seed inline with Inspire row; remove Advanced Settings accordion |
| `main_window.py` — menus | Add Advanced… item under Generation menu opening a simple dialog |
| `app_settings.py` | Add `clip_length_preset` ("short"/"standard"/"long"), `preferred_video_model`, `seed_mode` ("random"/"repeat"/"keep"), `pinned_seed` |
| `app/worker.py` | Already has `num_frames` — no change needed |
| `patches/` | Wan2.2 `num_frames` already wired in `TTWan22Runner` + `VideoGenerateRequest` — no additional patch needed |
| `PreferencesDialog` | Remove quality radio (now in panel); remove SkyReels frames dropdown (now in panel) |

## Open questions

- **Render time estimates:** Static labels for now ("~X min to render"). A `(model, steps, frames) → minutes` lookup is achievable once benchmark data exists. Labels should update when Clip Length changes (longer clip = longer render time).
- **Multiple servers of same type:** Edge case — two Wan2.2 instances on different ports. Not in scope for this redesign.
- **"Custom" Quality button width:** The four-button row (Fast / Standard / Cinematic / Custom · N) may be too wide at 310 px. Consider hiding "Custom" and replacing the matching preset label with a dim badge showing the override value instead.
