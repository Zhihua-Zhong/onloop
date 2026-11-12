"""Intake: polls become events by diffing snapshots. Scripted GitHub, real SQLite."""

import copy
import datetime as dt
import json

import pytest

from onloop import policy as pol
from onloop.ingest import github as ingest
from onloop.testing import FakeGit, ScriptedGitHub

from .conftest import BASE, SlotLedger

H1, H2, H3 = "1" * 40, "2" * 40, "3" * 40
T0 = dt.datetime(2026, 10, 12, 0, 0, tzinfo=dt.UTC)


def pr(n=1, head=H1, **over):
    f = {
        "number": n,
        "state": "OPEN",
        "draft": False,
        "base": "main",
        "head": head,
        "title": "[builder] x",
        "body": "",
        "commit_messages": [],
        "dismissals": [],
        "reviews": [],
        "checks": [],
        "updated_at": "2026-10-12T00:00:00Z",
        "merged_by": None,
        "merged_at": None,
        "merge_sha": None,
    }
    f.update(over)
    return f


def review(
    rid=1, state="APPROVED", commit=H1, login="rev-bot", type_="Bot", at="2026-10-12T00:05:00Z", body=""
):
    return {
        "id": rid,
        "author": {"login": login, "type": type_},
        "state": state,
        "commit": commit,
        "submitted_at": at,
        "body": body,
    }


def step(*prs, main="m" * 40, rate=None):
    return {"prs": {p["number"]: p for p in prs}, "main": main, "rate": rate}


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def minutes(self, m):
        self.t += dt.timedelta(minutes=m)


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    st = SlotLedger(tmp_path / "t.db", clock=clock)  # the ledger, read in the old shape
    git = FakeGit()
    git.patch_ids = {}
    yield st, git, clock
    st.close()


def run(world, gh, *, policy=None, n=1, **kw):
    st, git, _clock = world
    out = []
    for _ in range(n):
        out.append(ingest.tick(st, "o/r", gh, git, policy=policy, **kw))
        gh.advance()
    return out[-1] if n == 1 else out


def kinds(st, subject=None):
    return [e["kind"] for e in st.events(subject=subject)]


def snaps(st):
    """The ingest's snapshot of each pull request, which the ledger keeps as ingest state."""
    return ingest.state(st, "o/r").snaps


def bare(payload):
    """An event's payload without the repository the ledger adds to it."""
    return {k: v for k, v in payload.items() if k != "repo"}


@pytest.fixture
def policy():
    return pol.from_dict(BASE)


# -- the basics --------------------------------------------------------------------------------------------------------


def test_a_new_pull_request_is_an_opened_event(world):
    st, git, _ = world
    gh = ScriptedGitHub([step(pr())])
    res = run(world, gh)
    assert kinds(st) == ["pr.opened"] and res.full == [1]
    assert st.events()[0]["payload"]["head"] == H1


def test_a_second_poll_of_an_unchanged_list_costs_no_pull_request_read(world):
    st, git, _ = world
    gh = ScriptedGitHub(
        [
            step(pr(checks=[{"name": "ci", "conclusion": "SUCCESS", "id": 9}])),
            step(pr(checks=[{"name": "ci", "conclusion": "SUCCESS", "id": 9}])),
        ]
    )
    run(world, gh)
    gh.calls.clear()
    res = run(world, gh)
    assert res.not_modified and [c[0] for c in gh.calls] == ["open_pr_list", "branch_head"]
    assert kinds(st) == ["pr.opened", "check.completed"]


def test_re_reading_the_same_facts_appends_nothing(world):
    st, git, _ = world
    f = pr(reviews=[review()], checks=[{"name": "ci", "conclusion": "SUCCESS", "id": 9}])
    gh = ScriptedGitHub([step(f), step(dict(copy.deepcopy(f), updated_at="2026-10-12T00:10:00Z"))])
    run(world, gh)
    before = len(st.events())
    res = run(world, gh)
    assert res.full == [1] and len(st.events()) == before  # read again, nothing new to say


def test_a_review_becomes_one_event_with_its_head_and_author(world):
    st, git, _ = world
    gh = ScriptedGitHub(
        [
            step(pr()),
            step(pr(reviews=[review(body="Cross-owner: lead ok")], updated_at="2026-10-12T00:06:00Z")),
        ]
    )
    run(world, gh, n=2)
    rv = [e for e in st.events() if e["kind"] == "review.submitted"]
    assert len(rv) == 1 and rv[0]["actor"] == "rev-bot/Bot" and rv[0]["at"] == "2026-10-12T00:05:00.000Z"
    assert rv[0]["payload"]["current"] is True and rv[0]["payload"]["state"] == "APPROVED"
    assert "Cross-owner" not in json.dumps(rv[0]["payload"])  # the body is hashed, never stored in the log


def test_a_dismissed_review_is_one_dismissal_event(world):
    st, git, _ = world
    gh = ScriptedGitHub(
        [
            step(pr(reviews=[review()])),
            step(
                pr(
                    reviews=[review(state="DISMISSED")],
                    updated_at="2026-10-12T00:07:00Z",
                    dismissals=[
                        {
                            "review_id": 1,
                            "previous_state": "APPROVED",
                            "actor": {"login": "owner-a", "type": "User"},
                            "at": "2026-10-12T00:07:00Z",
                        }
                    ],
                )
            ),
        ]
    )
    run(world, gh, n=2)
    assert kinds(st).count("review.dismissed") == 1


# -- check runs do not bump updated_at ---------------------------------------------------------------------------------


def test_a_check_that_turns_green_is_seen_though_the_pull_request_did_not_change(world):
    st, git, clock = world
    pending = [{"name": "ci", "conclusion": None, "id": 5}]
    green = [{"name": "ci", "conclusion": "SUCCESS", "id": 5}]
    gh = ScriptedGitHub([step(pr(checks=pending)), step(pr(checks=green))])  # same updated_at in both
    run(world, gh)
    gh.calls.clear()
    res = run(world, gh)
    assert res.settling == [1] and res.full == []
    assert ("pr", 1) not in gh.calls and any(
        c[0] == "checks" for c in gh.calls
    )  # one light call, not a full read
    done = [e for e in st.events() if e["kind"] == "check.completed"]
    assert len(done) == 1 and done[0]["payload"]["conclusion"] == "SUCCESS"


def test_a_pull_request_with_no_checks_yet_is_polled_until_the_tick_cap(world):
    st, git, clock = world
    gh = ScriptedGitHub([step(pr(checks=[]))] * 6)
    run(world, gh)
    seen = [run(world, gh, settle_ticks=3).settling for _ in range(4)]
    assert seen == [[1], [1], [1], []]  # given up: a repository with no CI is not polled for ever
    assert "check.completed" not in kinds(st)


def test_a_long_queue_is_not_given_up_on_by_the_clock(world):
    """The cap counts ticks, not minutes: a pull request whose required check is still queued two hours on is polled."""
    st, git, clock = world
    gh = ScriptedGitHub([step(pr(checks=[]))] * 4)
    run(world, gh)
    clock.minutes(120)
    assert run(world, gh).settling == [1]


def test_polling_continues_while_a_required_check_is_pending_though_another_check_finished(world, policy):
    st, git, _ = world
    other = [{"name": "lint", "conclusion": "SUCCESS", "id": 1}]
    gh = ScriptedGitHub(
        [step(pr(checks=other))] * 2
        + [step(pr(checks=other + [{"name": "ci", "conclusion": "SUCCESS", "id": 2}]))]
    )
    run(world, gh, policy=policy)
    assert run(world, gh, policy=policy).settling == [1]  # lint is green, required `ci` has not run yet
    run(world, gh, policy=policy)
    assert [e["payload"]["conclusion"] for e in st.events(kind="check.completed")][-1] == "SUCCESS"


def test_a_new_head_gets_a_fresh_tick_budget(world):
    st, git, _ = world
    gh = ScriptedGitHub([step(pr(checks=[]))] * 3 + [step(pr(head=H2, checks=[], updated_at="z"))] * 2)
    run(world, gh)
    run(world, gh, settle_ticks=1)
    assert run(world, gh, settle_ticks=1).settling == []
    run(world, gh, settle_ticks=1)  # the push: read in full
    assert run(world, gh, settle_ticks=1).settling == [1]


def test_a_settled_pull_request_is_not_polled_by_head(world):
    st, git, _ = world
    done = [{"name": "ci", "conclusion": "SUCCESS", "id": 5}]
    gh = ScriptedGitHub([step(pr(checks=done))] * 3)
    run(world, gh)
    assert run(world, gh).settling == []


def test_a_red_check_after_a_green_one_is_a_second_event(world):
    st, git, _ = world
    gh = ScriptedGitHub(
        [
            step(pr(checks=[{"name": "ci", "conclusion": "SUCCESS", "id": 5}])),
            step(
                pr(
                    checks=[
                        {"name": "ci", "conclusion": "SUCCESS", "id": 5},
                        {"name": "ci", "conclusion": "FAILURE", "id": 6},
                    ],
                    updated_at="2026-10-12T00:20:00Z",
                )
            ),
        ]
    )
    run(world, gh, n=2)
    assert [e["payload"]["conclusion"] for e in st.events(kind="check.completed")] == ["SUCCESS", "FAILURE"]


# -- ready toggles and head moves ----------------------------------------------------------------------------------------


def test_toggling_ready_twice_on_the_same_head_gives_two_ready_events(world):
    st, git, _ = world
    gh = ScriptedGitHub(
        [
            step(pr(draft=True)),
            step(pr(draft=False, updated_at="a")),
            step(pr(draft=True, updated_at="b")),
            step(pr(draft=False, updated_at="c")),
        ]
    )
    run(world, gh, n=4)
    assert kinds(st) == ["pr.opened", "pr.ready", "pr.drafted", "pr.ready"]
    assert len({e["id"] for e in st.events()}) == 4


def test_a_head_that_moved_with_the_same_patch_is_not_substantive(world):
    st, git, _ = world
    git.patch_ids = {H1: "patch-a", H2: "patch-a"}
    gh = ScriptedGitHub([step(pr(head=H1)), step(pr(head=H2, updated_at="b"))])
    run(world, gh, n=2)
    moved = bare(st.events(kind="pr.head_moved")[0]["payload"])
    assert moved == {"old": H1, "new": H2, "substantive": False, "basis": "patch-id"}


def test_a_head_that_moved_with_another_patch_is_substantive(world):
    st, git, _ = world
    git.patch_ids = {H1: "patch-a", H2: "patch-b"}
    gh = ScriptedGitHub([step(pr(head=H1)), step(pr(head=H2, updated_at="b"))])
    run(world, gh, n=2)
    assert st.events(kind="pr.head_moved")[0]["payload"]["substantive"] is True


def test_a_head_whose_patch_cannot_be_computed_is_substantive_with_basis_unknown(world):
    st, git, _ = world
    git.patch_ids = {}
    gh = ScriptedGitHub([step(pr(head=H1)), step(pr(head=H2, updated_at="b"))])
    run(world, gh, n=2)
    payload = st.events(kind="pr.head_moved")[0]["payload"]
    assert (
        payload["substantive"] is True and payload["basis"] == "unknown"
    )  # fails closed for `if not substantive`


def test_checks_of_the_old_head_do_not_complete_on_the_new_one(world):
    st, git, _ = world
    ok = [{"name": "ci", "conclusion": "SUCCESS", "id": 5}]
    gh = ScriptedGitHub([step(pr(head=H1, checks=ok)), step(pr(head=H2, checks=ok, updated_at="b"))])
    run(world, gh, n=2)
    heads = [e["payload"]["head"] for e in st.events(kind="check.completed")]
    assert heads == [H1, H2]  # same run id, a new head: a new completion


# -- verdicts ------------------------------------------------------------------------------------------------------------


def test_an_approval_that_becomes_a_request_for_changes_is_a_reversal(world, policy):
    st, git, _ = world
    gh = ScriptedGitHub(
        [
            step(pr(reviews=[review(1)])),
            step(
                pr(
                    reviews=[review(1), review(2, state="CHANGES_REQUESTED", at="2026-10-12T00:30:00Z")],
                    updated_at="b",
                )
            ),
        ]
    )
    run(world, gh, n=2, policy=policy)
    rev = st.events(kind="verdict.reversed")
    assert (
        len(rev) == 1
        and rev[0]["payload"]["from"] == "APPROVED"
        and rev[0]["payload"]["to"] == "CHANGES_REQUESTED"
    )


def test_a_first_verdict_is_not_a_reversal(world, policy):
    st, git, _ = world
    gh = ScriptedGitHub([step(pr()), step(pr(reviews=[review(1)], updated_at="b"))])
    run(world, gh, n=2, policy=policy)
    assert st.events(kind="verdict.reversed") == []


def test_a_verdict_by_the_wrong_identity_is_not_a_reversal(world, policy):
    st, git, _ = world
    stranger = {"login": "someone", "type": "User"}
    r1 = dict(review(1), author=stranger)
    r2 = dict(review(2, state="CHANGES_REQUESTED", at="2026-10-12T00:30:00Z"), author=stranger)
    gh = ScriptedGitHub([step(pr(reviews=[r1])), step(pr(reviews=[r1, r2], updated_at="b"))])
    run(world, gh, n=2, policy=policy)
    assert st.events(kind="verdict.reversed") == []


# -- leaving the open list ------------------------------------------------------------------------------------------------


def test_a_merged_pull_request_is_a_merged_event_once(world):
    st, git, _ = world
    merged = pr(
        state="MERGED",
        merge_sha="c" * 40,
        merged_at="2026-10-12T01:00:00Z",
        merged_by={"login": "agent-bot", "type": "Bot"},
        updated_at="z",
    )
    gh = ScriptedGitHub([step(pr()), step(merged), step(merged)])
    run(world, gh, n=3)
    m = st.events(kind="pr.merged")
    assert len(m) == 1 and m[0]["payload"]["merge_sha"] == "c" * 40 and m[0]["actor"] == "agent-bot/Bot"
    assert m[0]["at"] == "2026-10-12T01:00:00.000Z"
    assert [v["state"] for v in snaps(st).values()] == ["MERGED"]


def test_a_closed_pull_request_is_a_closed_event(world):
    st, git, _ = world
    gh = ScriptedGitHub([step(pr()), step(pr(state="CLOSED", updated_at="z"))])
    run(world, gh, n=2)
    assert kinds(st) == ["pr.opened", "pr.closed"]


def test_a_reopened_pull_request_is_an_event_and_its_snapshot_is_refreshed(world):
    st, git, _ = world
    gh = ScriptedGitHub(
        [step(pr()), step(pr(state="CLOSED", updated_at="b")), step(pr(updated_at="c", title="again"))]
    )
    run(world, gh, n=3)
    assert kinds(st) == ["pr.opened", "pr.closed", "pr.reopened"]
    (row,) = snaps(st).values()
    assert (row["state"], row["updated_at"]) == ("OPEN", "c")


def test_the_last_review_before_a_merge_is_not_lost(world):
    st, git, _ = world
    merged = pr(
        state="MERGED",
        merge_sha="c" * 40,
        merged_at="2026-10-12T01:00:00Z",
        reviews=[review()],
        merged_by={"login": "agent-bot", "type": "Bot"},
        updated_at="z",
    )
    gh = ScriptedGitHub([step(pr()), step(merged)])
    run(world, gh, n=2)
    assert kinds(st) == ["pr.opened", "review.submitted", "pr.merged"]


def test_the_base_branch_advancing_is_an_event_but_the_first_look_is_not(world):
    st, git, _ = world
    gh = ScriptedGitHub([step(pr(), main="a" * 40), step(pr(), main="a" * 40), step(pr(), main="b" * 40)])
    run(world, gh, n=3)
    adv = st.events(kind="main.advanced")
    assert len(adv) == 1 and bare(adv[0]["payload"]) == {"old": "a" * 40, "new": "b" * 40}


# -- cost and failure -----------------------------------------------------------------------------------------------------


def test_the_rate_limit_is_reported_from_the_response(world):
    st, git, _ = world
    gh = ScriptedGitHub([step(pr(), rate={"remaining": 123, "limit": 5000, "reset": 1})])
    assert run(world, gh).rate["remaining"] == 123


def test_a_failed_read_leaves_the_log_and_the_snapshots_untouched(world):
    st, git, _ = world

    class Breaks(ScriptedGitHub):
        def branch_head(self, branch):
            raise RuntimeError("network down")

    gh = Breaks([step(pr(reviews=[review()]))])
    with pytest.raises(RuntimeError):
        run(world, gh)
    assert st.events() == [] and snaps(st) == {}
    assert ingest.state(st, "o/r").etag is None


def test_a_failure_while_applying_rolls_back_every_event_and_snapshot(world, monkeypatch):
    st, git, _ = world
    gh = ScriptedGitHub([step(pr(reviews=[review()]), pr(2, head=H2))])
    real = st.append
    count = {"n": 0}

    def flaky(event):
        count["n"] += 1
        if count["n"] == 3:
            raise RuntimeError("disk full")
        return real(event)

    monkeypatch.setattr(st, "append", flaky)
    with pytest.raises(RuntimeError):
        run(world, gh)
    assert st.events() == [] and snaps(st) == {}
    monkeypatch.setattr(st, "append", real)
    run(world, ScriptedGitHub([step(pr(reviews=[review()]), pr(2, head=H2))]))
    assert len(snaps(st)) == 2  # the retry loses nothing


def test_the_head_ref_is_fetched_only_when_a_head_is_new(world):
    st, git, _ = world
    gh = ScriptedGitHub(
        [
            step(pr(head=H1)),
            step(pr(head=H1, updated_at="b", title="renamed")),
            step(pr(head=H2, updated_at="c")),
        ]
    )
    run(world, gh, n=3)
    fetches = [c for c in git.calls if c[0] == "fetch"]
    assert len(fetches) == 2 and fetches[0][-1].endswith("pull/1/head:refs/onloop/pr/1")
