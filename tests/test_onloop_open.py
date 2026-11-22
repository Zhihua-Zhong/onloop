"""Telemetry for the sessions onloop starts (decision 0019, a8): `onloop open <role>`, the `/sessions/` route, and the
carrier's step events. Each session's OTel environment is its own and points at this onloopd only; every inherited
OTel variable is removed first; the steps are recorded as onloop dispatches the work."""

import json
import threading
import types

import pytest

from onloop import cli, telemetry
from onloop.daemon import server as srv
from onloop.daemon import sessions_route
from onloop.daemon import token as token_mod
from onloop.daemon.routes import Routes
from onloop.overlay.ledger import Ledger

from . import carrier_helpers as h

SID = "00000000-0000-4000-8000-0000000000c1"
ELSEWHERE = {
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.example:4318",
    "OTEL_EXPORTER_OTLP_HEADERS": "x-team=other",
    "OTEL_LOG_USER_PROMPTS": "1",
    "OTEL_LOG_RAW_API_BODIES": "1",
    "OTEL_RESOURCE_ATTRIBUTES": "team=other",
    "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
    "PATH": "/bin",
}


def test_a_sessions_otel_environment_is_onloops_own_and_nothing_inherited():
    env = telemetry.env(ELSEWHERE, port=8790, token="tok", role="builder")
    assert env["PATH"] == "/bin"
    otel = {k: v for k, v in env.items() if k.startswith("OTEL_")}
    assert otel == {
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "none",
        "OTEL_TRACES_EXPORTER": "none",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://{srv.HOST}:8790",
        "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer tok",
        "OTEL_RESOURCE_ATTRIBUTES": "onloop.role=builder",
        "OTEL_LOGS_EXPORT_INTERVAL": "5000",
        "OTEL_LOG_USER_PROMPTS": "0",
        "OTEL_LOG_TOOL_DETAILS": "0",
        "OTEL_METRICS_INCLUDE_ACCOUNT_UUID": "false",
    }
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert "collector.example" not in json.dumps(env) and "team=other" not in json.dumps(env)
    with pytest.raises(ValueError):
        telemetry.env({}, port=1, token="t", role="Not A Role")
    assert telemetry.without(ELSEWHERE) == {"PATH": "/bin"}


@pytest.mark.parametrize("text", ["task:t-12", "pr:7", "branch:feat/x", "node:a1", "item:task:abc"])
def test_a_step_is_kind_and_ref(text):
    kind, ref = telemetry.parse_step(text)
    assert f"{kind}:{ref}" == text


@pytest.mark.parametrize("text", ["", "t-12", "issue:7", "pr:", "pr: 7", "pr:" + "x" * 121])
def test_anything_else_is_not_a_step(text):
    with pytest.raises(ValueError):
        telemetry.parse_step(text)


def args(tmp_path, **kw):
    base = {
        "role": "builder",
        "step": None,
        "cwd": str(tmp_path),
        "claude": "claude-test",
        "dry_run": False,
        "ledger": str(tmp_path / "ledger.db"),
    }
    return types.SimpleNamespace(**{**base, **kw})


def beat(tmp_path, port=8790):
    (tmp_path / "onloopd.heartbeat.json").write_text(json.dumps({"port": port}), encoding="utf-8")
    secret, _ = token_mod.issue(tmp_path)
    return secret


def test_onloop_open_runs_with_project_and_local_settings_and_onloops_environment(tmp_path):
    secret = beat(tmp_path)
    argv, env, cwd, sid, step = cli.open_plan(args(tmp_path, step="pr:7"), {**ELSEWHERE, "CLAUDECODE": "1"})
    assert argv[0].endswith("claude-test") and argv[1:4] == [
        "--setting-sources",
        "project,local",
        "--session-id",
    ]
    assert argv[4] == sid and step == ("pr", "7") and cwd == str(tmp_path.resolve())
    assert env["OTEL_EXPORTER_OTLP_HEADERS"] == f"Authorization=Bearer {secret}"
    assert env["OTEL_RESOURCE_ATTRIBUTES"] == "onloop.role=builder"
    assert "CLAUDECODE" not in env  # a session that starts it does not attach the child to its own machinery


@pytest.mark.parametrize(
    ("environ", "kw", "has_beat"),
    [
        ({"ANTHROPIC_API_KEY": "x"}, {}, True),
        ({"ANTHROPIC_AUTH_TOKEN": "x"}, {}, True),
        ({}, {"step": "issue:7"}, True),
        ({}, {"role": "Not A Role"}, True),
        ({}, {}, False),
    ],
)
def test_onloop_open_refuses_a_metered_key_a_bad_step_or_role_and_no_onloopd(tmp_path, environ, kw, has_beat):
    if has_beat:
        beat(tmp_path)
    with pytest.raises(SystemExit) as e:
        cli.open_plan(args(tmp_path, **kw), environ)
    assert e.value.code == 2


def test_onloop_open_dry_run_prints_the_command_and_never_the_token(tmp_path, capsys):
    secret = beat(tmp_path)
    assert (
        cli.main(
            [
                "open",
                "builder",
                "--dry-run",
                "--claude",
                "claude-test",
                "--ledger",
                str(tmp_path / "ledger.db"),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert (
        "--setting-sources project,local" in out
        and f"OTEL_EXPORTER_OTLP_ENDPOINT=http://{srv.HOST}:8790" in out
    )
    assert secret not in out and "<onloopd's token>" in out


def test_the_sessions_route_logs_the_session_and_its_step_once(tmp_path):
    with Ledger(tmp_path / "l.db") as led:
        body = {"role": "builder", "session_id": SID, "cwd": "/w", "step": "task:t-12"}
        assert sessions_route.handle(led, "opened", body) == {
            "logged": 2,
            "kinds": ["session.opened", "step.started"],
        }
        assert (
            sessions_route.handle(led, "opened", body)["logged"] == 0
        )  # the same report twice is logged once
        sessions_route.handle(led, "ended", {**body, "exit_code": 0})
        rows = led.select("SELECT source, kind, subject_ref, actor, payload FROM events WHERE source = 'cli'")
    assert [(r["kind"], r["subject_ref"]) for r in rows] == [
        ("session.opened", f"session:{SID}"),
        ("step.started", "step:task:t-12"),
        ("step.ended", "step:task:t-12"),
    ]
    assert all(r["actor"] == "builder" for r in rows)
    ended = json.loads(rows[2]["payload"]) if isinstance(rows[2]["payload"], str) else rows[2]["payload"]
    assert ended["exit_code"] == 0 and ended["step"] == {"kind": "task", "ref": "t-12"}


@pytest.mark.parametrize(
    ("rest", "body", "code"),
    [
        ("closed", {"role": "builder", "session_id": SID}, 404),
        ("opened", {"session_id": SID}, 422),
        ("opened", {"role": "builder", "session_id": "has space"}, 422),
        ("opened", {"role": "builder", "session_id": SID, "step": "issue:7"}, 422),
    ],
)
def test_the_sessions_route_refuses_what_it_cannot_place(rest, body, code):
    with pytest.raises(srv.WriteRefused) as e:
        sessions_route.events_for(rest, body)
    assert e.value.status == code


@pytest.fixture
def onloopd(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    routes = Routes(led, threading.Lock())
    routes.add("/sessions/", sessions_route.handle)
    secret, _ = token_mod.issue(tmp_path)
    server = srv.make_server(led, port=0, writer=routes, token=secret)
    (tmp_path / "onloopd.heartbeat.json").write_text(
        json.dumps({"port": server.server_address[1]}), encoding="utf-8"
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield led
    server.shutdown()
    server.server_close()
    led.close()


def test_onloop_open_reports_the_start_runs_the_session_and_reports_its_end(tmp_path, onloopd, monkeypatch):
    ran = []

    def call(argv, env, cwd):
        ran.append((argv, env, cwd))
        return 3

    monkeypatch.setattr("subprocess.call", call)
    code = cli.main(
        [
            "open",
            "builder",
            "--step",
            "node:a1",
            "--claude",
            "claude-test",
            "--ledger",
            str(tmp_path / "ledger.db"),
        ]
    )
    assert code == 3 and len(ran) == 1
    (argv, env, _cwd) = ran[0]
    assert "project,local" in argv and env["OTEL_RESOURCE_ATTRIBUTES"] == "onloop.role=builder"
    rows = onloopd.select("SELECT kind, payload FROM events WHERE source = 'cli' ORDER BY seq")
    assert [r["kind"] for r in rows] == ["session.opened", "step.started", "step.ended"]
    sid = argv[argv.index("--session-id") + 1]
    assert all(r["payload"]["session_id"] == sid for r in rows) and rows[2]["payload"]["exit_code"] == 3


def test_onloop_open_starts_nothing_when_onloopd_does_not_take_the_start(tmp_path, monkeypatch, capsys):
    beat(tmp_path, port=1)  # nothing listens there
    monkeypatch.setattr("subprocess.call", lambda *a, **k: pytest.fail("started without onloopd"))
    assert (
        cli.main(["open", "builder", "--claude", "claude-test", "--ledger", str(tmp_path / "ledger.db")]) == 2
    )
    assert "nothing is opened" in capsys.readouterr().err


# -- the carrier ------------------------------------------------------------------------------------------------------


def test_a_carried_turn_gets_onloops_otel_environment_and_none_inherited(tmp_path):
    c = h.carrier(tmp_path, environ={**h.env(tmp_path), **ELSEWHERE})
    role = c.cfg.roles[0]
    assert telemetry.without(c.child_env(role)) == c.child_env(
        role
    )  # no OTel variable without onloopd's address
    secret = beat(tmp_path, port=8790)
    env = c.child_env(role)
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == f"http://{srv.HOST}:8790"
    assert env["OTEL_EXPORTER_OTLP_HEADERS"] == f"Authorization=Bearer {secret}"
    assert env["OTEL_RESOURCE_ATTRIBUTES"] == "onloop.role=builder" and "collector.example" not in json.dumps(
        env
    )
    c.led.close()


def test_a_carried_turn_with_no_item_records_no_step_and_its_run_records_where_it_works(tmp_path):
    """A run is not a step (decision 0019): a message with no item opens no step span, so rule E can place the turn's
    cost by the branch run.started records. The turn still gets onloop's OTel environment."""
    beat(tmp_path, port=8790)
    dump = tmp_path / "env.json"
    c = h.carrier(tmp_path, environ=h.env(tmp_path, FAKE_ENV_DUMP=dump))
    h.start(c)
    h.say(c, "please look at the parser")
    c.tick()
    assert c.led.select("SELECT kind FROM events WHERE kind LIKE 'step.%'") == []
    names = json.loads(dump.read_text(encoding="utf-8"))
    assert {
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "CLAUDE_CODE_ENABLE_TELEMETRY",
    } <= set(names)
    c.led.close()
