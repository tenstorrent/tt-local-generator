"""
ANSI art generator — block-character paintings using ANSI escape codes.

LLM produces a text-based image using Unicode block elements (█ ▓ ▒ ░ ▄ ▀ ■ …)
and ANSI 256-color or 16-color escape sequences. Renders in any color terminal.
"""

from __future__ import annotations

from artgen import ArtGenerator, register

_SUBJECT_EXAMPLES = (
    "a mountain at sunset, a lighthouse in a storm, a dragon skull, "
    "a coffee cup steaming, a retro computer, a black hole, a cat"
)

_COLOR_MODES = {
    "256": {
        "description": "ANSI 256-color mode",
        "escape_format": r"\033[38;5;{n}m for foreground, \033[48;5;{n}m for background",
        "palette_hint": (
            "Use the xterm 256-color palette. Color indices 0-15 are the standard 16 ANSI colors. "
            "Indices 16-231 are the 6×6×6 color cube. Indices 232-255 are grayscale. "
            "Use \\033[0m to reset."
        ),
    },
    "16": {
        "description": "ANSI 16-color mode",
        "escape_format": r"\033[3{n}m for foreground (n=0-7), \033[9{n}m for bright (n=0-7)",
        "palette_hint": (
            "Use only the 16 standard ANSI colors: 30-37 (dark), 90-97 (bright), "
            "40-47 (bg dark), 100-107 (bg bright). Use \\033[0m to reset."
        ),
    },
}

_STYLE_HINTS = {
    "landscape": "Wide panoramic composition. Sky fills the top half, terrain the bottom.",
    "portrait": "Centered subject, symmetric or near-symmetric layout, strong silhouette.",
    "logo": "Bold text or symbol treatment. Large block letters or a simple icon.",
    "scene": "Narrative moment — suggest foreground, midground, background.",
}


def _build_prompt(subject: str, width: int, color_mode: str, style: str) -> str:
    mode = _COLOR_MODES.get(color_mode, _COLOR_MODES["256"])
    height = max(20, width // 3)  # ~3:1 aspect ratio for terminal cells
    style_hint = _STYLE_HINTS.get(style, _STYLE_HINTS["scene"])

    return (
        f"Generate ANSI art of: {subject}\n\n"
        f"DIMENSIONS: {width} characters wide × {height} lines tall\n\n"
        f"COLOR MODE: {mode['description']}\n"
        f"  Escape format: {mode['escape_format']}\n"
        f"  {mode['palette_hint']}\n\n"
        f"COMPOSITION STYLE: {style_hint}\n\n"
        f"TECHNIQUE:\n"
        f"  - Primary block characters: █ ▓ ▒ ░ (full, dark, medium, light shade)\n"
        f"  - Half-block: ▄ ▀ (bottom/top half) for subpixel vertical resolution\n"
        f"  - Supplementary: ■ □ ▪ ▫ ◆ ◇ ● ○ ╔ ╗ ╚ ╝ ═ ║ for outlines and detail\n"
        f"  - Color transitions: gradually shift escape codes across rows/columns\n"
        f"  - Use background color (\\033[48;…m) for solid filled areas\n"
        f"  - Use foreground color (\\033[38;…m) + block char for textured areas\n"
        f"  - End every line with \\033[0m (reset)\n\n"
        f"OUTPUT FORMAT:\n"
        f"  - Raw terminal output: actual escape sequences as literal bytes\n"
        f"  - Write \\033 as the ESC character (byte 0x1B), not the literal text \\033\n"
        f"  - Each line is exactly {width} characters wide (excluding escape sequences)\n"
        f"  - No markdown, no explanation, no preamble — just the ANSI art lines."
    )


@register
class AnsiGenerator(ArtGenerator):
    name = "ansi"
    description = "ANSI block-character art using escape codes — renders in any color terminal"
    output_ext = ".ans"

    def add_args(self, parser) -> None:
        parser.add_argument(
            "--subject", default="a mountain at sunset",
            help=f'What to draw. Examples: {_SUBJECT_EXAMPLES}',
        )
        parser.add_argument(
            "--width", type=int, default=60, metavar="COLS",
            help="Width in terminal columns (default: 60)",
        )
        parser.add_argument(
            "--colors", choices=list(_COLOR_MODES), default="256",
            help="Color depth: 256 (default) or 16",
        )
        parser.add_argument(
            "--ansi-style", choices=list(_STYLE_HINTS), default="scene",
            dest="ansi_style",
            help="Composition style (default: scene)",
        )

    def build_prompt(self, args) -> str:
        return _build_prompt(
            getattr(args, "subject", "a mountain at sunset"),
            getattr(args, "width", 60),
            getattr(args, "colors", "256"),
            getattr(args, "ansi_style", "scene"),
        )

    def parse_output(self, raw: str, args) -> str:
        import re
        # Strip thinking blocks
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        # Strip markdown fences
        cleaned = re.sub(r"```\w*\s*|```", "", cleaned).strip()
        # Interpret \033 escape notation if LLM wrote literal backslash-033
        cleaned = cleaned.replace("\\033", "\033").replace("\\e", "\033")
        return cleaned
