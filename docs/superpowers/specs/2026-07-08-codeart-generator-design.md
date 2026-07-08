# codeart — code-as-art artgen generator (design)

Date: 2026-07-08
Status: approved (design), pending implementation plan

## Summary

A new artgen plugin, `codeart`, that generates **source code as art**: aesthetically
striking yet real code, steered by a free-text inspiration and an optional style.
It follows the existing single-pass `ArtGenerator` pattern (like `verse`) and is
auto-discovered into both the GUI artgen panel and the MCP tool surface — no wiring.

The distinguishing parameter is `should_compile`: a **prompt directive** asking the
model to make an earnest attempt at code that actually compiles/runs. It is *not*
enforced (no verify-and-retry loop). For Python only, the output is additionally
run through a safe, parse-only validation whose result is recorded as metadata.

## Goals / non-goals

Goals:
- Produce code artifacts from `(language, inspiration, style, should_compile)`.
- Default to an open, un-opinionated prompt; allow an optional `--style` bias.
- Report whether Python output actually parses, without ever executing it.
- Surface automatically in the GUI artgen panel and over MCP (plugin auto-discovery).

Non-goals (v1):
- No compile-verify-**retry** loop. `should_compile` is a directive, not a gate.
- No validation for non-Python languages (reported as "unvalidated").
- No execution of generated code, in any language, ever.
- No per-language output extension mapping (v1 saves `.py`). Noted as future work.
- No syntax-highlighted rendering (renders as a plain text artifact for now).

## Architecture

`plugins/codeart/plugin.py` defines `CodeArtGenerator(ArtGenerator)` and registers it
with `@register`. It **overrides `generate_artifact`** to pass a system prompt directly
to `call_fn`, exactly matching the established `verse` pattern:

```python
def generate_artifact(self, args, call_fn):
    system, user = _build_messages(args)         # module helper, unit-testable
    raw = call_fn(user, system=system)           # system passed directly
    return self.post_process(self.parse_output(raw, args), args)
```

`build_prompt(args)` returns just the **user** message (so `--simulate` still works); the
system prompt is built alongside it in `generate_artifact`.

### Base-class contract facts this design depends on

- `generate_artifact()` returns a **plain string** (the artifact source). There is no
  structured artifact/metadata channel. Extra metadata is communicated by stashing
  attributes on `args`, which the panel persists (see below).
- **System prompts are passed directly**, not via args. The base single-pass
  `generate_artifact` calls `call_fn(build_prompt(args))` with *no* system arg, so a
  generator that wants a system prompt must override `generate_artifact` and call
  `call_fn(user, system=system)` itself. `verse` does exactly this, and `call_fn` on
  every surface (GUI panel, `app/artgen/cli.py`, `mcp_server._make_call_fn`) accepts a
  `system=` kwarg — so this works identically in the GUI, the CLI, and over MCP. (No
  reliance on any `args`-stashed system prompt.)
- The GUI panel persists **all** args as record params: `params = vars(args).copy()`
  (`app/artgen_panel.py`). Underscore-prefixed attributes stashed on `args` (the
  established `verse`-family convention) therefore ride along into the saved record and
  can be displayed — this is how the Python-validation result is surfaced (below).
- The saved file extension comes from the class-level `output_ext` via
  `default_output()` (called with no args), so it cannot vary per run. v1 fixes it to
  `.py`.

## Parameters (`add_args`)

| Arg | Type / default | Meaning |
|---|---|---|
| `--language` | str, default `python` | Target language. Free-form; the model attempts it. Validation is Python-only in v1. |
| `--inspiration` | str, default `"the nature of recursion"` | Free-text theme / seed that steers the content. |
| `--style` | choice, default `auto` | `auto` = open prompt (no bias). Optional biases: `quine`, `ascii`, `poem`, `oneliner`, `glitch`, `unusually_verbose`, `function_oriented`. |
| `--should-compile` | bool, default `True` | `argparse.BooleanOptionalAction` → `--should-compile` / `--no-should-compile`. Prompt directive only; never enforced. |

### Style presets

- `auto` — no style guidance; `inspiration` + `language` do all the steering.
- `quine` — a program that prints its own source.
- `ascii` — source whose *visual layout* forms a shape/ascii art while staying valid.
- `poem` — code that reads like a poem (identifiers, strings, structure) yet is valid.
- `oneliner` — a single dense, elegant line.
- `glitch` — obfuscated-but-beautiful / cryptic yet valid.
- `unusually_verbose` — deliberately, expressively over-verbose: long descriptive
  identifiers, explicit intermediate variables, narrative comments — verbosity as the
  aesthetic, while remaining valid.
- `function_oriented` — decompose into many tiny, well-named functions so that the
  top-level *sequence of calls* reads as the art (the composition/invocation is the
  poem); each function is small and single-purpose.

## Prompt construction

- **System prompt (base):** instructs the model to write source code *as an art form* —
  aesthetically striking but real code; output only the code, no prose/markdown fences.
- **Style guidance:** if `style != auto`, append the preset's specific instruction.
- **should_compile directive:** if set, append a line such as: *"The code must be valid,
  complete {language} that compiles and runs as-is — make an earnest, correct attempt."*
  If unset, allow aesthetics to take precedence over strict correctness.
- **User message (`build_prompt`):** carries `language` and `inspiration` (and restates
  the style intent), e.g. *"Write {language} code as art on the theme: {inspiration}."*
- The system prompt is passed **directly** via `call_fn(user, system=system)` inside the
  `generate_artifact` override (matching `verse`) — works in the GUI, CLI, and MCP.

## Output & validation

- `parse_output`: base default (strip ``` fences and surrounding whitespace); also strip
  `<think>…</think>` blocks if present (some models emit them), consistent with other
  generators. Result is **pure source code** — no injected header comments (so quines and
  one-liners are not broken).
- `post_process`: if `language.strip().lower()` starts with `python`, run a safe
  validator; otherwise mark unvalidated. Returns the artifact **unchanged**.
- `validate_python(src) -> (ok: bool, error: str | None)`: uses `ast.parse(src)` inside
  try/except `SyntaxError`. **Parse-only; never `exec`/`eval`/`compile`-to-run.** No file
  writes, no imports of the target code.
- Results stashed on `args`:
  - `args._codeart_compiles` = `True` / `False` / `None` (non-Python / unvalidated)
  - `args._codeart_error` = the SyntaxError message when `False`, else `None`
  These persist into the saved record params (via `vars(args)`).
- GUI: after generation the status line shows a compile note (e.g. `✓ compiles` /
  `✗ SyntaxError: …`) derived from the stashed flags. (Small addition in the panel's
  post-generation status handling; details deferred to the plan.)

## Output artifact

- `output_ext = ".py"` (v1). Saved as a text file, thumbnailed and recorded like other
  text artgen artifacts; rendered via the existing attractor text path. No new rendering.

## Error handling

- LLM/transport errors surface through the existing panel/CLI paths (unchanged).
- Empty or fence-only responses → `parse_output` yields empty string; treated as a normal
  (empty) artifact, same as other generators. (No special-casing in v1.)
- `validate_python` never raises out of `post_process`: any unexpected exception is caught
  and treated as "unvalidated" (`None`) rather than failing generation.

## Testing

Unit tests (mirroring `tests/test_artgen_generators.py` style, mocked `call_fn`). A
module-level `_build_messages(args) -> (system, user)` helper builds both prompts and is
called directly in tests:
- `build_prompt` (the user message) includes `language` and `inspiration`; reflects `style`.
- `_build_messages`: the system prompt includes the should_compile directive when set and
  omits it when unset; `style != auto` adds the preset guidance, `auto` adds none.
- `--style` choices are wired; `auto` adds no style guidance.
- `parse_output` strips markdown fences and `<think>` blocks → clean source.
- `validate_python`: valid code → `(True, None)`; invalid → `(False, <msg>)`.
- `post_process` stashes `_codeart_compiles`/`_codeart_error` for Python; leaves `None`
  for a non-Python `--language`; returns the artifact unchanged in all cases.
- End-to-end `generate_artifact(args, call_fn)` with a mocked `call_fn` returns clean
  source and sets the compile flags.

## Future work (explicitly out of scope for v1)

- Per-language output extension mapping (`.rs`, `.c`, `.js`, …) — needs a way to vary the
  saved extension per run (base `default_output()` currently takes no args).
- Validation for compiled languages via compile-only toolchain calls (sandboxed, timed,
  never executed), behind a "toolchain present" check.
- Optional verify-and-retry loop (feed the SyntaxError back and regenerate).
- Syntax-highlighted rendering in the reading/attractor view.
```
