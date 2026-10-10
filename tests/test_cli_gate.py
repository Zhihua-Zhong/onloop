"""The command line's wiring of the gate: which merge commit and which policy reach the post-merge checks."""
from onloop import cli
from onloop import gate as gate_module


def test_the_cli_hands_the_merge_sha_and_the_base_policy_to_the_post_merge_checks(monkeypatch, capsys):
    seen = {}
    base_policy = object()

    class Hub:
        def __init__(self, repo):
            seen["repo"] = repo

    class FakeGitObject:
        pass

    def fake_run(number, client, git, *, base_branch, extra, post_merge, dry_run):
        seen["git"], seen["base_branch"] = git, base_branch
        seen["failed"] = post_merge(base_policy, "m" * 40)            # what gate.run calls after the merge
        return 0, ["merged"]

    def fake_post_merge_checks(git, policy, merge_sha, **kw):
        seen["checks_args"] = (git, policy, merge_sha)
        return []

    monkeypatch.setattr(cli, "GitHub", Hub)
    monkeypatch.setattr(cli, "Git", FakeGitObject)
    monkeypatch.setattr(gate_module, "run", fake_run)
    monkeypatch.setattr(gate_module, "run_post_merge_checks", fake_post_merge_checks)
    assert cli.main(["gate", "7", "--repo", "org/repo", "--base", "trunk"]) == 0
    git, policy, sha = seen["checks_args"]
    assert sha == "m" * 40 and policy is base_policy and git is seen["git"]       # not "HEAD", not the local policy
    assert seen["repo"] == "org/repo" and seen["base_branch"] == "trunk" and seen["failed"] == []
    assert "merged" in capsys.readouterr().out


def test_the_cli_reports_each_nonzero_exit_code_of_the_gate_with_its_reason(monkeypatch, capsys):
    monkeypatch.setattr(cli, "GitHub", lambda repo: None)
    monkeypatch.setattr(cli, "Git", lambda: None)
    for code in (1, 2, 3):
        monkeypatch.setattr(gate_module, "run", lambda *a, _c=code, **k: (_c, ["reason"]))
        assert cli.main(["gate", "7", "--repo", "org/repo"]) == code
        assert "reason" in capsys.readouterr().err
