"""Events: what onloop learned happened, as append-only facts with a deterministic id.

An event is a hint, never evidence. A handler that acts on one re-reads the live facts first. The id is the hash of
the repository, the kind, the subject and a *natural key* chosen so that seeing the same thing twice appends nothing
and seeing a genuinely new thing (a second ready on the same head) appends a second event.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# One timestamp format everywhere: UTC text, millisecond precision. Comparing the text compares the time.
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"
TS_PATTERN = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

KINDS = frozenset(
    {
        "pr.opened",
        "pr.reopened",
        "pr.ready",
        "pr.drafted",
        "pr.head_moved",
        "pr.merged",
        "pr.closed",
        "review.submitted",
        "review.dismissed",
        "verdict.reversed",
        "check.completed",
        "main.advanced",
        "gate.merged",
        "gate.refused",
        "gate.postmerge_pending",
        "gate.already_merged",
        "gate.postmerge_checked",
        "autogate.decided",
        "autoready.decided",
        "postmerge.resumed",
        "gate.reverted",
        "runner.stopped",
        "slot.requested",
        "slot.granted",
        "slot.released",
        "slot.expired",
        "slot.renewed",
        "preflight.reported",
        "handler.dead",
        "handler.unblocked",
        "runner.tick",
        "runner.takeover",
        "record.written",
        "review.thread_opened",
        "review.thread_resolved",
        "review.threads_read",
    }
)


class EventError(ValueError):
    pass


def norm_ts(value: str | dt.datetime | None) -> str | None:
    """Any ISO-8601 timestamp, or a datetime, as the one stored format; None stays None."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        d = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            d = dt.datetime.fromisoformat(text)
        except ValueError as e:
            raise EventError(f"not a timestamp: {value!r}") from e
    if d.tzinfo is not None:
        d = d.astimezone(dt.UTC).replace(tzinfo=None)
    return d.strftime(TS_FORMAT)[:-3] + "Z"


def now_ts() -> str:
    """Now, in the one stored format."""
    return norm_ts(dt.datetime.now(dt.UTC)) or ""


def check_ts(value: str) -> str:
    if not isinstance(value, str) or not TS_PATTERN.match(value):
        raise EventError(f"timestamp {value!r} is not in the stored format YYYY-MM-DDTHH:MM:SS.mmmZ")
    return value


def event_id(repo: str, kind: str, subject: str, key: str) -> str:
    """Hex sha256 of the four parts. The same four parts always give the same id; any difference gives another."""
    return hashlib.sha256("\x1f".join((repo, kind, subject, key)).encode("utf-8")).hexdigest()


def body_hash(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


@dataclass
class Event:
    kind: str
    subject: str  # 'pr#12', 'slot:db', 'main'
    key: str  # the natural key: what makes this occurrence this one
    payload: dict[str, Any] = field(default_factory=dict)
    actor: str | None = None  # 'login/Type'; 'onloop' for events onloop makes
    at: str | None = None  # when it happened, if the source says; else the observation time

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise EventError(f"unknown event kind {self.kind!r}")
        if not self.subject or not self.key:
            raise EventError("an event needs a subject and a natural key")


def actor_of(author: dict[str, Any] | None) -> str | None:
    if not author or not author.get("login"):
        return None
    return f"{author['login']}/{author.get('type') or 'Unknown'}"


_DURATION = re.compile(r"(\d+)([smhdw])")
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str) -> int:
    """A duration such as `30s`, `15m`, `1h30m`, `1d` or `1w`, in seconds. Anything else raises EventError."""
    if not isinstance(text, str) or not text or _DURATION.sub("", text):
        raise EventError(
            f"not a duration: {text!r}; write a number and a unit (s, m, h, d, w), as in 15m or 1h30m"
        )
    return sum(int(n) * _UNIT_S[u] for n, u in _DURATION.findall(text))


def shift_ts(ts: str, seconds: float) -> str:
    """A stored timestamp moved by `seconds`."""
    d = dt.datetime.strptime(check_ts(ts)[:-1], TS_FORMAT)
    return norm_ts(d + dt.timedelta(seconds=seconds)) or ""


def parse_ts(ts: str) -> dt.datetime:
    """A stored timestamp as an aware UTC datetime."""
    return dt.datetime.strptime(check_ts(ts)[:-1], TS_FORMAT).replace(tzinfo=dt.UTC)
