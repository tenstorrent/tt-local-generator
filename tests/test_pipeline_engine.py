import importlib.util, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
_ENGINE = Path(__file__).parent.parent / "app" / "pipeline_engine.py"

def _load():
    spec = importlib.util.spec_from_file_location("pipeline_engine", _ENGINE)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

eng = _load()
_FIX = Path(__file__).parent / "fixtures" / "mini_pipeline.json"

def test_load_spec_strips_metadata_keeps_nodes():
    spec = eng.load_spec(str(_FIX))
    assert set(spec) == {"1", "2", "3"}
    assert spec["1"]["class_type"] == "TTLGTextToImage"

def test_topo_order_respects_wires():
    spec = eng.load_spec(str(_FIX))
    order = eng.topo_order(spec)
    assert order.index("1") < order.index("2") < order.index("3")

def test_topo_order_detects_cycle():
    spec = {"1": {"class_type": "X", "inputs": {"a": ["2", "k"]}},
            "2": {"class_type": "X", "inputs": {"a": ["1", "k"]}}}
    with pytest.raises(ValueError):
        eng.topo_order(spec)

def test_topo_order_detects_dangling_wire():
    spec = {"1": {"class_type": "X", "inputs": {"a": ["99", "k"]}}}
    with pytest.raises(ValueError):
        eng.topo_order(spec)

def test_resolve_inputs_substitutes_wires():
    results = {"1": {"image_path": "/tmp/x.png"}}
    out = eng.resolve_inputs({"caption": ["1", "image_path"], "lit": 5}, results)
    assert out == {"caption": "/tmp/x.png", "lit": 5}

def test_dry_run_emits_signals_and_publishes_keys():
    spec = eng.load_spec(str(_FIX))
    lines = []
    results = eng.run(spec, dry_run=True, emit=lines.append)
    # every node ran and published its documented dry-run output
    assert "image_path" in results["1"]
    assert results["2"]["prompt"]           # PromptCompose fills prompt
    assert "text" in results["3"]
    # signals: a running+done per node, in topo order
    assert any(l == "NODE:1:running:" or l.startswith("NODE:1:running") for l in lines)
    assert any(l.startswith("NODE:3:done") for l in lines)
