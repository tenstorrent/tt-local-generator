"""
Emoji Storyteller — tells a story almost entirely in emoji and symbols.

A worked example for docs/tutorials/adding-an-artgen-plugin.md: a generator
that ships ZERO new infrastructure. It reuses whatever chat LLM is already
running on the artgen server (``uses_llm = True``, the default) — the base
class hands it a ``call_fn`` and it just crafts a good prompt. The whole
plugin is one ``ArtGenerator`` subclass plus a small ``mcp.json`` manifest.

Output is a short sequence of "scenes", one per line, each an emoji/symbol
rebus — a beginning, middle, and end told with pictures instead of prose.
"""

from __future__ import annotations

from artgen import ArtGenerator

# The house style, expressed as a system prompt. Kept in one place so
# build_prompt() (used by --simulate) and generate_artifact() agree.
_SYSTEM = (
    "You are Emoji Storyteller. You tell a complete little story using ONLY "
    "emoji, symbols, arrows, and punctuation — like a rebus or a silent film "
    "told in pictographs. Convey character, action, cause-and-effect, and a "
    "clear beginning / middle / end. Sequence emoji so the narrative reads "
    "left-to-right. Use arrows (->, =>), math/relational symbols, and "
    "punctuation to carry logic and beats. Do NOT explain the story, add a "
    "title, number the lines, or wrap the output in code fences. Keep it "
    "playful and legible — a reader should be able to 'read' the story. "
    "Never include gore or disturbing content."
)


def _build_user_message(theme: str, scenes: int, words: int) -> str:
    """The user turn: the concrete assignment for this run.

    Returned as-is by ``build_prompt`` so ``--simulate`` prints something
    meaningful without touching the network.
    """
    if words <= 0:
        word_rule = (
            "Use NO real words at all — emoji, symbols, arrows, and "
            "punctuation only."
        )
    else:
        word_rule = (
            f"Use at most {words} real word(s) in the whole story; everything "
            "else must be emoji, symbols, arrows, or punctuation."
        )
    return (
        f"Tell a story on the theme: {theme}\n"
        f"Give it {scenes} scene(s), one scene per line, in order. "
        f"{word_rule} "
        "Output only the scenes."
    )


class EmojiStorytellerGenerator(ArtGenerator):
    # `name` MUST match the primary tool name in mcp.json — the plugin loader
    # keys the registry (and the `tt-ctl artgen <name>` subcommand) off the
    # manifest's tool name, and default_output()/logging use this attribute.
    name = "emoji-storyteller"
    description = "Tells a story almost entirely in emoji and symbols"
    output_ext = ".txt"
    # uses_llm = True is inherited from ArtGenerator — this generator reuses the
    # running chat model on the artgen server; it starts/needs no new backend.

    def add_args(self, parser) -> None:
        parser.add_argument(
            "--theme", default="a hero's journey",
            help='Story seed (default: "a hero\'s journey")',
        )
        parser.add_argument(
            "--scenes", type=int, default=6, metavar="N",
            help="Number of story beats, one per line (default: 6)",
        )
        parser.add_argument(
            "--words", type=int, default=0, metavar="N",
            help="Max real words allowed; 0 = pure emoji/symbols (default: 0)",
        )

    def build_prompt(self, args) -> str:
        """The user message. Used directly by --simulate (dry run)."""
        theme = getattr(args, "theme", "a hero's journey")
        scenes = getattr(args, "scenes", 6)
        words = getattr(args, "words", 0)
        return _build_user_message(theme, scenes, words)

    def generate_artifact(self, args, call_fn) -> str:
        """Single LLM call with our house-style system prompt.

        `call_fn(prompt, system=None, max_tokens=None) -> raw text` is built by
        the caller (CLI or the artgen panel) and already points at the running
        chat model — we don't know or care which one.
        """
        raw = call_fn(self.build_prompt(args), system=_SYSTEM)
        return self.post_process(self.parse_output(raw, args), args)

    def parse_output(self, raw: str, args) -> str:
        """Strip <think> blocks and markdown fences a chat model may add."""
        import re
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        cleaned = re.sub(r"```\w*\s*|```", "", cleaned).strip()
        return cleaned

    def default_output(self):
        from pathlib import Path
        return Path("emoji-storyteller.txt")
