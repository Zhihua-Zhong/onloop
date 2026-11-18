"""One count of "waiting on you" on every view (finding #20): Now, Flow and the task list read the same projection."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

from onloop import tasks
from onloop.contract import reader
from onloop.overlay.ledger import Ledger
from onloop.report import views

NOW = "2026-11-09T14:52:00.000Z"
TOOL = Path(__file__).resolve().parent.parent / "tools" / "demo_ledger.py"
_spec = importlib.util.spec_from_file_location("demo_ledger", TOOL)
demo = importlib.util.module_from_spec(_spec)
sys.modules["demo_ledger"] = demo
_spec.loader.exec_module(demo)


def _counts(led, now):
    n = views.now(led, now)
    f = views.flow(led, now)
    t = views.tasks_view(led, now)
    human = next((r for r in f["resources"] if r["id"] == "human"), None)
    return {
        "now": len(n["waiting_on_you"]),
        "flow": f["waiting_on_you"]["count"],
        "flow_you_node": human["waiting"] if human else f["waiting_on_you"]["count"],
        "tasks_api": t["count"],
        "task_list": len(tasks.open_tasks(led, now)),
    }


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


@pytest.fixture(scope="module")
def demo_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "ledger.db"
    demo.build(path, NOW)
    return path


def test_now_flow_and_the_task_list_agree_on_the_demo_ledger(demo_path):
    with Ledger(demo_path, readonly=True) as led:
        c = _counts(led, NOW)
    assert len(set(c.values())) == 1 and c["now"] > 0, c


def test_they_agree_when_some_asks_are_not_cards_on_the_canvas(demo_path, tmp_path):
    """The dogfood's shape: asks on documents and decisions, which are no card on Flow's canvas. Flow says how many of
    the tasks are cards; the count is still the task list's."""
    path = tmp_path / "ledger.db"
    path.write_bytes(demo_path.read_bytes())
    with Ledger(path, clock=lambda: NOW) as led:
        for i, ref in enumerate(("docs/plan.md", "a-decision", "pr#99")):
            _v1_ask(led, f"ask{i}", ref)
        c = _counts(led, NOW)
        f = views.flow(led, NOW)["waiting_on_you"]
    assert len(set(c.values())) == 1, c
    assert f["count"] == len(f["task_ids"]) and f["count"] > len([c for c in f["cards"] if c == "pr#99"])


EXAMPLE = Path(__file__).resolve().parent.parent / "tools" / "contract_example.py"
_espec = importlib.util.spec_from_file_location("contract_example", EXAMPLE)
example = importlib.util.module_from_spec(_espec)
sys.modules.setdefault("contract_example", example)
_espec.loader.exec_module(example)
REPLAY_END = dt.datetime(2026, 11, 17, 2, 0, 0, tzinfo=dt.UTC)
REPLAY_NOW = "2026-11-17T03:00:00.000Z"


@pytest.fixture(scope="module")
def replay_path(tmp_path_factory):
    """The generic replay: two daily snapshots in contract 1.1, made up, imported as a team's export is. It has the
    shapes the demo lacks: imported turns, a wait on the person that opens a task, waits on a role and on the team."""
    root = tmp_path_factory.mktemp("replay")
    example.write(root, REPLAY_END, days=2, n=400, seed=3, contract="1.1")
    (root / "onloop").mkdir()
    results = reader.import_snapshots(root, root / "onloop" / "ledger.db")
    assert [r.status for r in results] == ["imported", "imported"], [r.as_dict() for r in results]
    return root / "onloop" / "ledger.db"


def test_now_flow_and_the_task_list_agree_on_the_generic_replay(replay_path):
    with Ledger(replay_path, readonly=True) as led:
        c = _counts(led, REPLAY_NOW)
        n = views.now(led, REPLAY_NOW)
        f = views.flow(led, REPLAY_NOW)
    assert len(set(c.values())) == 1 and c["now"] > 0, c
    assert (n["constraint"] is None) == (f["constraint"] is None)
    if n["constraint"] is not None:
        assert f["constraint"]["on_ref"] == n["constraint"]["on_ref"]
        assert f["constraint"]["waiters"] == len(n["constraint"]["waiters"])


def test_an_empty_ledger_has_nothing_waiting_anywhere(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: NOW) as led:
        assert set(_counts(led, NOW).values()) == {0}


def test_tasks_without_any_card_still_agree_and_the_constraint_counts_the_same(tmp_path):
    """The lead's check on the dogfood ledger: tasks, but no items at all. Flow had said nothing (an empty canvas) and
    "holding up 0 cards" while Now said 9 tasks and "lead holds 1 change"."""
    with Ledger(tmp_path / "l.db", clock=lambda: NOW) as led:
        for i, ref in enumerate(("docs/plan.md", "a-decision", "release-checklist")):
            _v1_ask(led, f"ask{i}", ref)
        led.append(
            {
                "id": "handoff",
                "source": "hook",
                "kind": "hook.stop",
                "subject_ref": "role:builder",
                "payload": {
                    "role": "builder",
                    "session_id": "s2",
                    "line": "outcome: handoff owner=lead ref=docs/plan.md",
                    "outcome": {"value": "handoff", "ref": "docs/plan.md", "owner": "lead", "waker": None},
                },
            }
        )
        c = _counts(led, NOW)
        n = views.now(led, NOW)
        f = views.flow(led, NOW)
    assert f["cards"] == [] and len(set(c.values())) == 1 and c["now"] == 3, c
    assert n["constraint"] is not None and f["constraint"] is not None
    assert f["constraint"]["waiters"] == len(n["constraint"]["waiters"])
    assert f["constraint"]["on_ref"] == n["constraint"]["on_ref"]
