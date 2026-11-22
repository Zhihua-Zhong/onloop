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


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_age_ticks_with_no_new_data_and_a_stalled_onloopd_says_so():
    """The project lead's finding on 8796: "1 s ago" still said two minutes later. An age rises with the page's clock
    between answers, and past two heartbeat periods without news the page says so; past five seconds without an
    answer, it says onloopd is not answering."""
    r = _node_test("tick.test.ts")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_old_link_to_a_retired_view_is_rewritten_to_where_its_parts_moved():
    """The onloop lead's ask on 211e547: `#/flow` reads `#/map` in the address, as `#/activity` reads `#/replay`."""
    r = _node_test("route.test.ts")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_view_recovers_when_onloopd_comes_back():
    """The lead's finding on 28edd47: a query in error is tried again every few seconds, the last data stays on
    screen while a refresh fails, and "Failed to fetch" is said in plain words."""
    r = _node_test("recovery.test.ts")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_replays_index_gives_the_ledgers_own_frames_in_the_page():
    """Decision 0019, spike S3: a frame from the page's index is the ledger's timeline at that time (the fixture
    tests/test_replay_api.py writes from derive.timeline), agrees with the naive fold, stops at now, and costs well
    under a millisecond at 100 000 requests."""
    r = _node_test("replay.test.ts")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_markdown_product_is_read_as_a_tree_never_as_html():
    """Decision 0019, a4: a step's Markdown is rendered from a tree; its raw HTML stays text and only http links are
    links."""
    r = _node_test("markdown.test.ts")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
