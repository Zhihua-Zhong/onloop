"""Shared fixtures: a small policy and a clean pull request, built by hand. No network, no repository."""
import copy

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
    return {"id": id_, "author": {"login": login, "type": type_}, "state": state, "commit": commit,
            "submitted_at": at}


@pytest.fixture
def facts():
    return {"number": 7, "state": "OPEN", "draft": False, "base": "main", "head": HEAD, "title": "[builder] fix",
            "body": "", "commit_messages": [], "reviews": [review()], "dismissals": [],
            "checks": [{"name": "ci", "conclusion": "SUCCESS"}]}
