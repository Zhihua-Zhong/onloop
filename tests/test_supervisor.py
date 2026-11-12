"""The supervisor's planner, step runner, attention rules and digest: planted cases that prove each check bites.

Live steps run a stand-in for `claude -p` (tests/fixtures/fake_claude.py).
"""

import copy
import datetime as dt
import json
import subprocess
import time

import pytest

from onloop import cli
from onloop.supervisor import asks as asks_mod
from onloop.supervisor import attention, config, notify, planner, schedule, sources, step, steplog
from onloop.supervisor.items import WorkItem, asks_of_private

from .supervisor_helpers import (
    EXAMPLE,
    NOW,
    RAW,
    NoPopen,
    env_without_key,
    gone,
    issue,
    make_cfg,
    pr,
    ts,
    write_fixture,
)


def plan(cfg, recs=(), mode="dry_run"):
    """What the planner would start now, on a fresh look at the source and the given step records."""
    snap = sources.make(cfg.source, cfg.asks, lambda: NOW).look()
    return planner.plan_once(cfg, snap, list(recs), mode, ts(NOW))


def due(cfg, recs=()):
    return [(d.role, d.item.id) for d in plan(cfg, recs).due]


def ran(p, outcome="dry_run", final=True, mode="dry_run"):
    """The step records of every step a plan starts, each ended with `outcome`."""
    out = []
    for d in p.due:
        common = {
            "mode": mode,
            "step_id": d.sid,
            "attempt": 1,
            "role": d.role,
            "item_id": d.item.id,
            "item_version": d.item.version,
            "round": d.round,
            "at": ts(NOW),
        }
        out += [
            {"type": "step.started", **common},
            {"type": "step.ended", **common, "outcome": outcome, "final": final},
        ]
    return out


# -- the command line the runner builds -----------------------------------------------------------------------------


def test_the_command_line_is_built_exactly_from_the_role_table(tmp_path):
    cfg = make_cfg(tmp_path)
    argv = step.build_command(("claude",), cfg.role("reviewer"), "00000000-0000-0000-0000-000000000001")
    assert argv == [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "10",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "Read,Bash",
        "--allowedTools",
        "Read,Bash(gh pr view *)",
        "--strict-mcp-config",
        "--setting-sources",
        "user",
        "--session-id",
        "00000000-0000-0000-0000-000000000001",
    ]
    none = step.build_command(("claude",), cfg.role("operator"), "u")
    assert none[none.index("--tools") + 1] == "" and "--allowedTools" not in none


def test_the_role_table_refuses_what_would_widen_or_break_a_step(tmp_path):
    def bad(**role):
        raw = copy.deepcopy(RAW)
        raw["roles"] = [{**raw["roles"][1], **role}]
        with pytest.raises(config.ConfigError) as e:
            config.from_dict(raw, tmp_path / "s.yaml")
        return str(e.value)

    assert "bypassPermissions is refused" in bad(permission_mode="bypassPermissions")
    assert "unknown variables secret" in bad(prompt="hi $secret")
    assert "comma" in bad(allowed_tools=["Bash(a,b)"])
    assert "unknown keys" in bad(extra_args=["--dangerously-skip-permissions"])
    assert "budget needs" in bad(budget={"wall": "5m"})
    assert "silence cannot be longer" in bad(budget={"wall": "5m", "turns": 3, "silence": "6m"})
    raw = copy.deepcopy(RAW)
    raw["temporal"] = {"instance": "main"}  # the Temporal layer is gone, and so is its block
    with pytest.raises(config.ConfigError, match="unknown keys"):
        config.from_dict(raw, tmp_path / "s.yaml")


# -- due, not due, and not twice ------------------------------------------------------------------------------------


def test_a_due_step_is_detected_and_is_not_due_again_until_the_item_changes(tmp_path):
    cfg = make_cfg(tmp_path, open_=[pr(12)])
    p = plan(cfg)
    assert [(d.role, d.item.id) for d in p.due] == [("reviewer", "pr#12")]
    recs = ran(p)
    assert due(cfg, recs) == []  # unchanged: nothing due
    write_fixture(tmp_path, open_=[pr(12, updated="2026-11-02T17:00:00Z")])
    assert due(cfg, recs) == [("reviewer", "pr#12")]  # a push: due again
    path = cfg.state_dir / "steps.jsonl"
    assert steplog.write_step_record(path, recs[1]) is True
    assert steplog.write_step_record(path, recs[1]) is False  # a record is written once


def test_a_draft_pull_request_is_not_in_the_reviewer_queue(tmp_path):
    assert due(make_cfg(tmp_path, open_=[pr(12, draft=True)])) == []


def test_a_running_role_is_not_double_started(tmp_path):
    cfg = make_cfg(tmp_path, open_=[pr(12), pr(13)])
    first = plan(cfg)
    assert [d.item.id for d in first.due] == ["pr#12"]  # wip 1
    again = plan(cfg, [r for r in ran(first) if r["type"] == "step.started"])
    assert again.due == []  # open in the log: the role is full
    assert (
        again.running["reviewer"] == 1 and again.pending["reviewer"]["count"] == 1
    )  # pr#13 waits, and is counted


def test_a_failed_attempt_is_left_to_the_engine_and_a_final_one_is_given_up(tmp_path):
    cfg = make_cfg(tmp_path)
    role = cfg.role("reviewer")
    item = WorkItem(kind="pr", id="pr#12", version="v1", updated=ts(NOW))
    sid = step.step_id("reviewer", "pr#12", "v1")

    def ended(final):
        return [
            {
                "type": "step.started",
                "mode": "live",
                "step_id": sid,
                "attempt": 1,
                "role": "reviewer",
                "item_id": "pr#12",
                "item_version": "v1",
                "round": 0,
                "at": ts(NOW),
            },
            {
                "type": "step.ended",
                "mode": "live",
                "step_id": sid,
                "attempt": 1,
                "role": "reviewer",
                "item_id": "pr#12",
                "item_version": "v1",
                "round": 0,
                "at": ts(NOW),
                "outcome": "failed",
                "final": final,
            },
        ]

    later = ts(NOW + dt.timedelta(hours=9))
    assert schedule.candidates(role, [item], steplog.step_view(ended(False), "live"), later) == []
    plan = schedule.Plan()
    assert schedule.candidates(role, [item], steplog.step_view(ended(True), "live"), later, plan) == []
    assert plan.given_up and plan.given_up[0]["step_id"] == sid
    assert schedule.next_due(role, "failed", ts(NOW), final=False) > ts(NOW)
    assert schedule.next_due(role, "failed", ts(NOW), final=True) is None


# -- the person's queue: drain ----------------------------------------------------------------------------------------


def test_an_ask_enters_its_owner_queue_when_its_dependency_clears(tmp_path):
    ask = issue(21, labels=["kind/ask", "role/author"], body="Depends on: #12")
    cfg = make_cfg(tmp_path, open_=[pr(12), ask], asks=[ask])
    assert due(cfg) == [("reviewer", "pr#12")]  # the ask waits on #12
    write_fixture(tmp_path, open_=[ask], asks=[ask])  # #12 merged
    assert due(cfg) == [("author", "ask#21")]


def test_waiting_on_ci_is_a_state_not_idleness(tmp_path):
    ask = issue(22, labels=["kind/ask", "role/author", "state/waiting-ci"], updated="2026-11-02T17:50:00Z")
    cfg = make_cfg(tmp_path, open_=[ask], asks=[ask])
    p = plan(cfg)
    assert p.due == []
    snap = sources.make(cfg.source, cfg.asks, lambda: NOW).look()
    state = planner.attention_state(cfg, snap, p, steplog.step_view([], "dry_run"), None)
    got = attention.evaluate_attention(state, ts(NOW), cfg.attention, [])
    assert not [a for a in got if a.rule == "role_starved" and a.status == "fire"]


# -- the step: dry run, progress, stall, overrun, the metered key ---------------------------------------------------


def test_a_dry_run_never_executes(tmp_path):
    NoPopen.calls = 0
    cfg = make_cfg(tmp_path, open_=[pr(12), issue(20, labels=["ops"])])
    res = step.run_step(
        cfg.role("reviewer"),
        WorkItem(kind="pr", id="pr#12", version="v"),
        sid="s-a",
        popen=NoPopen(),
        environ={step.METERED_KEY: "x"},
    )
    assert res.outcome == "dry_run" and res.pid is None and res.argv[:2] == ["claude", "-p"]
    assert NoPopen.calls == 0


def test_a_step_reports_progress_on_output_and_returns_its_result(tmp_path):
    cfg = make_cfg(tmp_path, open_=[pr(12)], live_fake=True)
    phases = []
    res = step.run_step(
        cfg.role("reviewer"),
        WorkItem(kind="pr", id="pr#12", version="v"),
        sid="s-p",
        claude=cfg.claude,
        live=True,
        out_dir=tmp_path / "out",
        progress=phases.append,
        progress_every=0.2,
        environ=env_without_key(FAKE_CLAUDE_MODE="slow", FAKE_CLAUDE_SECONDS="1.5"),
    )
    assert res.outcome == "ok" and res.num_turns == 3
    assert phases[0]["phase"] == "started" and phases[0]["pid"] and phases[0]["deadline"]
    assert "output" in {p["phase"] for p in phases} and {p["phase"] for p in phases} <= {
        "started",
        "output",
        "running",
    }


def _hang(tmp_path, **budget):
    raw_roles = copy.deepcopy(RAW["roles"])
    for r in raw_roles:
        r["budget"] = budget
    pids = tmp_path / "pids.txt"
    cfg = make_cfg(tmp_path, open_=[pr(12)], live_fake=True, roles=raw_roles)
    role = cfg.role("reviewer")
    res = step.run_step(
        role,
        WorkItem(kind="pr", id="pr#12", version="v"),
        sid="s-h",
        claude=cfg.claude,
        live=True,
        out_dir=tmp_path / "out",
        silence=role.silence,
        environ=env_without_key(FAKE_CLAUDE_MODE="hang", FAKE_CLAUDE_PIDS=str(pids)),
    )
    leader, grandchild = (int(x) for x in pids.read_text(encoding="utf-8").split())
    return res, leader, grandchild


def test_a_silent_step_is_stalled_by_its_own_pid(tmp_path):
    res, leader, grandchild = _hang(tmp_path, wall="60s", turns=5, silence="1s")
    assert res.outcome == "stalled" and str(res.pid) in res.killed
    assert gone(leader) and gone(grandchild)  # the whole tree, from that pid


def test_an_overrun_is_killed_by_its_own_pid(tmp_path):
    t0 = time.monotonic()
    res, leader, grandchild = _hang(tmp_path, wall="1s", turns=5)
    assert res.outcome == "overrun" and str(res.pid) in res.killed and time.monotonic() - t0 < 30
    assert gone(leader) and gone(grandchild)


def test_a_hard_deadline_wins_over_the_wall_budget(tmp_path):
    cfg = make_cfg(tmp_path, live_fake=True)
    t0 = time.time()
    res = step.run_step(
        cfg.role("reviewer"),
        WorkItem(kind="pr", id="pr#12", version="v"),
        sid="s-z",
        claude=cfg.claude,
        live=True,
        deadline=t0 + 1,
        environ=env_without_key(FAKE_CLAUDE_MODE="hang", FAKE_CLAUDE_PIDS=str(tmp_path / "p.txt")),
    )
    assert res.outcome == "overrun" and res.duration_s < 15


def test_a_failed_step_records_its_error(tmp_path):
    cfg = make_cfg(tmp_path, live_fake=True)
    bad = step.run_step(
        cfg.role("reviewer"),
        WorkItem(kind="pr", id="pr#12", version="v"),
        sid="s-y",
        claude=cfg.claude,
        live=True,
        out_dir=tmp_path / "out",
        environ=env_without_key(FAKE_CLAUDE_MODE="fail"),
    )
    assert bad.outcome == "failed" and "something broke" in bad.error


def test_a_live_step_refuses_a_metered_api_key(tmp_path):
    NoPopen.calls = 0
    cfg = make_cfg(tmp_path, open_=[pr(12)])
    with pytest.raises(step.MeteredKeyRefused):
        step.run_step(
            cfg.role("reviewer"),
            WorkItem(kind="pr", id="pr#12", version="v"),
            sid="s-b",
            live=True,
            popen=NoPopen(),
            environ={step.METERED_KEY: "sk-test"},
        )
    assert NoPopen.calls == 0


def test_a_configuration_error_exits_2_before_anything_starts(tmp_path, capsys):
    (tmp_path / "bad.yaml").write_text(
        "roles: [{name: x, permission_mode: bypassPermissions}]", encoding="utf-8"
    )
    assert cli.main(["supervise", "--config", str(tmp_path / "bad.yaml"), "--once"]) == 2
    assert "bypassPermissions is refused" in capsys.readouterr().err


def test_a_failed_source_plans_nothing(tmp_path, capsys):
    make_cfg(tmp_path, open_=[pr(12)])
    (tmp_path / "fixture.json").write_text("{not json", encoding="utf-8")
    assert cli.main(["supervise", "--config", str(tmp_path / "supervisor.yaml"), "--once"]) == 1
    assert "would run" not in capsys.readouterr().out


# -- the planner (supervise --once) ---------------------------------------------------------------------------------


def test_once_prints_every_due_command_and_writes_nothing(tmp_path, capsys):
    make_cfg(tmp_path, open_=[pr(12), issue(20, labels=["ops"])])
    assert cli.main(["supervise", "--config", str(tmp_path / "supervisor.yaml"), "--once"]) == 0
    out = capsys.readouterr().out
    assert (
        out.count("$ claude -p --output-format stream-json --verbose") == 2 and "nothing was started" in out
    )
    assert not (tmp_path / "state").exists()


# -- attention ----------------------------------------------------------------------------------------------------


def _ask_state(updated="2026-11-01T00:00:00.000Z", state="open"):
    return {"asks": [{"id": "ask#1", "title": "t", "state": state, "updated": updated, "owner": "author"}]}


def test_an_attention_rule_fires_once_and_respects_its_quiet_period(tmp_path):
    cfg = make_cfg(tmp_path).attention
    hist = []

    def at(hours):
        got = attention.evaluate_attention(
            _ask_state(), ts(dt.datetime(2026, 11, 1, tzinfo=dt.UTC) + dt.timedelta(hours=hours)), cfg, hist
        )
        hist.extend(a.as_dict() for a in got)
        return [a for a in got if a.status == "fire"]

    assert at(23) == []  # under the threshold
    first = at(25)
    assert len(first) == 1 and first[0].rule == "item_stale" and first[0].repeat == 1
    assert at(26) == [] and at(36) == []  # inside the 12 h quiet period
    second = at(38)
    assert len(second) == 1 and second[0].repeat == 2 and second[0].last  # the cap (2) is stated
    assert at(51) == [] and at(80) == []  # capped until it clears
    got = attention.evaluate_attention(
        _ask_state(state="done"), ts(dt.datetime(2026, 11, 5, tzinfo=dt.UTC)), cfg, hist
    )
    assert [a.status for a in got] == ["clear"]
    hist.extend(a.as_dict() for a in got)
    again = attention.evaluate_attention(_ask_state(), ts(dt.datetime(2026, 11, 6, tzinfo=dt.UTC)), cfg, hist)
    assert [a.repeat for a in again if a.status == "fire"] == [1]


def test_the_other_attention_rules_bite(tmp_path):
    cfg = make_cfg(tmp_path).attention
    now = ts(NOW)
    state = {
        "asks": [
            {"id": "ask#2", "title": "pick", "state": "waiting-human", "updated": now, "default": "yes"}
        ],
        "roles": {
            "reviewer": {
                "pending": 2,
                "pending_since": ts(NOW - dt.timedelta(hours=1)),
                "last_step_at": None,
            },
            "author": {"pending": 0, "pending_since": None, "last_step_at": None},
        },
        "heartbeat": {"at": ts(NOW - dt.timedelta(minutes=11)), "pid": 1, "host": "h"},
    }
    rules = sorted((a.rule, a.key) for a in attention.evaluate_attention(state, now, cfg, []))
    assert rules == [
        ("heartbeat_stale", "supervisor"),
        ("role_starved", "reviewer"),
        ("waiting_on_person", "person"),
    ]
    state["heartbeat"] = {"at": ts(NOW - dt.timedelta(minutes=2))}
    state["roles"]["reviewer"]["last_step_at"] = ts(NOW - dt.timedelta(minutes=5))
    assert [a.rule for a in attention.evaluate_attention(state, now, cfg, [])] == ["waiting_on_person"]
    state["heartbeat"] = None
    assert "heartbeat_stale" in [a.rule for a in attention.evaluate_attention(state, now, cfg, [])]


def test_the_outside_attention_check_flags_a_stale_heartbeat(tmp_path, capsys):
    cfg = make_cfg(tmp_path, open_=[pr(12)])
    old = ts(dt.datetime.now(dt.UTC) - dt.timedelta(hours=1))
    steplog.write_json_atomic(
        cfg.state_dir / "heartbeat.json", {"at": old, "pid": 1, "host": "h", "mode": "dry_run"}
    )
    assert cli.main(["attention", "--config", str(tmp_path / "supervisor.yaml")]) == 1
    assert "heartbeat_stale" in capsys.readouterr().out
    assert not (cfg.state_dir / "alerts.jsonl").exists()  # without --notify it writes nothing


# -- digest ---------------------------------------------------------------------------------------------------------


def test_the_digest_is_correct_for_a_small_queue():
    cfg = config.load(EXAMPLE / "supervisor.yaml")
    snap = sources.FixtureSource(cfg.source, cfg.asks, lambda: NOW).look()
    d = asks_mod.digest(snap.asks, "2026-11-01T18:00:00.000Z", ts(NOW), snap.open_ids)
    assert [(r["id"], r["waits_on"]) for r in d.moved] == [("ask#22", [])]
    assert d.unmoved == 1  # ask#21 last moved before `since`
    assert [r["id"] for r in d.done] == ["ask#19"]
    assert [(r["id"], r["default"]) for r in d.waiting] == [
        ("ask#23", "the 14th, if there is no answer by Friday")
    ]
    text = asks_mod.digest_text(d)
    assert text[0].endswith("1 moved, 1 done, 1 wait on you") and len(text) <= 10
    wide = asks_mod.digest(snap.asks, "2026-11-01T00:00:00.000Z", ts(NOW), snap.open_ids)
    assert [(r["id"], r["waits_on"]) for r in wide.moved] == [("ask#21", ["#12"]), ("ask#22", [])]
    nodefault = asks_mod.digest(
        [WorkItem(kind="ask", id="ask#9", version="", title="x", state="waiting-human")], ts(NOW), ts(NOW)
    )
    assert "none stated" in "\n".join(asks_mod.digest_text(nodefault))


def test_private_asks_are_digested_but_never_drained():
    rows = [
        {
            "id": "p1",
            "ask": "renew the passport photo",
            "owner": "author",
            "state": "open",
            "last_moved": "2026-11-02T10:00:00Z",
        }
    ]
    private = asks_of_private(rows)
    assert private[0].private and private[0].id == "ask:p1"
    assert asks_mod.drain_asks(private, set()) == {}
    d = asks_mod.digest(private, "2026-11-02T00:00:00.000Z", ts(NOW))
    assert [r["id"] for r in d.moved] == ["ask:p1"]


def test_the_digest_command_reads_the_snapshot_and_writes_nothing(tmp_path, capsys):
    cfg = make_cfg(
        tmp_path,
        open_=[],
        asks=[issue(30, labels=["kind/ask", "state/waiting-human"], body="Default: go ahead")],
    )
    snap = sources.make(cfg.source, cfg.asks, lambda: NOW).look()
    steplog.write_json_atomic(cfg.state_dir / "snapshot.json", snap.as_dict())
    before = sorted(p.name for p in cfg.state_dir.iterdir())
    assert (
        cli.main(["digest", "--config", str(tmp_path / "supervisor.yaml"), "--now", "2026-11-02T18:00:00Z"])
        == 0
    )
    out = capsys.readouterr().out
    assert "ask#30" in out and "Default: go ahead" in out
    assert sorted(p.name for p in cfg.state_dir.iterdir()) == before


# -- sources and notifiers ------------------------------------------------------------------------------------------


def test_the_github_source_reads_with_gets_only(tmp_path):
    calls = []

    def runner(argv):
        calls.append(argv)
        body = (
            json.dumps([pr(12), issue(21, labels=["kind/ask", "role/author"])])
            if "state=open" in argv[-1]
            else json.dumps([issue(21, labels=["kind/ask", "role/author"])])
        )
        return subprocess.CompletedProcess(argv, 0, body, "")

    raw = copy.deepcopy(RAW)
    raw["source"] = {"type": "github", "repo": "example-org/example-repo"}
    cfg = config.from_dict(raw, tmp_path / "s.yaml")
    snap = sources.make(cfg.source, cfg.asks, lambda: NOW, runner).look()
    assert [i.id for i in snap.items] == ["issue#21", "pr#12"] and [a.id for a in snap.asks] == ["ask#21"]
    assert all(a[:4] == ["gh", "api", "--method", "GET"] for a in calls)
    assert "labels=kind%2Fask" in calls[1][-1]

    def failing(argv):
        return subprocess.CompletedProcess(argv, 1, "", "HTTP 502")

    with pytest.raises(sources.SourceError):
        sources.make(cfg.source, cfg.asks, lambda: NOW, failing).look()


def test_notifiers_refuse_plain_http_and_build_desktop_commands_without_a_shell():
    with pytest.raises(ValueError):
        notify.WebhookNotifier("http://example.com/hook")
    notify.WebhookNotifier("http://localhost:9000/hook")
    posted = []

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    w = notify.WebhookNotifier(
        "https://example.com/hook", opener=lambda req, timeout: posted.append(req) or Resp()
    )
    assert w.send("t", "x", {"rule": "item_stale", "key": "ask#1"}) == "webhook 200"
    assert json.loads(posted[0].data)["rule"] == "item_stale"
    argv, env = notify.DesktopNotifier(platform="win32").command("title", "it's $(bad)")
    assert argv[0] == "powershell" and "$(bad)" not in argv[-1] and env["ONLOOP_NOTICE_TEXT"] == "it's $(bad)"
    assert notify.DesktopNotifier(platform="linux").command("a", "b")[0] == [
        "notify-send",
        "--app-name=onloop",
        "a",
        "b",
    ]


def test_the_example_configuration_loads_and_the_selftest_bites():
    cfg = config.load(EXAMPLE / "supervisor.yaml")
    assert [r.name for r in cfg.roles] == ["author", "reviewer", "operator"]
    ok, lines = attention.self_test()
    assert ok, lines
