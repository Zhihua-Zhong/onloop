"""The map's zoom 0 (decision 0018): colour as the worst of the children, a reason under every red or amber block,
"not reported" never zero, honest ages, and one count everywhere. All data here is made up."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from onloop import derive
from onloop.contract import reader
from onloop.overlay.ledger import Ledger

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("contract_example_z0", ROOT / "tools" / "contract_example.py")
example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(example)
END = dt.datetime(2026, 11, 17, 2, 0, 0, tzinfo=dt.UTC)
NOW = "2026-11-17T03:00:00.000Z"


def _replay(tmp_path_factory, contract):
    root = tmp_path_factory.mktemp(f"replay{contract}")
    example.write(root, END, days=2, n=300, seed=3, contract=contract)
    (root / "onloop").mkdir()
    assert [r.status for r in reader.import_snapshots(root, root / "onloop" / "ledger.db")] == [
        "imported"
    ] * 2
    return root / "onloop" / "ledger.db"


@pytest.fixture(scope="module")
def replay_1_1(tmp_path_factory):
    return _replay(tmp_path_factory, "1.1")


@pytest.fixture(scope="module")
def replay_1_0(tmp_path_factory):
    return _replay(tmp_path_factory, "1.0")


def _zoom0(path, now=NOW, **kw):
    with Ledger(path, readonly=True) as led:
        return derive.zoom0(led, now, **kw)


def test_a_block_is_the_worst_of_its_children_in_a_fixed_order():
    assert derive.worst(["moving", "grey", "red", "amber"]) == "red"
    assert derive.worst(["grey", "moving", "amber"]) == "amber"
    assert derive.worst(["grey", "moving", "done"]) == "moving"
    assert derive.worst(["grey", "done"]) == "grey"
    assert derive.worst(["done", "done"]) == "done" and derive.worst([]) == "grey"


def test_every_blocks_colour_is_computed_from_its_leaves_never_typed(replay_1_1):
    z = _zoom0(replay_1_1)
    kinds = [b["kind"] for b in z["blocks"]]
    assert kinds == ["checklist", "feedback", "changes"]  # grouped by origin, never by role
    for b in z["blocks"]:
        assert b["colour"] == derive.worst([lf["colour"] for lf in b["leaves"]]), b["id"]
        some_done = any(lf["colour"] == "done" for lf in b["leaves"])
        yours = any(lf["colour"] == "amber" and lf.get("who") == "you" for lf in b["leaves"])
        want = (
            "nothing moving"
            if b["colour"] == "grey" and some_done
            else derive.LABEL_MARKED
            if b["colour"] == "amber" and not yours
            else derive.LABELS[b["colour"]]
        )
        assert b["shape"] == derive.SHAPES[b["colour"]] and b["label"] == want, b["id"]
    assert {b["colour"] for b in z["blocks"]} >= {"amber", "red"}  # the replay has both, so the test can bite


def test_every_red_or_amber_block_says_which_child_and_why(replay_1_1):
    z = _zoom0(replay_1_1)
    for b in z["blocks"]:
        if b["colour"] in ("red", "amber"):
            r = b["reason"]
            assert r is not None, b["id"]
            child = next(lf for lf in b["leaves"] if lf["id"] == r["child"])
            assert child["colour"] == b["colour"] and (
                child["title"] in r["text"] or child["id"] in r["text"]
            )
        else:
            assert b["reason"] is None


def test_a_stage_with_no_evidence_is_not_reported_never_zero(replay_1_0, replay_1_1):
    """Contract 1.0 sends no checks and no reviews: those stages say "not reported", never 0 (Q24)."""
    old = next(b for b in _zoom0(replay_1_0)["blocks"] if b["kind"] == "changes")
    new = next(b for b in _zoom0(replay_1_1)["blocks"] if b["kind"] == "changes")
    by = {s["stage"]: s for s in old["stages"]}
    assert by["checks"]["reported"] is False and by["checks"]["count"] is None
    assert by["review"]["reported"] is False and by["review"]["count"] is None
    assert all(s["reported"] and isinstance(s["count"], int) for s in new["stages"])


def test_the_feedback_tally_reads_the_teams_declared_stages(replay_1_1):
    b = next(b for b in _zoom0(replay_1_1)["blocks"] if b["kind"] == "feedback")
    assert b["bar"]["label"] == "0 of 3 accepted" and b["reported_by_team"] is True
    assert b["tally"]["amber"] == 2 and sum(b["tally"].values()) == 3
    assert all(lf["reported_by_team"] for lf in b["leaves"])


def test_the_waits_are_ranked_oldest_first_and_the_persons_row_counts_their_tasks(replay_1_1):
    z = _zoom0(replay_1_1)
    ages = [w["oldest_s"] for w in z["waits"]]
    assert ages == sorted(ages, reverse=True)
    you = next(w for w in z["waits"] if w["holder"] == "you")
    assert you["count"] == z["signals"]["waiting_on_you"] == 1
    assert you["lower_bound"] is False and not you["age_text"].startswith(
        "≥"
    )  # PR 3's opening is in the export


def _unseen_start(tmp_path):
    """A wait on the person whose change onloop never saw opening: an export repeats it with its old since."""
    path = tmp_path / "l.db"
    with Ledger(path, clock=lambda: NOW) as led:
        led.append(
            {
                "id": "w1",
                "source": "import",
                "kind": "wait.opened",
                "subject_ref": "pr#9",
                "at": "2026-11-05T09:00:00.000Z",
                "payload": {
                    "id": "import:pr#9/human/2026-11-05T09:00:00.000Z",
                    "item_id": "pr#9",
                    "waiter": "pr#9",
                    "on_kind": "human",
                    "on_ref": "human",
                    "reason": "needs_human",
                    "ref": "pr#9",
                    "since": "2026-11-05T09:00:00.000Z",
                    "effect": "approve the change (pr#9), so it can move",
                },
            }
        )
    return path


def test_an_age_whose_start_onloop_cannot_see_is_shown_as_a_lower_bound(tmp_path):
    z = _zoom0(_unseen_start(tmp_path))
    you = next(w for w in z["waits"] if w["holder"] == "you")
    assert you["lower_bound"] is True and you["age_text"] == "≥ 11 days"
    assert you["note"] == "began before the first export"
    changes = next(b for b in z["blocks"] if b["kind"] == "changes")
    assert changes["colour"] == "amber" and "≥ 11 days" in changes["reason"]["text"]


def test_the_same_wait_is_exact_once_its_opening_is_seen(tmp_path):
    path = _unseen_start(tmp_path)
    with Ledger(path, clock=lambda: NOW) as led:
        led.append(
            {
                "id": "o1",
                "source": "import",
                "kind": "pr.opened",
                "subject_ref": "pr#9",
                "at": "2026-11-05T08:00:00.000Z",
                "payload": {"number": 9},
            }
        )
    you = next(w for w in _zoom0(path)["waits"] if w["holder"] == "you")
    assert you["lower_bound"] is False and you["age_text"] == "11 days" and you["note"] is None


def test_a_teams_unmoved_stage_turns_red_past_its_threshold_and_hours_can_be_set(replay_1_1):
    days = next(b for b in _zoom0(replay_1_1)["blocks"] if b["kind"] == "feedback")
    hours = next(b for b in _zoom0(replay_1_1, stuck={"declared": 1800})["blocks"] if b["kind"] == "feedback")
    assert days["tally"]["red"] == 0 and hours["tally"]["red"] == 1  # the one item with the team, at 1 h
    assert hours["colour"] == "red" and "no move for 1 h" in hours["reason"]["text"]


def test_team_quiet_is_the_teams_longest_pause_in_the_last_day(tmp_path):
    """Turns that overlap are one stretch of work; the longest gap between stretches is the answer (Q17)."""
    path = tmp_path / "l.db"
    export = dt.datetime(2026, 11, 17, 2, 40, tzinfo=dt.UTC)
    with Ledger(path, clock=lambda: NOW) as led:
        for n, (role, start, end) in enumerate(
            [("builder", (0, 0), (0, 10)), ("lead", (0, 5), (0, 20)), ("builder", (1, 58), (2, 30))]
        ):
            s_ = dt.datetime(2026, 11, 17, *start, tzinfo=dt.UTC)
            e_ = dt.datetime(2026, 11, 17, *end, tzinfo=dt.UTC)
            data = {"role": role, "session_id": "s", "prompt_id": f"p{n}", "started_at": example._ts(s_)}
            led.append(reader.to_event(example._record("onloop.turn.ended", f"t{n}", e_, export, data), "p"))
    m = _zoom0(path)["signals"]["team_quiet"]
    assert m["text"] == "1 h 38 min" and m["ongoing"] is False
    assert m["from"] == "2026-11-17T00:20:00.000Z" and m["to"] == "2026-11-17T01:58:00.000Z"


def test_plain_ages():
    assert derive.plain_age(40 * 60) == "40 min"
    assert derive.plain_age(98 * 60) == "1 h 38 min"
    assert derive.plain_age(14 * 3600) == "14 h"
    assert derive.plain_age(86400) == "1 day" and derive.plain_age(9 * 86400 + 5, True) == "≥ 9 days"


def test_a_goal_without_a_title_reads_in_plain_words():
    assert derive.plain_title("release-plan") == "Release plan"
    assert derive.plain_title("demo_checklist") == "Demo checklist"


def test_the_checklist_block_has_its_plain_title(replay_1_1):
    b = next(b for b in _zoom0(replay_1_1)["blocks"] if b["kind"] == "checklist")
    assert b["title"] == "Release plan"
