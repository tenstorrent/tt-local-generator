# In-place result surfacing in Create

**Date:** 2026-07-13
**Branch:** `feat/pipeline-editor` (local; not merged)
**Status:** design approved (self-approved per user instruction)
**Principle:** "see what you created the instant it's done — never go find it"
(memory: project-see-result-immediately). First felt-experience win of the
"coherent shell" program (ahead of the status service + vestige retirement).

## Problem

After hitting **Create**, `_on_generate` drops a `PendingCard` into the *medium's
gallery* and `_on_finished` calls `gallery.replace_pending_with(record)` — so the
result lands in the Discover gallery and you must switch surfaces to see it. The
creative loop wants the result **right where you made it**, the moment it's done.

## Goal

Show the in-progress and finished result **inside the Create view**: a live
pending state resolving in place to the finished artifact the instant it
completes, plus a short recents strip of this session's Create outputs — while
still persisting every result to history/Discover exactly as today.

## Non-goals

- No change to generation internals (`_on_generate`, workers, API client).
- No true progressive/partial-frame preview mid-diffusion — the inference server
  doesn't stream frames. "Live" here = pending state (spinner + elapsed +
  progress text) → finished artifact. (A real progressive preview is a future
  item if/when the server supports it.)
- Not a replacement for the Discover gallery — results still save there.

## Global constraints

- **Persistence unchanged:** a Create result must still be written to the store
  and appear in the Discover gallery (the record path via the worker → store →
  gallery stays intact). The inline panel is *additional* immediate feedback.
- **Width discipline:** the two panes are responsive — side-by-side when wide,
  stacked when narrow — and every content row wraps; the surface can never
  overflow horizontally (it already sits in `gtk_layout.wrap_centered` + a
  vertical `ScrolledWindow`).
- **GTK threading:** all worker callbacks reach the panel via `GLib.idle_add`
  (main thread only), like the existing gallery path.
- **Palette:** tt-vscode-toolkit variant (`#4FD1C5`/`#0F2A35`); `_CSS` bytes
  literals ASCII-only (glyphs in Python strings).
- **Migration-safe:** non-Create generation (attractor/TT-TV/queue) is
  unaffected — those jobs still route to the gallery exactly as today.
- System python; tests via `xvfb-run … pytest`. Version bump + changelog on
  landing. Local only. Known cffi flake deselected in full-suite runs.

## Architecture

### `CreateResultPanel` (new widget)

Lives in `app/create_view.py` (or a small sibling module imported by it). Owns:

- **Current-result area:** renders one of three states —
  - *pending* — a spinner + elapsed timer + latest progress message (reuse
    `PendingCard`'s elapsed-timer approach; a lightweight local widget is fine
    if `PendingCard` is too gallery-coupled).
  - *finished* — the artifact inline: image thumbnail, looping video (reuse
    `GenerationCard`'s hover/loop stream logic), or text; plus quick actions
    (Save / Remix / Showcase) where they already exist.
  - *error* — the error message inline.
- **Recents strip:** a wrapping `Gtk.FlowBox` of this session's finished Create
  results (cap `_RECENTS_MAX = 6`, newest first). Clicking a recent shows it in
  the current-result area. Cleared on app restart (session-scoped; the durable
  archive is Discover).

API (called on the main thread):

```python
class CreateResultPanel(Gtk.Box):
    def show_pending(self, prompt: str, medium) -> None
    def show_progress(self, message: str) -> None
    def show_finished(self, record) -> None   # renders + prepends to recents
    def show_error(self, message: str) -> None
    def clear(self) -> None
```

### CreateView two-pane responsive layout

CreateView's content becomes a responsive split:
- **Form pane** — the current surface (doors, chips, scoped model dropdown,
  RoleZonePanel, CTA), unchanged in behavior.
- **Result pane** — the `CreateResultPanel`.
- Responsiveness: a container that lays the two side-by-side above a width
  breakpoint and stacks them (form first) below it — implemented with a
  `Gtk.FlowBox` (both panes as flow children, `min-children-per-line=1`,
  `max-children-per-line=2`) or an equivalent width-aware box, so it degrades
  without manual resize handling and never overflows. CreateView exposes
  `self._result_panel` for wiring.

### main_window wiring (Create-originated jobs)

- Add `self._create_job_active: bool` (or reuse a target ref). In
  `_on_create_generate`, before dispatch: `self._create_view._result_panel.show_pending(prompt, medium)` and mark the job Create-originated.
- `_on_generate`: when the job is Create-originated, **skip the gallery pending
  card** (the result panel owns the pending UI) — the finished record still gets
  added to the gallery/store on completion, so Discover is unchanged.
- `_on_progress` / `_on_finished` / `_on_error`: when the current job is
  Create-originated, ALSO call the panel's `show_progress` / `show_finished` /
  `show_error` (in addition to the existing store/gallery record handling).
  Clear the Create-originated flag in `_on_finished`/`_on_error`.

This keeps generation and persistence untouched; it only *also* renders into the
panel for jobs the user launched from Create.

## Data flow

Create CTA → `_on_create_generate(medium, params)` → mark Create-job + panel
`show_pending` → `_on_generate(...)` → worker → callbacks: `_on_progress` →
panel `show_progress`; `_on_finished(record)` → store/gallery (as today) **and**
panel `show_finished(record)` (prepend to recents); `_on_error` → panel
`show_error`. Non-Create jobs skip the panel entirely.

## Error handling

- A panel render that raises must never break generation — wrap panel calls so a
  bad record still completes the store/gallery path (the panel is best-effort
  feedback).
- Missing/unreadable artifact → the panel shows an honest placeholder (mirrors
  the gallery's no-fabrication rule), never a broken image.
- Video stream unavailable → show the poster/thumbnail (reuse GenerationCard's
  lazy-stream + retry pattern).

## Testing

- `CreateResultPanel`: `show_pending` renders the pending state; `show_finished`
  renders the artifact and prepends to recents; recents caps at 6 (oldest
  dropped); `show_error` renders inline; `clear` resets. (xvfb.)
- Responsive container: with two panes it exposes both; asserts a wrapping
  container (no unbounded horizontal Box) so it can't overflow.
- Wiring (source/behavioral): a Create-originated job calls the panel's
  show_pending/finished; `_on_generate` skips the gallery pending card for
  Create jobs; a non-Create job (e.g. attractor path) does NOT touch the panel
  and still uses the gallery pending card. The finished record still reaches the
  store/gallery in both cases (persistence invariant).

## File summary

| File | Change |
|---|---|
| `app/create_view.py` | `CreateResultPanel`; two-pane responsive layout; expose `_result_panel` |
| `app/main_window.py` | mark Create-originated jobs; `_on_generate` skips gallery pending for them; `_on_progress/_on_finished/_on_error` forward to the panel; persistence unchanged |
| `tests/…` | CreateResultPanel states + recents cap; responsive no-overflow; Create-vs-non-Create wiring + persistence invariant |
