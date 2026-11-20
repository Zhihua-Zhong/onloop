"""Supervisor steps and telemetry (decision 0019, spike S4): a step never loads the user settings file, whose `env` would
win over its own, and its environment is onloop's own OTel environment toward a running onloopd, or none at all."""

import json

import pytest

from onloop import telemetry
from onloop.daemon import main as daemon
from onloop.daemon import token as token_mod
from onloop.supervisor import config, items, onestep, step

from .test_supervisor_gates import _live_cfg, env_without_key, gated_raw

ELSEWHERE = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.example:4318", "OTEL_LOGS_EXPORTER": "otlp"}


def test_a_step_loads_project_settings_by_default(tmp_path):
    cfg = config.from_dict(gated_raw(), tmp_path / "s.yaml")
    argv = step.build_command(("claude",), cfg.roles[0], "u")
    assert argv[argv.index("--setting-sources") + 1] == "project"


@pytest.mark.parametrize("sources", [["user"], ["user", "project"], ["project", "user", "local"]])
def test_a_step_that_asks_for_the_user_settings_file_is_refused(tmp_path, sources):
    with pytest.raises(config.ConfigError, match="may not name user"):
        config.from_dict(gated_raw(setting_sources=sources), tmp_path / "s.yaml")


class Spawned(Exception):
    pass


def spawn_env(tmp_path, monkeypatch, environ, *, beat=False):
    """The environment a live step would start with: the fake popen keeps it and refuses to start."""
    state = tmp_path / "onloopd"
    state.mkdir()
    if beat:
        (state / daemon.HEARTBEAT_FILE).write_text(json.dumps({"port": 8790}), encoding="utf-8")
        token_mod.issue(state)
    got = {}

    def popen(argv, **kw):
        got.update(kw["env"])
        raise OSError("not started in this test")

    cfg = _live_cfg(tmp_path)
    item = items.WorkItem(kind="pr", id="pr#12", version="v")
    r = step.run_step(
        cfg.roles[0],
        item,
        sid="s-1",
        claude=cfg.claude,
        live=True,
        environ=environ,
        popen=popen,
        onloopd=state,
    )
    assert r.outcome == "spawn_failed"
    return got, state


def test_a_live_step_without_onloopd_has_no_otel_variable_at_all(tmp_path, monkeypatch):
    env, _ = spawn_env(tmp_path, monkeypatch, {**env_without_key(), **ELSEWHERE})
    assert not [k for k in env if k.startswith("OTEL_")] and "CLAUDE_CODE_ENABLE_TELEMETRY" not in env
    assert env["ONLOOP_ROLE"] == "reviewer"


def test_a_live_step_with_onloopd_sends_to_it_only(tmp_path, monkeypatch):
    env, state = spawn_env(tmp_path, monkeypatch, {**env_without_key(), **ELSEWHERE}, beat=True)
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == f"http://{telemetry.HOST}:8790"
    assert env["OTEL_EXPORTER_OTLP_HEADERS"] == f"Authorization=Bearer {token_mod.read(state)}"
    assert env[
        "OTEL_RESOURCE_ATTRIBUTES"
    ] == "onloop.role=reviewer" and "collector.example" not in json.dumps(env)


def test_a_step_reports_to_the_onloopd_of_the_default_ledger(tmp_path):
    assert (
        onestep.onloopd_state({"ONLOOP_LEDGER": str(tmp_path / "x" / "ledger.db")})
        == (tmp_path / "x").resolve()
    )


def test_telemetry_reads_the_files_onloopd_writes():
    assert telemetry.HEARTBEAT_FILE == daemon.HEARTBEAT_FILE and telemetry.TOKEN_FILE == token_mod.TOKEN_FILE


def test_a_repositorys_allow_rule_does_not_widen_a_step(tmp_path):
    """The lead's ruling (decision 0019): a step runs with --restricted, which makes Claude Code ignore every settings
    file (its --help), so a repository's .claude/settings.json allow rule cannot add to the role's tools. Spike S4's
    addendum 2 could not show the widening in an untrusted folder; this holds the command to the rule."""
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(git push *)", "Write"]}}), encoding="utf-8"
    )
    cfg = config.from_dict(gated_raw(cwd=str(repo)), tmp_path / "s.yaml")
    role = cfg.roles[0]
    argv = step.build_command(("claude",), role, "u")
    assert "--restricted" in argv
    assert argv[argv.index("--allowedTools") + 1] == ",".join(role.allowed_tools)
    assert "git push" not in " ".join(argv) and "Write" not in argv[argv.index("--tools") + 1]


def test_a_folder_outside_cwd_is_read_only_when_the_role_declares_it(tmp_path):
    cfg = config.from_dict(gated_raw(), tmp_path / "s.yaml")
    assert "--add-dir" not in step.build_command(("claude",), cfg.roles[0], "u")
    cfg = config.from_dict(
        gated_raw(add_dirs=["../shared", str(tmp_path / "abs")]), tmp_path / "cfg" / "s.yaml"
    )
    argv = step.build_command(("claude",), cfg.roles[0], "u")
    dirs = [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]
    assert dirs == [str(tmp_path / "cfg" / ".." / "shared"), str(tmp_path / "abs")]
    with pytest.raises(config.ConfigError, match="add_dirs"):
        config.from_dict(gated_raw(add_dirs="../shared"), tmp_path / "s.yaml")
