import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import server_manager
import server_registry as sr
import tt_backend as tb
import ready_to_run as rtr

FIX = Path(__file__).parent / "fixtures" / "tt"


def _m(name, kind=None, engines=("media",), hardware=("p300x2",), installed=False):
    return tb.TtModel(name=name, kind=kind, engines=tuple(engines),
                      hardware=tuple(hardware), installed=installed,
                      backends=("inference-server",))


def _over(tmp_path, text):
    p = tmp_path / "o.yaml"
    p.write_text(text)
    return p


def test_boutique_models_come_from_servers_table(tmp_path):
    reg = sr.build_registry([], tmp_path / "none.yaml")
    for key, sdef in server_manager.SERVERS.items():
        e = reg[key]
        assert e.backend == "boutique" and e.sm_key == key
        assert e.capabilities == tuple(sdef.capabilities)
        assert e.runner_key == sdef.runner_key
    assert reg["flux"].hardware_group == "media"
    assert reg["artgen-qwen3-8b"].hardware_group == "artgen"
    assert reg["flux"].port == 8000 and reg["prompt-server"].port == 8001


def test_catalog_model_becomes_tt_entry(tmp_path):
    cat = [_m("Foo-LLM", engines=("vLLM",), hardware=("p300x2",)),
           _m("bundle/x", kind="container", engines=("vllm-plugin",), installed=True)]
    reg = sr.build_registry(cat, tmp_path / "none.yaml")
    e = reg["Foo-LLM"]
    assert (e.backend, e.tt_name, e.sm_key) == ("tt", "Foo-LLM", None)
    assert e.capabilities == ("artgen",) and e.hardware_group == "artgen"
    assert e.chips == 4          # one hardware entry, p300x2 is 4 chips
    assert reg["bundle/x"].installed is True


def test_override_prefers_boutique_script_when_tt_also_carries_it(tmp_path):
    cat = [_m("FLUX.1-schnell")]
    reg = sr.build_registry(cat, tmp_path / "none.yaml")
    assert reg["flux"].backend == "boutique"
    assert reg["flux"].tt_name == "FLUX.1-schnell"
    assert "FLUX.1-schnell" not in reg            # not listed twice
    reg = sr.build_registry(cat, _over(tmp_path, "models:\n  flux:\n    prefer: tt\n"))
    assert reg["flux"].backend == "tt" and reg["flux"].tt_name == "FLUX.1-schnell"
    reg = sr.build_registry(cat, _over(tmp_path, "models:\n  flux:\n    prefer: boutique\n"))
    assert reg["flux"].backend == "boutique"


def test_override_sets_chips_group_port(tmp_path):
    cat = [_m("Foo", engines=("media",), hardware=("p300x2", "t3k"))]
    reg = sr.build_registry(cat, _over(
        tmp_path, "models:\n  Foo:\n    chips: 2\n    hardware_group: media\n    port: 9000\n"))
    e = reg["Foo"]
    assert (e.chips, e.hardware_group, e.port) == (2, "media", 9000)


def test_unknown_chip_count_is_none_not_a_guess(tmp_path):
    cat = [_m("Multi", hardware=("p300x2", "t3k")), _m("Odd", hardware=("mystery9",))]
    reg = sr.build_registry(cat, tmp_path / "none.yaml")
    assert reg["Multi"].chips is None and reg["Odd"].chips is None
    assert sr.chips_for(reg["Multi"], None) is None
    plan = tb.ServePlan("Multi", "tt-model", (), "p300x2", None, 2)
    assert sr.chips_for(reg["Multi"], plan) == 2   # the dry-run plan wins
    assert sr.chips_for(reg["Multi"], tb.ServePlan("Multi", None, (), None, None, None)) is None


def test_bad_override_file_is_ignored(tmp_path):
    for text in ("models: [unclosed", "just a string", "models:\n  flux: 3\n",
                 "models:\n  flux:\n    prefer: nonsense\n    chips: lots\n"):
        reg = sr.build_registry([_m("FLUX.1-schnell")], _over(tmp_path, text))
        assert reg["flux"].backend == "boutique" and reg["flux"].chips == 4  # catalog value, no override applied
    assert "flux" in sr.build_registry([], tmp_path / "missing.yaml")


def test_registry_built_from_the_real_list_fixture(tmp_path):
    rows = json.loads((FIX / "list.json").read_text())["models"]
    # Same row -> TtModel mapping tt_backend.catalog() applies.
    cat = [tb.TtModel(name=r["name"], kind=r.get("kind"), engines=tuple(r.get("engines") or ()),
                      hardware=tuple(r.get("hardware") or ()), installed=bool(r.get("installed")),
                      backends=tuple(r.get("backends") or ())) for r in rows]
    assert len(cat) >= 14
    reg = sr.build_registry(cat, tmp_path / "none.yaml")
    for m in cat:
        if m.name == "FLUX.1-schnell":
            assert reg["flux"].backend == "boutique" and reg["flux"].tt_name == m.name
        elif m.name == "Wan2.2-T2V-A14B-Diffusers":
            assert reg["wan2.2"].tt_name == m.name
        elif m.name == "Qwen3-32B":
            assert reg["artgen-qwen3-32b"].backend == "boutique"
        elif m.name == "Llama-3.1-8B-Instruct":
            assert reg["artgen-llama-3.1-8b"].tt_name == m.name
        else:
            assert reg[m.name].backend == "tt"
    assert reg["Qwen3.8-27B"].capabilities == ("artgen",)


def test_non_utf8_override_file_is_ignored(tmp_path):
    p = tmp_path / "o.yaml"
    p.write_bytes(b"models:\n  flux:\n    prefer: \xff\xfe\n")
    reg = sr.build_registry([_m("FLUX.1-schnell")], p)
    assert reg["flux"].backend == "boutique"


def test_override_for_unknown_or_claimed_name_is_logged(tmp_path, caplog):
    import logging
    p = _over(tmp_path, "models:\n  Nope:\n    chips: 2\n  FLUX.1-schnell:\n    prefer: tt\n")
    with caplog.at_level(logging.WARNING, logger="server_registry"):
        sr.build_registry([_m("FLUX.1-schnell")], p)
    text = caplog.text
    assert "'Nope' matches no registry key" in text
    assert "'FLUX.1-schnell' names a catalog model that a boutique entry" in text
