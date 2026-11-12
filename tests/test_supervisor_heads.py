"""Pull requests keyed on their head commit, and kept out of a queue once a login has answered that head."""

import copy
import json
import subprocess

import pytest
import yaml

from onloop.supervisor import config, items, planner, schedule, sources, step, steplog
from onloop.supervisor.config import AnswerSpec, ConfigError

from .supervisor_helpers import NOW, RAW, pr, ts

A, B = "a" * 40, "b" * 40
BOT = "review-bot[bot]"
SPEC = AnswerSpec(BOT, comment_marker="<!-- verdict")


def pull(n, head, draft=False, updated="2026-11-01T09:00:00Z"):
    return {"number": n, "draft": draft, "head": {"sha": head}, "updated_at": updated}


def review(login, head, state="APPROVED"):
    return {"user": {"login": login}, "commit_id": head, "state": state}


def comment(login, body):
    return {"user": {"login": login}, "body": body}


def reviewer_raw(**trigger):
    raw = copy.deepcopy(RAW)
    raw["roles"] = [
        {
            **raw["roles"][1],
            "prompt": "Review $item_id at $item_head",
            "triggers": [
                {
                    "kind": "pr",
                    "draft": False,
                    "version": "head",
                    "unanswered_by": {"login": BOT, "comment_marker": "<!-- verdict"},
                    **trigger,
                }
            ],
        }
    ]
    return raw


def fixture_cfg(tmp_path, open_, pulls, reviews=None, comments=None, raw=None):
    raw = raw or reviewer_raw()
    (tmp_path / "fixture.json").write_text(
        json.dumps({"open": open_, "pulls": pulls, "reviews": reviews or {}, "comments": comments or {}}),
        encoding="utf-8",
    )
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return config.load(tmp_path / "s.yaml")


def look(cfg):
    return sources.make(cfg.source, cfg.asks, lambda: NOW).look()


# -- configuration --------------------------------------------------------------------------------------------------


def test_the_source_reads_heads_and_answers_only_when_a_trigger_asks_for_them(tmp_path):
    cfg = config.from_dict(reviewer_raw(), tmp_path / "s.yaml")
    assert cfg.source.heads and cfg.source.answers == (SPEC,)
    assert cfg.roles[0].triggers[0].version == "head"
    plain = config.from_dict(copy.deepcopy(RAW), tmp_path / "s.yaml")
    assert not plain.source.heads and plain.source.answers == ()


@pytest.mark.parametrize(
    "trigger, message",
    [
        ({"kind": "issue", "version": "head"}, "head only for kind pr"),
        ({"kind": "issue", "unanswered_by": BOT}, "only for kind pr"),
        ({"kind": "pr", "unanswered_by": "not a login"}, "GitHub login"),
        ({"kind": "pr", "unanswered_by": {"login": BOT, "review_states": ["DISMISSED"]}}, "review_states"),
        ({"kind": "pr", "unanswered_by": {"login": BOT, "extra": 1}}, "unknown keys"),
    ],
)
def test_bad_head_and_answer_triggers_are_refused(tmp_path, trigger, message):
    raw = copy.deepcopy(RAW)
    raw["roles"] = [{**raw["roles"][1], "triggers": [trigger]}]
    with pytest.raises(ConfigError, match=message):
        config.from_dict(raw, tmp_path / "s.yaml")


def test_a_prompt_can_live_in_its_own_file(tmp_path):
    (tmp_path / "reviewer.txt").write_text("Review $item_id at $item_head\n", encoding="utf-8")
    raw = reviewer_raw()
    role = raw["roles"][0]
    del role["prompt"]
    role["prompt_file"] = "reviewer.txt"
    cfg = config.from_dict(raw, tmp_path / "s.yaml")
    assert cfg.roles[0].prompt == "Review $item_id at $item_head\n"
    role["prompt"] = "x"
    with pytest.raises(ConfigError, match="not both"):
        config.from_dict(raw, tmp_path / "s.yaml")
    del role["prompt"]
    role["prompt_file"] = "missing.txt"
    with pytest.raises(ConfigError, match="could not be read"):
        config.from_dict(raw, tmp_path / "s.yaml")


# -- what counts as an answer ---------------------------------------------------------------------------------------


def test_only_the_login_on_exactly_this_head_answers_it():
    k = (SPEC.key,)
    assert items.answered_keys(A, [review(BOT, A)], [], (SPEC,)) == k
    assert items.answered_keys(A, [review(BOT, A, "CHANGES_REQUESTED")], [], (SPEC,)) == k
    assert items.answered_keys(A, [review(BOT, A, "COMMENTED")], [], (SPEC,)) == ()  # a comment-only review
    assert items.answered_keys(B, [review(BOT, A)], [], (SPEC,)) == ()  # an earlier head
    assert items.answered_keys(A, [review("someone", A)], [], (SPEC,)) == ()  # another login
    assert items.answered_keys(A, [], [comment(BOT, f'<!-- verdict {{"head": "{A}"}} -->')], (SPEC,)) == k
    assert items.answered_keys(B, [], [comment(BOT, f"<!-- verdict {A} -->")], (SPEC,)) == ()
    assert items.answered_keys(A, [], [comment(BOT, f"no verdict yet at {A}")], (SPEC,)) == ()  # no marker
    assert items.answered_keys(A, [], [comment("someone", f"<!-- verdict {A} -->")], (SPEC,)) == ()
    assert items.answered_keys("not-a-sha", [review(BOT, "not-a-sha")], [], (SPEC,)) == ()


# -- the queue ------------------------------------------------------------------------------------------------------


def _plan(cfg, snap, records):
    return schedule.plan_steps(
        cfg.roles,
        planner.queues_of(cfg, snap),
        steplog.step_view(records, "dry_run"),
        ts(NOW),
        max_parallel=4,
    )


def _ran(due):
    base = {
        "mode": "dry_run",
        "step_id": due.sid,
        "attempt": 1,
        "role": due.role,
        "item_id": due.item.id,
        "item_version": due.item.version,
        "round": due.round,
    }
    return [
        {"type": "step.started", **base, "at": ts(NOW)},
        {"type": "step.ended", **base, "at": ts(NOW), "outcome": "dry_run", "final": True},
    ]


def test_a_comment_or_label_on_the_same_head_is_not_a_new_step_but_a_new_head_is(tmp_path):
    cfg = fixture_cfg(tmp_path, [pr(12)], [pull(12, A)])
    first = _plan(cfg, look(cfg), [])
    assert [(d.item.id, d.item.version) for d in first.due] == [("pr#12", f"head={A}")]
    records = _ran(first.due[0])
    cfg = fixture_cfg(
        tmp_path,
        [pr(12, updated="2026-11-01T10:00:00Z", labels=["x"])],
        [pull(12, A, updated="2026-11-01T10:00:00Z")],
    )
    assert _plan(cfg, look(cfg), records).due == []  # updated_at moved, the head did not
    cfg = fixture_cfg(tmp_path, [pr(12, updated="2026-11-01T11:00:00Z")], [pull(12, B)])
    again = _plan(cfg, look(cfg), records)
    assert [d.item.version for d in again.due] == [f"head={B}"] and again.due[0].sid != first.due[0].sid


def test_an_answered_head_leaves_the_queue_and_a_draft_never_enters_it(tmp_path):
    cfg = fixture_cfg(
        tmp_path,
        [pr(12), pr(13), pr(14)],
        [pull(12, A), pull(13, B), pull(14, A, draft=True)],
        reviews={"12": [review(BOT, A)]},
        comments={"13": [comment(BOT, f"<!-- verdict {A} -->")]},
    )
    snap = look(cfg)
    assert [i.id for i in planner.queues_of(cfg, snap)["reviewer"]] == [
        "pr#13"
    ]  # 13's verdict is on another head
    pr12 = next(i for i in snap.items if i.id == "pr#12")
    assert pr12.head == A and pr12.answered == (SPEC.key,)
    assert items.WorkItem.from_dict(pr12.as_dict()) == pr12  # the snapshot keeps head and answers


def test_the_prompt_names_the_head(tmp_path):
    cfg = fixture_cfg(tmp_path, [pr(12)], [pull(12, A)])
    due = _plan(cfg, look(cfg), []).due[0]
    assert step.render_prompt(cfg.roles[0], due.item, sid=due.sid) == f"Review pr#12 at {A}"


def test_a_pull_request_missing_from_the_pulls_listing_waits_for_the_next_look(tmp_path):
    cfg = fixture_cfg(tmp_path, [pr(12)], [])
    assert planner.queues_of(cfg, look(cfg))["reviewer"] == []


# -- reading GitHub -------------------------------------------------------------------------------------------------


def test_the_github_source_reads_reviews_and_comments_again_only_when_the_pull_request_changed(tmp_path):
    raw = reviewer_raw()
    raw["source"] = {"type": "github", "repo": "example-org/example-repo"}
    del raw["asks"]  # no asks: only the pull request reads count
    cfg = config.from_dict(raw, tmp_path / "s.yaml")
    state = {"updated": "2026-11-01T09:00:00Z", "head": A}
    calls = []

    def runner(argv):
        calls.append(argv)
        path = argv[-1]
        if "/issues?state=open" in path:
            body = [pr(12, updated=state["updated"])]
        elif "/pulls?state=open" in path:
            body = [pull(12, state["head"], updated=state["updated"])]
        elif path.endswith("/pulls/12/reviews?per_page=100"):
            body = [review(BOT, A)]
        elif path.endswith("/issues/12/comments?per_page=100"):
            body = []
        else:
            raise AssertionError(path)
        return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")

    src = sources.make(cfg.source, cfg.asks, lambda: NOW, runner)
    assert src.look().items[0].answered == (SPEC.key,)
    assert len(calls) == 4 and all(a[:4] == ["gh", "api", "--method", "GET"] for a in calls)
    src.look()
    assert len(calls) == 6  # nothing changed: the two listings only
    state["head"] = B
    snap = src.look()
    assert len(calls) == 10 and snap.items[0].answered == ()  # a new head: read again, the old review is old
