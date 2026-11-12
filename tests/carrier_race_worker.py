"""One carrier process for the lease race (test_carrier_race.py): python -m tests.carrier_race_worker LEDGER N SEED NAME

It opens the shared ledger, carries one role on the shared session id with the fake CLI, and N times: waits a random
moment, queues a message, and runs one carrier pass. Its `claude agents --json` is the fake's, which lists what the
file FAKE_AGENTS holds. It prints one JSON line of what it did.
"""

from __future__ import annotations

import json
import os
import pathlib
import random
import sys

from onloop.carrier import deliveries
from onloop.carrier.loop import Carrier
from onloop.overlay.ledger import Ledger

from . import carrier_helpers as h


def main() -> int:
    path, n, seed, name = pathlib.Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    rnd = random.Random(seed)
    tmp = path.parent
    cfg = h.config(
        tmp,
        roles={"builder": h.role(cwd=tmp, runs_per_hour=1000, runs_per_day=10000, wall_per_day="24h")},
        wall_per_day="24h",
    )
    env = {**h.env(tmp), **{k: v for k, v in os.environ.items() if k.startswith("FAKE_")}}
    led = Ledger(path)
    c = Carrier(led, cfg, tmp, environ=env, login=h.LoggedIn(), hook_wait_s=2.0)
    c.boot()
    h.start(c)
    acted, waited = 0, {}
    for i in range(n):
        import time

        time.sleep(rnd.random() * 0.05)
        with c.w() as w:
            deliveries.create(
                w,
                role="builder",
                kind="message",
                body=f"{name} {i}",
                retention_s=86400,
                author="human",
                channel="cli",
            )
        rep = c.tick()
        acted += len(rep.acted)
        for why in rep.waiting.values():
            key = why.split(":")[0]
            waited[key] = waited.get(key, 0) + 1
    led.close()
    print(json.dumps({"name": name, "acted": acted, "waited": waited}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
