# tests/test_remix_graph.py
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


def _setup_plugins(tmp_path, monkeypatch, specs):
    """specs: list of (name, accepts_remix_from, can_remix_to)"""
    import plugin_loader
    plugin_loader._PLUGINS.clear()
    monkeypatch.setattr(plugin_loader, "_SEARCH_PATHS", [tmp_path])

    for name, afrom, ato in specs:
        d = tmp_path / name
        d.mkdir(exist_ok=True)
        manifest = {
            "x-ttlg": {
                "output_ext": ".txt",
                "media_type": "text",
                "accepts_remix_from": afrom,
                "can_remix_to": ato,
                "tab": "generative-art",
                "hardware": None,
            },
            "tools": [{
                "name": name,
                "description": name,
                "inputSchema": {"type": "object", "properties": {}, "required": []},
                "examples": [],
                "x-ttlg": {"streaming": None, "artifact_tool": True},
            }],
        }
        (d / "mcp.json").write_text(json.dumps(manifest))

    plugin_loader.load_plugins()


def test_remix_targets_for_verse(tmp_path, monkeypatch):
    """remix_targets_for('verse') returns plugins accepting verse as input."""
    _setup_plugins(tmp_path, monkeypatch, [
        ("video_gen", ["verse", "palette"], []),
        ("image_gen", ["verse"], ["video"]),
        ("midi_gen", ["palette"], []),
    ])
    from artgen import remix_targets_for
    targets = remix_targets_for("verse")
    names = [p.name for p in targets]
    assert "video_gen" in names
    assert "image_gen" in names
    assert "midi_gen" not in names


def test_remix_targets_for_palette(tmp_path, monkeypatch):
    _setup_plugins(tmp_path, monkeypatch, [
        ("video_gen", ["verse", "palette"], []),
        ("midi_gen", ["palette"], []),
        ("image_gen", ["verse"], []),
    ])
    from artgen import remix_targets_for
    names = [p.name for p in remix_targets_for("palette")]
    assert "video_gen" in names
    assert "midi_gen" in names
    assert "image_gen" not in names


def test_remix_context_fields():
    from artgen import RemixContext
    record = {"id": "abc", "prompt": "a blue fox", "media_type": "verse"}
    ctx = RemixContext(
        source_record=record,
        source_type="verse",
        target_type="video",
        hint="a blue fox",
    )
    assert ctx.source_type == "verse"
    assert ctx.target_type == "video"
    assert ctx.hint == "a blue fox"


def test_extract_remix_hint_default():
    """Default hint extraction returns the prompt field from a history record."""
    from artgen import extract_remix_hint
    record = {"prompt": "misty mountains at dawn", "media_type": "verse"}
    assert extract_remix_hint(record) == "misty mountains at dawn"


def test_extract_remix_hint_missing_prompt():
    from artgen import extract_remix_hint
    record = {"media_type": "palette"}
    assert extract_remix_hint(record) == ""
