"""Shared fixtures: a small policy and a clean pull request, built by hand. No network, no repository."""

import copy
import datetime as dt

import pytest

from onloop import policy as pol

BASE = {
    "human": "owner-a",
    "reviewer": "rev-bot",
    "agents": "agent-bot",
    "roles": {"builder": {"codeowner": "@org/builders"}, "lead": {}},
    "classes": {"copy_edits": "reviewer"},
    "tiers": ["routine", "full"],
    "paths": {
        "src/": {"owner": "builder"},
        "core/": {"owner": "builder", "tier": "full"},
        "docs/claims/": {"owner": "lead", "reserved": "claims"},
        "docs/copy/": {"owner": "lead", "reserved": "copy_edits"},
        "onloop.yaml": {"owner": "lead", "tier": "full", "reserved": "authority_widening"},
    },
    "required_checks": ["ci"],
}

HEAD = "a" * 40


@pytest.fixture
def raw():
    return copy.deepcopy(BASE)


@pytest.fixture
def policy(raw):
    return pol.from_dict(raw)


def review(login="rev-bot", type_="Bot", state="APPROVED", commit=HEAD, id_=1, at="2026-01-01T00:00:00Z"):
    return {
        "id": id_,
        "author": {"login": login, "type": type_},
        "state": state,
        "commit": commit,
        "submitted_at": at,
    }


@pytest.fixture
def facts():
    return {
        "number": 7,
        "state": "OPEN",
        "draft": False,
        "base": "main",
        "head": HEAD,
        "title": "[builder] fix",
        "body": "",
        "commit_messages": [],
        "reviews": [review()],
        "dismissals": [],
        "checks": [{"name": "ci", "conclusion": "SUCCESS"}],
    }


# A fixed clock for the stores' tests: the repository name and a clock a test moves by hand.
REPO = "o/r"
CLOCK_T0 = dt.datetime(2026, 10, 12, 0, 0, tzinfo=dt.UTC)


class Clock:
    def __init__(self):
        self.t = CLOCK_T0

    def __call__(self):
        return self.t.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def seconds(self, s):
        self.t += dt.timedelta(seconds=s)


class SlotLedger:
    """For the slot tests: an overlay ledger whose `events()` answers in the old store's shape (kind, subject, payload,
    at), so a test reads the slot events the same way it did when the slots lived in `onloop.db`."""

    def __new__(cls, *a, **kw):
        from onloop.overlay.ledger import Ledger

        class _SlotLedger(Ledger):
            def events(self, *, kind=None, subject=None):
                where, args = [], []
                if kind:
                    where.append("kind = ?")
                    args.append(kind)
                if subject:
                    where.append("subject_ref = ?")
                    args.append(subject)
                sql = (
                    "SELECT * FROM events"
                    + (" WHERE " + " AND ".join(where) if where else "")
                    + " ORDER BY seq"
                )
                rows = self._rows(sql, tuple(args))
                return [{**r, "subject": r["subject_ref"], "at": r["at"] or r["ts_system"]} for r in rows]

        return _SlotLedger(*a, **kw)
