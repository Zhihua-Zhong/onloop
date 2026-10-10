"""`onloop init`: write a starter policy, charters, decision template, ledger, CODEOWNERS and two workflows."""
from __future__ import annotations

import datetime as dt
import pathlib
from importlib.resources import files

from . import __version__, codeowners, policy

# What the starter workflows install. onloop is not on PyPI, so the default is the tagged release on GitHub; pass
# `--install-spec` for a fork, a different tag, or a package index.
DEFAULT_INSTALL_SPEC = f"onloop @ git+https://github.com/Zhihua-Zhong/onloop@v{__version__}"
DOT_DIRS = {"dot_github": ".github"}          # a leading-dot directory cannot ship reliably inside a wheel


def _template_files():
    root = files("onloop") / "templates"
    stack = [(root, pathlib.PurePosixPath())]
    while stack:
        node, rel = stack.pop()
        for child in sorted(node.iterdir(), key=lambda c: c.name):
            name = DOT_DIRS.get(child.name, child.name) if child.is_dir() else child.name
            if child.is_dir():
                stack.append((child, rel / name))
            else:
                yield rel / name, child.read_text(encoding="utf-8")


def init(target: str | pathlib.Path, *, human: str, reviewer: str, agents: str, force: bool = False,
         today: dt.date | None = None, install_spec: str = DEFAULT_INSTALL_SPEC) -> tuple[list[str], list[str]]:
    """Write the starter files under `target`. Returns (written, skipped). Existing files are kept unless `force`."""
    target = pathlib.Path(target)
    today = today or dt.date.today()
    subs = {"__HUMAN__": human, "__REVIEWER__": reviewer, "__AGENTS__": agents, "__TODAY__": today.isoformat(),
            "__INSTALL_SPEC__": install_spec}
    written: list[str] = []
    skipped: list[str] = []
    for rel, text in _template_files():
        for k, v in subs.items():
            text = text.replace(k, v)
        dest = target / rel
        if dest.exists() and not force:
            skipped.append(rel.as_posix())
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8", newline="\n")
        written.append(rel.as_posix())
    pol = policy.load(target / policy.POLICY_FILE)
    co = target / "CODEOWNERS"
    if co.exists() and not force:
        skipped.append("CODEOWNERS")
    else:
        co.write_text(codeowners.generate(pol), encoding="utf-8", newline="\n")
        written.append("CODEOWNERS")
    return written, skipped
