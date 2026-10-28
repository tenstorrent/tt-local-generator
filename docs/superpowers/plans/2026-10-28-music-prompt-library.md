# Music Prompt Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A library that turns typed text, menu and chip picks, or a magic-wand click into a great prompt for the selected music model, using descriptions of how styles sound and keeping artist names as inspiration only.

**Architecture:** `app/music_prompts/` holds style cards (YAML data), a parser (alias matching and layer splitting), a dialect renderer (MiniMax caption plus lyrics, Stable Audio tags, generic), a translator, and a wand. The Music panel and `tt-ctl music-prompt` call it. It is GTK-free.

**Tech Stack:** Python 3 stdlib, PyYAML (already used by `chip_config`), GTK4/PyGObject for the panel, pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-music-prompt-library-design.md` (section 10 holds the calls made after review).

**Where to work:** the worktree `/home/ttuser/code/tt-local-generator-music-prompts`, branch `music-prompts`, stacked on `generate_music` (PR #30). Do not touch `/home/ttuser/code/tt-local-generator` (another branch is being built there).

## Global Constraints

- `app/music_prompts/` imports only the standard library, PyYAML and `music_runtime` (for `MusicProfile`). No GTK. The LLM is reached only through an injected callable.
- An artist name or other `inspiration` entry never appears in a rendered prompt unless `include_names=True`. The default is off. **(decided)**
- The user's own unmatched words are never dropped or rewritten, and trimming never removes them or the base style's sound. If they alone exceed the limit, the prompt is returned over the limit with a note.
- `translate` and `wand` are deterministic and make no LLM call by default (`wand` is repeatable for a given `seed`). `smooth=True` and the lyrics writer are the only LLM paths, and tests stub them.
- Lyrics are produced only for a dialect with lyrics. A model that cannot sing gets the vocal layer rewritten as a lead-instrument line and a note says so.
- Length limits come from `MusicProfile.max_chars` (Stable Audio 2000, MiniMax caption 1000 unconfirmed, unknown 1000). Output is trimmed by dropping the lowest-priority clauses in this order from the end: user's own words, base style sound, vocal layer, instruments, tempo and key, production, mood, `avoid`, texture. Never mid-word.
- Cards are data in `app/music_prompts/cards/*.yaml`; user cards in `~/.config/tt-local-generator/music_styles/*.yaml` override shipped cards with the same `id`. A bad file is logged and skipped. Tests never read the real home directory.
- Card text is written fresh in plain words. The builder cross-checks each card against at least two sources when it has web access and records them in `notes`; otherwise `notes: unverified`.
- The existing `music` bank in `app/config/prompt_chips.yaml` stays. The Styles menu sits beside it.
- Existing tests keep passing. `collect()` of every non-music panel stays byte-identical. GTK calls only on the main thread; LLM or file work runs off it and reports through `GLib.idle_add`. `_CSS` byte literals stay ASCII-only.
- Version: new user-visible feature, minor bump as the branch tip's own commit (Task 9).

## Review Focus

1. An artist name leaking into a rendered prompt: pinned by a test that renders every shipped card in every dialect and layer position and scans for every alias of every artist card and every `inspiration` entry. Task 3 and again in Tasks 5 and 6 as cards are added.
2. The user's own words: "make it feel like a rainy Tuesday, Tortoise-like" keeps "rainy Tuesday" verbatim and replaces only "Tortoise-like". Trimming never drops it. Task 3.
3. The wand on a no-lyrics model never includes a vocal card, never picks a `clashes_with` pair, and is repeatable by seed. Task 4.
4. A bad or overriding user card file never breaks loading: invalid YAML, a card missing required fields, a duplicate id, a `goes_with` pointing at nothing. Task 1.
5. Ambiguous words: "the tortoise crossed the road" and "I need some funk in my soul"-style sentences do not match; "Tortoise-like" and "a Tortoise record" do. Task 2.
6. Output length: unknown limits, a limit smaller than the base sound, and unicode text. Task 3.

---

### Task 1: Style cards, schema and loader

**Files:**
- Create: `app/music_prompts/__init__.py`, `app/music_prompts/cards.py`, `app/music_prompts/cards/` with three starter cards (`post-rock.yaml`, `tortoise.yaml`, `bluegrass-vocal.yaml`), `app/music_prompts/CARDS.md` (the schema and how to add a card), `tests/test_music_cards.py`

**Interfaces:**
- Produces (in `cards.py`):
  - `@dataclass(frozen=True) StyleCard(id: str, kind: str, name: str, aliases: tuple[str, ...], family: str, inspiration: tuple[str, ...], sound: str, instruments: tuple[str, ...], rhythm: str, harmony: str, mood: str, production: str, tempo_bpm: tuple[int, int] | None, avoid: tuple[str, ...], lyric_themes: tuple[str, ...], vocal: str, tags: tuple[str, ...], goes_with: tuple[str, ...], clashes_with: tuple[str, ...], ambiguous: bool, cues: tuple[str, ...], parent: str | None, notes: str)` with `kind in {"style","artist","scene","instrument","vocal","texture","era"}`.
  - `@dataclass class Library(cards: dict[str, StyleCard], by_alias: dict[str, tuple[str, ...]])` with `card(id) -> StyleCard | None`, `families() -> tuple[str, ...]`, `of_kind(kind) -> list[StyleCard]`.
  - `validate_card(raw: dict) -> list[str]` (error messages; empty means valid; required fields: id, kind, name, family, sound for every kind; `parent` required for kind `artist`).
  - `load_library(shipped_dir: Path | None = None, user_dir: Path | None = None) -> Library` (shipped dir defaults to `app/music_prompts/cards`; user dir defaults to `~/.config/tt-local-generator/music_styles` and may be absent; a user card with the same `id` overrides; bad files and cards are logged and skipped; `goes_with`/`clashes_with` entries that name nothing are logged and kept as plain family tags).

- [ ] **Step 1: Write the failing tests** (`tests/test_music_cards.py`): `test_load_library_reads_shipped_cards`, `test_validate_card_reports_missing_required_fields`, `test_artist_card_requires_a_parent_style`, `test_user_card_overrides_a_shipped_card_with_the_same_id` (tmp dirs), `test_bad_yaml_file_is_skipped_and_logged`, `test_card_missing_fields_is_skipped_and_others_load`, `test_duplicate_id_within_one_directory_keeps_the_first_and_logs`, `test_missing_user_dir_is_fine`, `test_aliases_are_indexed_case_and_hyphen_insensitively` (`by_alias` keys are normalised lowercase with hyphens and underscores read as spaces), `test_every_shipped_card_passes_validate_card`, `test_unknown_goes_with_id_is_logged_not_fatal`.
- [ ] **Step 2: Run** `/usr/bin/python3 -m pytest tests/test_music_cards.py -q`. Expected: FAIL (package missing).
- [ ] **Step 3: Implement** `cards.py` with the existing YAML loading style from `app/chip_config.py`. Write the three starter cards from the spec's example (post-rock style, a Tortoise artist card with `parent: post-rock` and descriptive `sound` that never names the artist, a bluegrass vocal card with `lyric_themes`) and `CARDS.md`.
- [ ] **Step 4: Run the tests.** Expected: PASS. Mutate the override precedence on a copy and see `test_user_card_overrides...` fail; restore.
- [ ] **Step 5: Commit** `git commit -m "feat(music-prompts): style cards and loader"`.

### Task 2: Parser

**Files:**
- Create: `app/music_prompts/parse.py`, `tests/test_music_parse.py`

**Interfaces:**
- Consumes: `Library`, `StyleCard`.
- Produces: `@dataclass(frozen=True) Match(card_id: str, start: int, end: int, text: str)`; `@dataclass(frozen=True) Layer(role: str, text: str, matches: tuple[Match, ...], marker: str | None, vocal: bool)` with `role in {"base","layer"}`; `@dataclass(frozen=True) Parsed(text: str, layers: tuple[Layer, ...], leftover: tuple[str, ...])` (`leftover` is the user's words not covered by a match, in order); `LAYER_MARKERS: tuple[str, ...]` (`but`, `over`, `on top`, `with`, `plus`, `and someone is singing`, `meets`, `mixed with`, `layered with`); `parse(text: str, library: Library) -> Parsed`.
- Rules (spec 4.3 and section 10): case, punctuation and hyphen insensitive alias matching; `-like`, `-ish`, `like X` and `X-style` suffix/prefix forms match the card; longest match wins ("Parliament-Funkadelic" beats "Parliament"); an `ambiguous` card's alias matches only inside a longer alias, or with one of its `cues` within five words, or when capitalised in mid-sentence; a vocal-kind match, or text about singing, sets `Layer.vocal`; text before the first marker is the base layer and each later segment is a layer; markers inside a matched alias are not split points.

- [ ] **Step 1: Write the failing tests**: `test_example_sentence_splits_into_base_and_vocal_layer` ("tortoise-like song but someone is singing a bluegrass song on top": base matches `tortoise`, one vocal layer matches the bluegrass card, leftover keeps "song"), `test_alias_matching_ignores_case_and_hyphens` ("Tortoise", "tortoise", "TORTOISE", "tortoise-ish", "like Tortoise"), `test_longest_match_wins` (needs a P-Funk fixture library in the test), `test_ambiguous_word_without_cue_does_not_match` ("the tortoise crossed the road"; Review Focus 5), `test_ambiguous_word_with_cue_or_capital_matches` ("a Tortoise record", "I like Tortoise"), `test_marker_inside_an_alias_is_not_a_split` ("The Sea and Cake" with a fixture alias containing "and"), `test_each_marker_splits` (parametrised over `LAYER_MARKERS`), `test_unknown_text_has_no_matches_and_is_all_leftover`, `test_empty_and_whitespace_input`, `test_unicode_and_punctuation_are_kept_in_leftover`, `test_two_names_in_one_layer`, `test_user_words_order_is_preserved_in_leftover`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** with a tokenizer that records character offsets so leftovers keep the user's exact words. Use a small fixture library inside the tests; do not depend on the shipped card set.
- [ ] **Step 4: Run the tests.** Expected: PASS. Break the longest-match rule and watch its test fail; restore.
- [ ] **Step 5: Commit** `git commit -m "feat(music-prompts): parser"`.

### Task 3: Dialects, `translate`, length limits, profile fields

**Files:**
- Create: `app/music_prompts/dialects.py`, `app/music_prompts/translate.py`, `tests/test_music_translate.py`
- Modify: `app/music_prompts/__init__.py` (public API), `app/music_runtime.py` (`MusicProfile` gains `max_chars: Optional[int] = None` and `dialect: str = ""` at the end with defaults; the PROFILE_TABLE entries set MiniMax `max_chars=1000, dialect="caption+lyrics"` and Stable Audio `max_chars=2000, dialect="tags"`; GENERIC unchanged), `tests/test_music_runtime.py` (new fields)

**Interfaces:**
- Consumes: `Parsed`, `Library`, `MusicProfile`.
- Produces (`dialects.py`): `dialect_for(profile: MusicProfile | None) -> str` (`profile.dialect` if set, else `"caption+lyrics"` when `has_lyrics`, else `"generic"`); `@dataclass(frozen=True) Clause(kind: str, text: str, priority: int)`; `render(clauses, dialect: str, max_chars: int | None) -> tuple[str, tuple[str, ...]]` (prompt and notes; trims per the global order).
- Produces (`translate.py`): `@dataclass(frozen=True) Translation(prompt: str, lyrics: str | None, notes: tuple[str, ...], inspired_by: tuple[str, ...], used_cards: tuple[str, ...], dialect: str, added: tuple[str, ...])`; `translate(text: str, profile: MusicProfile | None, *, include_names: bool = False, library: Library | None = None, smooth: bool = False, smooth_fn: Callable[[str], str | None] | None = None, lyrics_fn: Callable[[str, tuple[str, ...]], str | None] | None = None) -> Translation`.
- Public API in `__init__.py`: `load_library`, `translate`, `wand` (Task 4), `menu_tree` (Task 4), `Translation`, `dialect_for`.
- Behaviour: base sound first, layers after, the user's own leftover words kept in place; tempo and key from the cards (base wins); `caption+lyrics` renders sentences and, when a vocal layer exists and the profile has lyrics, a lyrics draft from `lyrics_fn` (the LLM, stubbed in tests) or a skeleton from the vocal card's `lyric_themes` with `[Verse]`/`[Chorus]` tags; `tags` renders one comma-separated line and, when the profile has no lyrics and a vocal layer exists, writes the vocal as a lead-instrument line plus a note ("this model cannot sing; the vocal became a lead line"); `generic` is one plain sentence; `profile.triggers` are appended only when the caller passes `include_names=True`... no: triggers are appended when `text` contains the trigger phrase already (the user asked for it), never automatically; `smooth=True` runs `smooth_fn` (never adds names, falls back to the unsmoothed prompt when it returns None).

- [ ] **Step 1: Failing tests**: `test_example_sentence_caption_dialect` (golden meaning, not wording: post-rock sound terms from the Tortoise card and a bluegrass vocal description appear; lyrics start with `[Verse` ; spec section 5), `test_example_sentence_tags_dialect_turns_the_vocal_into_a_lead_line_with_a_note`, `test_generic_dialect_is_one_sentence`, `test_no_artist_name_in_any_rendered_prompt` (Review Focus 1: iterate every shipped card x every dialect x base and layer position; scan for every alias of every artist-kind card and every `inspiration` entry), `test_include_names_true_puts_inspiration_in_the_prompt`, `test_users_own_words_are_kept_verbatim` (Review Focus 2: "rainy Tuesday, Tortoise-like" keeps "rainy Tuesday"), `test_trim_never_drops_user_words_or_base_sound` (Review Focus 6; tiny max_chars returns over limit with a note), `test_trim_order_drops_avoid_and_texture_first`, `test_trim_never_cuts_mid_word`, `test_unknown_limit_uses_1000`, `test_unicode_text_is_kept`, `test_unknown_text_passes_through_unchanged_with_a_note`, `test_translate_is_deterministic_without_smooth`, `test_smooth_runs_only_when_asked_and_never_adds_names`, `test_lyrics_fn_none_falls_back_to_a_skeleton_from_lyric_themes`, `test_instrumental_request_has_no_lyrics`, `test_trigger_phrase_only_when_the_user_typed_it`, `test_dialect_for_uses_profile_dialect_then_has_lyrics_then_generic`, plus in `tests/test_music_runtime.py` `test_profile_table_sets_max_chars_and_dialect`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Keep `music_runtime.py` changes minimal and backward compatible (positional construction in existing tests must still work).
- [ ] **Step 4: Run these tests plus `tests/test_music_runtime.py tests/test_music_cards.py tests/test_music_parse.py`.** Expected: PASS. Remove the name-scrubbing on a copy and watch the no-names test fail; restore.
- [ ] **Step 5: Commit** `git commit -m "feat(music-prompts): dialects, translate, length limits"`.

### Task 4: Wand and menu tree

**Files:**
- Create: `app/music_prompts/wand.py`, `app/music_prompts/menu.py`, `tests/test_music_wand.py`
- Modify: `app/music_prompts/__init__.py`

**Interfaces:**
- Produces: `wand(profile: MusicProfile | None, *, seed: int | None = None, base: str | None = None, library: Library | None = None, smooth: bool = False, smooth_fn=None, lyrics_fn=None) -> Translation`; `@dataclass(frozen=True) MenuItem(card_id: str, label: str, tip: str)`, `@dataclass(frozen=True) MenuGroup(title: str, items: tuple[MenuItem, ...])`, `menu_tree(library: Library | None = None) -> list[MenuGroup]` (groups by kind and family: Styles by family, Scenes, Instruments, Vocals, Textures, Eras; tips come from the card's `sound` first sentence, never an artist name; artist cards appear under their parent style as "inspired by" sub-items whose label is the name and whose text expands to the description).
- Wand rules (spec 4.6 and section 10): pick two or three cards from different `family` values that are not in each other's `clashes_with`; add one scene or texture card, one tempo and one production note (from the cards); render through `translate`; for a dialect with no lyrics never pick a vocal-kind card; `seed` makes it repeatable and `None` uses a fresh random seed; a `base` text keeps what the user typed and adds one surprising layer; the result is never empty even with a one-card library (falls back to that card with a note).

- [ ] **Step 1: Failing tests** (fixture library of about 12 cards across 5 families): `test_wand_is_repeatable_with_a_seed_and_differs_without`, `test_wand_picks_at_least_two_families`, `test_wand_never_picks_a_clashes_with_pair` (run 200 seeds), `test_wand_on_a_no_lyrics_model_never_includes_a_vocal_card` (Review Focus 3; 200 seeds with a Stable Audio profile), `test_wand_with_base_keeps_the_users_words_and_adds_one_layer`, `test_wand_always_non_empty_even_with_a_one_card_library`, `test_wand_respects_the_length_limit`, `test_wand_output_has_no_artist_names`, `test_menu_tree_groups_by_kind_and_family`, `test_menu_tree_artist_cards_sit_under_their_parent_style`, `test_menu_tips_never_contain_an_artist_name`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** with `random.Random(seed)`; scoring is simple and documented (family distance and clash lists, no computed embeddings).
- [ ] **Step 4: Run.** Expected: PASS. Disable the clash check on a copy and watch its test fail; restore.
- [ ] **Step 5: Commit** `git commit -m "feat(music-prompts): wand and menu tree"`.

### Task 5: First card set (about 15 cards), then the operator's review

**Files:**
- Create: card files under `app/music_prompts/cards/` for: post-rock (style) with Tortoise, Yume Bitsu and The Sea and Cake (artist cards, `parent: post-rock`); bebop jazz; Parliament; Funkadelic; Parliament-Funkadelic; Tangerine Dream; Ash Ra Tempel; Kraftwerk; ratcheting synthesizers; chiptunes; synthpop; Grateful Dead songbook; accordion in the moonlight (scene); bagpipes on the beach at sunset (scene); bluegrass vocal (vocal). Keep the Task 1 starter cards if their ids match. `tests/test_music_shipped_cards.py`
- Modify: `app/music_prompts/CARDS.md` (add the review checklist)

**Interfaces:**
- Consumes: the schema and loader from Task 1.
- Produces: the shipped card set. Every card has `sound` written fresh in plain words (one to three sentences), `instruments`, `rhythm`, `harmony`, `mood`, `production`, `tempo_bpm`, `tags`, `avoid`, `family`, `goes_with`, `clashes_with`, and `notes` listing the sources (two or more) or `unverified`. Artist cards never repeat the artist's name in `sound`. Families used: `post-rock`, `jazz`, `funk`, `electronic`, `chip`, `folk`, `pop`, `scene` (adjust as needed and record the list in `CARDS.md`).

- [ ] **Step 1: Failing tests** (`tests/test_music_shipped_cards.py`): `test_all_fifteen_named_cards_exist_with_the_expected_aliases` (each of the operator's names resolves through `parse` to its card, including "Parliament-Funkadelic" beating the two single names), `test_every_shipped_card_has_two_sources_or_is_marked_unverified`, `test_no_card_sound_contains_an_artist_or_inspiration_name`, `test_every_card_has_tags_and_tempo_or_a_scene_reason` (scenes and instruments may omit tempo; styles may not), `test_goes_with_and_clashes_with_reference_real_ids_or_families`, `test_each_named_style_translates_in_all_three_dialects_without_error`, `test_the_example_sentence_still_translates` (Task 3's golden against the real cards).
- [ ] **Step 2: Run.** Expected: FAIL (cards missing).
- [ ] **Step 3: Write the cards.** Use web search where available to cross-check each card against at least two reputable sources and record them in `notes`. Do not copy text from any source. Mark any card you could not cross-check `notes: unverified`.
- [ ] **Step 4: Run all `tests/test_music_*` tests.** Expected: PASS.
- [ ] **Step 5: Produce the review sheet.** Write `docs/superpowers/specs/2026-10-28-music-prompt-cards-review.md`: a table with one row per card (id, aliases, the sound text, the three-dialect rendering of "<name>-like song", sources, verified or not), unverified cards listed first. Commit with `git commit -m "feat(music-prompts): first card set for review"`.
- [ ] **Step 6: STOP for the operator's review.** Execution pauses here by the spec's decision. The controller reports the review sheet to the operator, applies their corrections to the cards, and only then starts Task 6.

### Task 6: Neighbour cards (about 25 more) and wand variety

**Files:**
- Create: about 25 more card files (for example: Krautrock motorik, dub, shoegaze, math rock, afrobeat, bossa nova, surf rock, spaghetti-western soundtrack, gamelan, Celtic folk, Appalachian old-time, ambient drone, vaporwave, italo disco, trip hop, hyperpop, big-band swing, gospel choir, Mellotron strings, theremin, steel drums, harp, cello quartet, plus scene and texture cards such as "rain on a tin roof", "a lighthouse at dusk", "tape hiss"), `tests/test_music_wand_variety.py`
- Reviewed with the same rules as Task 5; the operator reviews them with the final sheet.

- [ ] **Step 1: Failing tests**: `test_library_has_at_least_forty_cards_in_at_least_eight_families`, `test_wand_produces_thirty_distinct_prompts_in_fifty_seeds`, `test_no_two_adjacent_wand_seeds_share_the_same_family_set` (spot check), `test_no_artist_name_in_any_wand_output_across_200_seeds_and_all_dialects` (Review Focus 1), `test_every_family_is_reachable_by_the_wand`, `test_all_shipped_cards_still_load_with_no_log_warnings` (assert no WARNING records from the loader).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Write the cards** under the Task 5 rules; update the review sheet with the new rows.
- [ ] **Step 4: Run all `tests/test_music_*` tests.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(music-prompts): neighbour cards and variety tests"`.

### Task 7: The Music panel: wand, Write the prompt, Styles, chips

**Files:**
- Modify: `app/create_param_panels.py` (`MusicParamPanel`, `ModifierPills`), `app/create_view.py` (wire the prompt setter), `config/` none
- Test: `tests/test_music_prompt_panel.py` (xvfb)

**Interfaces:**
- Consumes: `music_prompts.translate`, `wand`, `menu_tree`, `load_library`, the panel's `set_prompt_getter` seam (add the matching `set_prompt_setter(fn: Callable[[str], None] | None)`, wired by `CreateView` to the top prompt entry's `set_text`, because the panel's own prompt row is not mounted under `RoleZonePanel`).
- Produces: in `MusicParamPanel`: a **magic wand** button (tooltip "Surprise me"), a **Write the prompt** button, a **Smooth with AI** checkbox (off by default, enabled only when `prompt_client.music_llm_available()`), a **Styles** menu button built from `menu_tree()`, an **Undo** button restoring the text from before the last wand or Write the prompt, and a result strip under the prompt showing `Translation.added`, `notes` and "Inspired by: ..." (names appear here only). The wand and Write the prompt fill the prompt through the setter and, when the translation has lyrics, the lyrics box (only when it is empty or the user confirms replacing; a replace is undoable). They call the library off the GTK thread when `smooth` or the lyrics writer is on, and synchronously (it is fast) otherwise. Picking a Styles menu item inserts that card's name into the prompt text (the same place a chip would) so Write the prompt expands it. The existing free-text "In the style of {}" chip now runs the typed value through `translate` when applied (spec section 10): the pill shows the sound description, and an unknown value falls back to the old literal text.

- [ ] **Step 1: Failing tests**: `test_wand_button_fills_the_prompt_via_the_setter`, `test_wand_on_stable_audio_profile_leaves_lyrics_alone_and_shows_no_vocal`, `test_write_the_prompt_translates_the_typed_text_and_keeps_the_users_words`, `test_undo_restores_the_text_before_the_last_wand`, `test_lyrics_not_overwritten_when_the_user_has_typed_some_without_confirmation`, `test_result_strip_shows_added_phrases_notes_and_inspired_by_names_but_prompt_has_none`, `test_styles_menu_lists_groups_from_menu_tree`, `test_picking_a_style_inserts_its_name_for_the_next_translate`, `test_smooth_checkbox_disabled_without_an_llm`, `test_in_the_style_of_chip_translates_a_known_name_and_falls_back_for_an_unknown_one`, `test_no_library_work_on_the_gtk_thread_when_smooth_is_on` (the translate call happens on a worker thread; the result returns through `GLib.idle_add`), `test_collect_is_unchanged_by_the_new_controls` (collect equality with and without them), `test_panel_minimum_width_still_fits_800px` (reuse the existing bound).
- [ ] **Step 2: Run under xvfb.** Expected: FAIL.
- [ ] **Step 3: Implement.** Keep widget code in the panel's existing style. Take real screenshots under `GDK_BACKEND=x11 env -u WAYLAND_DISPLAY xvfb-run --auto-servernum` of the panel before and after a wand click and a Write the prompt for both profiles, read them, and fix clipping or legibility problems in this task.
- [ ] **Step 4: Run the new tests and `tests/test_music_param_panel.py tests/test_create_view_music.py tests/test_role_zone_panel.py`.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(music-prompts): wand, Write the prompt and Styles in the Music panel"`.

### Task 8: `tt-ctl music-prompt` and the Inspire path

**Files:**
- Modify: `tt-ctl`, `app/prompt_client.py` (`generate_prompt("music", seed)` calls the library first, the LLM second)
- Test: `tests/test_tt_ctl_music_prompt.py`, `tests/test_music_prompts.py` (existing music prompt tests)

**Interfaces:**
- Produces: `tt-ctl music-prompt "<text>" [--model <id>] [--dialect caption+lyrics|tags|generic] [--wand] [--seed N] [--include-names] [--json]`. `--model` resolves with the profile table in `music_runtime.py` (`profile_for` on a model string, no server probe) and falls back to generic; `--dialect` overrides. Text output prints the prompt, then the lyrics under a `lyrics:` line when present, then notes; `--json` prints `Translation` as an object. With neither text nor `--wand` it exits 2 with usage.
- `prompt_client.generate_prompt("music", seed_text)` returns `translate(seed_text, None).prompt` when the library recognises anything in the text (or `wand(None)` for empty text) and only then falls back to the LLM path; `"lyrics"` is unchanged.

- [ ] **Step 1: Failing tests**: `test_cli_translates_the_example_sentence`, `test_cli_model_minimax_returns_lyrics_and_stable_audio_does_not`, `test_cli_wand_with_seed_is_repeatable`, `test_cli_json_has_prompt_lyrics_notes_inspired_by`, `test_cli_no_names_in_prompt_unless_flagged`, `test_cli_without_text_or_wand_exits_two`, `test_inspire_music_uses_the_library_before_the_llm` (a stub LLM must not be called for a recognised name), `test_inspire_music_falls_back_to_the_llm_for_unknown_text`, `test_existing_music_prompt_tests_still_pass` (run the file).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** The CLI stays out of the way of the server-control `tt-ctl` changes on another branch: add one subparser and one `cmd_music_prompt`, nothing else.
- [ ] **Step 4: Run these tests.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(music-prompts): tt-ctl music-prompt and Inspire path"`.

### Task 9: Docs, version bump and final check

**Files:**
- Modify: `CLAUDE.md` (a "Music prompt library (v0.NNN.0)" section in the file's style), `README.md` (a short paragraph), `app/music_prompts/CARDS.md` if needed, `VERSION`, `debian/changelog`

- [ ] **Step 1:** Add the CLAUDE.md section: the original prompt, the architecture, the decisions from the spec (descriptions not names, deterministic by default, dialects, user cards, the review gate), what review found, and what was NOT verified (no real music generated from these prompts yet, MiniMax caption limit unconfirmed, card descriptions reviewed only by the operator's sheet). Plain-English rules from `~/CLAUDE.md` apply; sweep for `, not `, `rather than`, `instead of`, `honest`.
- [ ] **Step 2:** Run `xvfb-run --auto-servernum /usr/bin/python3 -m pytest tests/ -q` and report exact counts and every failure by name (known flakes in CLAUDE.md are the only expected failures).
- [ ] **Step 3:** By hand, run `./tt-ctl music-prompt "tortoise-like song but someone is singing a bluegrass song on top" --model minimax` and again with `--model stable-audio`, and `./tt-ctl music-prompt --wand --seed 7`; paste the output into the final report.
- [ ] **Step 4:** Bump `VERSION` (next minor) and prepend a `debian/changelog` stanza as one commit `chore: bump version to X.Y.Z`. Commit docs first as `docs: music prompt library`. Push nothing and open no PR until the operator asks.
