"""Shared setup for the carrier's tests: a ledger, a carried role, and the fake CLI (fixtures/fake_claude_carrier.py)."""

from __future__ import annotations

import pathlib
import sys

from onloop.carrier import config as carrier_config
from onloop.carrier import listing
from onloop.carrier.loop import Carrier
from onloop.overlay.ledger import Ledger

FAKE = pathlib.Path(__file__).parent / "fixtures" / "fake_claude_carrier.py"
SRC = str(pathlib.Path(__file__).resolve().parent.parent / "src")
SID = "00000000-0000-4000-8000-0000000000b1"
SID2 = "00000000-0000-4000-8000-0000000000b2"


def role(name="builder", sid=SID, cwd=".", opt_in=("message",), **kw):
    raw = {"session_id": sid, "cwd": str(cwd), "tools": ["Read", "Edit"], "opt_in": list(opt_in), **kw}
    return raw


def config(tmp_path, roles=None, **top):
    problems: list[str] = []
    raw = {"roles": roles if roles is not None else {"builder": role(cwd=tmp_path)}, **top}
    cfg = carrier_config.parse(raw, problems)
    assert not problems, problems
    return carrier_config.CarrierConfig(**{**cfg.__dict__, "claude": (sys.executable, str(FAKE))})


class Listings:
    """A stand-in for `claude agents --json`, already filtered as listing.read does."""

    def __init__(self, rows=None, ok=True):
        self.rows, self.ok, self.calls = rows or [], ok, 0

    def __call__(self, bound):
        self.calls += 1
        if not self.ok:
            return listing.Listing(ok=False, error="claude agents --json exited 1")
        return listing.Listing(ok=True, holders=listing.held(self.rows, bound))


class LoggedIn:
    def __init__(self, why=None):
        self.why, self.calls = why, 0

    def check(self):
        self.calls += 1
        return self.why

    def forget(self):
        pass


def env(tmp_path, **fake):
    out = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
        "FAKE_SRC": SRC,
        "FAKE_HOOK_LEDGER": str(tmp_path / "ledger.db"),
        "FAKE_HOOK_ROLE": "builder",
        "PYTHONPATH": SRC,
    }
    out.update({k: str(v) for k, v in fake.items()})
    return out


def carrier(tmp_path, cfg=None, *, listings=None, login=None, environ=None, **kw):
    led = Ledger(tmp_path / "ledger.db")
    c = Carrier(
        led,
        cfg or config(tmp_path),
        tmp_path,
        environ=environ if environ is not None else env(tmp_path),
        read_listing=listings or Listings(),
        login=login or LoggedIn(),
        hook_wait_s=kw.pop("hook_wait_s", 5.0),
        **kw,
    )
    c.boot()
    return c


def start(c):
    with c.w() as led:
        led.append(
            {
                "id": "start-1",
                "source": "cli",
                "kind": "carrier.enabled",
                "subject_ref": "carrier",
                "payload": {"by": "test"},
            }
        )


def say(c, text, role="builder"):
    from onloop.carrier import deliveries

    with c.w() as led:
        return deliveries.create(
            led,
            role=role,
            kind="message",
            body=text,
            retention_s=86400,
            author="human",
            channel="cli",
            origin="human",
            source="cli",
        )


def delivery(c, did):
    return c.led.select("SELECT * FROM deliveries WHERE id = ?", (did,))[0]


def kinds(c, prefix=""):
    return [
        r["kind"]
        for r in c.led.select("SELECT kind FROM events ORDER BY seq")
        if r["kind"].startswith(prefix)
    ]
