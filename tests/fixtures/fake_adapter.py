"""A read adapter that is not GitHub (decision 0013): it proves the core needs nothing GitHub-specific for tasks, goals
and outside events. Generic names only. Its host is a dict the test edits; every read goes through the read-only
client, so a write-shaped request fails the test."""

from __future__ import annotations

from typing import Any

from onloop.adapters import Edit, EventState, ExternalAsk, GoalSnapshot, ReadOnlyClient


class FakeHost:
    def __init__(self):
        self.asks: dict[str, dict[str, Any]] = {}
        self.goals: dict[str, dict[str, Any]] = {}
        self.events: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body: Any) -> Any:
        self.calls.append((method, path))
        if path == "asks":
            return [dict(v, id=k) for k, v in self.asks.items()]
        if path.startswith("goal/"):
            return self.goals[path[5:]]
        if path.startswith("event/"):
            return self.events.get(path[6:], "gone")
        if path == "graphql":
            return {"data": {}}
        raise KeyError(path)


class FakeAdapter:
    capabilities = frozenset({"asks", "goals", "events"})
    event_kinds = frozenset({"fake-run"})

    def __init__(self, id: str = "board", host: FakeHost | None = None, **_: Any):  # noqa: A002
        self.id = id
        self.host = host or FakeHost()
        self.client = ReadOnlyClient(self.host)

    def asks(self, cursor):
        out = []
        for a in self.client.get("asks"):
            out.append(
                ExternalAsk(
                    id=a["id"],
                    url=f"https://example.invalid/board/{a['id']}",
                    title=a.get("title", ""),
                    state=a.get("state", "open"),
                    fields=a.get("fields", {}),
                    source_at=a.get("at"),
                    closed_at=a.get("closed_at"),
                    closed_by=a.get("closed_by"),
                )
            )
        return out, "c1"

    def goal(self, locator):
        g = self.client.get(f"goal/{locator}")
        edits = tuple(Edit(e["id"], e["at"], tuple(tuple(i) for i in e["items"])) for e in g["edits"])
        return GoalSnapshot(g.get("title"), edits, tuple(tuple(c) for c in g.get("comments", [])))

    def event(self, kind, locator):
        state = self.client.get(f"event/{kind}:{locator}")
        return EventState(state, detail=f"{kind} {locator} is {state}")
