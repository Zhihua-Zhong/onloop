"""onloopd's HTTP server over a fresh demo ledger, for the browser tests and the screenshots.

    python e2e/demo_server.py [PORT]

It builds the demo ledger (tools/demo_ledger.py, generic sample data) as of now in a new directory under web/.demo/
(gitignored, inside the checkout, so a server that is killed leaves nothing outside it), writes
a heartbeat next to it every few seconds as onloopd does, and serves it with the real server (its Host check, its CSP,
its read-only routes) and the built UI (src/onloop/ui). Loopback only. It stops on Ctrl+C or when its parent closes it.
"""

from __future__ import annotations

import os
import pathlib
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
from onloop.daemon import server as srv  # noqa: E402


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8792
    tmp = HERE.parent / ".demo" / str(os.getpid())
    tmp.mkdir(parents=True)
    try:
        path = tmp / "ledger.db"
        demo_ledger.build(path)
        beat = demo_ledger.heartbeat(path, ev.now_ts())
        reader = daemon.LockedReader(path)
        server = srv.make_server(reader, port=port, heartbeat=beat)
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # its own directory, made above
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
