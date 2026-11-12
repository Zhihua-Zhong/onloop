"""A carrier process that is killed mid-run (test_carrier_crash.py): python -m tests.carrier_crash_worker LEDGER

It carries one role with the fake CLI in `hang` mode, queues one message, and runs one pass, which never ends by
itself: the test kills this process by its pid while the run is live.
"""

from __future__ import annotations

import os
import pathlib
import sys

from onloop.carrier import deliveries
from onloop.carrier.loop import Carrier
from onloop.overlay.ledger import Ledger

from . import carrier_helpers as h


def main() -> int:
    path = pathlib.Path(sys.argv[1])
    tmp = path.parent
    env = {**h.env(tmp), **{k: v for k, v in os.environ.items() if k.startswith("FAKE_")}}
    c = Carrier(Ledger(path), h.config(tmp), tmp, environ=env, login=h.LoggedIn())
    c.boot()
    h.start(c)
    with c.w() as w:
        deliveries.create(
            w, role="builder", kind="message", body="hello", retention_s=86400, author="human", channel="cli"
        )
    c.tick()
    return 0


if __name__ == "__main__":
    sys.exit(main())
