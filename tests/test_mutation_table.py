"""The mutation table is evidence only while every entry still applies. A full run takes about forty minutes; this takes seconds,
so an entry whose text a commit moved fails on that commit, not hours later."""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_table(path=ROOT / "tools" / "mutation_check.py"):
    spec = importlib.util.spec_from_file_location("mutation_check_table", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MUTATIONS


def problems(table, root=ROOT):
    """Every entry whose old text is not in its file exactly once, whose change changes nothing, or whose tests do not exist."""
    out = []
    for label, file, old, new, selectors in table:
        path = root / file
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        n = text.count(old) if old else 0
        if n != 1:
            out.append(f"{label}: the old text occurs {n} times in {file}")
        if old == new:
            out.append(f"{label}: the change changes nothing")
        for sel in selectors:
            if not (root / sel.split("::")[0]).is_file():
                out.append(f"{label}: {sel} is not a test file")
    labels = [e[0] for e in table]
    out += [f"two entries are both called {x!r}" for x in sorted({x for x in labels if labels.count(x) > 1})]
    return out


def test_every_mutation_entry_applies():
    assert problems(load_table()) == []


def test_the_check_bites_when_an_entry_text_has_moved(tmp_path):
    (tmp_path / "onloop").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "onloop" / "a.py").write_text("x = 1\ny = 2\ny = 2\n", encoding="utf-8")
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    ok = ("fine", "onloop/a.py", "x = 1", "x = 2", ["tests/test_a.py::test_x"])
    assert problems([ok], tmp_path) == []
    assert problems([("moved", "onloop/a.py", "x = 3", "x = 4", ["tests/test_a.py"])], tmp_path) == [
        "moved: the old text occurs 0 times in onloop/a.py"
    ]
    assert "twice" in problems([("twice", "onloop/a.py", "y = 2", "y = 3", ["tests/test_a.py"])], tmp_path)[0]
    assert (
        "changes nothing"
        in problems([("same", "onloop/a.py", "x = 1", "x = 1", ["tests/test_a.py"])], tmp_path)[0]
    )
    assert (
        "not a test file"
        in problems([("nofile", "onloop/a.py", "x = 1", "x = 2", ["tests/test_missing.py::t"])], tmp_path)[0]
    )
    assert any("both called" in p for p in problems([ok, ok], tmp_path))
