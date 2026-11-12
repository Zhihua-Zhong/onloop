"""Shared fixtures for the supervisor's tests: issue and pull-request JSON, a configuration, the step stand-in."""

import copy
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

import yaml

from onloop import events as ev
from onloop import procs
from onloop.supervisor import config, step, steplog

HERE = pathlib.Path(__file__).parent
FAKE = HERE / "fixtures" / "fake_claude.py"
EXAMPLE = HERE.parent / "examples" / "supervisor"
NOW = dt.datetime(2026, 11, 2, 18, 0, tzinfo=dt.UTC)


def ts(d):
    return ev.norm_ts(d)


def pr(n, updated="2026-11-01T09:00:00Z", labels=(), draft=False):
    return {
        "number": n,
        "title": f"pr {n}",
        "state": "open",
        "draft": draft,
        "pull_request": {},
        "html_url": f"https://github.com/example-org/example-repo/pull/{n}",
        "updated_at": updated,
        "labels": [{"name": x} for x in labels],
    }


def issue(n, updated="2026-11-01T09:00:00Z", labels=(), body="", state="open", closed_at=None):
    return {
        "number": n,
        "title": f"issue {n}",
        "state": state,
        "html_url": f"https://github.com/example-org/example-repo/issues/{n}",
        "updated_at": updated,
        "created_at": updated,
        "labels": [{"name": x} for x in labels],
        "body": body,
        "closed_at": closed_at,
        "state_reason": "completed" if state == "closed" else None,
    }


ROLE = {
    "cwd": ".",
    "permission_mode": "dontAsk",
    "budget": {"wall": "5m", "turns": 10},
    "wip": 1,
    "prompt": "Do $item_id: $item_title",
}
RAW = {
    "state_dir": "state",
    "claude": ["claude"],
    "interval": "1s",
    "max_parallel": 4,
    "stop_grace": "2s",
    "source": {"type": "fixture", "fixture": "fixture.json"},
    "asks": {
        "label": "kind/ask",
        "labels": {
            "role_prefix": "role/",
            "state": {"state/waiting-human": "waiting-human", "state/waiting-ci": "waiting-external"},
        },
        "depends_on": {"dependencies": True, "body_patterns": [r"(?im)^\s*Depends on:.*$"]},
    },
    "roles": [
        {
            **ROLE,
            "name": "author",
            "allowed_tools": ["Read", "Edit", "Bash(git commit *)"],
            "triggers": [{"kind": "ask"}],
        },
        {
            **ROLE,
            "name": "reviewer",
            "allowed_tools": ["Read", "Bash(gh pr view *)"],
            "triggers": [{"kind": "pr", "draft": False}],
        },
        {
            **ROLE,
            "name": "operator",
            "allowed_tools": [],
            "triggers": [{"kind": "issue", "labels_any": ["ops"]}],
        },
    ],
    "attention": {
        "item_stale": {"threshold": "24h", "quiet": "12h"},
        "role_starved": {"threshold": "30m", "quiet": "1h"},
        "max_repeats": 2,
    },
}


def write_fixture(tmp_path, open_=None, asks=None):
    (tmp_path / "fixture.json").write_text(
        json.dumps({"open": open_ or [], "asks": asks or []}), encoding="utf-8"
    )


def make_cfg(tmp_path, open_=None, asks=None, live_fake=False, **over):
    """A configuration in `tmp_path`, written as supervisor.yaml too. `live_fake` points `claude` at the stand-in."""
    raw = copy.deepcopy(RAW)
    if live_fake:
        raw["claude"] = [sys.executable, str(FAKE)]
    for k, v in over.items():
        raw[k] = v
    write_fixture(tmp_path, open_, asks)
    (tmp_path / "supervisor.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return config.from_dict(raw, tmp_path / "supervisor.yaml")


def with_budget(cfg_raw_roles, **budget):
    for r in cfg_raw_roles:
        r["budget"] = {**r["budget"], **budget}
    return cfg_raw_roles


class NoPopen:
    calls = 0

    def __call__(self, *a, **k):
        NoPopen.calls += 1
        raise AssertionError("dry run started a process")


def records(cfg, type_=None):
    recs = steplog.StepLog(cfg.state_dir / "steps.jsonl").records
    return [r for r in recs if type_ is None or r["type"] == type_]


def env_without_key(**kw):
    env = {k: v for k, v in os.environ.items() if k not in (*step.METERED_KEYS, step.OAUTH_TOKEN)}
    env.update(kw)
    return env


def gone(pid, within=10.0):
    end = time.monotonic() + within
    while time.monotonic() < end:
        if not procs.pid_alive(pid):
            return True
        time.sleep(0.2)
    return False


def kill_started_tree(pid):
    """Stop a process tree this test started (directly or through a worker it started), by its pid."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(pid, 9)
        except OSError:
            pass
