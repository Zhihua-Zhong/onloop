"""One count of "waiting on you" on every view (finding #20): Now, Flow and the task list read the same projection."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from onloop import tasks
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


def test_an_empty_ledger_has_nothing_waiting_anywhere(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: NOW) as led:
        assert set(_counts(led, NOW).values()) == {0}
