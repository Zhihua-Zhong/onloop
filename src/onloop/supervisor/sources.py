"""Event sources: what the supervisor looks at.

    class EventSource:
        def look(self) -> Snapshot           # every work item now, read completely, or SourceError

Polling is the only source today: `onloop step`, `onloop supervise --once` and `onloop digest --refresh` call `look`.
An event (a webhook, a CI trigger) is a hint, never evidence, so `look` stays the record either way.

`GitHubSource` reads with `gh api` GETs only, through onloop.github's GET helpers: one paginated listing of the open
issues and pull requests (labels included), and one of the issues carrying the ask label, open or closed. When a
trigger keys on head commits or waits for an answer (`version: head`, `unanswered_by`), it also lists the open pull
requests (`GET /repos/{repo}/pulls`, for each head sha) and, for each open pull request, its reviews and issue
comments. Those two are read again only when the pull request's `updated_at` or head changed since the last look, so a
quiet repository costs two listings a look. A failed read raises; a partial picture is never scheduled from, because a
dependency missing from it would look closed, and an answer missing from it would look absent.

For the pull requests a check-gated trigger or a role with `priority_paths` could take (`items.watched`), it also
reads, by GET: the check runs and commit statuses at the head (`/commits/{sha}/check-runs`, `/commits/{sha}/status`),
the required check names of the base branch's ruleset (`/rules/branches/{base}`, kept for an hour), and the changed
paths (`/pulls/{n}/files`). Paths and green checks are kept until the head changes; red checks are read again after
`RED_RECHECK`, pending ones at every look, because a check finishing does not move the pull request's `updated_at`.

`FixtureSource` reads the same JSON from a file on every look, for tests and offline dry runs: `open`, `asks`,
`blocked_by`, and for heads and answers `pulls`, `reviews` and `comments` (the last two keyed by pull request number),
`checks` (keyed by sha: `{"check_runs": [...], "statuses": [...]}`), `files` (keyed by number) and `rules` (keyed by
branch: the ruleset's rules).
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Protocol

import yaml

from .. import events as ev
from .. import github
from .. import ledger as ledger_mod
from . import asks as asks_mod
from . import items as items_mod
from .config import AsksConfig, SourceSpec
from .items import WorkItem

RED_RECHECK = 600.0  # seconds before the checks of a red head are read again (a re-run can turn them green)
RULES_TTL = 3600.0  # seconds a branch ruleset's required check names are kept


class SourceError(RuntimeError):
    pass


def required_names(rules: list[dict[str, Any]]) -> tuple[str, ...]:
    """The required status check names in a branch's rules (`GET /repos/{repo}/rules/branches/{branch}`)."""
    out: list[str] = []
    for r in rules or []:
        if isinstance(r, dict) and r.get("type") == "required_status_checks":
            for c in (r.get("parameters") or {}).get("required_status_checks") or []:
                if c.get("context") and c["context"] not in out:
                    out.append(str(c["context"]))
    return tuple(out)


def _needs(spec: SourceSpec) -> tuple[str, ...]:
    """Which check names count, per the triggers: "ruleset" if any trigger asks for the ruleset's, else "all"."""
    return tuple(sorted({t.require_checks for _, t in spec.watch if t.require_checks}))


@dataclass
class Snapshot:
    items: list[WorkItem] = field(default_factory=list)  # open pull requests, open issues, ledger rows
    asks: list[WorkItem] = field(default_factory=list)  # every ask, open or closed, private ones included
    open_ids: set[str] = field(default_factory=set)
    read_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "read_at": self.read_at,
            "items": [i.as_dict() for i in self.items],
            "asks": [a.as_dict() for a in self.asks],
            "open_ids": sorted(self.open_ids),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Snapshot:
        return cls(
            [WorkItem.from_dict(i) for i in d.get("items") or []],
            [WorkItem.from_dict(a) for a in d.get("asks") or []],
            set(d.get("open_ids") or []),
            d.get("read_at") or "",
        )


class EventSource(Protocol):
    def look(self) -> Snapshot: ...


def _role_prefix(asks: AsksConfig) -> str:
    return str(((asks.mapping.get("labels") or {}).get("role_prefix")) or "role/")


def _private(asks: AsksConfig) -> list[WorkItem]:
    if asks.private_file is None or not asks.private_file.exists():
        return []
    try:
        return items_mod.asks_of_private(yaml.safe_load(asks.private_file.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValueError) as e:
        raise SourceError(f"the private asks file could not be read: {e}") from e


def _ledger(spec: SourceSpec) -> list[WorkItem]:
    if spec.ledger is None:
        return []
    try:
        return items_mod.ledger_items(ledger_mod.load(spec.ledger))
    except Exception as e:  # noqa: BLE001 - any failure is a failed read
        raise SourceError(f"the ledger could not be read: {e}") from e


def pr_items(
    open_raw: list[dict[str, Any]],
    spec: SourceSpec,
    asks: AsksConfig,
    pulls: list[dict[str, Any]],
    answered: dict[int, tuple[str, ...]] | None = None,
    checks: dict[int, str] | None = None,
    paths: dict[int, tuple[tuple[str, ...], bool]] | None = None,
) -> list[WorkItem]:
    items = items_mod.items_of_open(open_raw, _role_prefix(asks))
    return items_mod.with_heads(items, pulls, answered, checks, paths) if spec.heads else items


def _capped(paths: list[str]) -> tuple[tuple[str, ...], bool]:
    return tuple(paths[: items_mod.PATHS_CAP]), len(paths) > items_mod.PATHS_CAP


def build_snapshot(
    open_raw: list[dict[str, Any]],
    asks_raw: list[dict[str, Any]],
    spec: SourceSpec,
    asks: AsksConfig,
    now: str,
    blocked_by: dict[int, list[int]] | None = None,
    pulls: list[dict[str, Any]] | None = None,
    answered: dict[int, tuple[str, ...]] | None = None,
    checks: dict[int, str] | None = None,
    paths: dict[int, tuple[tuple[str, ...], bool]] | None = None,
) -> Snapshot:
    items = pr_items(open_raw, spec, asks, pulls or [], answered, checks, paths) + _ledger(spec)
    try:
        ask_items = (
            items_mod.asks_of_issues(asks_raw, asks, spec.repo, blocked_by) if asks.label else []
        ) + _private(asks)
    except ValueError as e:
        raise SourceError(str(e)) from e
    return Snapshot(items, ask_items, asks_mod.open_id_set(items, ask_items), now)


class FixtureSource:
    """{"open": [issue JSON...], "asks": [issue JSON...], "blocked_by": {"12": [5]}} from a file, read on every look."""

    def __init__(self, spec: SourceSpec, asks: AsksConfig, clock: Any):
        self.spec, self.asks, self.clock = spec, asks, clock

    def look(self) -> Snapshot:
        try:
            raw = json.loads(self.spec.fixture.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        except (OSError, ValueError) as e:
            raise SourceError(f"the fixture could not be read: {e}") from e
        blocked = {int(k): [int(x) for x in v] for k, v in (raw.get("blocked_by") or {}).items()}
        pulls = raw.get("pulls") or []
        answered = {}
        if self.spec.answers:
            for pr in pulls:
                n = int(pr["number"])
                answered[n] = items_mod.answered_keys(
                    str((pr.get("head") or {}).get("sha") or ""),
                    (raw.get("reviews") or {}).get(str(n)) or [],
                    (raw.get("comments") or {}).get(str(n)) or [],
                    self.spec.answers,
                )
        checks: dict[int, str] = {}
        paths: dict[int, tuple[tuple[str, ...], bool]] = {}
        if self.spec.watch:
            for it in pr_items(raw.get("open") or [], self.spec, self.asks, pulls, answered):
                if not it.head or not items_mod.watched(it, self.spec.watch):
                    continue
                if self.spec.checks:
                    pr = next(x for x in pulls if int(x["number"]) == it.number)
                    names = _names_for(
                        self.spec, (raw.get("rules") or {}).get((pr.get("base") or {}).get("ref"), [])
                    )
                    c = (raw.get("checks") or {}).get(it.head) or {}
                    checks[it.number] = items_mod.checks_state(
                        c.get("check_runs") or [], c.get("statuses") or [], names
                    )
                if self.spec.globs:
                    paths[it.number] = _capped(list((raw.get("files") or {}).get(str(it.number)) or []))
        return build_snapshot(
            raw.get("open") or [],
            raw.get("asks") or [],
            self.spec,
            self.asks,
            ev.norm_ts(self.clock()) or "",
            blocked,
            pulls,
            answered,
            checks,
            paths,
        )


def _names_for(spec: SourceSpec, rules: list[dict[str, Any]]) -> tuple[str, ...]:
    """The check names that count: the ruleset's required ones when a trigger asks for them and the ruleset has any;
    otherwise every reported check (an empty tuple)."""
    return required_names(rules) if "ruleset" in _needs(spec) else ()


class GitHubSource:
    def __init__(self, spec: SourceSpec, asks: AsksConfig, clock: Any, runner: Any = None):
        self.spec, self.asks, self.clock = spec, asks, clock
        self.run = runner  # None: onloop.github's own runner
        self._answers: dict[
            int, tuple[str, str, tuple[str, ...]]
        ] = {}  # number -> (updated_at, head, answered)
        self._files: dict[
            int, tuple[str, tuple[tuple[str, ...], bool]]
        ] = {}  # number -> (head, (paths, more))
        self._checks: dict[str, tuple[str, float]] = {}  # sha -> (state, read at)
        self._rules: dict[str, tuple[tuple[str, ...], float]] = {}  # branch -> (required names, read at)

    def _get_object(self, path: str) -> dict[str, Any]:
        try:
            return github.get_object(path, self.run)
        except github.GitHubError as e:
            raise SourceError(str(e)) from e

    def _seconds(self) -> float:
        c = self.clock()
        return c.timestamp() if isinstance(c, dt.datetime) else float(c)

    def _required(self, branch: str) -> tuple[str, ...]:
        if "ruleset" not in _needs(self.spec):
            return ()
        now = self._seconds()
        got = self._rules.get(branch)
        if got and now - got[1] < RULES_TTL:
            return got[0]
        names = required_names(
            self._get(f"repos/{self.spec.repo}/rules/branches/{urllib.parse.quote(branch, safe='')}")
        )
        self._rules[branch] = (names, now)
        return names

    def _checks_at(self, sha: str, branch: str) -> str:
        now = self._seconds()
        got = self._checks.get(sha)
        if got and (got[0] == "green" or (got[0] == "red" and now - got[1] < RED_RECHECK)):
            return got[0]
        repo = self.spec.repo
        runs = self._get_object(f"repos/{repo}/commits/{sha}/check-runs?per_page=100").get("check_runs") or []
        statuses = self._get_object(f"repos/{repo}/commits/{sha}/status?per_page=100").get("statuses") or []
        state = items_mod.checks_state(runs, statuses, self._required(branch))
        self._checks[sha] = (state, now)
        return state

    def _paths_at(self, n: int, sha: str) -> tuple[tuple[str, ...], bool]:
        got = self._files.get(n)
        if got and got[0] == sha:
            return got[1]
        listed = self._get(f"repos/{self.spec.repo}/pulls/{n}/files?per_page=100")
        m = _capped([str(f.get("filename") or "") for f in listed])
        self._files[n] = (sha, m)
        return m

    def _get(self, path: str) -> list[Any]:
        try:
            return github.get_list(path, self.run)  # GET with --paginate; raises on any failed page
        except github.GitHubError as e:
            raise SourceError(str(e)) from e

    def look(self) -> Snapshot:
        repo = self.spec.repo
        now = ev.norm_ts(self.clock()) or ""
        open_raw = self._get(f"repos/{repo}/issues?state=open&per_page=100")
        asks_raw: list[dict[str, Any]] = []
        blocked: dict[int, list[int]] = {}
        if self.asks.label:
            label = urllib.parse.quote(self.asks.label, safe="")
            asks_raw = self._get(f"repos/{repo}/issues?state=all&labels={label}&per_page=100")
            for i in asks_raw:
                if "pull_request" not in i and (i.get("issue_dependencies_summary") or {}).get("blocked_by"):
                    n = int(i["number"])
                    blocked[n] = [
                        int(c["number"])
                        for c in self._get(f"repos/{repo}/issues/{n}/dependencies/blocked_by?per_page=100")
                    ]
        pulls: list[dict[str, Any]] = []
        answered: dict[int, tuple[str, ...]] = {}
        if self.spec.heads:
            pulls = self._get(f"repos/{repo}/pulls?state=open&per_page=100")
        if self.spec.answers:
            updated = {
                int(i["number"]): str(i.get("updated_at") or "") for i in open_raw if "pull_request" in i
            }
            seen = set()
            for pr in pulls:
                n, head = int(pr["number"]), str((pr.get("head") or {}).get("sha") or "")
                seen.add(n)
                key = (updated.get(n, str(pr.get("updated_at") or "")), head)
                cached = self._answers.get(n)
                if cached and cached[:2] == key:
                    answered[n] = cached[2]
                    continue
                got = items_mod.answered_keys(
                    head,
                    self._get(f"repos/{repo}/pulls/{n}/reviews?per_page=100"),
                    self._get(f"repos/{repo}/issues/{n}/comments?per_page=100"),
                    self.spec.answers,
                )
                self._answers[n] = (*key, got)
                answered[n] = got
            for n in set(self._answers) - seen:  # closed or merged: forget it
                del self._answers[n]
        checks: dict[int, str] = {}
        paths: dict[int, tuple[tuple[str, ...], bool]] = {}
        if self.spec.watch:
            base = {int(pr["number"]): str((pr.get("base") or {}).get("ref") or "") for pr in pulls}
            for it in pr_items(open_raw, self.spec, self.asks, pulls, answered):
                if not it.head or it.number is None or not items_mod.watched(it, self.spec.watch):
                    continue
                if self.spec.checks:
                    checks[it.number] = self._checks_at(it.head, base.get(it.number, ""))
                if self.spec.globs:
                    paths[it.number] = self._paths_at(it.number, it.head)
            open_shas = {str((pr.get("head") or {}).get("sha") or "") for pr in pulls}
            for sha in set(self._checks) - open_shas:
                del self._checks[sha]
            for n in set(self._files) - set(base):
                del self._files[n]
        return build_snapshot(
            open_raw, asks_raw, self.spec, self.asks, now, blocked, pulls, answered, checks, paths
        )


def make(spec: SourceSpec, asks: AsksConfig, clock: Any, runner: Any = None) -> EventSource:
    if spec.type == "fixture":
        return FixtureSource(spec, asks, clock)
    return GitHubSource(spec, asks, clock, runner)
