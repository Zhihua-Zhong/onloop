"""The C4 model (docs/architecture/model.yaml) and its generator (tools/diagrams/c4.py)."""

from __future__ import annotations

import copy
import importlib.util
import re
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "diagrams" / "c4.py"
_spec = importlib.util.spec_from_file_location("c4", TOOL)
c4 = importlib.util.module_from_spec(_spec)
sys.modules["c4"] = c4
_spec.loader.exec_module(c4)


def model():
    return c4.load()


def test_the_repository_model_is_consistent():
    assert c4.check(model()) == []


def test_every_element_carries_the_required_fields_and_components_a_code_path():
    m = model()
    for el in m["elements"]:
        for key in c4.REQUIRED:
            assert el.get(key), (el["id"], key)
        if el["type"] == "component":
            assert el["code"].startswith("src/onloop/") or el["code"] == "not yet created", el["id"]


def test_built_component_code_paths_exist_on_this_branch():
    root = TOOL.parents[2]
    for el in model()["elements"]:
        if el["type"] == "component" and el["status"] == "built":
            assert (root / el["code"]).exists(), el["code"]


def test_a_view_naming_an_unknown_element_fails():
    m = model()
    m["views"][0]["elements"]["no-such-element"] = {"x": 0, "y": 0, "w": 10, "h": 10}
    assert any("unknown element 'no-such-element'" in p for p in c4.check(m))


def test_a_relationship_whose_end_is_not_in_the_view_fails():
    m = model()
    view = m["views"][0]
    rid = next(iter(view["relationships"]))
    rel = next(r for r in m["relationships"] if r["id"] == rid)
    del view["elements"][rel["to"]]
    assert any(f"relationship {rid!r} has its to end" in p for p in c4.check(m))


def test_a_dynamic_step_whose_end_is_not_in_the_view_fails():
    m = model()
    view = next(v for v in m["views"] if v.get("steps"))
    view["steps"][0] = dict(view["steps"][0], to="nowhere")
    assert any("step 1 has its to end 'nowhere' outside the view" in p for p in c4.check(m))


def test_build_refuses_an_inconsistent_model():
    m = copy.deepcopy(model())
    m["relationships"].append(
        {"id": "x", "from": "lead", "to": "ghost", "description": "d", "technology": "t"}
    )
    try:
        c4.build(m, code_ref=None)
    except c4.ModelError as exc:
        assert "unknown to element 'ghost'" in str(exc)
    else:
        raise AssertionError("build accepted an inconsistent model")


def test_one_page_per_view_and_ip_literals_carry_the_scan_marker(tmp_path):
    m = model()
    for view in m["views"]:
        if view.get("kind") == "code":
            view["code"]["ref"] = None  # the work tree, so the test does not depend on another branch
    pages = c4.build(m, code_ref=None)
    out = tmp_path / "c4.drawio"
    c4.write(pages, out)
    text = out.read_text(encoding="utf-8")
    assert text.count("<diagram ") == len(m["views"])
    for line in text.splitlines():
        if re.search(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d)", line):
            assert c4.SCAN_ALLOW in line


def test_the_code_view_resolves_imports_to_modules():
    files = {
        "onloop/__init__.py": "",
        "onloop/a/__init__.py": "",
        "onloop/a/x.py": '"""X."""\nfrom . import y\nfrom ..b import z as zz\nimport os\n',
        "onloop/a/y.py": '"""Y."""\n',
        "onloop/b/__init__.py": "",
        "onloop/b/z.py": '"""Z."""\nfrom onloop.a.y import thing\n',
    }
    graph = c4.import_graph(files)
    assert graph["onloop.a.x"].imports == {"onloop.a.y", "onloop.b.z"}
    assert graph["onloop.b.z"].imports == {"onloop.a.y"}
    assert graph["onloop.a.x"].doc == "X."


def test_the_version_comes_from_the_package():
    import onloop

    assert c4.version() == onloop.__version__


def test_parameters_are_substituted_and_drift_is_reported(tmp_path):
    src = tmp_path / "src" / "x.py"
    src.parent.mkdir()
    src.write_text("PORT = 9000\nSTEPS: list[str] = [A, B, C]\n", encoding="utf-8")
    spec = {"file": "src/x.py", "name": "PORT", "value": 9001}
    assert c4.code_value(spec, tmp_path) == 9000
    assert c4.code_value({"file": "src/x.py", "name": "STEPS", "len": True}, tmp_path) == 3
    drift = c4.param_drift({"params": {"port": spec}}, tmp_path)
    assert drift == ["port: the model shows 9001, src/x.py has 9000 (PORT)"]
    model_file = tmp_path / "m.yaml"
    model_file.write_text(
        'params:\n  port: {value: 9001, file: src/x.py, name: PORT}\nmeta: {s: "on :${port}"}\n',
        encoding="utf-8",
    )
    assert c4.load(model_file)["meta"]["s"] == "on :9001"


def test_the_repository_model_uses_only_known_parameters():
    text = c4.MODEL.read_text(encoding="utf-8")
    assert "${" in text
    assert "${" not in repr(model()["elements"])
