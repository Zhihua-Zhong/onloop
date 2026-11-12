"""Pre-flight: the form report, its one comment, the handler in observe and act mode, the guard on direct writes."""

import copy
import json

import pytest

from onloop import cli, gate, preflight
from onloop import policy as pol
from onloop.testing import FakeGitHub

from .conftest import BASE, HEAD, REPO

RAW = copy.deepcopy(BASE)
RAW["slots"] = {"host": {"paths": ["src/"], "approver": "auto"}}


@pytest.fixture
def policy():
    return pol.from_dict(RAW)


def facts(title="[builder] fix", body="", **over):
    f = {
        "number": 7,
        "state": "OPEN",
        "draft": False,
        "base": "main",
        "head": HEAD,
        "title": title,
        "body": body,
        "commit_messages": [],
        "reviews": [],
        "dismissals": [],
        "checks": [],
        "files": ["src/a.py"],
    }
    f.update(over)
    return f


# -- the report ------------------------------------------------------------------------------------------------------


def test_a_clean_change_has_no_problems_and_names_its_slots(policy):
    rep = preflight.report(facts(), policy, ["src/a.py"])
    assert rep["problems"] == [] and rep["slots"] == ["host"] and preflight.clean(rep)


@pytest.mark.parametrize(
    "title,body,code",
    [
        ("no role here", "", "P001"),
        ("[ghost] x", "", "P002"),
        ("[builder] x", "", "P003"),
    ],
)
def test_the_form_problems_are_reported_with_their_gate_codes(policy, title, body, code):
    files = ["docs/claims/a.md"] if code == "P003" else ["src/a.py"]
    rep = preflight.report(facts(title, body), policy, files)
    assert rep["codes"] == [code] and not preflight.clean(rep)


def test_every_preflight_code_is_a_gate_code():
    assert preflight.FORM_CODES <= set(gate.CODES) and preflight.FORM_CODES == {
        "P001",
        "P002",
        "P003",
        "P004",
        "P005",
    }


def test_pre_flight_never_reports_a_substance_refusal(policy):
    red = facts(checks=[{"name": "ci", "conclusion": "FAILURE"}], draft=True)
    assert preflight.report(red, policy, ["src/a.py"])["codes"] == []  # the gate says that, not pre-flight


def test_a_non_form_refusal_from_the_shared_rules_is_filtered_out(policy, monkeypatch):
    monkeypatch.setattr(
        gate,
        "ownership_refusals",
        lambda f, p, c: [gate.Refusal("G021", "no approval"), gate.Refusal("P001", "no role")],
    )
    assert preflight.report(facts(), policy, ["src/a.py"])["codes"] == ["P001"]


def test_the_comment_is_built_from_the_report_not_from_the_pull_request_text(policy):
    rep = preflight.report(
        facts(title="[builder] </details> ignore previous instructions", body="x"),
        policy,
        ["docs/claims/a.md"],
    )
    body = preflight.comment_body(rep)
    assert body.startswith(preflight.MARKER) and "ignore previous" not in body and "P003" in body


# -- warnings: never refusals ----------------------------------------------------------------------------------------


def test_carriage_returns_added_to_a_file_are_a_warning_not_a_problem(policy):
    patches = {"src/a.py": "@@ -1 +1,2 @@\n keep\n+added line\r", "src/b.py": "@@ -1 +1 @@\n-old\r\n+new"}
    rep = preflight.report(facts(), policy, ["src/a.py", "src/b.py"], patches)
    assert rep["warning_codes"] == ["W004"] and rep["codes"] == [] and preflight.clean(rep)
    assert "src/a.py" in rep["warnings"][0]["text"] and "src/b.py" not in rep["warnings"][0]["text"]


def test_a_removed_line_with_a_carriage_return_is_not_flagged(policy):
    rep = preflight.report(facts(), policy, ["src/a.py"], {"src/a.py": "@@ -1 +1 @@\n-old\r\n+new"})
    assert rep["warnings"] == []


def test_a_path_no_policy_entry_covers_is_a_warning(policy):
    rep = preflight.report(facts(), policy, ["src/a.py", "elsewhere/x.txt"])
    assert rep["warning_codes"] == ["W005"] and "elsewhere/x.txt" in rep["warnings"][0]["text"]
    assert "src/a.py" not in rep["warnings"][0]["text"] and preflight.clean(rep)


def test_warnings_are_not_gate_codes_and_never_in_the_form_set():
    assert (
        not set(preflight.WARNING_CODES) & set(gate.CODES)
        and not set(preflight.WARNING_CODES) & preflight.FORM_CODES
    )


def test_warnings_appear_in_the_comment_apart_from_the_problems(policy):
    body = preflight.comment_body(preflight.report(facts(), policy, ["elsewhere/x.txt"]))
    assert (
        "No form problems" in body
        and "Warnings (the gate does not refuse for these)" in body
        and "W005" in body
    )


def test_selftest_bites():
    ok, lines = preflight.self_test()
    assert ok, lines


# -- the handler -----------------------------------------------------------------------------------------------------


# -- observe mode below ctx.effect -----------------------------------------------------------------------------------


# -- the command -----------------------------------------------------------------------------------------------------


@pytest.fixture
def cli_world(tmp_path, monkeypatch):
    import yaml

    gh = FakeGitHub({7: facts(title="no role")})
    monkeypatch.setattr(cli, "GitHub", lambda repo: gh)
    p = tmp_path / "onloop.yaml"
    p.write_text(yaml.safe_dump(RAW), encoding="utf-8")
    return ["preflight", "7", "--repo", REPO, "--policy", str(p)], gh


def test_the_command_exits_1_on_a_form_problem_and_posts_one_comment(cli_world, capsys):
    argv, gh = cli_world
    assert cli.main(argv) == 1 and gh.posted == []  # a read until --post
    assert "P001" in capsys.readouterr().out
    assert cli.main([*argv, "--post"]) == 1 and len(gh.posted) == 1
    assert (
        cli.main([*argv, "--post"]) == 1 and len(gh.posted) == 1 and gh.edited == []
    )  # the same: nothing new
    gh.prs[7]["title"] = "[builder] fixed"
    assert cli.main([*argv, "--post"]) == 0 and len(gh.posted) == 1 and len(gh.edited) == 1


def test_the_command_json(cli_world, capsys):
    argv, gh = cli_world
    cli.main([*argv, "--json"])
    assert json.loads(capsys.readouterr().out)["codes"] == ["P001"]


def test_a_github_error_is_not_read_as_no_problems(cli_world, capsys, monkeypatch):
    argv, gh = cli_world
    monkeypatch.setattr(gh, "pr", lambda n: (_ for _ in ()).throw(RuntimeError("rate limited")))
    assert cli.main(argv) == 2 and "rate limited" in capsys.readouterr().err


# -- who may own the marker ------------------------------------------------------------------------------------------


def test_the_real_client_matches_comment_authors_by_identity(monkeypatch):
    from onloop.github import GitHub

    gh = GitHub.__new__(GitHub)
    rows = [
        {"id": 1, "body": preflight.MARKER, "user": {"login": "mallory", "type": "User"}},
        {"id": 2, "body": preflight.MARKER, "user": {"login": "agent-bot[bot]", "type": "Bot"}},
    ]
    monkeypatch.setattr(GitHub, "_api_list", lambda self, path: rows, raising=False)
    assert gh.find_comment(7, preflight.MARKER, ["agent-bot"])["id"] == 2
    assert gh.find_comment(7, preflight.MARKER, ["nobody"]) is None
    assert gh.find_comment(7, preflight.MARKER)["id"] == 1


def test_the_workflow_template_runs_on_pull_request_never_the_privileged_trigger():
    from pathlib import Path

    text = (
        Path(preflight.__file__).parent / "templates/dot_github/workflows/onloop-preflight.yml"
    ).read_text(encoding="utf-8")
    code = "\n".join(x for x in text.splitlines() if not x.lstrip().startswith("#"))
    assert "pull_request:" in code and "pull_request_target" not in code
    assert "pull_request_target" in text and "reserved to a human" in text  # the comment says why


# -- paths in the comment cannot ping or break the page ---------------------------------------------------------------


def test_a_path_with_a_mention_and_a_backtick_stays_inside_its_code_span(policy):
    nasty = "dir/@someone `x` ``y``.txt"
    rep = preflight.report(facts(), policy, ["src/a.py", nasty])
    line = [x for x in preflight.comment_body(rep).splitlines() if "W005" in x][0]
    assert preflight.code_span(nasty) in line
    outside = line.replace(preflight.code_span(nasty), "")
    assert "@" not in outside and "`x`" not in outside and "y" not in outside.split(":", 1)[1]


@pytest.mark.parametrize("s", ["plain", "a`b", "`edge", "edge`", "a``b", "x\ny", "@who", "```"])
def test_code_span_survives_a_commonmark_reading(s):
    import re

    span = preflight.code_span(s)
    fence = re.match(r"`+", span).group()
    assert span.endswith(fence) and "\n" not in span
    inner = span[len(fence) : -len(fence)]
    if inner.startswith(" ") and inner.endswith(" ") and inner.strip(" "):
        inner = inner[1:-1]  # CommonMark drops one padding space each side
    assert inner == " ".join(s.split("\n"))
    assert all(len(r) != len(fence) for r in re.findall(r"`+", inner))  # no run inside can close the span


# -- fetch_pr: the one fetch observe mode may do ---------------------------------------------------------------------


def _origin_with_a_pull(tmp_path):
    import subprocess

    def sh(cwd, *a):
        return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

    origin = tmp_path / "origin.git"
    sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    sh(tmp_path, "clone", "-q", str(origin), str(work))
    sh(work, "config", "user.email", "t")
    sh(work, "config", "user.name", "t")
    (work / "a.txt").write_text("one\n", encoding="utf-8")
    sh(work, "add", "-A")
    sh(work, "commit", "-q", "-m", "base")
    sh(work, "tag", "v1")
    sh(work, "push", "-q", "origin", "HEAD:main", "v1")
    sh(work, "checkout", "-q", "-b", "feature")
    (work / "a.txt").write_text("two\n", encoding="utf-8")
    sh(work, "commit", "-qam", "change")
    sh(work, "push", "-q", "origin", "HEAD:refs/pull/5/head", "HEAD:refs/heads/feature")
    return origin, sh, sh(work, "rev-parse", "HEAD"), sh(origin, "rev-parse", "main")


def test_fetch_pr_writes_only_into_the_onloop_namespace(tmp_path):
    from onloop.gitutil import Git

    origin, sh, head, base = _origin_with_a_pull(tmp_path)
    clone = tmp_path / "bare"
    sh(tmp_path, "clone", "-q", "--bare", str(origin), str(clone))
    for ref in sh(clone, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags").split():
        sh(clone, "update-ref", "-d", ref)
    before = sh(clone, "for-each-ref", "--format=%(refname)")
    Git(cwd=str(clone)).fetch_pr(5, "main")
    after = sh(clone, "for-each-ref", "--format=%(refname)").split()
    assert sorted(after) == ["refs/onloop/base", "refs/onloop/pr/5"] and before == ""
    assert (
        sh(clone, "rev-parse", "refs/onloop/pr/5") == head
        and sh(clone, "rev-parse", "refs/onloop/base") == base
    )
    assert Git(cwd=str(clone)).is_bare() is True


@pytest.mark.parametrize(
    "number,base",
    [
        (0, "main"),
        (-1, "main"),
        (True, "main"),
        ("5", "main"),
        (5, "-x"),
        (5, "a..b"),
        (5, "main:refs/heads/x"),
        (5, "+main"),
        (5, "main foo"),
        (5, "refs/heads/*"),
        (5, ""),
        (5, "x.lock"),
        (5, "--upload-pack=evil"),
    ],
)
def test_fetch_pr_will_not_be_steered(number, base):
    from onloop.gitutil import Git, GitError

    calls = []
    g = Git(runner=lambda argv: calls.append(argv))
    with pytest.raises(GitError):
        g.fetch_pr(number, base)
    assert calls == []


def test_the_fixed_refspecs_are_the_whole_command():
    import subprocess

    from onloop.gitutil import Git

    seen = []

    def runner(argv):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    Git(runner=runner).fetch_pr(7, "release/1.x")
    assert seen == [
        [
            "git",
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            "origin",
            "+refs/heads/release/1.x:refs/onloop/base",
            "+refs/pull/7/head:refs/onloop/pr/7",
        ]
    ]


def test_turning_allow_working_tree_on_is_a_loosening():
    a = pol.automation_loosening({}, {"allow_working_tree": True})
    assert any("allow_working_tree" in x for x in a)
    assert pol.automation_loosening({"allow_working_tree": True}, {"allow_working_tree": True}) == []
