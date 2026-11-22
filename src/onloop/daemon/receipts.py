"""What onloopd's write path received since it started: every write request by route and answer, and every telemetry
record kept, stored or dropped as already logged (the onloop lead's rule after the dogfood's round 2, 2026-11-22: a
silent drop must never be invisible again). Counts only, in memory, never a body; `GET /health` shows them.

    requests  {route: {status: n}}       route is the path's first segment (`hooks`, `v1`, `ui`, ...); a refused write
                                         counts under its status (401, 403, 413, 415, ...), as does each 200
    records   {source: {...}}            for the routes that report them (OTLP logs), per record:
                received                   every record in the bodies
                not_kept_kind              of a kind onloop does not keep, dropped on purpose (only model requests,
                                           prompts and tool results are kept; the rest is never read)
                unplaced                   of a kept kind but with no session id, so it cannot be placed
                kept                       received less those two: what onloop takes
                stored                     of those, the ones logged
                duplicate                  of those, the ones whose id was already in the ledger (a redelivery)
    So received = not_kept_kind + unplaced + kept, and kept = stored + duplicate: nothing is lost unseen.
"""

from __future__ import annotations

import threading
from typing import Any

RECEIPT = (
    "_receipt"  # the key a write route's answer carries its record counts under; taken off before the answer
)
RECORD_FIELDS = ("received", "not_kept_kind", "unplaced", "kept", "stored", "duplicate")


class Receipts:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, dict[str, int]] = {}
        self._records: dict[str, dict[str, int]] = {}

    def request(self, route: str, status: int) -> None:
        with self._lock:
            by = self._requests.setdefault(route, {})
            by[str(status)] = by.get(str(status), 0) + 1

    def records(self, source: str, counts: dict[str, Any]) -> None:
        with self._lock:
            acc = self._records.setdefault(source, dict.fromkeys(RECORD_FIELDS, 0))
            for f in RECORD_FIELDS:
                v = counts.get(f)
                if isinstance(v, int) and v > 0:
                    acc[f] += v

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requests": {r: dict(sorted(v.items())) for r, v in sorted(self._requests.items())},
                "records": {s: dict(v) for s, v in sorted(self._records.items())},
            }


def route_of(path: str) -> str:
    """The route a write is counted under: the path's first segment."""
    return path.strip("/").split("/", 1)[0] or "/"
