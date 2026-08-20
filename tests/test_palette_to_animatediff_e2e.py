# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import recipes, palette_prompt, pipeline_engine as pe


def test_palette_to_animatediff_pipeline_assembles_and_runs_dry():
    goal = recipes.Goal("anim", "An animation", "🕺", "gif", "scoped",
                        (("TTLGAnimateDiff", {}),))
    literal = palette_prompt.literal_prompt(
        {"colors": [{"hex": "#1a2b3c"}], "lore": "moody dusk"})
    spec = recipes.build_seed_spec(
        goal, seed_artifact=None,
        prepend_steps=(("TTLGPaletteToPrompt", {"prompt": literal}),))
    # The adapter's prompt reaches AnimateDiff via a wire.
    assert spec["2"]["inputs"]["prompt"] == ["1", "prompt"]
    # And the assembled pipeline resolves the wire when run (dry).
    order = pe.topo_order({k: v for k, v in spec.items() if not k.startswith("_")})
    assert order == ["1", "2"]
    out1 = pe.HANDLERS["TTLGPaletteToPrompt"]("1", spec["1"]["inputs"], None)
    assert "#1a2b3c" in out1["prompt"] and "moody dusk" in out1["prompt"]
