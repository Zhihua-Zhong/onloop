"""The supervisor's records: an append-only JSONL step log, an alert log, and an atomically written heartbeat file.

Each line of `steps.jsonl` is one JSON object with a `type`:

    supervisor.started / supervisor.stopped     pid, host, mode
    step.started                                step_id, attempt, role, item_id, item_version, round, mode, pid, deadline, command
    step.killed                                 step_id, attempt, pid, how, reason (overrun | interrupted): an event, never silence
    step.ended                                  step_id, attempt, outcome, exit_code, duration_s, next_due, and the rest of the result
    source.failed                               the event source could not be read; nothing was scheduled from it

`write(record)` is idempotent for step records: a second record with the same (type, step_id, attempt) is not appended
and the call returns False. So a step that is retried by an outer engine, or a supervisor restarted half-way, never
writes a step twice. Lines are flushed and synced as they are written; a reader never sees half a line, except the
last one after a crash, which `read` skips and reports.

`heartbeat.json` is rewritten through a temporary file and a rename, so a watchdog never reads half of it.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
from collections.abc import Iterable
from typing import Any

from .. import fsutil

STEP_TYPES = ("step.started", "step.killed", "step.ended")


def _key(rec: dict[str, Any]) -> tuple[str, str, int] | None:
    if rec.get("type") in STEP_TYPES and rec.get("step_id"):
        return (rec["type"], rec["step_id"], int(rec.get("attempt") or 1))
    return None


class StepLog:
    """An append-only JSONL file with an index of the step records already in it.

    The file is the record, not this object: when its size differs from what this object last read (another worker,
    another thread's instance or a restart wrote to it), it is read again before any answer or write."""

    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)
        self._lock = threading.Lock()
        self.bad_lines = 0
        self._size = -1
        self._records: list[dict[str, Any]] = []
        self._keys: set[tuple[str, str, int]] = set()
        self._refresh()

    def _refresh(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        if size == self._size:
            return
        self.bad_lines = 0
        self._records = list(self._load())
        self._keys = {k for k in map(_key, self._records) if k}
        self._size = size

    def _load(self) -> Iterable[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                self.bad_lines += 1
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out

    @property
    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            self._refresh()
            return list(self._records)

    def has(self, type_: str, step_id: str, attempt: int = 1) -> bool:
        with self._lock:
            self._refresh()
            return (type_, step_id, attempt) in self._keys

    def write(self, record: dict[str, Any]) -> bool:
        """Append one record, unless a step record with its key is already there. Returns whether it was appended."""
        line = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if "\n" in line:
            raise ValueError("a record must serialise to one line")
        key = _key(record)
        with self._lock:
            self._refresh()
            if key and key in self._keys:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._records.append(record)
            if key:
                self._keys.add(key)
            self._size = self.path.stat().st_size
            return True


def write_step_record(path: str | pathlib.Path, record: dict[str, Any]) -> bool:
    """Append one record to the log at `path`, idempotent on (type, step_id, attempt)."""
    return StepLog(path).write(record)


def write_json_atomic(path: str | pathlib.Path, obj: Any) -> None:
    fsutil.write_atomic(path, json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def read_json(path: str | pathlib.Path) -> Any:
    p = pathlib.Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# -- what the log says now ------------------------------------------------------------------------------------------


def step_view(records: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    """Per step id (in one mode): its attempts, whether one is open (started, not ended), the last outcome and end.

    Returns {"steps": {step_id: {...}}, "last_start": {role: at}, "last_item": {(role, item_id): {...}}}."""
    steps: dict[str, dict[str, Any]] = {}
    last_start: dict[str, str] = {}
    for r in records:
        if r.get("type") not in STEP_TYPES or r.get("mode") != mode:
            continue
        s = steps.setdefault(
            r["step_id"],
            {
                "role": r.get("role"),
                "item_id": r.get("item_id"),
                "item_version": r.get("item_version"),
                "round": r.get("round", 0),
                "attempts": 0,
                "open": {},
                "outcome": None,
                "ended_at": None,
                "final": True,
            },
        )
        att = int(r.get("attempt") or 1)
        if r["type"] == "step.started":
            s["attempts"] = max(s["attempts"], att)
            s["open"][att] = r
            if r.get("role") and (r.get("at") or "") > last_start.get(r["role"], ""):
                last_start[r["role"]] = r["at"]
        elif r["type"] == "step.ended":
            s["attempts"] = max(s["attempts"], att)
            s["open"].pop(att, None)
            s["outcome"], s["ended_at"] = r.get("outcome"), r.get("at")
            s["final"] = bool(r.get("final", True))
    last_item: dict[tuple[str, str], dict[str, Any]] = {}
    for sid, s in steps.items():
        k = (s["role"], s["item_id"])
        prev = last_item.get(k)
        if prev is None or (s["ended_at"] or "") > (prev["ended_at"] or ""):
            last_item[k] = {**s, "step_id": sid}
    return {"steps": steps, "last_start": last_start, "last_item": last_item}
