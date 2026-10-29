# Music prompt library (design)

Date: 2026-10-28. Branch: `music-prompts` (stacked on `generate_music`, PR #30). Status: written for the operator's review; nothing is built.

Prompt (operator): "let's start working on a musical prompt generation library. Everyone loves all kinds of music. I would love some prompts that
get to the heart of post-rock like Tortoise, Yume Bitsu, The Sea and Cake. I'd like jazz with a be-bop flavor. Or many flavors. Funk like Parliament.
Funk like Funkadelic. Funk like Parliament-Funkadelic. Tangerine Dream. Ash Ra Tempel. Kraftwerk. Ratcheting synthesizers. Chiptunes. Synthpop. The songbook
of the grateful dead. someone playing an accordion in the moonlight. someone playing the bag pipes on the beach at sunset."

Decisions from the conversation are marked **(decided)**. Anything marked **(my call)** was chosen while writing and is open to change.

## 1. Intent

Someone types what they want ("tortoise-like song but someone is singing a bluegrass song on top"), or picks it from menus and chips, or clicks the magic wand
for a combination they have never heard. The library turns that into a great prompt for the selected music model. **(decided)** The user does not need to know
what makes a style sound the way it does. The library knows, and says it in words a music model responds to.

## 2. Goals, non-goals, anti-goals

Goals
- **Style cards**: a data library of styles, artists, scenes, instruments and vocal approaches, each with plain-words descriptions of the sound. Artist names are
  kept as inspiration; the prompt carries the description of the sound, not the name. **(decided)**
- **A translator** that reads typed text, finds known names and styles, replaces each with its sound description, keeps the rest of what the user wrote, and
  handles layers ("X but someone is singing Y on top"). Unrecognised text passes through. **(decided)**
- **Menus and chips** built from the same cards, so picking and typing produce the same kind of prompt. **(decided)**
- **The wand**: one click gives a combination of musical aspects drawn from distant corners of the library. **(decided)**
- **One output per model dialect.** MiniMax gets a descriptive caption plus lyrics; Stable Audio gets one tight tagged prompt. The translator says what the
  chosen model cannot do (Stable Audio cannot sing) and adapts the prompt. **(my call: dialects; the example outputs in section 5 were accepted by the operator)**
- A first set of cards built from the operator's list: post-rock (Tortoise, Yume Bitsu, The Sea and Cake), bebop, Parliament, Funkadelic, Parliament-Funkadelic,
  Tangerine Dream, Ash Ra Tempel, Kraftwerk, ratcheting synthesizers, chiptunes, synthpop, the Grateful Dead songbook, accordion in the moonlight, bagpipes on the
  beach at sunset, plus a bluegrass vocal layer and enough neighbours to make the wand interesting. **(decided: list; my call: neighbours)**
- Users can add their own cards by dropping files in a folder. **(my call; the operator did not say.)**

Non-goals
- Generating audio, analysing audio, or checking how a model actually sounds against a card.
- Imitating a real artist's voice. Cards describe sound and instrumentation. They do not ask for a named person's voice. The MiniMax card says not to imitate a real
  person's voice.
- Training or fine-tuning anything.
- A full music-theory engine. Cards carry tempo ranges, keys and moods as hints.

Anti-goals (a failure even if it technically works)
- An artist name appearing in the output prompt without the user asking for it. A prompt that is just the name with a style tag is the failure this library exists to avoid.
- Descriptions that are wrong about the music (a wrong instrument, a wrong era). Cards are reviewed by the operator before they ship.
- A wand that returns mush: five styles stacked that no one could play together.
- A translator that quietly rewrites what the user typed beyond the recognised names. What it adds is shown, and the user can edit the result.
- Output that exceeds a model's text limits.

## 3. What exists

- `app/generate_prompt.py` and `app/word_banks.py`: the three-tier prompt generator (algorithmic word banks, Markov, LLM polish) for video, image, animate, skyreels.
- `app/prompt_client.py` (music PR): prompt types `music` and `lyrics`, `music_llm_available()`, a lyrics system prompt that asks for `[Verse]`/`[Chorus]` tags.
- `app/config/prompt_chips.yaml`: a `music` chip bank (Genre, Mood, Instruments, Tempo, Era, "In the style of" free text with `{}` placeholder, Surprise chips).
- `app/music_runtime.py`: `MusicProfile` per model (`text_fields`, `has_lyrics`, duration, steps, `triggers`, `credit`, and wire names) and the MiniMax and Stable Audio profile table.
- The Music panel: a prompt, lyrics, chips, an Inspire button on prompt and lyrics, and a `prompt_getter` seam.
- Model facts (music PR wire notes): MiniMax takes caption and lyrics, and the song ends when the lyrics do. Stable Audio takes one prompt and no lyrics.

## 4. Design

### 4.1 Package `app/music_prompts/` (stdlib plus PyYAML where the project already uses it; no GTK)

- `cards.py`: `StyleCard` dataclass and loader. Data lives in `app/music_prompts/cards/*.yaml` (shipped) and `~/.config/tt-local-generator/music_styles/*.yaml`
  (user cards; a user card with the same `id` overrides a shipped one). A bad file is logged and skipped. **(my call)**
- `parse.py`: finds known names in text and splits layers.
- `compose.py`: renders the parsed result for a dialect.
- `wand.py`: picks a combination.
- `dialects.py`: one renderer per output kind, selected from the model's `MusicProfile`.
- `__init__.py`: the public functions in section 4.5.

### 4.2 A style card

Fields **(my call)**:
- `id`, `kind` (`style | artist | scene | instrument | vocal | texture | era`), `name`, `aliases` (including artist names, misspellings, and phrases like "bagpipes"),
  `family` (a cluster tag used by the wand: `post-rock`, `funk`, `electronic`, `folk`, `jazz`, `chip`, ...), `inspiration` (the artists and records the card draws on, shown
  in the UI and never put in a prompt by default),
- `sound` (one to three sentences of plain description, no artist names), `instruments` (list), `rhythm`, `harmony`, `mood`, `production`, `tempo_bpm` (range), `keys` or modes (optional),
- `avoid` (things to leave out), `lyric_themes` (for vocal cards and styles that sing), `vocal` (how the voice sounds, when the card is a vocal style),
- `tags` (short tag forms for tag-style prompts), `goes_with` and `clashes_with` (family or id lists the wand uses), and `notes` (curation notes).
Artist cards and style cards link: an artist card names the style card it points to and adds what is specific to that artist.

### 4.3 The translator

`translate(text, profile, options) -> Translation`:
1. Normalise and tokenise; find card matches by alias (longest match first, case and punctuation insensitive; "tortoise-like", "tortoise-ish" and "like tortoise" all match `tortoise`).
2. Split layers on markers ("but", "over", "on top", "with", "plus", "and someone is singing", "meets", "mixed with"), so "tortoise-like song but someone is singing a bluegrass song on top"
   becomes a base (post-rock, from the Tortoise card) and a vocal layer (bluegrass vocal). A card of kind `vocal` or text about singing sets the vocal layer.
3. Resolve each layer to its card sound, then compose: base sound first, layers after, the user's own unmatched words kept in place, and tempo/key chosen from the cards (the base wins on conflict).
4. Render for the dialect (4.4). Produce `notes` (what the model cannot do, what was added, what was dropped) and `inspired_by` (names, for the UI only).
5. Optional LLM pass (`prompt_client`): smooth the prose, never add names. The library works without it.

### 4.4 Dialects

Chosen from the `MusicProfile`:
- **caption+lyrics** (MiniMax): a descriptive caption in sentences, and a lyrics draft when a vocal layer exists. Lyrics come from the LLM when one is reachable. Without one, a skeleton from the card's `lyric_themes`
  with `[Verse]`/`[Chorus]` tags. Instrumental requests get no lyrics. The caption respects the model's character limit.
- **tags** (Stable Audio): one comma-separated prompt: style tags, instruments, texture, tempo, key. A vocal layer becomes a lead-instrument line and a note says so.
- **generic** (unknown model): one plain sentence.
The profile's `triggers` (for example `fiona crapple`) can be appended when the user asks. The credit line is unchanged.

### 4.5 Public API

```
load_library() -> Library
translate(text: str, profile: MusicProfile | None, *, include_names: bool = False, library: Library | None = None) -> Translation
wand(profile: MusicProfile | None, *, seed: int | None = None, base: str | None = None, library: Library | None = None) -> Translation
menu_tree(library: Library | None = None) -> list[MenuGroup]      # for menus and chips
```
`Translation(prompt: str, lyrics: str | None, notes: tuple[str, ...], inspired_by: tuple[str, ...], used_cards: tuple[str, ...], dialect: str)`.
`include_names=True` puts inspiration names in the prompt; it is off by default.

### 4.6 The wand

Picks two or three cards from different `family` values that do not appear in each other's `clashes_with`, adds one scene or texture card, one tempo and one production note, and renders them through `translate`.
`seed` makes it repeatable; a fresh seed each click. A `base` text keeps what the user already typed and adds one surprising layer. **(my call: families and clash lists are curated data, not computed.)**

### 4.7 In the app and the CLI

- Music panel: a **magic wand** button beside the prompt, a **Write the prompt** action that translates what is typed, a **Styles** menu and chips generated from `menu_tree()` (they sit beside the hand-written music bank in `prompt_chips.yaml`; retiring overlapping hand-written chips is a later change, see section 10), a preview showing "Inspired by: ..." and the model notes, and an undo that restores the typed text. The result fills the prompt and, when the dialect has lyrics, the lyrics box. **(my call: layout)**
- `tt-ctl music-prompt "<text>" [--model <id>] [--wand] [--seed N] [--json]`. **(my call)**
- The existing Inspire button keeps working; its music type calls the library first and the LLM second.

## 5. The operator's example, as accepted

Input: "tortoise-like song but someone is singing a bluegrass song on top".
MiniMax caption: "Patient post-rock in the Chicago tradition: interlocking clean electric guitars and vibraphone, dub-weighted bass, loose, jazz-inflected drums, slow-building crescendos, warm analog room sound.
Over it, a bluegrass vocal: high lonesome lead with tight close harmony, plain-spoken and a little twang, dry and forward in the mix. Around 90 bpm." plus a lyrics draft about a long road home.
Stable Audio: "post-rock, interlocking clean guitars, vibraphone, dub bass, jazz-inflected drums, slow build, plaintive dobro carrying a high-lonesome melody, warm analog, 90 bpm, major key", with a note that the model cannot sing.
These are fixtures for the golden tests; the wording is the builder's within these meanings.
(Operator approved 2026-10-28: "loose, jazz-inflected drums" in place of "brushed drums", and "dobro" in place of "pedal steel", following the reviewed Tortoise and bluegrass vocal cards.)

## 6. Constraints

- No GTK in `music_prompts`. The Music panel and `tt-ctl` call it.
- Cards are data, not code. Descriptions are written fresh and checked against reputable sources (the builder cross-checks each card with at least two sources and records them in the card's `notes`).
- No artist name in a rendered prompt unless `include_names=True`. A test scans every shipped card's rendered output for every alias of every artist card.
- Prompt length limits come from the model profile (MiniMax caption limit, Stable Audio `MAX_PROMPT_CHARS`); output is trimmed by dropping the lowest-priority clauses, never by cutting mid-word.
- The operator reviews the first card set before it ships.
- Version: new user-visible feature, minor bump on its own commit at the branch tip.

## 7. Left to the builder

- Exact wording of every card's sound description, within the card fields above.
- The marker list for layer splitting and how fuzzy the matching is.
- How the wand scores a combination and the number of cards per pick.
- Menu layout and copy.

## 8. Open facts to confirm early

1. The MiniMax caption length limit and the phrasing it responds best to. A few real clips would tell, and the servers are not up now, so the first cards are written from the model cards and the operator listens later.
2. Whether Stable Audio responds better to tags or sentences (the demo prompts in its repo are the starting point).
3. How the existing Inspire and chip code reads `prompt_chips.yaml`, so the generated menus replace the overlapping hand-written categories cleanly.
4. Which neighbour cards the wand needs for variety (aim for about 40 cards in the first set).

## 9. Testing

- Golden tests on the operator's example sentence for each dialect, and on short inputs for every card in the operator's list.
- Translator: alias matching (case, hyphens, "-like", "-ish"), longest match, layer splitting for each marker, unknown text unchanged, empty input, a name that is also a word, two names in one layer.
- No-names test across all shipped cards and dialects; the `include_names=True` path includes them.
- Wand: always at least two families, never a `clashes_with` pair, repeatable with a seed, different without one, always non-empty, respects the length limit.
- Dialects: lyrics only when the profile has them, a vocal layer turns into a lead-instrument line with a note for a no-lyrics model, trigger phrase only on request.
- Card loader: a bad user card is skipped with a log line, a user card overrides a shipped one, every shipped card has the required fields, `goes_with`/`clashes_with` reference real ids or families.
- Each new test is seen failing against the broken or absent code before it is trusted.

## 10. Calls made after the builder check (my calls, open to change)

- **Length limits.** `MusicProfile` gains `max_chars: int | None` (a small change to `music_runtime.py`, on this stacked branch). Stable Audio: 2000 (`MAX_PROMPT_CHARS`).
  MiniMax: 1000 for the caption only, marked unconfirmed in the profile table until a live clip shows the real limit. Lyrics are never trimmed. `None` means no limit known: use 1000.
- **Trimming order.** Drop from the end of this list, never past the first three: the user's own unmatched words, the base style sound, the vocal layer, instruments, tempo and key, production, mood, `avoid`, texture.
  The user's words and the base sound are never dropped. If they alone exceed the limit, the prompt is returned over the limit with a note.
- **Determinism.** `translate` and `wand` are deterministic and make no LLM call by default (`wand` is deterministic for a given `seed`). `smooth=True` adds the LLM pass. The CLI and golden tests never set it. The panel's Write-the-prompt action has a
  "Smooth with AI" checkbox, off by default. Lyrics are a separate step: an LLM writes them when one is reachable, and a skeleton from the card's `lyric_themes` is used otherwise. Tests stub the lyrics writer.
- **What the UI shows.** `Translation` carries `added: tuple[str, ...]` (the phrases the cards contributed) beside `notes`. The panel lists them under the prompt. Span-level highlighting is not built.
- **Wand and models without lyrics.** For a dialect with no lyrics, vocal-kind cards are left out of wand picks, so the wand never asks Stable Audio to sing. For a lyrics dialect a vocal layer is allowed. `profile=None` means the generic dialect. With no server running, translate and the wand still work (generic dialect), and the panel says "pick a server to tune the prompt for a model".
- **Chips.** The generated Styles menu is new and sits beside the existing `music` bank in `prompt_chips.yaml`. The existing free-text "In the style of {}" chip passes the typed value through `translate` when the chip is applied, so a typed artist name becomes its sound description at that moment and the user sees it in the pills. Other chips append their text at generate time, as today.
  Write the prompt translates the typed prompt text only. It does not touch chips.
- **Authoring and the review gate.** Build the loader and tests first, then about 15 cards (the operator's list plus the bluegrass vocal layer), then stop for the operator's review of those cards, then the roughly 25 neighbour cards.
  The builder writes each card from its own knowledge and, where it has web access, cross-checks it against at least two sources and records them in `notes`. A card it could not cross-check carries `notes: unverified`, and the review lists those first.
- **Ambiguous words.** A card can set `ambiguous: true` and `cues: [...]` (words that must appear nearby, such as "band", "album", "song", "style"). An ambiguous alias ("tortoise", "sea", "funk") matches only inside a longer alias, or with a cue within five words, or when capitalised in mid-sentence.
  Longest match wins: "Parliament-Funkadelic" beats "Parliament" and "Funkadelic"; "Parliament" alone matches the Parliament card; "funk" alone matches the generic funk style card.
- **CLI.** `tt-ctl music-prompt --model <id>` resolves the id with the profile table in `music_runtime.py` (no server probe) and falls back to the generic dialect. `--dialect caption+lyrics|tags|generic` overrides it.
