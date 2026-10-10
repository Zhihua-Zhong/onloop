"""The gate's git state, against real repositories in a temporary directory (no network, no GitHub)."""
import pathlib
import subprocess

from onloop import gate
from onloop.gitutil import Git


def sh(cwd, *args):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return p.stdout.strip()


def make_origin_and_clone(tmp_path):
    origin = tmp_path / "origin.git"
    sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    clone = tmp_path / "clone"
    sh(tmp_path, "clone", "-q", str(origin), str(clone))
    sh(clone, "config", "user.email", "tester")
    sh(clone, "config", "user.name", "t")
    (clone / "a.txt").write_text("one\n", encoding="utf-8")
    sh(clone, "add", "-A")
    sh(clone, "commit", "-q", "-m", "base")
    sh(clone, "push", "-q", "origin", "HEAD:main")
    return origin, clone


def test_commands_run_in_a_clean_worktree_of_the_given_revision_and_it_is_removed(tmp_path):
    _origin, clone = make_origin_and_clone(tmp_path)
    old = sh(clone, "rev-parse", "HEAD")
    (clone / "a.txt").write_text("two\n", encoding="utf-8")
    sh(clone, "commit", "-qam", "next")
    seen = {}

    def shell(cmd, cwd):
        seen["text"] = (pathlib.Path(cwd) / "a.txt").read_text(encoding="utf-8")
        seen["cwd"] = cwd

        class Done:
            returncode = 0
        return Done()
    git = Git(cwd=str(clone))
    out = gate.run_commands(git, [{"name": "t", "run": "x"}], old, "project check", shell=shell)
    assert out == [] and seen["text"] == "one\n"                          # the old revision, not the checkout
    assert not pathlib.Path(seen["cwd"]).exists()                         # removed afterwards
    assert "onloop-wt-" not in sh(clone, "worktree", "list")


def test_a_real_failing_command_is_a_refusal_naming_the_check(tmp_path):
    _origin, clone = make_origin_and_clone(tmp_path)
    rev = sh(clone, "rev-parse", "HEAD")
    out = gate.run_commands(Git(cwd=str(clone)), [{"name": "boom", "run": "exit 3"}], rev, "post-merge check")
    assert out and "post-merge check 'boom' failed" in out[0] and "exit 3" in out[0]


def test_revert_merge_restores_the_tree_in_a_worktree_and_leaves_the_operators_checkout_alone(tmp_path):
    origin, clone = make_origin_and_clone(tmp_path)
    before = sh(clone, "rev-parse", "HEAD")
    sh(clone, "checkout", "-q", "-b", "feature")
    (clone / "b.txt").write_text("feature\n", encoding="utf-8")
    sh(clone, "add", "-A")
    sh(clone, "commit", "-q", "-m", "feature work")
    sh(clone, "checkout", "-q", "main")
    sh(clone, "merge", "-q", "--no-ff", "-m", "Merge pull request #7", "feature")
    merge = sh(clone, "rev-parse", "HEAD")
    sh(clone, "push", "-q", "origin", "HEAD:main")
    sh(clone, "checkout", "-q", "-b", "operators-own-work")                # what the operator has checked out
    new = gate.revert_merge(Git(cwd=str(clone)), merge, 7, "main")
    assert sh(clone, "rev-parse", "--abbrev-ref", "HEAD") == "operators-own-work"
    assert sh(origin, "rev-parse", "main") == new
    msg = sh(origin, "log", "-1", "--format=%B", "main")
    assert f"Gate-Revert: #7 {merge}" in msg
    assert sh(origin, "rev-parse", "main^{tree}") == sh(origin, "rev-parse", f"{before}^{{tree}}")
    assert "onloop-wt-" not in sh(clone, "worktree", "list")


def test_an_unfetchable_pull_request_head_is_a_clean_refusal_with_real_git(tmp_path):
    from onloop.testing import FakeGitHub
    _origin, clone = make_origin_and_clone(tmp_path)
    facts = {"number": 7, "state": "OPEN", "draft": False, "base": "main", "head": "a" * 40, "title": "[x] y",
             "body": "", "commit_messages": [], "reviews": [], "dismissals": [], "checks": []}
    code, notes = gate.run(7, FakeGitHub({7: facts}), Git(cwd=str(clone)))
    assert code == 1 and "cannot fetch #7" in notes[0]
