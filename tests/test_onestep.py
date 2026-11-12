"""`onloop step`: one governed step for one pull request, for a CI job. Due only when the role's triggers say so;
the same command, budget and result file as the supervisor's steps."""

import json

import pytest

from onloop import cli
from onloop.supervisor import onestep, step

from .supervisor_helpers import env_without_key, pr
from .test_supervisor_gates import A, B, _live_cfg, fixture_cfg, gated_raw, pull, run


def test_an_early_event_is_not_due_and_says_why(tmp_path):
    cfg = fixture_cfg(
        tmp_path,
        [pr(12), pr(13)],
        [pull(12, A), pull(13, B)],
        checks={A: {"check_runs": [run("unit"), run("lint", status="in_progress")]}},
    )
    code, s = onestep.run_one(
        cfg,
        "reviewer",
        12,
        live=True,
        environ=env_without_key(),
        popen=lambda *a, **k: pytest.fail("started a process"),
    )
    assert code == 0 and not s["due"] and "pending" in s["reason"] and s["head"] == A
    code, s = onestep.run_one(cfg, "reviewer", 99, live=False, environ=env_without_key())
    assert code == 0 and not s["due"] and "not open" in s["reason"]


def test_a_due_pull_request_in_dry_run_prints_the_governed_command(tmp_path):
    cfg = _live_cfg(tmp_path)
    said = []
    code, s = onestep.run_one(cfg, "reviewer", 12, live=False, environ=env_without_key(), say=said.append)
    assert (
        code == 0
        and s["due"]
        and s["outcome"] == "dry_run"
        and s["step_id"] == step.step_id("reviewer", "pr#12", f"head={A}")
    )
    assert "--permission-mode dontAsk" in said[0] and "--strict-mcp-config" in said[0]


def test_a_live_step_writes_the_result_file_and_the_summary(tmp_path):
    cfg = _live_cfg(tmp_path, result_file="r/${item_number}_${item_head_short}.md")
    out, state = tmp_path / "out", tmp_path / "state"
    verdict = {"verdict": "merge", "reviewed_head": A}
    env = env_without_key(FAKE_CLAUDE_STRUCTURED=json.dumps(verdict))
    cfg = onestep.with_dirs(cfg, state, out)
    code, s = onestep.run_one(cfg, "reviewer", 12, live=True, environ=env)
    assert code == 0 and s["outcome"] == "ok" and s["num_turns"] == 3
    side = json.loads((out / "r" / f"12_{A[:8]}.md.json").read_text(encoding="utf-8"))
    assert side["structured"] == verdict and s["result_json"] == str(
        (out / "r" / f"12_{A[:8]}.md.json").resolve()
    )
    assert (state / "steps").is_dir()  # the stream-json output is kept in --state


@pytest.mark.parametrize("key", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"])
def test_a_live_step_refuses_a_metered_credential(tmp_path, key):
    cfg = _live_cfg(tmp_path)
    code, s = onestep.run_one(cfg, "reviewer", 12, live=True, environ={**env_without_key(), key: "x"})
    assert code == 2 and key in s["error"]


def test_the_setup_token_credential_is_taken_without_asking_auth_status():
    def no_run(*a, **k):
        raise AssertionError("asked auth status")

    assert (
        step.check_login(("claude",), {step.OAUTH_TOKEN: "t"}, run=no_run)["authMethod"] == "oauth_token_env"
    )


def test_the_command_line_writes_last_step_json(tmp_path, capsys):
    cfg = fixture_cfg(tmp_path, [pr(12)], [pull(12, A)], raw=gated_raw())
    code = cli.main(
        [
            "step",
            "--config",
            str(cfg.path),
            "--role",
            "reviewer",
            "--pr",
            "12",
            "--state",
            str(tmp_path / "st"),
        ]
    )
    assert code == 0
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed == json.loads((tmp_path / "st" / "last-step.json").read_text(encoding="utf-8"))
    assert printed["due"] is False and "pending" in printed["reason"]  # no checks reported yet


# -- what a person sees in the CI run ---------------------------------------------------------------------------------

NOT_DUE = {
    "due": False,
    "item": "pr#7",
    "head": "a" * 40,
    "reason": "the required checks at the head are red",
}
RAN = {"due": True, "item": "pr#7", "head": "b" * 40, "outcome": "ok", "num_turns": 8, "duration_s": 25.4}


def test_a_step_that_was_not_due_says_so_in_the_job_and_in_an_annotation(tmp_path):
    said, summary = [], tmp_path / "summary.md"
    onestep.ci_report(
        "reviewer", NOT_DUE, {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary)}, said.append
    )
    assert said == [
        "reviewer: not run (not due): the required checks at the head are red pr#7",
        "::notice title=reviewer%3A not run::reviewer: not run (not due): "
        "the required checks at the head are red pr#7",
    ]
    assert (
        summary.read_text(encoding="utf-8")
        == "reviewer: not run (not due): the required checks at the head are red pr#7\n"
    )


def test_a_step_that_ran_writes_its_line_and_no_annotation(tmp_path):
    said, summary = [], tmp_path / "summary.md"
    onestep.ci_report(
        "reviewer", RAN, {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary)}, said.append
    )
    assert said == ["reviewer: ran pr#7 at bbbbbbbb: ok, 8 turns, 25.4 s"]
    assert summary.read_text(encoding="utf-8") == "reviewer: ran pr#7 at bbbbbbbb: ok, 8 turns, 25.4 s\n"


def test_outside_github_actions_nothing_is_reported(tmp_path):
    said = []
    onestep.ci_report("reviewer", NOT_DUE, {"GITHUB_STEP_SUMMARY": str(tmp_path / "s.md")}, said.append)
    assert said == [] and not (tmp_path / "s.md").exists()


def test_a_refusal_is_reported_and_a_message_cannot_inject_a_second_command():
    said = []
    onestep.ci_report(
        "reviewer",
        {"due": False, "error": "refused\n::error::forged %"},
        {"GITHUB_ACTIONS": "true"},
        said.append,
    )
    assert said == [
        "reviewer: not run (refused): refused ::error::forged %",
        "::notice title=reviewer%3A not run::reviewer: not run (refused): refused ::error::forged %25",
    ]
    assert not any(line.startswith("::error") or "\n" in line or "\r" in line for line in said)
