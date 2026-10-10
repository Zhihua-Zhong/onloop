import copy

import pytest

from onloop import gate
from onloop import policy as pol
from onloop.classify import classify
from onloop.testing import FakeGit, FakeGitHub

from .conftest import HEAD, review


def decide(facts, policy, paths, **kw):
    return gate.decide(facts, policy, classify(paths, policy), **kw)


def test_a_clean_change_may_merge(facts, policy):
    assert decide(facts, policy, ["src/a.py"]) == []


def test_evidence_is_bound_to_the_head(facts, policy):
    stale = dict(facts, reviews=[review(commit="b" * 40)])
    assert any("no reviewer approval" in r for r in decide(stale, policy, ["src/a.py"]))
    red = dict(facts, checks=[{"name": "ci", "conclusion": "FAILURE"}])
    assert any("required check 'ci'" in r for r in decide(red, policy, ["src/a.py"]))
    assert any("missing" in r for r in decide(dict(facts, checks=[]), policy, ["src/a.py"]))


def test_only_the_reviewer_identity_counts_as_the_reviewer(facts, policy):
    for impostor in (review(type_="User"), review(login="agent-bot"), review(login="someone", type_="Bot")):
        assert decide(dict(facts, reviews=[impostor]), policy, ["src/a.py"])


def test_reviews_that_assert_nothing_do_not_approve(facts, policy):
    for state in ("COMMENTED", "PENDING", "DISMISSED"):
        assert decide(dict(facts, reviews=[review(state=state)]), policy, ["src/a.py"])


def test_a_standing_request_for_changes_blocks_even_with_an_approval(facts, policy):
    blocked = dict(facts, reviews=[review(), review(state="CHANGES_REQUESTED", id_=2, at="2026-01-02T00:00:00Z")])
    assert any("requested changes" in r for r in decide(blocked, policy, ["src/a.py"]))
    later = dict(blocked, reviews=blocked["reviews"] + [review(id_=3, at="2026-01-03T00:00:00Z")])
    assert decide(later, policy, ["src/a.py"]) == []                       # the latest review of an identity governs


def test_a_dismissal_by_anyone_else_cannot_lift_a_request_for_changes(facts, policy):
    cr = review(state="DISMISSED", id_=2, at="2026-01-02T00:00:00Z")
    by_agent = {"review_id": 2, "previous_state": "CHANGES_REQUESTED", "actor": {"login": "agent-bot", "type": "Bot"}}
    assert decide(dict(facts, reviews=[review(), cr], dismissals=[by_agent]), policy, ["src/a.py"])
    by_reviewer = dict(by_agent, actor={"login": "rev-bot", "type": "Bot"})
    assert decide(dict(facts, reviews=[review(), cr], dismissals=[by_reviewer]), policy, ["src/a.py"]) == []


def test_a_reserved_class_needs_the_human_on_the_same_head(facts, policy):
    f = dict(facts, title="[lead] claim")
    assert any("human" in r for r in decide(f, policy, ["docs/claims/x.md"]))
    human = review(login="owner-a", type_="User", id_=2, at="2026-01-01T01:00:00Z")
    assert decide(dict(f, reviews=f["reviews"] + [human]), policy, ["docs/claims/x.md"]) == []
    old = dict(human, commit="c" * 40)
    assert decide(dict(f, reviews=f["reviews"] + [old]), policy, ["docs/claims/x.md"])
    impostor = review(login="owner-a", type_="Bot", id_=2)
    assert decide(dict(f, reviews=f["reviews"] + [impostor]), policy, ["docs/claims/x.md"])


def test_an_authoring_role_is_required_and_must_own_the_paths(facts, policy):
    assert any("no authoring role" in r for r in decide(dict(facts, title="fix"), policy, ["src/a.py"]))
    assert any("not a role" in r for r in decide(dict(facts, title="[ghost] fix"), policy, ["src/a.py"]))
    assert any("Cross-owner" in r for r in decide(dict(facts, title="[builder] x"), policy, ["docs/copy/x.md"]))
    ok = dict(facts, title="[builder] x", body="Cross-owner: lead - the copy is part of this fix")
    assert decide(ok, policy, ["docs/copy/x.md"]) == []
    trailer = dict(facts, title="fix", commit_messages=["fix\n\nRole: builder\n"])
    assert decide(trailer, policy, ["src/a.py"]) == []


def test_state_draft_and_base_are_judged_live_but_not_by_the_audit(facts, policy):
    for bad in (dict(facts, state="CLOSED"), dict(facts, draft=True), dict(facts, base="other")):
        assert decide(bad, policy, ["src/a.py"])
        assert decide(bad, policy, ["src/a.py"], live=False) == []


def test_the_projects_own_checks_arrive_as_refusals(facts, policy):
    assert decide(facts, policy, ["src/a.py"], extra_refusals=["project check 'tests' failed"])


# -- plan and run, with a fake GitHub and a fake git -------------------------------------------------------------

def _world(raw, files, head_raw=None, **facts_over):
    import yaml
    base_text = yaml.safe_dump(raw)
    commits = {"origin/main": {"parents": [], "files": {pol.POLICY_FILE: base_text}, "message": "", "tree": "T"},
               HEAD: {"parents": ["origin/main"], "message": "", "tree": "U",
                      "files": {pol.POLICY_FILE: yaml.safe_dump(head_raw) if head_raw else base_text}}}
    git = FakeGit(commits, {("origin/main", HEAD): files})
    git.pr_heads[7] = HEAD
    f = {"number": 7, "state": "OPEN", "draft": False, "base": "main", "head": HEAD, "title": "[builder] fix",
         "body": "", "commit_messages": [], "reviews": [review()], "dismissals": [],
         "checks": [{"name": "ci", "conclusion": "SUCCESS"}], "merge_sha": "m" * 40}
    f.update(facts_over)
    return git, FakeGitHub({7: f})


def test_run_merges_with_the_head_it_decided_on(raw):
    git, client = _world(raw, ["src/a.py"])
    code, notes = gate.run(7, client, git)
    assert code == 0 and client.merged == [(7, HEAD)]


def test_run_refuses_and_does_not_merge(raw):
    git, client = _world(raw, ["src/a.py"], reviews=[])
    code, notes = gate.run(7, client, git)
    assert code == 1 and client.merged == [] and any("reviewer approval" in n for n in notes)


def test_dry_run_decides_without_merging(raw):
    git, client = _world(raw, ["src/a.py"])
    code, _ = gate.run(7, client, git, dry_run=True)
    assert code == 0 and client.merged == []


def test_a_pull_request_that_loosens_the_policy_needs_the_human_whatever_the_head_says(raw):
    loose = copy.deepcopy(raw)
    loose["paths"]["docs/claims/"].pop("reserved")
    git, client = _world(raw, ["onloop.yaml"], head_raw=loose, title="[lead] relax")
    code, notes = gate.run(7, client, git)
    assert code == 1 and any("human" in n for n in notes)
    human = review(login="owner-a", type_="User", id_=2, at="2026-01-01T01:00:00Z")
    git, client = _world(raw, ["onloop.yaml"], head_raw=loose, title="[lead] relax", reviews=[review(), human])
    assert gate.run(7, client, git)[0] == 0


def test_a_head_that_deletes_or_breaks_the_policy_is_authority_widening(raw):
    git, client = _world(raw, ["onloop.yaml"], title="[lead] x")
    git.commits[HEAD]["files"].pop(pol.POLICY_FILE)
    assert gate.run(7, client, git)[0] == 1
    git, client = _world(raw, ["onloop.yaml"], title="[lead] x")
    git.commits[HEAD]["files"][pol.POLICY_FILE] = "a: [broken"
    code, notes = gate.run(7, client, git)
    assert code == 1 and any("human" in n for n in notes)


def test_no_policy_on_the_base_means_no_merge(raw):
    git, client = _world(raw, ["src/a.py"])
    git.commits["origin/main"]["files"] = {}
    code, notes = gate.run(7, client, git)
    assert code == 1 and any("no policy" in n for n in notes)


def test_a_failed_post_merge_check_reverts_with_the_trailer_the_audit_verifies(raw):
    git, client = _world(raw, ["src/a.py"])
    code, notes = gate.run(7, client, git, post_merge=lambda p, sha: ["post-merge check 'smoke' failed"])
    assert code == 2 and any("reverted" in n for n in notes)
    calls = [c for c in git.calls]
    assert git.worktrees == ["origin/main"]                              # made in a temporary worktree of the fresh tip
    assert not any(c[0] == "checkout" for c in calls)                    # the operator's own checkout is never touched
    assert ("revert", "-m", "1", "--no-edit", "m" * 40) in calls
    assert any(c[:2] == ("commit", "--amend") and "Gate-Revert: #7 " + "m" * 40 in c[3] for c in calls)
    assert any(c[0] == "push" for c in calls)


@pytest.mark.parametrize("title,msgs,want", [
    ("[Builder] x", [], "builder"), ("x", ["a\n\nRole: lead\n", "b\n\nRole: builder\n"], "builder"),
    ("no role", [], None), ("[a b] x", [], None)])
def test_the_authoring_role_comes_from_the_title_then_the_last_trailer(title, msgs, want):
    assert gate.authoring_role(title, msgs) == want


def test_the_self_test_bites():
    ok, lines = gate.self_test()
    assert ok, lines


# -- the review's must-fixes -----------------------------------------------------------------------------------------

def test_a_required_check_is_green_only_if_every_run_of_that_name_is_green_in_either_order(facts, policy):
    red, green = {"name": "ci", "conclusion": "FAILURE"}, {"name": "ci", "conclusion": "SUCCESS"}
    for order in ([red, green], [green, red]):
        assert any("not green" in r for r in decide(dict(facts, checks=order), policy, ["src/a.py"])), order
    assert decide(dict(facts, checks=[green, green]), policy, ["src/a.py"]) == []
    pending = {"name": "ci", "conclusion": None}
    assert decide(dict(facts, checks=[green, pending]), policy, ["src/a.py"])


def test_an_unfetchable_head_is_a_clean_refusal_not_a_traceback(raw):
    git, client = _world(raw, ["src/a.py"])
    git.unfetched = True
    code, notes = gate.run(7, client, git)
    assert code == 1 and "cannot fetch #7" in notes[0] and client.merged == []


def test_the_head_is_fetched_from_the_pull_requests_own_ref_and_must_be_the_head_that_was_read(raw):
    git, client = _world(raw, ["src/a.py"])
    gate.run(7, client, git, dry_run=True)
    assert git.calls[0] == ("fetch", "origin", "main", "+refs/pull/7/head:refs/onloop/pr/7")
    git, client = _world(raw, ["src/a.py"])
    git.pr_heads[7] = "d" * 40                                           # it moved after the facts were read
    code, notes = gate.run(7, client, git)
    assert code == 1 and "moved" in notes[0] and client.merged == []


class Shifting(FakeGitHub):
    """The pull request as the gate reads it the first time, then as it is a moment later."""

    def __init__(self, first, second):
        super().__init__({7: first})
        self.reads = [first, second]

    def pr(self, number):
        return self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]


def test_the_pull_request_is_read_again_right_before_the_merge(raw):
    git, client = _world(raw, ["src/a.py"])
    first = client.prs[7]
    later = dict(first, reviews=[review(state="DISMISSED")])             # the approval was withdrawn meanwhile
    shifting = Shifting(first, later)
    code, notes = gate.run(7, shifting, git)
    assert code == 1 and shifting.merged == [] and any("reviewer approval" in n for n in notes)
    moved = Shifting(first, dict(first, head="e" * 40))
    code, notes = gate.run(7, moved, _world(raw, ["src/a.py"])[0])
    assert code == 1 and moved.merged == [] and "moved" in notes[0]


def test_the_projects_checks_and_post_merge_checks_run_in_worktrees_with_commands_from_the_base_policy(raw):
    raw["checks"] = [{"name": "tests", "run": "pytest -q"}]
    raw["post_merge_checks"] = [{"name": "smoke", "run": "make smoke"}]
    base = pol.from_dict(raw)
    git = FakeGit()
    ran = []

    class Done:
        def __init__(self, code):
            self.returncode = code

    def shell(cmd, cwd):
        ran.append(cmd)
        return Done(1 if cmd == "make smoke" else 0)
    assert gate.run_project_checks(git, base, "h" * 40, shell=shell) == []
    out = gate.run_post_merge_checks(git, base, "m" * 40, shell=shell)
    assert ran == ["pytest -q", "make smoke"] and git.worktrees == ["h" * 40, "m" * 40]
    assert any("post-merge check 'smoke' failed" in o for o in out)


def test_a_worktree_that_cannot_be_made_is_a_refusal_not_a_crash(raw):
    raw["checks"] = [{"name": "tests", "run": "x"}]

    class Broken(FakeGit):
        def in_worktree(self, rev):
            raise RuntimeError("no disk")
    out = gate.run_project_checks(Broken(), pol.from_dict(raw), "h" * 40)
    assert out and "cannot run the project checks" in out[0]


def test_a_broken_or_refusing_plugin_refuses_the_merge(facts, policy, monkeypatch):
    class EP:
        def __init__(self, name, fn):
            self.name, self._fn = name, fn

        def load(self):
            return self._fn

    def boom(f, p, c):
        raise RuntimeError("plugin bug")
    eps = [EP("bad", boom), EP("strict", lambda f, p, c: ["no Fridays"]), EP("quiet", lambda f, p, c: None)]
    monkeypatch.setattr(gate, "entry_points", lambda group: eps)
    out = gate.plugin_refusals(facts, policy, classify(["src/a.py"], policy))
    assert any("plugin 'bad' failed: RuntimeError" in o for o in out) and "no Fridays" in out
    assert len(out) == 2
    monkeypatch.setattr(gate, "entry_points", lambda group: [])
    assert gate.plugin_refusals(facts, policy, classify(["src/a.py"], policy)) == []
