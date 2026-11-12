"""onloopd's HTTP server over a fresh demo ledger, for the browser tests and the screenshots.

    python e2e/demo_server.py [PORT]

It builds the demo ledger (tools/demo_ledger.py, generic sample data) as of now in a new directory under web/.demo/
(gitignored, inside the checkout, so a server that is killed leaves nothing outside it), writes
a heartbeat next to it every few seconds as onloopd does, and serves it with the real server (its Host check, its CSP,
its routes, the message route the compose box posts to) and the built UI (src/onloop/ui). Loopback only. It stops on Ctrl+C or when its parent closes it.
"""

from __future__ import annotations

import os
import pathlib
import secrets
import shutil
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import demo_ledger  # noqa: E402

from onloop import events as ev  # noqa: E402
from onloop.daemon import main as daemon  # noqa: E402
from onloop.daemon import messages_route  # noqa: E402
from onloop.daemon import server as srv  # noqa: E402
from onloop.daemon.routes import Routes  # noqa: E402
from onloop.daemon.server import WriteRefused  # noqa: E402
from onloop.overlay.ledger import Ledger  # noqa: E402


def demo_carrier(tmp: pathlib.Path) -> object:
    """The demo's carrier: block, as the demo ledger's story has it (builder and designer take messages; the reviewer
    is carried but has opted in to nothing)."""
    from onloop.carrier import config as carrier_config

    problems: list[str] = []
    roles = {
        name: {
            "session_id": f"00000000-0000-4000-8000-00000000000{n}",
            "cwd": str(tmp),
            "tools": ["Read"],
            "opt_in": opt,
        }
        for n, (name, opt) in enumerate(
            (("builder", ["message", "corrective"]), ("designer", ["message", "event"]), ("reviewer", []))
        )
    }
    cfg = carrier_config.parse({"roles": roles}, problems)
    assert not problems, problems
    return cfg


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8792
    tmp = HERE.parent / ".demo" / str(os.getpid())
    tmp.mkdir(parents=True)
    try:
        path = tmp / "ledger.db"
        demo_ledger.build(path)
        beat = demo_ledger.heartbeat(path, ev.now_ts())
        reader = daemon.LockedReader(path)
        writer_ledger = Ledger(path)
        routes = Routes(writer_ledger, threading.Lock())
        cfg = demo_carrier(tmp)
        # A browser test can make the demo look like a default install, with no carrier: block, by writing
        # `demo-no-carrier` next to the ledger (and removing it after); the real onloopd reads onloopd.yaml.
        off = tmp / "demo-no-carrier"
        take = messages_route.make()

        def messages(led, rest, body):
            if off.exists():
                raise WriteRefused(
                    404, "messages are off: no carrier configured (a carrier: block in onloopd.yaml)"
                )
            return take(led, rest, body)

        routes.add("/messages/", messages)
        server = srv.make_server(
            reader,
            port=port,
            heartbeat=beat,
            writer=routes,
            token=secrets.token_urlsafe(32),
            carrier=lambda: None if off.exists() else cfg,
        )
        stop = threading.Event()

        def beating() -> None:
            while not stop.wait(5):
                demo_ledger.heartbeat(path, ev.now_ts())

        threading.Thread(target=beating, daemon=True).start()
        print(f"demo onloopd on http://localhost:{port}, ledger {path}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.server_close()
            reader.close()
            writer_ledger.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # its own directory, made above
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
