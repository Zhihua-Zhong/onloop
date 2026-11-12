"""onloopd's write routes: what a POST to `/hooks/...` or `/sources/...` appends to the ledger.

    Routes(ledger, lock)          the server's Writer: dispatches a path to its handler, under the one writer lock
    Routes.add(prefix, handler)   handler(ledger, rest_of_path, body) -> dict, appending events through the ledger

Every event a route appends gets the ledger's own `ts_system`; a time the sender claims is kept in the payload as
`claimed_at` and never used as the event's time (story 6). A route raises WriteRefused for a body it will not take.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from ..overlay.ledger import Ledger
from .server import WriteRefused

Handler = Callable[[Ledger, str, dict[str, Any]], dict[str, Any]]


class Routes:
    def __init__(self, ledger: Ledger, lock: threading.Lock):
        self._ledger = ledger
        self._lock = lock
        self._routes: list[tuple[str, Handler]] = []

    def add(self, prefix: str, handler: Handler) -> None:
        if not prefix.startswith(("/hooks/", "/sources/")) or not prefix.endswith("/"):
            raise ValueError(f"a write route is under /hooks/ or /sources/ and ends with '/': {prefix!r}")
        self._routes.append((prefix, handler))

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        for prefix, handler in sorted(self._routes, key=lambda r: -len(r[0])):
            if path.startswith(prefix):
                with self._lock:
                    return handler(self._ledger, path[len(prefix) :], body)
        raise WriteRefused(404, f"no write route for {path}")
