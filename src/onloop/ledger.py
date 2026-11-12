"""The ledger: one file that says what the team is doing, what waits for the human, and what each role does next.

`ledger.yaml` holds:
    focus:        one sentence, what matters this week
    workstreams:  [{id, title, owner, state, since, note}]
    decisions:    [{id, title, size, status, asked, recommendation}]   decisions waiting for the human
    queues:       {role: [{id, title, state, ref}]}                       what each role pulls next
    changelog:    [{date, who, summary}]                                  append-only
    sla:          {state: days}                                           optional, read by the watchdog

Roles pull their next item from their own queue; an empty queue is reported to the lead. That is what stops a
team of agents stalling while it waits to be told what to do. `check` validates the file, and, given an earlier
copy, that the changelog only grew. `render` prints a status page; `next_item` answers "what do I do now".
"""

from __future__ import annotations

import datetime as dt
import html
import pathlib
import re
from typing import Any

import yaml

from . import identity

WORKSTREAM_STATES = ("planned", "active", "blocked", "done")
ITEM_STATES = ("todo", "doing", "blocked", "done")
DECISION_STATUS = ("pending", "decided")
DECISION_SIZES = ("small", "medium", "large")
DELIVERABLE_STATES = ("planned", "building", "ready", "blocked")
QUESTION_STATUS = ("open", "answered")
LEDGER_FILE = "ledger.yaml"
_COMMIT = re.compile(r"[0-9a-f]{7,40}")


class LedgerError(ValueError):
    pass


def load(path: str | pathlib.Path = LEDGER_FILE) -> dict[str, Any]:
    p = pathlib.Path(path)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise LedgerError(f"cannot read {p}: {e}") from e
    if not isinstance(data, dict):
        raise LedgerError(f"{p} is not a mapping")
    return data


def _date(value: Any) -> bool:
    if isinstance(value, dt.date):
        return True
    try:
        dt.date.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def _evidence(value: Any) -> bool:
    """Evidence is a link to where it can be seen, or a commit."""
    text = str(value or "")
    return text.startswith("https://") and len(text) > len("https://") or bool(_COMMIT.fullmatch(text))


def check(
    ledger: Any,
    roles: list[str] | None = None,
    previous: dict[str, Any] | None = None,
    human: str | None = None,
) -> list[str]:
    """Every problem with the ledger. `roles` (from the policy) makes unknown owners and queues a problem; `previous`
    (an earlier copy, usually the base branch's) makes any rewrite or removal of changelog history a problem."""
    if not isinstance(ledger, dict):
        return ["the ledger is not a mapping"]
    problems: list[str] = []
    if not isinstance(ledger.get("focus"), str) or not ledger.get("focus"):
        problems.append("`focus` must be one sentence")
    ids: set[str] = set()

    def new_id(kind: str, ident: Any) -> None:
        if not isinstance(ident, str) or not ident:
            problems.append(f"a {kind} has no id")
        elif ident in ids:
            problems.append(f"the id {ident!r} appears more than once")
        else:
            ids.add(ident)

    for w in ledger.get("workstreams") or []:
        new_id("workstream", w.get("id") if isinstance(w, dict) else None)
        if not isinstance(w, dict):
            problems.append("a workstream is not a mapping")
            continue
        if w.get("state") not in WORKSTREAM_STATES:
            problems.append(
                f"workstream {w.get('id')!r} has state {w.get('state')!r}; use {', '.join(WORKSTREAM_STATES)}"
            )
        if roles is not None and w.get("owner") not in roles:
            problems.append(f"workstream {w.get('id')!r} is owned by {w.get('owner')!r}, which is not a role")
        if w.get("since") is not None and not _date(w["since"]):
            problems.append(f"workstream {w.get('id')!r} has a `since` that is not a date")
    for d in ledger.get("decisions") or []:
        if not isinstance(d, dict):
            problems.append("a decision is not a mapping")
            continue
        new_id("decision", d.get("id"))
        if d.get("status") not in DECISION_STATUS:
            problems.append(
                f"decision {d.get('id')!r} has status {d.get('status')!r}; use {', '.join(DECISION_STATUS)}"
            )
        if d.get("size") not in DECISION_SIZES:
            problems.append(
                f"decision {d.get('id')!r} has size {d.get('size')!r}; use {', '.join(DECISION_SIZES)}"
            )
        if d.get("status") == "pending" and not d.get("recommendation"):
            problems.append(
                f"pending decision {d.get('id')!r} has no recommendation: ask for yes or no with a default"
            )
        if d.get("decided_by") is not None and roles is not None and d["decided_by"] not in [*roles, human]:
            problems.append(
                f"decision {d.get('id')!r} was decided by {d['decided_by']!r}, who is not a role or the human"
            )
        if d.get("decided_on") is not None and not _date(d["decided_on"]):
            problems.append(f"decision {d.get('id')!r} has a `decided_on` that is not a date")
        if d.get("evidence") is not None and not _evidence(d["evidence"]):
            problems.append(
                f"decision {d.get('id')!r} has evidence that is neither an https link nor a commit"
            )
    problems.extend(_deliverables(ledger.get("deliverables"), roles, new_id))
    problems.extend(_questions(ledger.get("questions"), new_id))
    queues = ledger.get("queues") or {}
    if not isinstance(queues, dict):
        problems.append("`queues` must map a role to a list of items")
        queues = {}
    for role, items in queues.items():
        if roles is not None and role not in roles:
            problems.append(f"queue {role!r} is not a role")
        for it in items or []:
            if not isinstance(it, dict):
                problems.append(f"queue {role!r} holds an item that is not a mapping")
                continue
            new_id("queue item", it.get("id"))
            if it.get("state") not in ITEM_STATES:
                problems.append(
                    f"queue item {it.get('id')!r} has state {it.get('state')!r}; use {', '.join(ITEM_STATES)}"
                )
    log = ledger.get("changelog")
    if not isinstance(log, list):
        problems.append("`changelog` must be a list")
        log = []
    for i, e in enumerate(log):
        if not (isinstance(e, dict) and _date(e.get("date")) and e.get("who") and e.get("summary")):
            problems.append(f"changelog entry {i + 1} needs a date, who and summary")
    if previous is not None:
        old = previous.get("changelog") or []
        if log[: len(old)] != old:
            problems.append("the changelog is append-only: an earlier entry was changed or removed")
    return problems


def _deliverables(items: Any, roles: list[str] | None, new_id) -> list[str]:
    if items is None:
        return []
    if not isinstance(items, list):
        return ["`deliverables` must be a list"]
    out: list[str] = []
    for d in items:
        if not isinstance(d, dict):
            out.append("a deliverable is not a mapping")
            continue
        name = d.get("id")
        new_id("deliverable", name)
        if not d.get("title"):
            out.append(f"deliverable {name!r} has no title")
        if d.get("state") not in DELIVERABLE_STATES:
            out.append(
                f"deliverable {name!r} has state {d.get('state')!r}; use {', '.join(DELIVERABLE_STATES)}"
            )
        if roles is not None and d.get("owner") not in roles:
            out.append(f"deliverable {name!r} is owned by {d.get('owner')!r}, which is not a role")
        if d.get("state") == "ready" and not d.get("fallback"):
            out.append(
                f"deliverable {name!r} is ready but has no fallback: say what is shown if the live link fails"
            )
        if d.get("link") is not None and not str(d["link"]).startswith("https://"):
            out.append(f"deliverable {name!r} has a link that is not https")
        if d.get("due") is not None and not _date(d["due"]):
            out.append(f"deliverable {name!r} has a `due` that is not a date")
        v = d.get("verified")
        if v is not None:
            if not isinstance(v, dict) or set(v) - {"on", "by", "evidence"}:
                out.append(f"deliverable {name!r}: `verified` is {{on, by, evidence}}")
                continue
            if not _date(v.get("on")):
                out.append(f"deliverable {name!r}: `verified.on` is not a date")
            if roles is not None and v.get("by") not in roles:
                out.append(f"deliverable {name!r}: `verified.by` {v.get('by')!r} is not a role")
            elif identity.same_role(v.get("by"), d.get("owner")):
                out.append(
                    f"deliverable {name!r}: `verified.by` is its owner; an owner cannot vouch for their own work"
                )
            if not _evidence(v.get("evidence")):
                out.append(f"deliverable {name!r}: `verified.evidence` must be an https link or a commit")
    return out


def _questions(items: Any, new_id) -> list[str]:
    if items is None:
        return []
    if not isinstance(items, list):
        return ["`questions` must be a list"]
    out: list[str] = []
    for q in items:
        if not isinstance(q, dict):
            out.append("a question is not a mapping")
            continue
        new_id("question", q.get("id"))
        if not q.get("title"):
            out.append(f"question {q.get('id')!r} has no title")
        if q.get("status", "open") not in QUESTION_STATUS:
            out.append(
                f"question {q.get('id')!r} has status {q.get('status')!r}; use {', '.join(QUESTION_STATUS)}"
            )
        if q.get("asked") is not None and not _date(q["asked"]):
            out.append(f"question {q.get('id')!r} has an `asked` that is not a date")
    return out


def next_item(ledger: dict[str, Any], role: str) -> dict[str, Any] | None:
    """What `role` does next: the item it is already doing, else the first todo, else None (an empty queue)."""
    items = (ledger.get("queues") or {}).get(role) or []
    for state in ("doing", "todo"):
        for it in items:
            if it.get("state") == state:
                return it
    return None


def render_markdown(ledger: dict[str, Any]) -> str:
    out = ["# Status", "", f"**Focus:** {ledger.get('focus', '')}", ""]
    pending = [d for d in ledger.get("decisions") or [] if d.get("status") == "pending"]
    out += ["## Waiting for the human", ""]
    out += [
        f"- **{d['id']}** ({d.get('size')}): {d.get('title')}. Recommendation: {d.get('recommendation')}"
        for d in pending
    ] or ["Nothing."]
    out += ["", "## Workstreams", ""]
    out += [
        f"- **{w['id']}** [{w.get('state')}] {w.get('title')} (owner: {w.get('owner')})"
        for w in ledger.get("workstreams") or []
    ] or ["None."]
    out += ["", "## Queues", ""]
    for role, items in sorted((ledger.get("queues") or {}).items()):
        out.append(f"### {role}")
        out += [f"- [{it.get('state')}] {it.get('id')}: {it.get('title')}" for it in items or []] or [
            "- (empty)"
        ]
        out.append("")
    out += ["## Recent changes", ""]
    out += [
        f"- {e['date']} {e['who']}: {e['summary']}" for e in (ledger.get("changelog") or [])[-8:][::-1]
    ] or ["None."]
    return "\n".join(out) + "\n"


def render_html(ledger: dict[str, Any]) -> str:
    body = html.escape(render_markdown(ledger))
    return (
        "<!doctype html><meta charset=utf-8><title>Status</title>"
        "<style>body{font:15px/1.5 system-ui;max-width:60rem;margin:2rem auto;padding:0 1rem}"
        "pre{white-space:pre-wrap}</style><pre>" + body + "</pre>"
    )


def self_test() -> tuple[bool, list[str]]:
    good = {
        "focus": "ship",
        "workstreams": [
            {"id": "w1", "title": "t", "owner": "builder", "state": "active", "since": "2026-01-01"}
        ],
        "decisions": [
            {"id": "d1", "title": "t", "size": "small", "status": "pending", "recommendation": "yes"}
        ],
        "queues": {"builder": [{"id": "q1", "title": "t", "state": "todo"}]},
        "changelog": [{"date": "2026-01-01", "who": "lead", "summary": "start"}],
    }
    roles = ["builder", "lead"]

    def mutated(**kw):
        d = yaml.safe_load(yaml.safe_dump(good))
        d.update(kw)
        return d

    cases = [
        ("a good ledger passes", check(good, roles, good), False),
        (
            "a bad workstream state fails",
            check(mutated(workstreams=[dict(good["workstreams"][0], state="x")]), roles),
            True,
        ),
        (
            "a duplicate id fails",
            check(mutated(queues={"builder": [{"id": "w1", "title": "t", "state": "todo"}]}), roles),
            True,
        ),
        ("an unknown role fails", check(mutated(queues={"ghost": []}), roles), True),
        (
            "a pending decision without a recommendation fails",
            check(mutated(decisions=[dict(good["decisions"][0], recommendation="")]), roles),
            True,
        ),
        (
            "a rewritten changelog fails",
            check(
                mutated(changelog=[{"date": "2026-01-01", "who": "lead", "summary": "edited"}]), roles, good
            ),
            True,
        ),
        ("a removed changelog entry fails", check(mutated(changelog=[]), roles, good), True),
    ]
    ok, lines = True, []
    for label, problems, want_bad in cases:
        right = bool(problems) == want_bad
        ok &= right
        lines.append(f"  {'ok' if right else 'FAILED'} {label}" + ("" if right else f" (got: {problems})"))
    right = (next_item(good, "builder") or {}).get("id") == "q1" and next_item(good, "lead") is None
    ok &= right
    lines.append(
        f"  {'ok' if right else 'FAILED'} a role pulls its next item and an empty queue reads as empty"
    )
    return ok, lines
