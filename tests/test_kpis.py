"""The three KPIs, over fixture event streams. Every number is counted from events."""

import copy
import json

import pytest

from onloop import cli, kpi
from onloop import events as ev
from onloop import policy as pol

from .conftest import BASE, REPO, Clock
from .conftest import SlotLedger as Store

D0 = "2026-10-05T00:00:00.000Z"


def at(hours=0.0, day=5):
    """A stored timestamp: October `day`, 00:00 plus `hours`."""
    import datetime as dt

    base = dt.datetime(2026, 10, day, tzinfo=dt.UTC) + dt.timedelta(hours=hours)
    return ev.norm_ts(base)


@pytest.fixture
def st(tmp_path):
    clock = Clock()
    clock.t = clock.t.replace(day=30)  # "now" is late October, after every fixture
    s = Store(tmp_path / "k.db", clock=clock)
    yield s
    s.close()


_n = {"n": 0}


def put(st, kind, subject, when, actor=None, key=None, **payload):
    """One event in the ledger, as the GitHub ingest or the slots log it."""
    _n["n"] += 1
    eid = ev.event_id(REPO, kind, subject, key or f"k{_n['n']}")
    st.append(
        {
            "id": eid,
            "source": "legacy" if kind.startswith("slot.") else "github",
            "kind": kind,
            "subject_ref": subject,
            "actor": actor,
            "at": when,
            "payload": payload,
        }
    )


# -- lead time -------------------------------------------------------------------------------------------------------------


def merged(st, n, opened_h, merged_h, draft=False, ready_h=None):
    put(st, "pr.opened", f"pr#{n}", at(opened_h), head="h", draft=draft)
    if ready_h is not None:
        put(st, "pr.ready", f"pr#{n}", at(ready_h), head="h")
    put(st, "pr.merged", f"pr#{n}", at(merged_h), head="h")


def test_lead_time_is_the_median_and_p90_of_open_to_merge_in_hours(st):
    for i, hrs in enumerate([1, 2, 3, 4, 5, 6, 7, 8, 9, 100], start=1):
        merged(st, i, 0, hrs)
    lt = kpi.lead_time(st)
    assert lt["n"] == 10 and lt["median_hours"] == 5.5 and lt["p90_hours"] == 9


def test_a_pull_request_that_started_as_a_draft_is_measured_from_ready(st):
    merged(st, 1, 0, 10, draft=True, ready_h=8)
    assert kpi.lead_time(st)["median_hours"] == 2


def test_a_draft_never_readied_and_a_merge_with_no_open_are_counted_apart(st):
    put(st, "pr.opened", "pr#1", at(0), head="h", draft=True)
    put(st, "pr.merged", "pr#1", at(5), head="h")
    put(st, "pr.merged", "pr#2", at(5), head="h")
    lt = kpi.lead_time(st)
    assert lt["n"] == 0 and lt["unmeasured"] == ["pr#1", "pr#2"] and lt["median_hours"] is None


def test_the_window_is_by_merge_time(st):
    merged(
        st,
        1,
        0,
        2,
    )
    merged(st, 2, 24 * 3, 24 * 3 + 6)
    assert kpi.lead_time(st, since=at(24 * 2))["n"] == 1
    assert kpi.lead_time(st, until=at(24 * 2))["n"] == 1
    assert kpi.lead_time(st, since=at(24 * 2))["median_hours"] == 6


# -- idle with work waiting ------------------------------------------------------------------------------------------------


def test_overlapping_waits_on_one_role_are_counted_once(st):
    put(st, "pr.opened", "pr#1", at(0), head="a", draft=False)
    put(st, "pr.opened", "pr#2", at(1), head="b", draft=False)
    put(st, "review.submitted", "pr#1", at(4), commit="a", head="a", state="CHANGES_REQUESTED")
    put(st, "review.submitted", "pr#2", at(5), commit="b", head="b", state="CHANGES_REQUESTED")
    r = kpi.idle(st)["roles"]["reviewer"]
    assert r["hours"] == 5 and r["summed_hours"] == 8 and r["intervals"] == 2  # 0-5 once, not 4 + 4


def test_a_review_on_an_older_head_does_not_end_the_wait(st):
    put(st, "pr.opened", "pr#1", at(0), head="a", draft=False)
    put(st, "pr.head_moved", "pr#1", at(2), old="a", new="b", substantive=True)
    put(st, "review.submitted", "pr#1", at(3), commit="a", head="b", state="APPROVED")
    put(st, "review.submitted", "pr#1", at(6), commit="b", head="b", state="APPROVED")
    ivs = [(a, b) for a, b, _ in kpi.waiting_intervals(st)["reviewer"]]
    assert ivs == [(at(0), at(2)), (at(2), at(6))]


def test_a_harmless_push_does_not_restart_the_wait(st):
    put(st, "pr.opened", "pr#1", at(0), head="a", draft=False)
    put(st, "pr.head_moved", "pr#1", at(2), old="a", new="b", substantive=False)
    put(st, "review.submitted", "pr#1", at(4), commit="b", head="b", state="APPROVED")
    r = kpi.idle(st)["roles"]["reviewer"]
    assert r["hours"] == 4 and r["intervals"] == 1


def test_an_approved_head_waits_on_the_gate_until_it_merges_or_is_refused(st):
    put(st, "pr.opened", "pr#1", at(0), head="a", draft=False)
    put(st, "review.submitted", "pr#1", at(1), commit="a", head="a", state="APPROVED")
    put(st, "pr.merged", "pr#1", at(4), head="a")
    put(st, "pr.opened", "pr#2", at(0), head="c", draft=False)
    put(st, "review.submitted", "pr#2", at(1), commit="c", head="c", state="APPROVED")
    put(st, "gate.refused", "pr#2", at(3), head="c", codes=["G031"])
    idl = kpi.idle(st)["roles"]
    assert idl["gate"]["hours"] == 3 and idl["gate"]["intervals"] == 2  # 1-4 and 1-3 overlap: the union is 3


def test_a_draft_waits_on_nobody_until_it_is_ready(st):
    put(st, "pr.opened", "pr#1", at(0), head="a", draft=True)
    put(st, "pr.ready", "pr#1", at(10), head="a")
    put(st, "review.submitted", "pr#1", at(12), commit="a", head="a", state="APPROVED")
    assert kpi.idle(st)["roles"]["reviewer"]["hours"] == 2


def test_a_slot_request_waits_on_its_approver_until_granted_or_expired(st):
    put(st, "slot.requested", "slot:db", at(0), request=1, holder="x", role="r", ref="a")
    put(st, "slot.granted", "slot:db", at(3), request=1, grant=1, granted_by="human")
    put(st, "slot.requested", "slot:db", at(1), request=2, holder="y", role="r", ref="b")
    put(st, "slot.expired", "slot:db", at(5), what="request", request=2)
    idl = kpi.idle(st, slot_approvers={"db": "owner-a"})["roles"]["owner-a"]
    assert idl["hours"] == 5 and idl["intervals"] == 2


def test_open_waits_run_to_the_end_of_the_window_and_the_window_clips(st):
    put(st, "pr.opened", "pr#1", at(0), head="a", draft=False)
    assert kpi.idle(st, until=at(6))["roles"]["reviewer"]["hours"] == 6
    assert kpi.idle(st, since=at(2), until=at(6))["roles"]["reviewer"]["hours"] == 4


# -- reverts ---------------------------------------------------------------------------------------------------------------


def test_reverts_are_counted_over_merges_with_both_numbers_shown(st):
    for i in range(1, 5):
        merged(st, i, 0, i)
    put(st, "gate.reverted", "pr#3", at(3.5), head="h", merge_sha="m", revert_sha="r")
    rv = kpi.reverts(st)
    assert rv["merges"] == 4 and rv["reverts"] == 1 and rv["rate"] == 0.25
    assert kpi.reverts(st, since=at(24 * 4))["rate"] is None
    assert kpi.reverts(st)["gate"] == "recorded"


def test_merges_without_any_gate_decision_leave_the_gate_counts_unknown_never_zero(st):
    merged(st, 1, 0, 1)
    merged(st, 2, 0, 2)
    rv = kpi.reverts(st)
    assert (rv["merges"], rv["reverts"], rv["rate"], rv["gate"]) == (2, None, None, "unknown")
    put(
        st, "gate.merged", "pr#2", at(2), head="h"
    )  # one decision recorded in the window: the count is known again
    rv = kpi.reverts(st)
    assert (rv["reverts"], rv["rate"], rv["gate"]) == (0, 0.0, "recorded")
    assert kpi.reverts(st, since=at(100))["gate"] == "no merges"


# -- the command ------------------------------------------------------------------------------------------------------------


def test_the_kpi_command_prints_the_three_with_their_definitions(tmp_path, capsys):
    db = tmp_path / "c.db"
    with Store(db, clock=Clock()) as s:
        merged(s, 1, 0, 2)
    assert cli.main(["kpi", "--ledger", str(db)]) == 0
    out = capsys.readouterr().out
    assert "lead time: n=1" in out and "hours from pr.opened" in out
    assert "reverts: unknown" in out and "not 0" in out  # a merge, and no gate decision in the ledger
    assert cli.main(["kpi", "--ledger", str(db), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["lead_time"]["n"] == 1


def test_the_kpi_command_without_a_database_says_so(tmp_path, capsys):
    assert cli.main(["kpi", "--ledger", str(tmp_path / "none.db")]) == 1


def test_a_draft_that_is_pushed_to_and_never_readied_waits_on_nobody(st):
    put(st, "pr.opened", "pr#1", at(0), head="a", draft=True)
    put(st, "pr.head_moved", "pr#1", at(5), old="a", new="b", substantive=True)
    assert "reviewer" not in kpi.idle(st)["roles"]


# -- a drive-by review is not the verdict ------------------------------------------------------------------------------------------


def test_a_review_by_anyone_but_the_reviewer_neither_ends_the_wait_nor_starts_the_gates(st):
    put(st, "pr.opened", "pr#1", at(0), head="a", draft=False)
    put(st, "review.submitted", "pr#1", at(2), actor="drive-by/User", commit="a", head="a", state="APPROVED")
    put(st, "review.submitted", "pr#1", at(5), actor="rev-bot/Bot", commit="a", head="a", state="APPROVED")
    put(st, "pr.merged", "pr#1", at(9), head="a")
    ivs = kpi.waiting_intervals(st, reviewer="rev-bot")
    assert [(a, b) for a, b, _ in ivs["reviewer"]] == [(at(0), at(5))]
    assert [(a, b) for a, b, _ in ivs["gate"]] == [(at(5), at(9))]  # not from hour 2
    assert kpi.idle(st, reviewer="rev-bot")["roles"]["gate"]["hours"] == 4


def test_a_reviewer_name_without_a_type_matches_the_app_and_a_lookalike_login_does_not(st):
    put(st, "pr.opened", "pr#1", at(0), head="a", draft=False)
    put(st, "review.submitted", "pr#1", at(1), actor="rev-bot-2/Bot", commit="a", head="a", state="APPROVED")
    put(
        st,
        "review.submitted",
        "pr#1",
        at(3),
        actor="REV-BOT/Bot",
        commit="a",
        head="a",
        state="CHANGES_REQUESTED",
    )
    ivs = kpi.waiting_intervals(st, reviewer="rev-bot")
    assert [(a, b) for a, b, _ in ivs["reviewer"]] == [(at(0), at(3))]
    assert "gate" not in ivs


def test_a_user_with_the_reviewers_name_is_not_the_reviewer(st):
    put(st, "pr.opened", "pr#1", at(0), head="a", draft=False)
    put(st, "review.submitted", "pr#1", at(2), actor="rev-bot/User", commit="a", head="a", state="APPROVED")
    ivs = kpi.waiting_intervals(st, reviewer="rev-bot")  # the gate's rule: the App, a Bot
    assert [(a, b) for a, b, _ in ivs["reviewer"]] == [(at(0), st.now())] and "gate" not in ivs
    put(st, "review.submitted", "pr#1", at(4), actor="rev-bot/Bot", commit="a", head="a", state="APPROVED")
    assert [(a, b) for a, b, _ in kpi.waiting_intervals(st, reviewer="rev-bot")["reviewer"]] == [
        (at(0), at(4))
    ]


def test_the_kpi_command_hands_the_policys_reviewer_to_the_waits(tmp_path, capsys):
    import json as _json

    db = tmp_path / "k.db"
    s = Store(db, clock=Clock())
    put(s, "pr.opened", "pr#1", at(0), head="a", draft=False)
    put(s, "review.submitted", "pr#1", at(2), actor="drive-by/User", commit="a", head="a", state="APPROVED")
    s.close()
    raw = copy.deepcopy(BASE)
    pf = tmp_path / "onloop.yaml"
    pf.write_text(pol.yaml.safe_dump(raw), encoding="utf-8")
    assert cli.main(["kpi", "--ledger", str(db), "--policy", str(pf), "--until", at(10), "--json"]) == 0
    roles = _json.loads(capsys.readouterr().out)["idle"]["roles"]
    assert roles["reviewer"]["hours"] == 10 and "gate" not in roles
