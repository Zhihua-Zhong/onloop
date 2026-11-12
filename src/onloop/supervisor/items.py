"""Work items: what a role's queue is made of, read from GitHub issues and pull requests, the asks and the ledger.

Everything here is a pure function of data already read, so a test feeds it recorded JSON and no network.

A `WorkItem` carries a `version`: a short text that changes whenever the item changes in a way a role should look at
again (GitHub's `updated_at` moves on every push, label, comment and edit). The scheduler keys its steps on
(role, item id, version), which is what makes "the queue changed since the role's last step" exact.

A trigger with `version: head` keys a pull request on its head commit instead, so a comment, a label or a role's own
review does not make it due again; only a new head does. A trigger with `unanswered_by` keeps a pull request out of the
queue once that login has answered its current head (`answered_keys`). A trigger with `require: {checks: green}`
admits a pull request only while the checks at its head are green (`checks_state`). A role's `priority_paths` puts
pull requests whose changed paths match first: all of them (`priority_when: all`, the default) or any (`any`).

Text that comes from GitHub (titles, labels) is data. It reaches a prompt only through the template's named variables,
on one line, cut to `TITLE_CAP` characters.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field, replace
from typing import Any

from .. import events as ev
from . import github_issues
from . import nodes as model
from .config import AnswerSpec, AsksConfig, RoleSpec, Trigger

TITLE_CAP = 200
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")


def one_line(text: Any, cap: int = TITLE_CAP) -> str:
    """Outside text as one plain line: control characters and newlines become spaces, the result is cut to `cap`."""
    s = _CTRL.sub(" ", str(text or "")).strip()
    return s if len(s) <= cap else s[: cap - 1] + "…"


@dataclass(frozen=True)
class WorkItem:
    kind: str  # pr | issue | ask | ledger
    id: str  # pr#12, issue#40, ask#40, ask:p1 (private), ledger:Q3
    version: str
    title: str = ""
    number: int | None = None
    url: str = ""
    labels: tuple[str, ...] = ()
    state: str = "open"
    updated: str | None = None  # stored timestamp format
    draft: bool = False
    owner: str | None = None
    depends_on: tuple[str, ...] = ()  # ids of the items this one waits for ("#5", or an ask id)
    done_when: str | None = None
    default: str | None = None
    closed_at: str | None = None
    private: bool = False
    cls: str = model.DEFAULT_CLASS  # expedite | standard | background (asks)
    head: str | None = None  # pull requests: the head commit's sha, when the source read it
    answered: tuple[str, ...] = ()  # pull requests: the AnswerSpec keys that answered the current head
    checks: str | None = None  # pull requests: green | pending | red | none at the head, when read
    paths: tuple[str, ...] = ()  # pull requests: the changed paths, when read (at most PATHS_CAP)
    paths_more: bool = False  # pull requests: more paths changed than `paths` holds
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    def as_dict(self) -> dict[str, Any]:
        out = {
            k: getattr(self, k)
            for k in (
                "kind",
                "id",
                "version",
                "title",
                "number",
                "url",
                "state",
                "updated",
                "draft",
                "owner",
                "done_when",
                "default",
                "closed_at",
                "private",
                "cls",
                "head",
                "checks",
                "paths_more",
            )
        }
        out["labels"] = list(self.labels)
        out["depends_on"] = list(self.depends_on)
        out["answered"] = list(self.answered)
        out["paths"] = list(self.paths)
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkItem:
        tuples = ("labels", "depends_on", "answered", "paths")
        return cls(
            **{
                **{k: d[k] for k in d if k not in (*tuples, "extra")},
                **{k: tuple(d.get(k) or ()) for k in tuples},
            }
        )


def _labels(raw: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            ((lb.get("name") if isinstance(lb, dict) else str(lb)) or "") for lb in raw.get("labels") or []
        )
    )


def owner_of(labels: tuple[str, ...], role_prefix: str) -> str | None:
    roles = sorted(lb[len(role_prefix) :] for lb in labels if role_prefix and lb.startswith(role_prefix))
    return roles[0] if roles else None


def item_of_issue(raw: dict[str, Any], role_prefix: str = "role/") -> WorkItem:
    """One entry of `GET /repos/{repo}/issues` (which lists pull requests too) as a pull request or issue item."""
    is_pr = "pull_request" in raw
    n = int(raw["number"])
    labels = _labels(raw)
    draft = bool(raw.get("draft")) if is_pr else False
    updated = ev.norm_ts(raw.get("updated_at"))
    url = raw.get("html_url") or ""
    return WorkItem(
        kind="pr" if is_pr else "issue",
        id=f"{'pr' if is_pr else 'issue'}#{n}",
        number=n,
        version=f"{updated}|draft={int(draft)}",
        title=one_line(raw.get("title")),
        url=url if url.startswith("https://") else "",
        labels=labels,
        state=str(raw.get("state") or "open"),
        updated=updated,
        draft=draft,
        owner=owner_of(labels, role_prefix),
    )


def items_of_open(issues: list[dict[str, Any]], role_prefix: str = "role/") -> list[WorkItem]:
    return sorted(
        (item_of_issue(i, role_prefix) for i in issues if i.get("state", "open") == "open"),
        key=lambda w: (w.kind, w.number or 0),
    )


_SHA = re.compile(r"^[0-9a-f]{40}$")


def answered_keys(
    head: str, reviews: list[dict[str, Any]], comments: list[dict[str, Any]], specs: tuple[AnswerSpec, ...]
) -> tuple[str, ...]:
    """Which answer specs the current `head` has, from the pull request's reviews and issue comments (GitHub's JSON).

    A review counts when its author is the spec's login, its `commit_id` is exactly the head and its state is one of the
    spec's states. A comment counts when its author is the login and its body holds the spec's marker and the full head
    sha. Nothing on an earlier head counts, and nothing from another login."""
    if not _SHA.match(head or ""):
        return ()
    out = []
    for spec in specs:
        hit = any(
            (r.get("user") or {}).get("login") == spec.login
            and r.get("commit_id") == head
            and r.get("state") in spec.review_states
            for r in reviews
        )
        if not hit and spec.comment_marker:
            hit = any(
                (c.get("user") or {}).get("login") == spec.login
                and spec.comment_marker in (c.get("body") or "")
                and head in (c.get("body") or "")
                for c in comments
            )
        if hit:
            out.append(spec.key)
    return tuple(out)


GOOD_CONCLUSIONS = ("success", "neutral", "skipped")


def latest_runs(check_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The runs that stand: per (name, app), those with the latest `started_at`. A run without one starts at ""."""

    def key(r: dict[str, Any]) -> tuple[str, Any]:
        return str(r.get("name") or ""), (r.get("app") or {}).get("id")

    last: dict[tuple[str, Any], str] = {}
    for r in check_runs:
        k = key(r)
        last[k] = max(last.get(k, ""), str(r.get("started_at") or ""))
    return [r for r in check_runs if str(r.get("started_at") or "") == last[key(r)]]


def checks_state(
    check_runs: list[dict[str, Any]], statuses: list[dict[str, Any]], required: tuple[str, ...] = ()
) -> str:
    """The checks at one commit: `green`, `pending`, `red`, or `none` when nothing reported.

    `check_runs` are GitHub's check runs at the commit, `statuses` the commit statuses (the latest per context).
    With `required` names (a branch ruleset's required checks), only those count: each must have reported and passed,
    and a required one not yet reported is pending. Without them, every reported check counts.

    The check-runs endpoint returns one run per check suite, so a check cancelled in one workflow run and passed in a
    later one is listed twice. As GitHub does, only the latest run of a name from one app counts, by `started_at`. Runs
    of a name from different apps, or with the same start, all count, and the worse one stands."""
    worst = {"green": 0, "pending": 1, "red": 2}
    seen: dict[str, str] = {}

    def note(name: str, state: str) -> None:  # two reports under one name: the worse one stands
        if worst[state] >= worst.get(seen.get(name, "green"), 0):
            seen[name] = state

    for r in latest_runs(check_runs):
        if r.get("status") != "completed":
            note(str(r.get("name") or ""), "pending")
        else:
            note(str(r.get("name") or ""), "green" if r.get("conclusion") in GOOD_CONCLUSIONS else "red")
    for st in statuses:
        note(
            str(st.get("context") or ""),
            {"success": "green", "pending": "pending"}.get(str(st.get("state")), "red"),
        )
    states = [seen.get(n, "pending") for n in required] if required else list(seen.values())
    if not states:
        return "none"
    if "red" in states:
        return "red"
    if "pending" in states:
        return "pending"
    return "green"


PATHS_CAP = 300


def path_matches(path: str, globs: tuple[str, ...]) -> bool:
    """fnmatch, case-sensitive: `*` also matches `/`, so `web/**` is everything under web/."""
    return any(fnmatch.fnmatchcase(path, g) for g in globs)


def first_in_line(role: RoleSpec, item: WorkItem) -> bool:
    """Whether the role's `priority_paths` put this item first. With `all`, a pull request whose paths were not all
    read (`paths_more`) or that changed nothing is not first: unknown is not priority."""
    if not role.priority_paths or not item.paths:
        return False
    if role.priority_when == "any":
        return any(path_matches(p, role.priority_paths) for p in item.paths)
    return not item.paths_more and all(path_matches(p, role.priority_paths) for p in item.paths)


def with_heads(
    items: list[WorkItem],
    pulls: list[dict[str, Any]],
    answered: dict[int, tuple[str, ...]] | None = None,
    checks: dict[int, str] | None = None,
    paths: dict[int, tuple[tuple[str, ...], bool]] | None = None,
) -> list[WorkItem]:
    """Pull request items with their head sha (from `GET /repos/{repo}/pulls`), the answers on it, and where read, its
    checks and the priority globs it matches. The pulls listing is the authority for the draft flag too."""
    by_n = {int(p["number"]): p for p in pulls if isinstance(p, dict) and p.get("number") is not None}
    out = []
    for it in items:
        p = by_n.get(it.number or -1) if it.kind == "pr" else None
        if p is None:
            out.append(it)
            continue
        sha = str((p.get("head") or {}).get("sha") or "")
        draft = bool(p.get("draft", it.draft))
        out.append(
            replace(
                it,
                head=sha if _SHA.match(sha) else None,
                draft=draft,
                version=f"{it.updated}|draft={int(draft)}",
                answered=(answered or {}).get(it.number or -1, ()),
                checks=(checks or {}).get(it.number or -1),
                paths=(paths or {}).get(it.number or -1, ((), False))[0],
                paths_more=(paths or {}).get(it.number or -1, ((), False))[1],
            )
        )
    return out


def watched(item: WorkItem, watch: tuple[tuple[str, Trigger], ...]) -> bool:
    """Whether a (role, trigger) pair that needs checks or paths could take this pull request, leaving the checks
    aside: only such pull requests are worth the extra reads."""
    return any(_matches(replace(t, require_checks=None), item, role) for role, t in watch)


# -- the person's asks ----------------------------------------------------------------------------------------------


def _source_config(asks: AsksConfig, repo: str) -> github_issues.SourceConfig:
    raw = {"repo": repo if repo else "local/asks", **asks.mapping}
    try:
        return github_issues.SourceConfig.from_dict(raw)
    except github_issues.AdapterError as e:
        raise ValueError(f"asks: {e}") from e


def asks_of_issues(
    issues: list[dict[str, Any]],
    asks: AsksConfig,
    repo: str = "",
    blocked_by: dict[int, list[int]] | None = None,
) -> list[WorkItem]:
    """Issues carrying the ask label as ask items. State, owner, dependencies and the default come from the work graph's
    issue mapping (`supervisor/github_issues.py`), configured by `asks.labels`, `asks.depends_on` and `asks.fields`; the
    "done when" line from `asks.fields.done_when`."""
    sc = _source_config(asks, repo)
    done_re = re.compile(asks.done_when_pattern)
    out = []
    for raw in issues:
        if "pull_request" in raw or (asks.label and asks.label not in _labels(raw)):
            continue
        node = github_issues.node_of(sc, raw, blocked_by=(blocked_by or {}).get(int(raw["number"])))
        m = done_re.search(raw.get("body") or "")
        cls = node["class"]
        out.append(
            WorkItem(
                kind="ask",
                id=f"ask#{raw['number']}",
                number=int(raw["number"]),
                version="",
                title=one_line(node["title"]),
                url=node["url"] or "",
                labels=_labels(raw),
                state=node["state"],
                updated=node["updated"],
                owner=node["owner"],
                depends_on=tuple(node["depends_on"]),
                done_when=one_line(m.group(1) if m and m.groups() else (m.group(0) if m else "")) or None,
                default=one_line(node["default"]) if node["default"] else None,
                closed_at=ev.norm_ts(raw.get("closed_at")),
                cls=cls,
            )
        )
    return sorted(out, key=lambda w: w.number or 0)


PRIVATE_KEYS = {
    "id",
    "ask",
    "owner",
    "state",
    "done_when",
    "last_moved",
    "depends_on",
    "default",
    "closed_at",
    "class",
}


def asks_of_private(rows: Any) -> list[WorkItem]:
    """Items from the person's private file: a YAML list of {id, ask, owner, state, done_when, last_moved, depends_on,
    default, closed_at}. They are shown in the digest and watched by the attention rules; they never become a role's
    step, because a step acts on shared state and these must stay out of it."""
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError("the private asks file must be a list of items")
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or not r.get("id") or not r.get("ask"):
            raise ValueError(f"private ask {i}: needs id and ask")
        unknown = sorted(set(r) - PRIVATE_KEYS)
        if unknown:
            raise ValueError(f"private ask {r['id']}: unknown keys {', '.join(unknown)}")
        state = r.get("state", "open")
        if state not in model.STATES:
            raise ValueError(f"private ask {r['id']}: state must be one of {', '.join(model.STATES)}")
        out.append(
            WorkItem(
                kind="ask",
                id=f"ask:{r['id']}",
                version="",
                title=one_line(r["ask"]),
                state=state,
                updated=ev.norm_ts(str(r["last_moved"])) if r.get("last_moved") else None,
                owner=r.get("owner"),
                depends_on=tuple(f"ask:{d}" for d in r.get("depends_on") or []),
                done_when=r.get("done_when"),
                default=r.get("default"),
                closed_at=ev.norm_ts(str(r["closed_at"])) if r.get("closed_at") else None,
                private=True,
                cls=r.get("class", model.DEFAULT_CLASS),
            )
        )
    return out


def ledger_items(ledger: dict[str, Any]) -> list[WorkItem]:
    """The ledger's role queues (todo and doing) as items owned by their role."""
    out = []
    for role, rows in sorted((ledger.get("queues") or {}).items()):
        for r in rows or []:
            if not isinstance(r, dict) or r.get("state") not in ("todo", "doing") or not r.get("id"):
                continue
            out.append(
                WorkItem(
                    kind="ledger",
                    id=f"ledger:{r['id']}",
                    title=one_line(r.get("title")),
                    state=str(r["state"]),
                    owner=str(role),
                    version=f"{r['state']}|{one_line(r.get('title'))}|{r.get('ref') or ''}",
                )
            )
    return out


# -- a role's queue -------------------------------------------------------------------------------------------------


def _matches(t: Trigger, item: WorkItem, role: str) -> bool:
    if t.kind != item.kind:
        return False
    if t.labels_any and not set(t.labels_any) & set(item.labels):
        return False
    if t.labels_none and set(t.labels_none) & set(item.labels):
        return False
    if t.draft is not None and item.draft != t.draft:
        return False
    if t.unanswered_by is not None and t.unanswered_by.key in item.answered:
        return False
    if t.require_checks and item.checks != "green":
        return False
    if t.kind in ("ask", "ledger") or t.owned:
        return item.owner == role
    return True


def queue_for(role: RoleSpec, items: list[WorkItem], drained: list[WorkItem]) -> list[WorkItem]:
    """The role's queue: pull requests, issues and ledger rows matching a trigger, and the drained asks it owns.

    Order: expedite first, then items the role's `priority_paths` put first (`first_in_line`), then the oldest change
    first, then the id. One item appears once."""
    seen: dict[str, WorkItem] = {}
    for it in [*items, *drained]:
        if it.kind == "ask" and it not in drained:
            continue
        if it.id in seen:
            continue
        t = next((t for t in role.triggers if _matches(t, it, role.name)), None)
        if t is None:
            continue
        if t.version == "head":
            if not it.head:
                continue  # a head-keyed trigger never falls back to keying on every change
            it = with_version(it, f"head={it.head}")
        seen[it.id] = it
    rank = {"expedite": 0, "standard": 1, "background": 2}
    return sorted(
        seen.values(),
        key=lambda w: (rank.get(w.cls, 1), 0 if first_in_line(role, w) else 1, w.updated or "", w.id),
    )


def with_version(item: WorkItem, version: str) -> WorkItem:
    return replace(item, version=version)
