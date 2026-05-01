"""
Circuit generator — logic gate / wiring diagram SVG.

LLM draws a small schematic: inputs, named gates (AND, OR, NOT, XOR), connecting
wires, and a labelled output node. No real simulation — pure visual logic art.
"""

from __future__ import annotations

from artgen import ArtGenerator, register

SVG_W = 700
SVG_H = 400

_GATE_SHAPES = {
    "and": "D-shape body (flat left, curved right)",
    "or": "Shield-shape (curved left and right, pointed right tip)",
    "not": "Triangle with a small inversion circle at the output",
    "xor": "Like OR but with an extra curved line parallel to the left edge",
    "nand": "AND gate with an inversion circle at the output",
    "nor": "OR gate with an inversion circle at the output",
}

_DIAGRAM_STYLES = {
    "clean": {
        "bg": "#0a0f18", "wire": "#4FD1C5", "gate_fill": "#0F2A35",
        "gate_stroke": "#4FD1C5", "label": "#81E6D9", "output": "#F4C471",
        "adjective": "clean, technical, blueprint",
    },
    "neon": {
        "bg": "#000000", "wire": "#FF00FF", "gate_fill": "#1A001A",
        "gate_stroke": "#FF00FF", "label": "#00FFFF", "output": "#FFFF00",
        "adjective": "neon, cyberpunk, glowing",
    },
    "paper": {
        "bg": "#F5F0E8", "wire": "#2C3E50", "gate_fill": "#FFFFFF",
        "gate_stroke": "#2C3E50", "label": "#2C3E50", "output": "#8B3A3A",
        "adjective": "hand-drawn, paper schematic, pencil",
    },
}


def _build_prompt(inputs: list[str], gates: list[str], depth: int, style: str) -> str:
    pal = _DIAGRAM_STYLES.get(style, _DIAGRAM_STYLES["clean"])
    w, h = SVG_W, SVG_H
    gate_list = ", ".join(f"{g.upper()} ({_GATE_SHAPES[g]})" for g in gates if g in _GATE_SHAPES)

    return (
        f"Generate a logic gate / wiring diagram SVG ({w}×{h}px).\n"
        f"Style: {pal['adjective']}\n\n"
        f"CIRCUIT SPEC:\n"
        f"  Inputs: {', '.join(inputs)} (label each on the left side)\n"
        f"  Gates to include: {gate_list}\n"
        f"  Depth: {depth} gate levels (inputs → level 1 gates → … → output)\n"
        f"  Output: one final output node labelled Q on the right side\n\n"
        f"DRAWING CONVENTIONS:\n"
        f"  - Input labels: <text> on far left, one per vertical row, fill={pal['label']}\n"
        f"  - Gate bodies: Use <path> or <rect>+<path> to approximate the gate shape\n"
        f"    Gate fill={pal['gate_fill']}, stroke={pal['gate_stroke']}, stroke-width=1.5\n"
        f"  - Wires: <line> or <polyline>, stroke={pal['wire']}, stroke-width=1.5\n"
        f"    Wires turn at 90° corners only. Junction dots: <circle r='3' fill={pal['wire']}/>\n"
        f"  - Output node: <circle r='6', fill={pal['output']}/> with label Q\n"
        f"  - Gate labels: small <text> inside or below each gate, fill={pal['label']}, font-size=9\n\n"
        f"PALETTE: bg={pal['bg']} · wire={pal['wire']} · gate fill={pal['gate_fill']} "
        f"stroke={pal['gate_stroke']} · labels={pal['label']} · output={pal['output']}\n\n"
        f"RULES:\n"
        f"  - Background: <rect width='{w}' height='{h}' fill='{pal['bg']}'/>\n"
        f"  - SVG root: <svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{w}\" height=\"{h}\">\n"
        f"  - No external hrefs, no <image>, no <use>\n"
        f"  - Leave comfortable margins — don't crowd elements to the edges\n"
        f"  - Output ONLY the complete SVG, no explanation, no markdown."
    )


@register
class CircuitGenerator(ArtGenerator):
    name = "circuit"
    description = "Logic gate / wiring diagram SVG: AND/OR/NOT/XOR gates, labelled inputs and output"
    output_ext = ".svg"

    _valid_gates = list(_GATE_SHAPES)
    _valid_inputs = list("ABCDEFGH")

    def add_args(self, parser) -> None:
        parser.add_argument(
            "--inputs", default="A,B,C",
            help="Comma-separated input signal names (default: A,B,C)",
        )
        parser.add_argument(
            "--gates", default="and,or",
            help=f"Comma-separated gate types to include: {', '.join(self._valid_gates)} (default: and,or)",
        )
        parser.add_argument(
            "--depth", type=int, default=2, choices=[1, 2, 3],
            help="Number of gate levels deep (default: 2)",
        )
        parser.add_argument(
            "--circuit-style", choices=list(_DIAGRAM_STYLES), default="clean",
            dest="circuit_style",
            help="Visual style (default: clean)",
        )

    def build_prompt(self, args) -> str:
        inputs = [s.strip().upper() for s in getattr(args, "inputs", "A,B,C").split(",")]
        raw_gates = [g.strip().lower() for g in getattr(args, "gates", "and,or").split(",")]
        gates = [g for g in raw_gates if g in self._valid_gates] or ["and", "or"]
        return _build_prompt(inputs, gates, getattr(args, "depth", 2), getattr(args, "circuit_style", "clean"))

    def parse_output(self, raw: str, args) -> str:
        import re
        import artgen as _artgen
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        cleaned = re.sub(r"```\w*\s*|```", "", cleaned).strip()
        cleaned = _artgen.repair_svg(cleaned)
        m = re.search(r"(<svg\b[^>]*?>.*?</svg>)", cleaned, re.DOTALL | re.IGNORECASE)
        if not m:
            raise ValueError("LLM response did not contain valid SVG markup")
        return m.group(1)
