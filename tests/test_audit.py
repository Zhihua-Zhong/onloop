import copy

import yaml

from onloop import audit
from onloop import policy as pol
from onloop.testing import FakeGit, FakeGitHub

from .conftest import BASE, review

ROOT, MERGE, HEAD = "a" * 40, "c" * 40, "b" * 40
BOT = {"login": "agent-bot", "type": "Bot"}


def world(raw=None, files=("src/a.py",), head_raw=None, **over):
    raw = raw or copy.deepcopy(BASE)
    commits = {ROOT: {"parents": [], "message": "root", "tree": "T0", "files": {pol.POLICY_FILE: yaml.safe_dump(raw)}},
               HEAD: {"parents": [ROOT], "message": "", "tree": "T1",
                      "files": {pol.POLICY_FILE: yaml.safe_dump(head_raw or raw)}},
               MERGE: {"parents": [ROOT, HEAD], "message": "Merge", "tree": "T2", "files": {}}}
    facts = {"number": 7, "state": "MERGED", "draft": False, "base": "main", "head": HEAD, "title": "[builder] fix",
             "body": "", "commit_messages": [], "reviews": [review(commit=HEAD)], "dismissals": [],
             "checks": [{"name": "ci", "conclusion": "SUCCESS"}], "merged_by": BOT,
             "merged_at": "2026-01-02T00:00:00Z", "merge_sha": MERGE}
    facts.update(over)
    git = FakeGit(commits, {(ROOT, HEAD): list(files)}, order=[ROOT, MERGE])
    return git, FakeGitHub({7: facts}, {MERGE: 7})


def alarms(git, client):
    return audit.audit(git, client, since=ROOT)


def test_a_merge_of_something_other_than_the_head_alarms_for_that_reason_alone():
    git, client = world(head="d" * 40, reviews=[review(commit="d" * 40)])
    got = alarms(git, client)
    assert len(got) == 1 and "not the head" in got[0][1]


def test_a_clean_gate_merge_is_accepted():
    assert alarms(*world()) == []


def test_the_human_may_merge_anything_by_hand():
    git, client = world(files=("docs/claims/x.md",), merged_by={"login": "owner-a", "type": "User"}, reviews=[])
    assert alarms(git, client) == []


def test_each_way_a_merge_can_fail_to_be_a_gate_merge_alarms():
    late = review(commit=HEAD, at="2026-01-03T00:00:00Z")
    cases = [
        dict(merged_by={"login": "someone", "type": "User"}),
        dict(merged_by={"login": "agent-bot", "type": "User"}),            # a User may register the same name
        dict(head="d" * 40, reviews=[review(commit="d" * 40)]),          # approved, but it is not what was merged
        dict(reviews=[]),
        dict(reviews=[late]),                                              # approved only after the merge
        dict(reviews=[review(commit="d" * 40)]),                           # approved another head
        dict(checks=[{"name": "ci", "conclusion": "FAILURE"}]),
        dict(title="fix"),                                                 # no authoring role
    ]
    for over in cases:
        assert alarms(*world(**over)), over


def test_a_reserved_path_merged_by_the_gate_needs_the_humans_approval_on_the_head():
    git, client = world(files=("docs/claims/x.md",), title="[lead] x")
    assert alarms(git, client)
    human = review(login="owner-a", type_="User", id_=2, at="2026-01-01T01:00:00Z", commit=HEAD)
    git, client = world(files=("docs/claims/x.md",), title="[lead] x", reviews=[review(commit=HEAD), human])
    assert alarms(git, client) == []
    stale = dict(human, commit="d" * 40)
    git, client = world(files=("docs/claims/x.md",), title="[lead] x", reviews=[review(commit=HEAD), stale])
    assert alarms(git, client)


def test_an_approval_dismissed_before_the_merge_does_not_count():
    human = review(login="owner-a", type_="User", id_=2, state="DISMISSED", at="2026-01-01T01:00:00Z", commit=HEAD)
    dismissal = {"review_id": 2, "previous_state": "APPROVED", "actor": {"login": "owner-a", "type": "User"}}
    git, client = world(files=("docs/claims/x.md",), title="[lead] x", reviews=[review(commit=HEAD), human],
                        dismissals=[dismissal])
    assert alarms(git, client)


def test_the_policy_is_read_at_the_merges_first_parent_not_at_the_tip():
    """A policy that later reserved a path cannot condemn an earlier merge, and a later loosening cannot excuse one."""
    older = copy.deepcopy(BASE)
    older["paths"].pop("docs/claims/")
    git, client = world(raw=older, files=("docs/claims/x.md",), title="[builder] x")
    assert alarms(git, client) == []


def test_a_gate_merge_that_loosened_the_policy_without_the_human_alarms():
    loose = copy.deepcopy(BASE)
    loose["paths"]["docs/claims/"].pop("reserved")
    git, client = world(files=("onloop.yaml",), head_raw=loose, title="[lead] relax")
    assert alarms(git, client)


def test_a_direct_push_and_a_merge_without_a_pull_request_alarm():
    git, client = world()
    git.commits["e" * 40] = {"parents": [MERGE], "message": "fix", "tree": "T3", "files": {}}
    git.order.append("e" * 40)
    assert any("straight" in why for _s, why in alarms(git, client))
    git, client = world()
    client.commit_pr.clear()
    assert any("no pull request" in why for _s, why in alarms(git, client))


def test_a_gate_revert_is_verified_by_parent_target_and_tree():
    git, client = world()
    rev = "e" * 40
    git.commits[rev] = {"parents": [MERGE], "message": f"Revert\n\nGate-Revert: #7 {MERGE}\n", "tree": "T0", "files": {}}
    git.order.append(rev)
    assert alarms(git, client) == []
    git.commits[rev]["tree"] = "T9"
    assert alarms(git, client)
    git.commits[rev].update(tree="T0", message="Revert\n\nGate-Revert: #7 " + "f" * 40 + "\n")
    assert alarms(git, client)
    git.commits[rev].update(message=f"Revert\n\nGate-Revert: #7 {MERGE}\n", parents=[ROOT])
    assert alarms(git, client)


def test_a_missing_or_broken_policy_at_the_first_parent_alarms():
    git, client = world()
    git.commits[ROOT]["files"] = {}
    assert alarms(git, client)
    git.commits[ROOT]["files"] = {pol.POLICY_FILE: "a: [broken"}
    assert alarms(git, client)


def test_the_self_test_bites():
    ok, lines = audit.self_test()
    assert ok, lines
