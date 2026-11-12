"""The reviewer's verdict, read from the log in one place.

A review is the reviewer's verdict only if it was submitted by the policy's reviewer App (a `Bot` of that login, so a
`User` who registered the same name is not the reviewer) and says APPROVED or CHANGES_REQUESTED. A COMMENTED review, or a
review by anyone else, is a review but not a verdict. Three readers of reviews have each forgotten the author filter once
(the KPI waits, `reviewer_ack`, the delta pane), so every reader of `review.submitted` events asks here, and
`tests/test_verdicts.py` fails if another module reads them directly. Only intake, which records the events, and the
event vocabulary itself are exempt.

`reviewer=None` means "no policy was given": every author counts, as it did for a KPI run without a policy.
"""

from __future__ import annotations

from typing import Any

from . import identity

KIND = "review.submitted"
VERDICT_STATES = ("APPROVED", "CHANGES_REQUESTED")


def _author(e: dict[str, Any]) -> Any:
    return e.get("actor") or e["payload"].get("author")


def by_reviewer(e: dict[str, Any], reviewer: str | None) -> bool:
    """Did this event come from the reviewer App?"""
    return is_reviewer(_author(e), reviewer)


def is_reviewer(who: Any, reviewer: str | None) -> bool:
    """Is `who` the reviewer App? The login and the type Bot, as the gate reads it, so a User who registered the same
    name is not the reviewer. Anything that asks "is this the reviewer" asks here, a review's author or a thread's."""
    if reviewer is None:
        return True
    try:
        return bool(who) and identity.is_login(who, reviewer, "Bot")
    except ValueError:
        return False


def verdict_state(e: dict[str, Any], reviewer: str | None) -> str | None:
    """APPROVED or CHANGES_REQUESTED when `e` is the reviewer's verdict, else None."""
    if e["kind"] != KIND or not by_reviewer(e, reviewer):
        return None
    state = e["payload"].get("state")
    return state if state in VERDICT_STATES else None


def current_verdict(e: dict[str, Any], reviewer: str | None) -> str | None:
    """The state of a verdict that was bound to the head it was submitted on, else None."""
    state = verdict_state(e, reviewer)
    return state if state and e["payload"].get("commit") == e["payload"].get("head") else None


def _row(e: dict[str, Any]) -> dict[str, Any]:
    p = e["payload"]
    return {"author": _author(e), "state": p.get("state"), "commit": p.get("commit"), "at": e.get("at")}


def latest_verdict(events: list[dict[str, Any]], reviewer: str | None) -> dict[str, Any] | None:
    """The reviewer's latest verdict in `events` (one pull request's, in log order), whatever head it names."""
    found = [e for e in events if verdict_state(e, reviewer)]
    return _row(found[-1]) if found else None


def standing_verdict(events: list[dict[str, Any]], reviewer: str | None, head: str) -> dict[str, Any] | None:
    """The reviewer's latest verdict that names `head`, or None: no verdict, or only verdicts of other heads."""
    found = [e for e in events if verdict_state(e, reviewer) and e["payload"].get("commit") == head]
    return _row(found[-1]) if found else None


def other_reviews(events: list[dict[str, Any]], reviewer: str | None) -> list[dict[str, Any]]:
    """Reviews that are not the reviewer's verdict: comments, and anything by anyone else. Shown apart, never as a verdict."""
    return [_row(e) for e in events if e["kind"] == KIND and not verdict_state(e, reviewer)]


def changes_requested_on(
    events: list[dict[str, Any]], reviewer: str | None, head: str
) -> list[dict[str, Any]]:
    """The events in which the reviewer asked for changes on `head`."""
    return [
        e
        for e in events
        if verdict_state(e, reviewer) == "CHANGES_REQUESTED" and e["payload"].get("head") == head
    ]
