"""onloopd's configuration file, `onloopd.yaml`: what onloopd watches, as opposed to the flags that say how it runs.

    port: 8790                 optional: the port on the loopback address (`--port` overrides it)
    schedules:                 the scheduled jobs onloopd expects to hear from (decision 0008)
      - id: nightly-backup     lowercase letters, digits, '.', '_' or '-'; the id in the ping URL
        name: Nightly backup   optional; defaults to the id
        period: 1d             how often it runs: a number and a unit (s, m, h, d, w), as in 15m or 1h30m
        grace: 1h              optional, default 1h: how late a run may be before it counts as missed
        owner: ops             the role an alert about it goes to
    monitors:                  optional: what a `continue ... waker=monitor:<id>` names
      - id: build-watch        lowercase letters, digits, '.', '_' or '-'
        url: http://localhost:9000/health   on the loopback address only; alive while it answers 2xx within 2 s
    github:                    optional: the repository onloopd reads into the ledger (GETs only)
      repo: owner/name
      base: main               optional, default main
      every: 1m                optional, default 1m, at least 30s: how often it reads
      workdir: path            optional: a clone to take patch ids in; without one every push counts as substantive
      policy: path             optional: the onloop.yaml whose required checks and identities intake uses
    carrier: ...               optional: the session carrier, off by default (carrier/config.py, decision 0010)
    heartbeat_schedule: onloop-heartbeat   optional: the schedule above that is onloop's own heartbeat, pinged from
                               outside it (its dead-man switch, decision 0008); the Now view's health shows its state.
                               Without it, the Now view says "dead-man not configured"

The file is parsed with a safe YAML parser and checked whole: every problem is reported as one sentence and onloopd
does not start (fail closed, story 8). A key the file does not know is a problem, not ignored, so a typo cannot
silently turn a check off.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from .. import events as ev

CONFIG_FILE = "onloopd.yaml"
DEFAULT_GRACE = "1h"
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_TOP = ("port", "schedules", "monitors", "github", "heartbeat_schedule", "carrier")
_SCHEDULE = ("id", "name", "period", "grace", "owner")
_GITHUB = ("repo", "base", "every", "workdir", "policy")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


class ConfigError(ValueError):
    def __init__(self, path: pathlib.Path | str, problems: list[str]):
        self.problems = problems
        super().__init__(f"{path}: " + "; ".join(problems))


@dataclass(frozen=True)
class Schedule:
    id: str
    name: str
    period: str
    grace: str
    owner: str

    def declaration(self) -> dict[str, Any]:
        """The payload of its `schedule.declared` event."""
        return {
            "id": self.id,
            "name": self.name,
            "period": self.period,
            "grace": self.grace,
            "owner_role": self.owner,
        }


@dataclass(frozen=True)
class GitHubWatch:
    repo: str
    base: str = "main"
    every_s: int = 60
    workdir: str | None = None
    policy: str | None = None


@dataclass(frozen=True)
class OnloopdConfig:
    schedules: tuple[Schedule, ...] = field(default_factory=tuple)
    github: GitHubWatch | None = None
    monitors: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (id, url)
    port: int | None = None
    heartbeat_schedule: str | None = None  # the schedule that is onloop's own dead-man switch
    carrier: Any = None  # carrier.config.CarrierConfig, or None: the session carrier is not configured


def _monitors(raw: Any, problems: list[str]) -> list[tuple[str, str]]:
    from ..detect.wakers import loopback

    if not isinstance(raw, list):
        problems.append("monitors must be a list")
        return []
    out = []
    for n, m in enumerate(raw):
        where = f"monitors[{n}]"
        if not isinstance(m, dict):
            problems.append(f"{where} must be a mapping with id and url")
            continue
        for key in sorted(set(m) - {"id", "url"}):
            problems.append(f"{where} has an unknown key {key!r}; the keys are id, url")
        mid, url = m.get("id"), m.get("url")
        if not isinstance(mid, str) or not _ID.match(mid):
            problems.append(f"{where}.id must be lowercase letters, digits, '.', '_' or '-' (at most 64)")
            continue
        if not isinstance(url, str) or not loopback(url):
            problems.append(
                f"{where}.url must be an http(s) URL on the loopback address (localhost, or a loopback IP address)"
            )
            continue
        if mid in {i for i, _ in out}:
            problems.append(f"monitor id {mid!r} is named twice")
            continue
        out.append((mid, url))
    return out


def _github(raw: Any, problems: list[str]) -> GitHubWatch | None:
    if not isinstance(raw, dict):
        problems.append("github must be a mapping with at least repo")
        return None
    for key in sorted(set(raw) - set(_GITHUB)):
        problems.append(f"github has an unknown key {key!r}; the keys are {', '.join(_GITHUB)}")
    repo, base = raw.get("repo"), raw.get("base", "main")
    ok = True
    if not isinstance(repo, str) or not _REPO.match(repo):
        problems.append("github.repo must be owner/name")
        ok = False
    if not isinstance(base, str) or not _BRANCH.match(base):
        problems.append("github.base must be a branch name")
        ok = False
    every = 60
    try:
        every = ev.parse_duration(raw.get("every", "1m"))
        if every < 30:
            problems.append("github.every must be at least 30s")
            ok = False
    except ev.EventError as e:
        problems.append(f"github.every: {e}")
        ok = False
    paths = {}
    for key in ("workdir", "policy"):
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            problems.append(f"github.{key} must be a path")
            ok = False
        paths[key] = value
    if not ok:
        return None
    return GitHubWatch(repo=repo, base=base, every_s=every, workdir=paths["workdir"], policy=paths["policy"])


def _schedule(n: int, raw: Any, problems: list[str]) -> Schedule | None:
    where = f"schedules[{n}]"
    if not isinstance(raw, dict):
        problems.append(f"{where} must be a mapping with id, period and owner")
        return None
    for key in sorted(set(raw) - set(_SCHEDULE)):
        problems.append(f"{where} has an unknown key {key!r}; the keys are {', '.join(_SCHEDULE)}")
    sid, owner = raw.get("id"), raw.get("owner")
    ok = True
    if not isinstance(sid, str) or not _ID.match(sid):
        problems.append(f"{where}.id must be lowercase letters, digits, '.', '_' or '-' (at most 64)")
        ok = False
    if not isinstance(owner, str) or not _ROLE.match(owner):
        problems.append(f"{where}.owner must be a role name: lowercase letters, digits, '_' or '-'")
        ok = False
    name = raw.get("name", sid)
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        problems.append(f"{where}.name must be a non-empty string of at most 120 characters")
        ok = False
    durations = {}
    for key, default in (("period", None), ("grace", DEFAULT_GRACE)):
        value = raw.get(key, default)
        try:
            seconds = ev.parse_duration(value)
        except ev.EventError as e:
            problems.append(f"{where}.{key}: {e}")
            ok = False
            continue
        if key == "period" and seconds < 60:
            problems.append(f"{where}.period must be at least 1m")
            ok = False
        durations[key] = value
    if not ok:
        return None
    return Schedule(
        id=sid, name=name.strip(), period=durations["period"], grace=durations["grace"], owner=owner
    )


def parse(data: Any, path: pathlib.Path | str = CONFIG_FILE) -> OnloopdConfig:
    """Check a parsed `onloopd.yaml` whole. Raises ConfigError listing every problem."""
    if data is None:
        return OnloopdConfig()
    if not isinstance(data, dict):
        raise ConfigError(path, ["the file must be a mapping at the top level"])
    problems: list[str] = [
        f"unknown top-level key {key!r}; the keys are {', '.join(_TOP)}"
        for key in sorted(set(data) - set(_TOP))
    ]
    raw = data.get("schedules") or []
    if not isinstance(raw, list):
        problems.append("schedules must be a list")
        raw = []
    schedules = [s for s in (_schedule(n, r, problems) for n, r in enumerate(raw)) if s]
    seen: set[str] = set()
    for s in schedules:
        if s.id in seen:
            problems.append(f"schedule id {s.id!r} is declared twice")
        seen.add(s.id)
    github = _github(data["github"], problems) if data.get("github") is not None else None
    monitors = _monitors(data["monitors"], problems) if data.get("monitors") is not None else []
    port = data.get("port")
    if port is not None and (
        not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535
    ):
        problems.append("port must be a whole number from 1024 to 65535")
    beat = data.get("heartbeat_schedule")
    if beat is not None and (not isinstance(beat, str) or beat not in seen):
        problems.append("heartbeat_schedule must name one of the schedules declared in this file")
    carrier = None
    if data.get("carrier") is not None:
        from ..carrier import config as carrier_config

        carrier = carrier_config.parse(data["carrier"], problems)
    if problems:
        raise ConfigError(path, problems)
    return OnloopdConfig(
        carrier=carrier,
        schedules=tuple(schedules),
        github=github,
        monitors=tuple(monitors),
        port=port,
        heartbeat_schedule=beat,
    )


def load(path: pathlib.Path) -> OnloopdConfig:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(path, [f"cannot read it: {e}"]) from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(path, [f"not valid YAML: {e}"[:300]]) from e
    return parse(data, path)
