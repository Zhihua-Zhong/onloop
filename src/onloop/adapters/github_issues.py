"""The GitHub issues read adapter (decision 0013): issues with a label are asks to the person.

    adapters:
      - id: tracker
        kind: github-issues
        repo: owner/name
        asks_label: needs-human

An issue's body may say what the ask is worth in lines of the same keys as the outcome line: `Effect:`, `Goal:`,
`Weight:`, `Cost-From:` or `Days:`, `Holds:`, `Default:`. It is read with `gh api --method GET`, through the read-only
client, with the person's own `gh` login.

What is not live yet, and why: the goal checklist's edit history (GraphQL `userContentEdits`) and the `event:` checks
(Actions runs, check runs, reviews) wait for spikes R5-S1 and R5-S2, which need a test repository the project lead
approves. Until then this adapter declares only `asks`; goals and events are exercised through fakes in the tests.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from . import ExternalAsk, NotAvailable, ReadOnlyClient

FIELD_LINES = {
    "effect": "effect",
    "goal": "goal",
    "weight": "weight",
    "cost-from": "cost_from",
    "cost_from": "cost_from",
    "days": "days",
    "holds": "holds",
    "default": "default",
}
_LINE = re.compile(r"^\s*([A-Za-z_-]+)\s*:\s*(.+?)\s*$")


def body_fields(body: str | None) -> dict[str, str]:
    """The ask keys an issue body states, one per line (`Effect: approve it, so it merges`); the first of each wins."""
    out: dict[str, str] = {}
    for line in (body or "").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        key = FIELD_LINES.get(m.group(1).lower())
        if key and key not in out:
            out[key] = m.group(2).strip().strip('"')
    return out


def gh_transport(method: str, path: str, body: Any) -> Any:
    from .. import github

    if method != "GET":
        raise NotAvailable("this transport reads with GET only")
    return github.get_list(path) if "?" in path or path.endswith("/issues") else github.get_object(path)


class GitHubIssues:
    capabilities = frozenset({"asks"})
    event_kinds = frozenset()

    def __init__(
        self,
        *,
        id: str,  # noqa: A002 - the adapter's id, as onloopd.yaml names it
        repo: str,
        asks_label: str | None = None,
        transport: Callable[[str, str, Any], Any] | None = None,
        **_: Any,
    ):
        self.id = id
        self.repo = repo
        self.asks_label = asks_label
        self.client = ReadOnlyClient(transport or gh_transport)

    def asks(self, cursor: str | None) -> tuple[list[ExternalAsk], str | None]:
        if not self.asks_label:
            return [], cursor
        q = f"repos/{self.repo}/issues?state=all&per_page=100&labels={self.asks_label}"
        if cursor:
            q += f"&since={cursor}"
        out, high = [], cursor
        for i in self.client.get(q) or []:
            if "pull_request" in i:  # the issues list also holds pull requests
                continue
            updated = i.get("updated_at")
            high = max(high or "", updated or "") or high
            out.append(
                ExternalAsk(
                    id=str(i["number"]),
                    url=i.get("html_url"),
                    title=i.get("title") or "",
                    state=i.get("state") or "open",
                    fields=body_fields(i.get("body")),
                    labels=tuple(sorted(lb.get("name", "") for lb in i.get("labels") or [])),
                    source_at=updated,
                    closed_at=i.get("closed_at"),
                    closed_by=((i.get("closed_by") or {}).get("login")),
                )
            )
        return out, high

    def goal(self, locator: str):
        raise NotAvailable("the GitHub goal checklist is read from the edit history once spike R5-S1 has run")

    def event(self, kind: str, locator: str):
        raise NotAvailable("the GitHub event checks wait for spike R5-S2")
