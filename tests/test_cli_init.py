import json
import subprocess
import sys

from onloop import __version__, codeowners, github
from onloop import policy as pol
from onloop.cli import main
from onloop.init import init


def test_init_in_an_empty_repository_gives_a_policy_that_checks_and_matching_codeowners(tmp_path, capsys):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert main(["init", "--dir", str(tmp_path), "--human", "owner-a", "--reviewer", "rev-bot",
                 "--agents", "agent-bot"]) == 0
    for rel in ("onloop.yaml", "ledger.yaml", "CODEOWNERS", "docs/decisions/TEMPLATE.md",
                "docs/charter/roles/lead.md", "docs/charter/roles/operator.md", "docs/charter/roles/reviewer.md",
                "docs/charter/roles/builder.md", ".github/workflows/onloop-gate.yml",
                ".github/workflows/onloop-audit.yml"):
        assert (tmp_path / rel).is_file(), rel
    policy_file = str(tmp_path / "onloop.yaml")
    assert main(["policy", "check", "--policy", policy_file]) == 0
    assert main(["codeowners", "--check", "--policy", policy_file, "--file", str(tmp_path / "CODEOWNERS")]) == 0
    assert main(["ledger", "check", "--file", str(tmp_path / "ledger.yaml"), "--policy", policy_file]) == 0


def test_codeowners_check_fails_when_the_file_drifts_and_write_repairs_it(tmp_path):
    init(tmp_path, human="owner-a", reviewer="rev-bot", agents="agent-bot")
    co = tmp_path / "CODEOWNERS"
    args = ["--policy", str(tmp_path / "onloop.yaml"), "--file", str(co)]
    co.write_text(co.read_text(encoding="utf-8") + "/src/ @someone\n", encoding="utf-8")
    assert main(["codeowners", "--check", *args]) == 1
    assert main(["codeowners", "--write", *args]) == 0
    assert main(["codeowners", "--check", *args]) == 0


def test_init_keeps_existing_files_unless_forced(tmp_path):
    mine = tmp_path / "onloop.yaml"
    mine.write_text("keep: me" + chr(10), encoding="utf-8")
    try:
        init(tmp_path, human="a", reviewer="b", agents="c")
    except pol.PolicyError:
        pass                                  # the kept file is not a usable policy, so CODEOWNERS is not generated
    else:
        raise AssertionError("expected the kept, invalid policy to be reported")
    assert mine.read_text(encoding="utf-8") == "keep: me" + chr(10)
    written, skipped = init(tmp_path, human="a", reviewer="b", agents="c", force=True)
    assert "onloop.yaml" in written and "keep" not in mine.read_text(encoding="utf-8")
    written, skipped = init(tmp_path, human="a", reviewer="b", agents="c")
    assert written == [] and "onloop.yaml" in skipped and "CODEOWNERS" in skipped


def test_the_codeowners_are_generated_from_the_policy_most_specific_last(tmp_path):
    init(tmp_path, human="owner-a", reviewer="rev-bot", agents="agent-bot")
    text = (tmp_path / "CODEOWNERS").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert "/onloop.yaml @owner-a" in lines and "/docs/decisions/ @owner-a" in lines
    assert all(ln.split()[1] == "@owner-a" for ln in lines)      # the starter's roles declare no handle of their own
    assert text == codeowners.generate(pol.load(tmp_path / "onloop.yaml"))


def test_help_and_version_work_and_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: onloop" in capsys.readouterr().out
    out = subprocess.run([sys.executable, "-m", "onloop.cli", "--version"], capture_output=True, text=True)
    assert out.stdout.strip() == f"onloop {__version__}"


def test_selftest_command_passes(capsys):
    assert main(["selftest"]) == 0
    assert "every checker still bites" in capsys.readouterr().out


def test_ledger_next_and_render_commands(tmp_path, capsys):
    init(tmp_path, human="owner-a", reviewer="rev-bot", agents="agent-bot")
    f = str(tmp_path / "ledger.yaml")
    assert main(["ledger", "next", "--role", "builder", "--file", f]) == 0
    assert "Q4" in capsys.readouterr().out
    assert main(["ledger", "next", "--role", "nobody", "--file", f]) == 1
    assert main(["ledger", "render", "--file", f]) == 0
    assert "Status" in capsys.readouterr().out


def test_policy_check_reports_problems_and_fails(tmp_path, capsys):
    p = tmp_path / "onloop.yaml"
    p.write_text("human: a\n", encoding="utf-8")
    assert main(["policy", "check", "--policy", str(p)]) == 1
    assert "policy:" in capsys.readouterr().err


# -- the GitHub client, with gh replaced -----------------------------------------------------------------------

class Done:
    def __init__(self, out, code=0):
        self.stdout, self.stderr, self.returncode = out, "", code


def test_the_client_reads_a_pull_request_into_plain_facts_and_merges_bound_to_the_head():
    calls = []

    def runner(argv):
        calls.append(argv)
        url = argv[-1]
        if url.endswith("pulls/9"):
            return Done(json.dumps({"state": "open", "merged": False, "draft": False, "base": {"ref": "main"},
                                    "head": {"sha": "h" * 40}, "title": "[builder] t", "body": "b"}))
        if url.endswith("pulls/9/reviews"):
            return Done(json.dumps([{"id": 1, "user": {"login": "rev-bot", "type": "Bot"}, "state": "APPROVED",
                                     "commit_id": "h" * 40, "submitted_at": "2026-01-01T00:00:00Z"}]))
        if url.endswith("pulls/9/commits"):
            return Done(json.dumps([{"commit": {"message": "fix\n\nRole: builder"}}]))
        if "check-runs" in url:
            return Done(json.dumps({"check_runs": [{"name": "ci", "conclusion": "success"}]}))
        if url.endswith("issues/9/timeline"):
            return Done(json.dumps([{"event": "review_dismissed", "actor": {"login": "x", "type": "User"},
                                     "dismissed_review": {"review_id": 1, "state": "changes_requested"}}]))
        return Done("[]")

    gh = github.GitHub("org/repo", runner)
    facts = gh.pr(9)
    assert facts["head"] == "h" * 40 and facts["state"] == "OPEN" and facts["checks"] == [
        {"name": "ci", "conclusion": "SUCCESS"}]
    assert facts["reviews"][0]["commit"] == "h" * 40 and facts["dismissals"][0]["review_id"] == 1
    gh.merge(9, "h" * 40)
    assert calls[-1][-2:] == ["--match-head-commit", "h" * 40]


def test_json_stream_reads_paginated_output():
    assert github.json_stream('[1,2]\n[3]\n  {"a": 1}') == [[1, 2], [3], {"a": 1}]


def test_a_failing_gh_call_raises_with_a_short_reason():
    gh = github.GitHub("org/repo", lambda argv: Done("", 1))
    try:
        gh.pr(1)
    except github.GitHubError as e:
        assert "failed" in str(e)
    else:
        raise AssertionError("expected a GitHubError")


def test_a_rest_apps_login_with_the_bot_suffix_matches_the_bare_slug_in_the_policy():
    assert github.actor({"login": "agent-bot[bot]", "type": "Bot"}) == {"login": "agent-bot", "type": "Bot"}
    assert github.actor({"login": "agent-bot[bot]", "type": "User"})["login"] == "agent-bot[bot]"   # a User is not the App
    assert github.actor(None) == {"login": None, "type": None}


def test_the_pull_request_facts_carry_when_the_head_was_pushed():
    def runner(argv):
        url = argv[-1]
        if url.endswith("pulls/9"):
            return Done(json.dumps({"state": "open", "merged": False, "base": {"ref": "main"}, "head": {"sha": "h" * 40}}))
        if url.endswith("pulls/9/commits"):
            return Done(json.dumps([{"commit": {"message": "a", "committer": {"date": "2026-01-01T00:00:00Z"}}},
                                    {"commit": {"message": "b", "committer": {"date": "2026-01-02T00:00:00Z"}}}]))
        return Done("[]")
    assert github.GitHub("org/repo", runner).pr(9)["head_pushed_at"] == "2026-01-02T00:00:00Z"
