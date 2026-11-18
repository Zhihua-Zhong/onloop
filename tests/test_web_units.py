"""The web page's pure rules, run by node's own test runner, so a mutation entry can name them. No browser and no
node_modules: the files under test import nothing but each other."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
NODE = shutil.which("node")


def _node_test(name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [NODE, "--test", "--experimental-strip-types", f"test/{name}"],
        cwd=WEB,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_maps_rules_hold_in_the_page():
    """A geometric zoom never changes the level (Q23); Escape at zoom 0 goes nowhere; the phone's order."""
    r = _node_test("map.test.ts")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
