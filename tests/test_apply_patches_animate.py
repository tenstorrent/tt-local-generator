import subprocess, sys, textwrap
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "bin" / "apply_patches.sh"

def test_step7_targets_video_yaml_not_model_spec_py():
    text = SCRIPT.read_text()
    # Step 7 must append to the YAML catalog, not the dead model_spec.py anchor.
    assert 'workflows/model_specs/dev/video.yaml' in text
    assert 'Wan-AI/Wan2.2-Animate-14B-Diffusers' in text
    # The dead ModelSpecTemplate injection for Animate is gone.
    assert 'ModelSpecTemplate(' not in text or 'Wan2.2-Animate' not in _animate_block(text)

def test_steps_8_and_9_are_retired():
    text = SCRIPT.read_text()
    assert 'DeepSeek-R1-Distill-Llama-70B' not in text
    assert 'stable-diffusion-xl-base-1.0-img-2-img' not in text
    # Header no longer advertises 9 steps.
    assert 'Step 8' not in text and 'Step 9' not in text

def test_animate_yaml_append_is_idempotent(tmp_path):
    # Extract Step 7's python heredoc and run it twice against a temp video.yaml.
    body = _extract_step7_python(SCRIPT.read_text())
    yaml = tmp_path / "video.yaml"
    yaml.write_text("- weights:\n    - Existing/Model\n")
    for _ in range(2):
        subprocess.run([sys.executable, "-c", body, str(yaml)], check=True)
    text = yaml.read_text()
    assert text.count("Wan-AI/Wan2.2-Animate-14B-Diffusers") == 1  # appended once

def _extract_step7_python(script_text):
    # The Step 7 heredoc is delimited by <<'PYEOF' ... PYEOF after the Animate echo.
    start = script_text.index("Wan2.2-Animate-14B-Diffusers YAML entry")
    heredoc_open = script_text.index("<<'PYEOF'", start) + len("<<'PYEOF'")
    heredoc_close = script_text.index("PYEOF", heredoc_open)
    return textwrap.dedent(script_text[heredoc_open:heredoc_close]).strip()

def _animate_block(text):
    i = text.find('Wan2.2-Animate')
    return text[max(0, i-200):i+200] if i >= 0 else ''
