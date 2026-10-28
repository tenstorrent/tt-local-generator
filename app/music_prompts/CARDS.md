# Style cards

A style card describes how something sounds, in plain words. The music prompt
library uses cards to turn a name ("Tortoise-like") into a description a music
model can use. Artist names stay out of rendered prompts unless the caller asks
for them.

## Where cards live

- Shipped cards: `app/music_prompts/cards/*.yaml`.
- User cards: `~/.config/tt-local-generator/music_styles/*.yaml`. The folder may
  be absent. A user card with the same `id` as a shipped card replaces it.

One file holds one card (a mapping) or several (a list of mappings). A file that
is not valid YAML is logged and skipped. A card that fails validation is logged
and skipped; the other cards still load. If two cards in the same folder share
an `id`, the first (by file name) is kept and the repeat is logged.

## Fields

Required for every card: `id`, `kind`, `name`, `family`, `sound`.
Artist cards also require `parent`.

| Field | Meaning |
|---|---|
| `id` | Unique key, lowercase with hyphens (`post-rock`). |
| `kind` | `style`, `artist`, `scene`, `instrument`, `vocal`, `texture` or `era`. |
| `name` | Display name. It is also matched as an alias. |
| `aliases` | Other names and phrases that should find this card. |
| `family` | Cluster tag the wand uses to mix different families (`post-rock`, `jazz`, `funk`, `electronic`, `chip`, `folk`, `pop`, `scene`). |
| `inspiration` | Artists and records the card draws on. Shown in the UI, never put in a prompt by default. |
| `sound` | One to three plain sentences. Never names an artist. Start with an ordinary word: a caption may print it after the style name ("Post-rock: mostly instrumental rock ..."), which lowercases the first letter. |
| `instruments`, `rhythm`, `harmony`, `mood`, `production` | Short plain descriptions. |
| `tempo_bpm` | `[low, high]` whole numbers (floats and booleans are rejected), with 0 < low <= high. Styles need it; scenes and instruments may omit it. |
| `avoid` | Things to leave out. Shown in the notes, never written into a prompt. |
| `lyric_themes` | Topics a sung layer can be about (vocal cards, singing styles). |
| `vocal` | How the voice sounds, for vocal cards. |
| `key` | The key, only when it is true of the style (`major key`, `D minor`). Optional; most cards leave it empty. Used in tag and caption prompts; the base card's key wins, else a vocal card's. |
| `lead_instrument` | Vocal cards: the instrument that carries the melody when the model cannot sing (Stable Audio). Optional; without it the prompt says "plaintive lead line phrased like a <style> vocal". The bluegrass vocal card uses `pedal steel`. |
| `tags` | Short tag forms for tag-style prompts. |
| `goes_with`, `clashes_with` | Card ids or family names the wand favours or avoids. An entry that names nothing is logged and kept as a plain family tag. |
| `ambiguous` | True when the name is also an ordinary word (Tortoise). The parser then needs a cue word or a capital letter. |
| `cues` | Words that confirm an ambiguous card when they appear within five words. |
| `parent` | For `artist` cards, the `id` of the style card it points to. |
| `notes` | Sources (two or more) or the single word `unverified`. |

List fields (`aliases`, `instruments`, `avoid`, `tags` and so on) must be YAML lists of strings; text fields must be strings. A card with a wrong type is skipped with a message.
`avoid` never goes into a prompt (music models tend to hear "no X" as "X"); the translator lists it in its notes as "Not asked for: ...". Entries that mention singing, vocals, verses, choruses or lyrics are left out of that note when the user asks for a vocal or an instrumental.
If a card's own text does name an artist or an `inspiration` entry, the translator replaces it with the style name, but write the card so it does not need that.
An artist card whose `parent` names no card, or a card that is not a style, is kept and a warning is logged. A missing or empty shipped folder logs a warning.

If two cards share a normalised alias, `by_alias[key]` holds every id, in card order: shipped cards first (by file name), then new user ids. A user card that overrides a shipped id keeps the original position.

`by_alias` normalises names: lowercase, hyphens and underscores read as spaces,
runs of spaces collapsed. "Post-Rock", "post_rock" and "post  rock" are one key.

## Adding a card

1. Copy a shipped card of the same `kind` and change every field.
2. Write `sound` fresh in your own words. Do not copy text from a source and do
   not name an artist in it.
3. For an artist card, put the artist in `name` and `inspiration`, set `parent`
   to a style card id, and describe the artist's sound in `sound` with
   instruments, rhythm and production.
4. Record at least two sources in `notes`, or write `unverified`.
5. For a shipped card, run `/usr/bin/python3 -m pytest tests/test_music_cards.py -q`.
   For a user card, drop the file in the user folder and restart the app; check
   the log for "music card ... skipped" lines if it does not show up.
