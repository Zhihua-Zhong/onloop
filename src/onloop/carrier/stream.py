"""Reading a run's stream-json output (CLI 2.1.321; spikes S2, R4-S1, R4-S6, R4-S7).

    Stream.feed(line)      one stdout line; returns what it was ("replay", "result", "rate_limit", ...)
    Stream.usage()         what the rate-limit events and the result say about the plan's limits

What a run's stream holds:
- `system/init` first;
- with `--replay-user-messages`, the user message written on stdin comes back as `{"type": "user", "isReplay": true}`
  once the CLI starts processing it: that is `processing`, not receipt (a merged message comes back once);
- one `rate_limit_event` per turn, before its `result`, with `rate_limit_info.status` (`allowed`,
  `allowed_warning`, `rejected`), `resetsAt` (epoch seconds), `rateLimitType`, `isUsingOverage`, and a
  `utilization` per window;
- one `result` per turn: `is_error`, `subtype`, `terminal_reason`, `api_error_status`, `num_turns`,
  `permission_denials` (a refused tool exits 0 with `is_error: false`: this list is the only sign).

A line that is not valid UTF-8 or not JSON makes the stream `malformed` (story 8): the run fails, it is not guessed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

USAGE_STATUSES = ("allowed", "allowed_warning", "rejected")


def text_of(content: Any) -> str:
    """A user message's text: a string, or the text blocks of a content list joined by newlines."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Stream:
    expect: str | None = None  # the sha256 of the composed message, to know its replay
    init: bool = False
    replayed: bool = False
    results: list[dict[str, Any]] = field(default_factory=list)
    rate_limit: dict[str, Any] | None = None
    malformed: str | None = None
    lines: int = 0

    def feed(self, raw: bytes | str) -> str:
        self.lines += 1
        try:
            line = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
        except UnicodeDecodeError:
            self.malformed = self.malformed or "a line of the run's output is not valid UTF-8"
            return "malformed"
        line = line.strip()
        if not line:
            return "blank"
        try:
            obj = json.loads(line)
        except ValueError:
            self.malformed = self.malformed or "a line of the run's output is not JSON"
            return "malformed"
        if not isinstance(obj, dict):
            return "other"
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init":
            self.init = True
            return "init"
        if kind == "user" and obj.get("isReplay"):
            text = text_of((obj.get("message") or {}).get("content"))
            if self.expect is None or digest(text) == self.expect:
                self.replayed = True
                return "replay"
            return "other_replay"
        if kind == "rate_limit_event":
            info = obj.get("rate_limit_info")
            if isinstance(info, dict):
                self.rate_limit = _keep_rate_limit(info)
            return "rate_limit"
        if kind == "result":
            self.results.append(obj)
            return "result"
        return "other"

    @property
    def result(self) -> dict[str, Any] | None:
        return self.results[-1] if self.results else None

    def denials(self) -> int:
        return sum(len(r.get("permission_denials") or []) for r in self.results)


def _keep_rate_limit(info: dict[str, Any]) -> dict[str, Any]:
    """The fields onloop acts on and shows, nothing else."""
    out = {k: info.get(k) for k in ("status", "resetsAt", "rateLimitType", "overageStatus", "isUsingOverage")}
    windows = info.get("unifiedWindows")
    if isinstance(windows, dict):
        out["utilization"] = {
            str(k): v.get("utilization")
            for k, v in windows.items()
            if isinstance(v, dict) and "utilization" in v
        }
    return out


@dataclass(frozen=True)
class Usage:
    limited: bool  # the plan's limit is reached: stop until `resets_at`
    warning: bool  # close to it
    overage: bool  # the run used spend beyond the subscription
    resets_at: int | None  # epoch seconds


def usage(rate_limit: dict[str, Any] | None, result: dict[str, Any] | None) -> Usage:
    """What the limits say. Only structured fields are read, never the English text of a result."""
    rl = rate_limit or {}
    status = rl.get("status")
    api = str((result or {}).get("api_error_status") or "")
    limited = status == "rejected" or api == "429"
    reset = rl.get("resetsAt")
    return Usage(
        limited=limited,
        warning=status == "allowed_warning",
        overage=rl.get("isUsingOverage") is True,
        resets_at=int(reset) if isinstance(reset, (int, float)) and not isinstance(reset, bool) else None,
    )
