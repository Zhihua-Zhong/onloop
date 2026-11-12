"""After the merge: the clone must fetch the merge GitHub made before it checks or reverts anything."""

import subprocess

import pytest

from onloop import gate
from onloop.gitutil import Git
from onloop.testing import FakeGit, FakeGitHub

from .test_gate import _world


def sh(cwd, *args):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return p.stdout.strip()


# -- with fakes ---------------------------------------------------------------------------------------------------


class FetchBreaksAfterTheMerge(FakeGit):
    """The first fetch (the pull request head) works; the one after the merge does not."""

    def _run(self, *args):
        if args[:1] == ("fetch",) and any(c[:1] == ("fetch",) for c in self.calls):
            raise RuntimeError("network down")
        return super()._run(*args)


def _world_with(raw, git_class=FakeGit, post_merge_checks=True, **facts_over):
    if post_merge_checks:
        raw = {**raw, "post_merge_checks": [{"name": "smoke", "run": "true"}]}
    git, client = _world(raw, ["src/a.py"], **facts_over)
    if git_class is not FakeGit:
        other = git_class(git.commits, git.changed, git.order)
        other.pr_heads = git.pr_heads
        git = other
    return git, client


def test_the_merge_is_fetched_before_the_post_merge_checks_run(raw):
    git, client = _world_with(raw)
    seen = []
    code, _ = gate.run(7, client, git, post_merge=lambda p, sha: seen.append(sha) or [])
    assert code == 0 and seen == ["m" * 40]
    assert [c for c in git.calls if c[0] == "fetch"][-1] == ("fetch", "origin", "main")  # after the merge


def test_with_no_post_merge_checks_the_merge_is_not_fetched_at_all(raw):
    git, client = _world_with(raw, post_merge_checks=False, merge_sha=None)
    ran = []
    code, notes = gate.run(7, client, git, post_merge=lambda p, sha: ran.append(sha) or ["x"])
    assert code == 0 and ran == [] and notes == ["merged #7 at " + "a" * 7]
    assert (
        len([c for c in git.calls if c[0] == "fetch"]) == 1
    )  # only the pull request's head, before the merge


def test_no_merge_commit_means_nothing_is_checked_or_reverted(raw):
    git, client = _world_with(raw, merge_sha=None)
    ran = []
    code, notes = gate.run(7, client, git, post_merge=lambda p, sha: ran.append(sha) or ["x"])
    assert code == 3 and ran == [] and "by hand" in notes[-1]
    assert not any(c[0] == "revert" for c in git.calls)


def test_a_merge_the_clone_cannot_fetch_is_not_reverted_blind(raw):
    git, client = _world_with(raw, FetchBreaksAfterTheMerge)
    ran = []
    code, notes = gate.run(7, client, git, post_merge=lambda p, sha: ran.append(sha) or ["x"])
    assert code == 3 and ran == [] and "cannot fetch the merge" in notes[-1]
    assert not any(c[0] == "revert" for c in git.calls)


def test_a_merge_still_absent_after_the_fetch_is_not_reverted_blind(raw):
    class MergeNeverArrives(FakeGit):
        def has_commit(self, sha):
            return sha != "m" * 40

    git, client = _world_with(raw, MergeNeverArrives)
    code, notes = gate.run(7, client, git, post_merge=lambda p, sha: ["x"])
    assert code == 3 and "not available locally" in notes[-1]
    assert not any(c[0] == "revert" for c in git.calls)


def test_revert_merge_refuses_when_it_is_not_told_which_merge():
    with pytest.raises(ValueError):
        gate.revert_merge(FakeGit(), "", 7, "main")


# -- with real git: the merge is pushed from another clone ----------------------------------------------------------

POLICY = """
human: owner-a
reviewer: rev-bot
agents: agent-bot
roles: {builder: {}, lead: {}}
paths:
  src/: {owner: builder}
  onloop.yaml: {owner: lead, tier: full, reserved: authority_widening}
required_checks: [ci]
post_merge_checks:
  - {name: smoke, run: "CMD"}
"""


def merge_world(tmp_path, post_merge_command):
    """A bare origin; clone B holds the work and later pushes the merge; the operator's clone A is made before it."""
    origin = tmp_path / "origin.git"
    sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    b = tmp_path / "other"
    sh(tmp_path, "clone", "-q", str(origin), str(b))
    sh(b, "config", "user.email", "tester")
    sh(b, "config", "user.name", "t")
    (b / "onloop.yaml").write_text(POLICY.replace("CMD", post_merge_command), encoding="utf-8")
    (b / "src").mkdir()
    (b / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    sh(b, "add", "-A")
    sh(b, "commit", "-q", "-m", "base")
    sh(b, "push", "-q", "origin", "HEAD:main")
    before = sh(b, "rev-parse", "HEAD")
    sh(b, "checkout", "-q", "-b", "feature")
    (b / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    sh(b, "commit", "-qam", "change")
    head = sh(b, "rev-parse", "HEAD")
    sh(b, "push", "-q", "origin", "HEAD:refs/pull/7/head")
    sh(b, "checkout", "-q", "main")
    a = tmp_path / "operator"
    sh(tmp_path, "clone", "-q", str(origin), str(a))  # cloned BEFORE the merge exists
    sh(a, "config", "user.email", "tester")
    sh(a, "config", "user.name", "t")

    class Hub(FakeGitHub):
        def merge(self, number, head_sha):  # GitHub makes the merge, somewhere else
            sh(b, "merge", "-q", "--no-ff", "-m", "Merge pull request #7", "feature")
            sh(b, "push", "-q", "origin", "HEAD:main")
            self.prs[7]["merge_sha"] = sh(b, "rev-parse", "HEAD")
            self.merged.append((number, head_sha))

    facts = {
        "number": 7,
        "state": "OPEN",
        "draft": False,
        "base": "main",
        "head": head,
        "title": "[builder] change",
        "body": "",
        "commit_messages": [],
        "dismissals": [],
        "merge_sha": None,
        "reviews": [
            {
                "id": 1,
                "author": {"login": "rev-bot", "type": "Bot"},
                "state": "APPROVED",
                "commit": head,
                "submitted_at": "2026-01-01T00:00:00Z",
            }
        ],
        "checks": [{"name": "ci", "conclusion": "SUCCESS"}],
    }
    return origin, a, Hub({7: facts}), before


def test_post_merge_checks_see_a_merge_that_was_pushed_from_another_clone(tmp_path):
    origin, a, hub, _before = merge_world(tmp_path, "exit 0")
    git = Git(cwd=str(a))
    before_merge = sh(origin, "rev-parse", "main")
    seen = {}

    def post_merge(base, merge_sha):
        seen["sha"] = merge_sha
        return gate.run_post_merge_checks(git, base, merge_sha)

    code, notes = gate.run(7, hub, git, post_merge=post_merge)
    assert code == 0, notes
    assert seen["sha"] != before_merge and seen["sha"] == sh(origin, "rev-parse", "main")
    assert git.has_commit(seen["sha"])


def test_a_failing_post_merge_check_reverts_a_merge_made_elsewhere(tmp_path):
    origin, a, hub, before = merge_world(tmp_path, "exit 1")
    git = Git(cwd=str(a))
    code, notes = gate.run(
        7, hub, git, post_merge=lambda base, sha: gate.run_post_merge_checks(git, base, sha)
    )
    assert code == 2, notes
    assert sh(origin, "rev-parse", "main^{tree}") == sh(origin, "rev-parse", f"{before}^{{tree}}")
    assert "Gate-Revert: #7 " in sh(origin, "log", "-1", "--format=%B", "main")


# -- standalone `onloop gate` on an ordinary clone (0.1/0.2 users, no runner) -----------------------------------------


def test_standalone_gate_on_an_ordinary_clone_fetches_decides_and_merges(tmp_path):
    origin, a, hub, before = merge_world(tmp_path, "exit 0")
    assert sh(a, "rev-parse", "--is-bare-repository") == "false"  # a clone with a working tree
    refs_before = set(sh(a, "for-each-ref", "--format=%(refname)").split())
    branch, tree_before = sh(a, "rev-parse", "--abbrev-ref", "HEAD"), sh(a, "status", "--porcelain")
    git = Git(cwd=str(a))
    code, notes = gate.run(
        7, hub, git, post_merge=lambda base, sha: gate.run_post_merge_checks(git, base, sha)
    )
    assert code == 0, notes
    assert hub.merged == [(7, hub.prs[7]["head"])]
    new_refs = set(sh(a, "for-each-ref", "--format=%(refname)").split()) - refs_before
    assert new_refs == {"refs/onloop/base", "refs/onloop/pr/7"}  # the read side lives in onloop's namespace
    assert (
        sh(a, "rev-parse", "--abbrev-ref", "HEAD") == branch and sh(a, "status", "--porcelain") == tree_before
    )


def test_a_revert_pushes_to_the_real_remote_base_branch_not_the_onloop_namespace(tmp_path):
    origin, a, hub, before = merge_world(tmp_path, "exit 1")
    git = Git(cwd=str(a))
    code, notes = gate.run(
        7, hub, git, post_merge=lambda base, sha: gate.run_post_merge_checks(git, base, sha)
    )
    assert code == 2, notes
    assert sh(origin, "rev-parse", "refs/heads/main^{tree}") == sh(origin, "rev-parse", f"{before}^{{tree}}")
    assert "Gate-Revert: #7 " in sh(origin, "log", "-1", "--format=%B", "refs/heads/main")
    assert (
        sh(origin, "for-each-ref", "--format=%(refname)", "refs/onloop") == ""
    )  # nothing of ours was pushed
