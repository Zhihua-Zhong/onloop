"""A pull request is due only while its head's checks are green; priority paths go first; a step's result goes to a
file the supervisor writes; a step loads only the role's settings."""

import copy
import json
import pathlib
import subprocess

import pytest
import yaml

from onloop.supervisor import config, items, onestep, planner, sources, step
from onloop.supervisor.config import ConfigError

from .supervisor_helpers import NOW, RAW, env_without_key, pr

A, B = "a" * 40, "b" * 40
BOT = "review-bot[bot]"
REQ = ("unit", "lint")


def run(name, conclusion="success", status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion if status == "completed" else None}


def pull(n, head, draft=False, base="main"):
    return {
        "number": n,
        "draft": draft,
        "head": {"sha": head},
        "base": {"ref": base},
        "updated_at": "2026-11-01T09:00:00Z",
    }


RULES = [
    {"type": "pull_request"},
    {
        "type": "required_status_checks",
        "parameters": {"required_status_checks": [{"context": "unit"}, {"context": "lint"}]},
    },
]


def gated_raw(names="ruleset", **role):
    raw = copy.deepcopy(RAW)
    del raw["asks"]
    raw["roles"] = [
        {
            **raw["roles"][1],
            "prompt": "Review $item_id at $item_head",
            "wip": 2,
            "triggers": [
                {
                    "kind": "pr",
                    "draft": False,
                    "version": "head",
                    "unanswered_by": BOT,
                    "require": {"checks": "green", "names": names},
                }
            ],
            **role,
        }
    ]
    return raw


def fixture_cfg(tmp_path, open_, pulls, checks=None, files=None, rules=None, raw=None):
    (tmp_path / "fixture.json").write_text(
        json.dumps(
            {
                "open": open_,
                "pulls": pulls,
                "checks": checks or {},
                "files": files or {},
                "rules": rules or {"main": RULES},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(raw or gated_raw()), encoding="utf-8")
    return config.load(tmp_path / "s.yaml")


def queue(cfg):
    return [
        i.id
        for i in planner.queues_of(cfg, sources.make(cfg.source, cfg.asks, lambda: NOW).look())["reviewer"]
    ]


# -- what green means -----------------------------------------------------------------------------------------------


def test_green_means_every_required_check_passed_and_nothing_else_counts():
    assert items.checks_state([run("unit"), run("lint"), run("docs", "failure")], [], REQ) == "green"
    assert items.checks_state([run("unit"), run("lint", status="in_progress")], [], REQ) == "pending"
    assert items.checks_state([run("unit")], [], REQ) == "pending"  # lint has not reported yet
    assert items.checks_state([run("unit"), run("lint", "failure")], [], REQ) == "red"
    assert items.checks_state([run("unit"), run("lint", "timed_out")], [], REQ) == "red"
    assert items.checks_state([run("unit"), run("lint", "skipped")], [], REQ) == "green"
    assert items.checks_state([run("unit")], [{"context": "lint", "state": "success"}], REQ) == "green"
    assert (
        items.checks_state([run("unit"), run("unit", "failure"), run("lint")], [], REQ) == "red"
    )  # worse wins


def at(r, started, app=15368):
    return {**r, "started_at": started, "app": {"id": app}}


def test_a_check_re_run_in_a_later_suite_counts_only_by_its_latest_run():
    # The shape found on a real head: the check-runs endpoint lists one run per suite, cancelled ones included.
    runs = [
        at(run("unit", "cancelled"), "2026-11-03T01:40:32Z"),
        at(run("unit"), "2026-11-04T00:45:03Z"),
        at(run("lint", "cancelled"), "2026-11-03T02:17:24Z"),
        at(run("lint", "cancelled"), "2026-11-03T06:16:25Z"),
        at(run("lint"), "2026-11-04T01:43:52Z"),
    ]
    assert items.checks_state(runs, [], REQ) == "green"
    later_failure = [
        at(run("unit"), "2026-11-03T00:00:00Z"),
        at(run("unit", "failure"), "2026-11-04T00:00:00Z"),
        at(run("lint"), "2026-11-04T00:00:00Z"),
    ]
    assert items.checks_state(later_failure, [], REQ) == "red"
    rerunning = [
        at(run("unit"), "2026-11-03T00:00:00Z"),
        at(run("unit", status="in_progress"), "2026-11-04T00:00:00Z"),
        at(run("lint"), "2026-11-04T00:00:00Z"),
    ]
    assert items.checks_state(rerunning, [], REQ) == "pending"
    other_app = [
        at(run("unit", "failure"), "2026-11-03T00:00:00Z"),
        at(run("unit"), "2026-11-04T00:00:00Z", app=1),
        at(run("lint"), "2026-11-04T00:00:00Z"),
    ]
    assert (
        items.checks_state(other_app, [], REQ) == "red"
    )  # another app's later pass does not clear this app's failure


def test_without_required_names_every_reported_check_counts():
    assert items.checks_state([run("unit"), run("docs")], [], ()) == "green"
    assert items.checks_state([run("unit"), run("docs", "failure")], [], ()) == "red"
    assert items.checks_state([], [{"context": "ci", "state": "pending"}], ()) == "pending"
    assert items.checks_state([], [], ()) == "none"  # nothing reported: not green


def test_the_ruleset_names_the_required_checks():
    assert sources.required_names(RULES) == REQ
    assert sources.required_names([{"type": "deletion"}]) == ()


# -- configuration --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change, message",
    [
        ({"triggers": [{"kind": "issue", "require": {"checks": "green"}}]}, "only for kind pr"),
        ({"triggers": [{"kind": "pr", "require": {"checks": "blue"}}]}, "checks: green"),
        ({"triggers": [{"kind": "pr", "require": {"checks": "green", "names": "some"}}]}, "require.names"),
        ({"setting_sources": ["everything"]}, "setting_sources"),
        ({"setting_sources": []}, "setting_sources"),
        ({"result_file": "out/$item_title.md"}, "may name only"),
        ({"result_file": "../outside/$item_number.md"}, "inside the configuration"),
        ({"result_schema": "missing.json"}, "not a readable JSON file"),
        ({"priority_when": "some"}, "priority_when"),
        ({"after_result": {"command": ["post"]}}, "needs result_file"),
        (
            {"result_file": "r/$item_number.md", "after_result": {"command": []}},
            "a program and its arguments",
        ),
        (
            {"result_file": "r/$item_number.md", "after_result": {"command": ["post", "$item_title"]}},
            "may name only",
        ),
        ({"result_file": "r/$item_number.md", "after_result": {"command": ["post"], "shell": True}}, "shell"),
    ],
)
def test_bad_gate_and_result_settings_are_refused(tmp_path, change, message):
    raw = gated_raw(**change)
    with pytest.raises(ConfigError, match=message):
        config.from_dict(raw, tmp_path / "s.yaml")


def test_the_source_reads_checks_and_paths_only_for_roles_that_need_them(tmp_path):
    cfg = config.from_dict(gated_raw(priority_paths=["web/**"]), tmp_path / "s.yaml")
    assert cfg.source.checks and cfg.source.globs == ("web/**",) and len(cfg.source.watch) == 1
    plain = config.from_dict(copy.deepcopy(RAW), tmp_path / "s.yaml")
    assert not plain.source.checks and plain.source.watch == () and plain.source.globs == ()


# -- the queue ------------------------------------------------------------------------------------------------------


def test_only_a_green_head_is_due_and_a_new_push_is_judged_on_its_own_checks(tmp_path):
    good = {"check_runs": [run("unit"), run("lint")]}
    cfg = fixture_cfg(
        tmp_path,
        [pr(12), pr(13), pr(14)],
        [pull(12, A), pull(13, B), pull(14, "c" * 40)],
        checks={
            A: good,
            B: {"check_runs": [run("unit"), run("lint", "failure")]},
            "c" * 40: {"check_runs": [run("unit", status="queued")]},
        },
    )
    assert queue(cfg) == ["pr#12"]  # 13 is red, 14 pending
    cfg = fixture_cfg(
        tmp_path, [pr(12), pr(13)], [pull(12, A), pull(13, "d" * 40)], checks={A: good, "d" * 40: good}
    )
    assert queue(cfg) == ["pr#12", "pr#13"]  # 13 pushed a new head, and it is green


def test_names_all_counts_every_check(tmp_path):
    checks = {A: {"check_runs": [run("unit"), run("lint"), run("docs", "failure")]}}
    assert queue(fixture_cfg(tmp_path, [pr(12)], [pull(12, A)], checks=checks)) == ["pr#12"]
    assert (
        queue(fixture_cfg(tmp_path, [pr(12)], [pull(12, A)], checks=checks, raw=gated_raw(names="all"))) == []
    )


@pytest.mark.parametrize(
    "when, expected",
    [("all", ["pr#11", "pr#12", "pr#10", "pr#13"]), ("any", ["pr#11", "pr#12", "pr#13", "pr#10"])],
)
def test_priority_paths_go_first_then_the_oldest(tmp_path, when, expected):
    good = {"check_runs": [run("unit"), run("lint")]}
    raw = gated_raw(priority_paths=["web/**", "tests/unit/test_web_*.py"], priority_when=when)
    cfg = fixture_cfg(
        tmp_path,
        [
            pr(10, updated="2026-10-01T00:00:00Z"),
            pr(11, updated="2026-10-02T00:00:00Z"),
            pr(12, updated="2026-10-03T00:00:00Z"),
            pr(13, updated="2026-10-04T00:00:00Z"),
        ],
        [pull(10, A), pull(11, A), pull(12, A), pull(13, A)],
        checks={A: good},
        files={
            "10": ["db/x.sql"],
            "11": ["web/a.ts", "tests/unit/test_web_panel.py"],
            "12": ["web/src/a/b.ts"],
            "13": ["web/b.ts", "db/y.sql"],
        },
        raw=raw,
    )
    assert queue(cfg) == expected  # 13 changes web/ and db/: first only when any path will do


def test_paths_not_all_read_are_not_priority_under_all(tmp_path):
    raw = gated_raw(priority_paths=["web/**"])
    role = config.from_dict(raw, tmp_path / "s.yaml").roles[0]
    some = items.WorkItem(kind="pr", id="pr#1", version="v", paths=("web/a.ts",), paths_more=True)
    assert not items.first_in_line(role, some)
    assert items.first_in_line(role, items.WorkItem(kind="pr", id="pr#1", version="v", paths=("web/a.ts",)))
    assert not items.first_in_line(role, items.WorkItem(kind="pr", id="pr#1", version="v"))
    assert sources._capped([f"web/{i}.ts" for i in range(items.PATHS_CAP + 1)])[1] is True


# -- reading GitHub -------------------------------------------------------------------------------------------------


class FakeGitHub:
    def __init__(self, heads, runs, files=None):
        self.heads, self.runs, self.files, self.calls = heads, runs, files or {}, []

    def __call__(self, argv):
        self.calls.append(argv)
        path = argv[-1]
        if "/issues?state=open" in path:
            body = [pr(n) for n in self.heads]
        elif "/pulls?state=open" in path:
            body = [pull(n, h, draft=(n == 99)) for n, h in self.heads.items()]
        elif "/reviews?" in path:
            body = (
                [{"user": {"login": BOT}, "commit_id": self.heads[13], "state": "APPROVED"}]
                if "/13/" in path
                else []
            )
        elif "/comments?" in path:
            body = []
        elif "/rules/branches/main" in path:
            body = RULES
        elif "/check-runs?" in path:
            body = {"check_runs": self.runs[path.split("/commits/")[1].split("/")[0]]}
        elif "/status?" in path:
            body = {"state": "success", "statuses": []}
        elif "/files?" in path:
            body = [{"filename": f} for f in self.files.get(int(path.split("/pulls/")[1].split("/")[0]), [])]
        else:
            raise AssertionError(path)
        return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")

    def reads(self, part):
        return sum(1 for a in self.calls if part in a[-1])


def test_the_github_source_reads_checks_with_gets_only_and_rereads_only_what_can_change(tmp_path):
    raw = gated_raw(priority_paths=["web/**"])
    raw["source"] = {"type": "github", "repo": "example-org/example-repo"}
    cfg = config.from_dict(raw, tmp_path / "s.yaml")
    clock = {"t": NOW}
    gh = FakeGitHub(
        {12: A, 13: B, 99: "c" * 40, 14: "d" * 40},
        {
            A: [run("unit"), run("lint")],
            B: [run("unit"), run("lint")],
            "d" * 40: [run("unit"), run("lint", status="in_progress")],
        },
        files={12: ["web/x.ts"]},
    )
    src = sources.make(cfg.source, cfg.asks, lambda: clock["t"], gh)
    snap = src.look()
    got = {i.id: (i.checks, i.paths) for i in snap.items}
    assert got["pr#12"] == ("green", ("web/x.ts",)) and got["pr#14"] == ("pending", ())
    assert got["pr#13"] == (None, ()) and got["pr#99"] == (
        None,
        (),
    )  # answered, draft: no checks or paths read
    assert all(a[:4] == ["gh", "api", "--method", "GET"] for a in gh.calls)
    assert gh.reads("/check-runs?") == 2 and gh.reads("/files?") == 2 and gh.reads("/rules/") == 1
    src.look()
    assert gh.reads("/check-runs?") == 3  # only the pending head is read again
    assert gh.reads("/files?") == 2 and gh.reads("/rules/") == 1  # paths per head; rules for an hour
    gh.runs["d" * 40] = [run("unit"), run("lint", "failure")]
    src.look()
    assert gh.reads("/check-runs?") == 4
    src.look()
    assert gh.reads("/check-runs?") == 4  # red: not read again before RED_RECHECK
    import datetime as dt

    clock["t"] = NOW + dt.timedelta(seconds=sources.RED_RECHECK + 1)
    gh.runs["d" * 40] = [run("unit"), run("lint")]
    snap = src.look()
    assert gh.reads("/check-runs?") == 5 and {i.id: i.checks for i in snap.items}["pr#14"] == "green"
    assert gh.reads("/rules/") == 1


# -- the step -------------------------------------------------------------------------------------------------------


def test_the_command_loads_only_the_role_settings_and_asks_for_the_schema(tmp_path):
    (tmp_path / "schema.json").write_text(
        '{"type": "object", "properties": {"verdict": {"type": "string"}}}', encoding="utf-8"
    )
    cfg = config.from_dict(
        gated_raw(result_schema="schema.json", setting_sources=["user", "local"]), tmp_path / "s.yaml"
    )
    argv = step.build_command(("claude",), cfg.roles[0], "u")
    assert argv[argv.index("--setting-sources") + 1] == "user,local"
    assert json.loads(argv[argv.index("--json-schema") + 1])["properties"] == {"verdict": {"type": "string"}}


def _live_cfg(tmp_path, **role):
    import sys

    from .supervisor_helpers import FAKE

    raw = gated_raw(**role)
    raw["claude"] = [sys.executable, str(FAKE)]
    good = {"check_runs": [run("unit"), run("lint")]}
    return fixture_cfg(
        tmp_path,
        [pr(12), pr(13), pr(14)],
        [pull(12, A), pull(13, B), pull(14, "c" * 40)],
        checks={A: good, B: good, "c" * 40: good},
        raw=raw,
    )


def test_the_supervisor_writes_the_result_file_and_the_step_needs_no_write_tool(tmp_path):
    cfg = _live_cfg(tmp_path, result_file="shadow/${item_number}_${item_head_short}.md")
    assert not {"Edit", "Write"} & set(step.base_tools(cfg.roles[0].allowed_tools))
    verdict = {"verdict": "merge", "reviewed_head": A}
    code, s = onestep.run_one(
        cfg,
        "reviewer",
        12,
        live=True,
        environ=env_without_key(FAKE_CLAUDE_STRUCTURED=json.dumps(verdict)),
        say=lambda m: None,
    )
    assert code == 0
    md = tmp_path / "shadow" / f"12_{A[:8]}.md"
    text = md.read_text(encoding="utf-8")
    assert '"verdict": "merge"' in text and "gh pr comment 1" in text and "done (" in text
    side = json.loads((tmp_path / "shadow" / f"12_{A[:8]}.md.json").read_text(encoding="utf-8"))
    assert side["structured"] == verdict and side["num_turns"] == 3 and side["outcome"] == "ok"
    assert s["result_file"] == str(md.resolve())


def test_each_attempt_gets_a_new_session_id_and_the_command_carries_it(tmp_path):
    cfg = _live_cfg(tmp_path)
    item = items.WorkItem(kind="pr", id="pr#12", version="v")
    first = step.run_step(
        cfg.roles[0],
        item,
        sid="s-1",
        claude=cfg.claude,
        live=True,
        out_dir=tmp_path / "out",
        environ=env_without_key(),
    )
    assert first.argv[first.argv.index("--session-id") + 1] == first.session_id
    again = step.run_step(cfg.roles[0], item, sid="s-1")
    assert again.session_id != first.session_id  # the same step id, another session


def test_a_live_step_refuses_to_start_when_the_cli_is_not_logged_in(tmp_path, capsys, monkeypatch):
    import sys

    from onloop import cli

    from .supervisor_helpers import FAKE

    claude = (sys.executable, str(FAKE))
    assert step.check_login(claude, env_without_key())["loggedIn"] is True
    with pytest.raises(step.NotLoggedIn, match="not logged in"):
        step.check_login(claude, env_without_key(FAKE_CLAUDE_LOGGED_IN="0"))
    with pytest.raises(step.NotLoggedIn, match="API key"):
        step.check_login(
            claude,
            run=lambda *a, **k: subprocess.CompletedProcess(
                a, 0, '{"loggedIn": true, "authMethod": "api_key"}', ""
            ),
        )
    assert (
        step.check_login(claude, run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "not json", "")) == {}
    )
    cfg_path = tmp_path / "s.yaml"
    _live_cfg(tmp_path)
    for key in (*step.METERED_KEYS, step.OAUTH_TOKEN):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FAKE_CLAUDE_LOGGED_IN", "0")
    assert (
        cli.main(
            [
                "step",
                "--config",
                str(cfg_path),
                "--role",
                "reviewer",
                "--pr",
                "12",
                "--live",
                "--state",
                str(tmp_path / "st"),
            ]
        )
        == 2
    )
    assert "not logged in" in capsys.readouterr().out


# -- after_result ---------------------------------------------------------------------------------------------------

AFTER = (
    "import pathlib, sys; pathlib.Path(sys.argv[1] + '.seen').write_text(sys.argv[2] + ' ' + sys.argv[3]); "
    "print('posted'); sys.exit(int(sys.argv[4]))"
)


@pytest.mark.parametrize("exit_code", [0, 3])
def test_after_result_runs_on_the_written_result_and_its_failure_is_final(tmp_path, exit_code):
    import sys

    cfg = _live_cfg(
        tmp_path,
        result_file="out/${item_number}_${item_head_short}.md",
        after_result={
            "command": [
                sys.executable,
                "-c",
                AFTER,
                "${result_json}",
                "${item_number}",
                "${item_head_short}",
                str(exit_code),
            ],
            "timeout": "30s",
        },
    )
    verdict = {"verdict": "merge", "reviewed_head": A}
    code, s = onestep.run_one(
        cfg,
        "reviewer",
        12,
        live=True,
        environ=env_without_key(FAKE_CLAUDE_STRUCTURED=json.dumps(verdict)),
        say=lambda m: None,
    )
    assert code == (1 if exit_code else 0)
    side = tmp_path / "out" / f"12_{A[:8]}.md.json"
    assert (
        json.loads(side.read_text(encoding="utf-8"))["structured"] == verdict
    )  # written before the command ran
    assert (tmp_path / "out" / f"12_{A[:8]}.md.json.seen").read_text(encoding="utf-8") == f"12 {A[:8]}"
    assert s["after_result"]["exit_code"] == exit_code
    assert "posted" in pathlib.Path(s["after_result"]["output"]).read_text(encoding="utf-8")
    assert s["outcome"] == ("after_failed" if exit_code else "ok")


def test_after_result_runs_only_live(tmp_path):
    cfg = _live_cfg(
        tmp_path, result_file="out/${item_number}.md", after_result={"command": ["no-such-program-anywhere"]}
    )
    code, s = onestep.run_one(cfg, "reviewer", 12, live=False, environ=env_without_key(), say=lambda m: None)
    assert code == 0 and s["outcome"] == "dry_run" and "after_result" not in s
