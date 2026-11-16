"""Read adapters (decision 0013): onloop reads a tracker, a goal store or an outside event through one boundary, and
never writes through it.

    Adapter                      the interface: `id`, `capabilities` (asks, goals, events), `event_kinds`, and
                                 `asks(cursor)`, `goal(locator)`, `event(kind, locator)`. There is no write method.
    ReadOnlyClient               what an adapter is given to reach its host: a REST GET, or a GraphQL document whose
                                 operations are all queries. Anything write-shaped raises WriteShaped.
    load(entries) / set_registry / registry()
                                 the adapters onloopd.yaml declares, built-in (`github-issues`) or from the
                                 `onloop.adapters` entry points
    EventProbe                   the waker probe for `waker=event:<kind>:<locator>` (decision 0012)

A cursor is opaque and belongs to the adapter (a time window, a Relay cursor and a high-water mark): the core only keeps
it. Evidence carries the host's own time when it has one (`source_at`) and onloop's (`observed_at`); a host that gives
no time (a reaction) is never given a guessed one.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

CAPABILITIES = ("asks", "goals", "events")
EVENT_STATES = ("pending", "fired", "gone", "unknown")


class WriteShaped(PermissionError):
    """An adapter tried something that could write: a REST method other than GET, or a GraphQL mutation."""


class NotAvailable(RuntimeError):
    """The adapter cannot do this yet (a capability it does not declare, or a live check still waiting on a spike)."""


@dataclass(frozen=True)
class ExternalAsk:
    id: str  # the host's id, stable (an issue number)
    url: str | None
    title: str
    state: str  # open or closed
    fields: Mapping[str, str] = field(
        default_factory=dict
    )  # the ask keys read from the body (Effect:, Goal:, ...)
    labels: tuple[str, ...] = ()
    source_at: str | None = None  # the host's time of the last change, when it gives one
    closed_at: str | None = None
    closed_by: str | None = None


@dataclass(frozen=True)
class Edit:
    edit_id: str
    at: str  # the edit's own time, from the host
    items: tuple[tuple[int, str, bool], ...]  # (number, text, checked) after this edit


@dataclass(frozen=True)
class GoalSnapshot:
    title: str | None
    edits: tuple[Edit, ...]  # oldest first; the last is the body as it is now
    comments: tuple[tuple[str, str, str], ...] = ()  # (url, at, text): where a tick's evidence may be named


@dataclass(frozen=True)
class EventState:
    state: str  # pending, fired, gone or unknown
    detail: str = ""
    source_at: str | None = None

    def __post_init__(self) -> None:
        if self.state not in EVENT_STATES:
            raise ValueError(f"an event is {', '.join(EVENT_STATES)}, not {self.state!r}")


class Adapter(Protocol):
    id: str
    capabilities: frozenset[str]
    event_kinds: frozenset[str]

    def asks(self, cursor: str | None) -> tuple[list[ExternalAsk], str | None]: ...

    def goal(self, locator: str) -> GoalSnapshot: ...

    def event(self, kind: str, locator: str) -> EventState: ...


# -- the read-only client ----------------------------------------------------------------------------------------------

_STRING = re.compile(r'"""(?:.|\n)*?"""|"(?:\\.|[^"\\])*"')
_COMMENT = re.compile(r"#[^\n]*")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def graphql_operations(document: str) -> list[str]:
    """The operation types of a GraphQL document, at the top level: query, mutation, subscription (a bare `{...}` is a
    query). Strings and comments are removed first, so a word inside them does not count."""
    text = _COMMENT.sub(" ", _STRING.sub('""', document))
    ops, depth, i = [], 0, 0
    while i < len(text):
        ch = text[i]
        if ch == "{":
            if depth == 0 and (not ops or ops[-1] == "_closed"):
                ops.append("query")
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and ops:
                ops.append("_closed")
        elif depth == 0:
            m = _WORD.match(text, i)
            if m:
                word = m.group(0)
                if word in ("query", "mutation", "subscription"):
                    ops.append(word)
                elif word == "fragment":
                    ops.append("fragment")
                i = m.end()
                continue
        i += 1
    return [o for o in ops if o not in ("_closed", "fragment")]


class ReadOnlyClient:
    """What an adapter reaches its host with. `transport(method, path, body)` does the I/O; this checks the shape."""

    def __init__(self, transport: Callable[[str, str, Any], Any]):
        self._transport = transport
        self.requests = 0

    def get(self, path: str) -> Any:
        self.requests += 1
        return self._transport("GET", path, None)

    def graphql(self, document: str, variables: Mapping[str, Any] | None = None) -> Any:
        ops = graphql_operations(document)
        if not ops or any(o != "query" for o in ops):
            raise WriteShaped(f"a read adapter sends GraphQL queries only, not {', '.join(ops) or 'nothing'}")
        self.requests += 1
        return self._transport("POST", "graphql", {"query": document, "variables": dict(variables or {})})

    def request(self, method: str, path: str, body: Any = None) -> Any:
        if method.upper() != "GET":
            raise WriteShaped(f"a read adapter sends GET only, not {method.upper()} {path}")
        return self.get(path)


# -- the registry --------------------------------------------------------------------------------------------------

_registry: dict[str, Adapter] = {}


def builtin_kinds() -> dict[str, Callable[..., Adapter]]:
    from .github_issues import GitHubIssues

    return {"github-issues": GitHubIssues}


def kinds() -> dict[str, Callable[..., Adapter]]:
    from importlib.metadata import entry_points

    out = dict(builtin_kinds())
    for ep in entry_points(group="onloop.adapters"):
        out.setdefault(ep.name, ep.load())
    return out


def load(entries: Iterable[Mapping[str, Any]]) -> dict[str, Adapter]:
    known = kinds()
    out: dict[str, Adapter] = {}
    for e in entries:
        make = known.get(e["kind"])
        if make is None:
            raise ValueError(f"no read adapter of kind {e['kind']!r}; known: {', '.join(sorted(known))}")
        out[e["id"]] = make(**e)
    return out


def set_registry(adapters: Mapping[str, Adapter]) -> None:
    _registry.clear()
    _registry.update(adapters)


def registry() -> dict[str, Adapter]:
    return dict(_registry)


def for_event_kind(kind: str) -> Adapter | None:
    for a in _registry.values():
        if "events" in a.capabilities and kind in a.event_kinds:
            return a
    return None


# -- the waker probe for `event:` --------------------------------------------------------------------------------------

PENDING_ON_SPIKE = {
    "gh-run": "the GitHub event checks wait for spike R5-S2 (a test repository the project lead approves)",
    "gh-checks": "the GitHub event checks wait for spike R5-S2 (a test repository the project lead approves)",
    "gh-review": "the GitHub event checks wait for spike R5-S2 (a test repository the project lead approves)",
}


class EventProbe:
    """`waker=event:<kind>:<locator>`: alive while the event is pending, dead when its source says it does not exist
    or no adapter knows the kind, unknown when it cannot tell. When it fires, a `waker.fired` event is logged once and
    the waker stays alive: what follows is the waiting role's wake, not a dead waker."""

    kind = "event"

    def check(self, ref: str, ledger: Any, now: str, since: str) -> Any:
        from ..detect.wakers import Probed

        ekind, _, locator = ref.partition(":")
        adapter = for_event_kind(ekind)
        if adapter is None:
            if ekind in PENDING_ON_SPIKE:
                return Probed("unknown", f"event:{ekind} is not checked yet: {PENDING_ON_SPIKE[ekind]}")
            return Probed("dead", f"no read adapter for event kind {ekind!r} is configured in onloopd.yaml")
        got = adapter.event(ekind, locator)
        if got.state == "pending":
            return Probed("alive", f"event {ref} is pending")
        if got.state == "gone":
            return Probed("dead", f"event {ref} does not exist at its source: {got.detail}"[:200])
        if got.state == "unknown":
            return Probed("unknown", f"event {ref} could not be read: {got.detail}"[:200])
        ledger.append(
            {
                "id": f"waker.fired:event:{ref}:{since}",
                "source": "adapter",
                "kind": "waker.fired",
                "subject_ref": f"waker:event:{ref}",
                "actor": f"adapter:{adapter.id}",
                "payload": {
                    "waker": f"event:{ref}",
                    "detail": got.detail,
                    "source_at": got.source_at,
                    "observed_at": now,
                },
            }
        )
        return Probed("alive", f"event {ref} fired: {got.detail}"[:200])


def new_event_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"
