"""The plain-language check, first version (finding #26): every task title a person reads leads with plain words."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from onloop import cli, plaintext
from onloop import tasks as T
from onloop.overlay.ledger import Ledger

NOW = "2026-11-09T14:52:00.000Z"
TOOL = Path(__file__).resolve().parent.parent / "tools" / "demo_ledger.py"
_spec = importlib.util.spec_from_file_location("demo_ledger", TOOL)
demo = importlib.util.module_from_spec(_spec)
sys.modules["demo_ledger"] = demo
_spec.loader.exec_module(demo)


@pytest.mark.parametrize(
    "wrong",
    [
        "outcome: blocked owner=human ref=r5-planning [incomplete]",
        "pr#204 · demo/7 · costing now",
        "t-demo0009 superseded (turn:demo-lead-retention)",
        "builder: 1 item, dead waker",
        "Restart the waker waker:monitor:ci-release-0.4",
        "Incomplete: outcome: blocked owner=human ref=#1",
    ],
)
def test_the_check_bites_on_codes_first_and_jargon(wrong):
    assert plaintext.problems(wrong)


@pytest.mark.parametrize(
    "right",
    [
        "Approve the schema change for the import · costing now (pr#204, demo/7)",
        "An ask with no effect stated, about: r5-planning",
        "An ask with no effect stated (pr#45)",
        "Keeping the source's timestamps: researched (f-3a9c)",
        "- Merge the change · from the lead, 5 days ago (incomplete · task:t-0123456789)",
    ],
)
def test_the_check_passes_plain_text_with_codes_in_parentheses(right):
    assert plaintext.problems(right) == []


def _v1_ask(led, eid, ref):
    led.append(
        {
            "id": eid,
            "source": "hook",
            "kind": "hook.stop",
            "subject_ref": "role:lead",
            "payload": {
                "role": "lead",
                "session_id": "s",
                "line": f"outcome: blocked owner=human ref={ref}",
                "outcome": {"value": "blocked", "ref": ref, "owner": "human", "waker": None},
            },
        }
    )


def test_a_version_1_ask_reads_as_a_plain_title_with_the_raw_line_kept(tmp_path):
    """Finding #26: "outcome: blocked owner=human ref=r5-planning" becomes "An ask with no effect stated, about:
    r5-planning", from the lead, with its age; the line itself is kept only for hover and the drill-down."""
    with Ledger(tmp_path / "l.db", clock=lambda: NOW) as led:
        for i, ref in enumerate(("r5-planning", "state/design/r5_plan.md", "pr#45", "#12")):
            _v1_ask(led, f"old{i}", ref)
        cards = T.open_tasks(led, "2026-11-14T14:52:00.000Z")
    by_ref = {c["ref"]: c for c in cards}
    c = by_ref["r5-planning"]
    assert c["headline"] == "An ask with no effect stated, about: r5-planning"
    assert c["raw_line"] == "outcome: blocked owner=human ref=r5-planning"
    assert c["asked_by"] == "the lead"
    assert cli.brief_line(c) == (
        f"- An ask with no effect stated, about: r5-planning · from the lead, 5 days ago (incomplete · task:{c['id']})"
    )
    assert by_ref["pr#45"]["headline"] == "An ask with no effect stated (pr#45)"
    for c in cards:
        for line in (c["headline"], cli.brief_line(c), cli._task_line(c)):
            assert plaintext.problems(line) == [], line


def test_every_task_title_over_the_demo_ledger_is_plain(tmp_path):
    path = tmp_path / "demo.db"
    demo.build(path, NOW)
    with Ledger(path, readonly=True) as led:
        cards = T.open_tasks(led, NOW)
        digest = T.digest(led, None, NOW)
    assert cards
    for c in cards + digest["resolved_without_you"] + digest["returned"]:
        for line in (c["headline"], cli.brief_line(c), cli._task_line(c)):
            assert plaintext.problems(line) == [], line
