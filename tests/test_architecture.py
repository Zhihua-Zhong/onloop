"""The package's layers (docs/architecture.md): a module imports from its own layer or a lower one, never a higher one."""

import ast
import pathlib

PKG = pathlib.Path(__file__).resolve().parent.parent / "src" / "onloop"

LAYERS = {
    0: {
        "events",
        "policy",
        "identity",
        "github",
        "gitutil",
        "procs",
        "fsutil",
        "sqlitedb",
        "asapp",
        "watchdog",
        "testing",
    },
    1: {
        "ledger",
        "classify",
        "codeowners",
        "gate",
        "audit",
        "verdicts",
        "anchor",
        "hooks",
        "init",
        "liveness",
        "overlay",
        "disposition",
    },
    2: {
        "intake",
        "slots",
        "preflight",
        "kpi",
        "ingest",
        "detect",
        "report",
    },
    3: {"supervisor", "daemon"},
    4: {"cli"},
}
LAYER_OF = {name: n for n, names in LAYERS.items() for name in names}


def top(path: pathlib.Path, root: pathlib.Path = PKG) -> str:
    """The top-level name a file belongs to: `gate` for gate.py, `supervisor` for supervisor/render.py."""
    rel = path.relative_to(root)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def imported(path: pathlib.Path, root: pathlib.Path = PKG) -> set[str]:
    """The top-level onloop names a file imports, relative imports resolved against its package."""
    depth = len(path.relative_to(root).parts) - 1  # 0 for onloop/x.py, 1 for onloop/pkg/x.py
    out = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.ImportFrom):
            if n.level == 0:
                parts = (n.module or "").split(".")
                if parts[0] == "onloop" and len(parts) > 1:
                    out.add(parts[1])
                continue
            if n.level - 1 < depth:  # inside the same subpackage
                continue
            if n.module:
                out.add(n.module.split(".")[0])
            else:
                out |= {a.name for a in n.names}
        elif isinstance(n, ast.Import):
            out |= {a.name.split(".")[1] for a in n.names if a.name.startswith("onloop.")}
    return out - {"__version__", "__init__"}


def test_every_module_is_in_a_layer():
    names = {top(p) for p in PKG.rglob("*.py") if p.name != "__init__.py" or p.parent != PKG}
    assert names - set(LAYER_OF) == set()


def upward_imports(root: pathlib.Path = PKG) -> list[str]:
    out = []
    for p in sorted(root.rglob("*.py")):
        if p == root / "__init__.py":
            continue
        mine = LAYER_OF[top(p, root)]
        out += [f"{p.relative_to(root)} imports {n}" for n in imported(p, root) if LAYER_OF.get(n, -1) > mine]
    return out


def test_no_module_imports_from_a_higher_layer():
    assert upward_imports() == []


def test_the_check_bites(tmp_path):
    """A layer-0 module that imports the gate, and a subpackage module that imports the command line, are caught."""
    (tmp_path / "supervisor").mkdir()
    (tmp_path / "events.py").write_text("from . import gate\n", encoding="utf-8")
    (tmp_path / "gate.py").write_text("from .events import check_ts\n", encoding="utf-8")
    (tmp_path / "supervisor" / "render.py").write_text(
        "from .. import cli\nfrom . import model\n", encoding="utf-8"
    )
    got = [line.replace("\\", "/") for line in upward_imports(tmp_path)]
    assert got == ["events.py imports gate", "supervisor/render.py imports cli"]
