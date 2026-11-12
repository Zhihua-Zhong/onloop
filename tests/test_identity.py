"""One identity form, normalised at the boundary, compared only with same_identity."""

import pytest

from onloop import identity


@pytest.mark.parametrize(
    "raw,canon",
    [
        ("octocat", "octocat"),
        ("OctoCat", "octocat"),
        ("octocat/User", "octocat/User"),
        ("octocat/user", "octocat/User"),
        ("some-app[bot]/Bot", "some-app/Bot"),
        ("  alice / Bot ", "alice/Bot"),
    ],
)
def test_canonical_form(raw, canon):
    assert identity.canonical(raw) == canon


@pytest.mark.parametrize(
    "a,b,same",
    [
        ("agent-x", "agent-x/Bot", True),
        ("agent-x/Bot", "agent-x", True),
        ("Agent-X", "agent-x/Bot", True),
        ("agent-x/Bot", "agent-x/User", False),
        ("agent-x", "agent-y", False),
        ("agent-x/Bot", "agent-x/bot", True),
        ("agent", "agent-x", False),
    ],
)
def test_same_identity(a, b, same):
    assert identity.same_identity(a, b) is same
    assert identity.same_identity(b, a) is same


@pytest.mark.parametrize("bad", ["", "   ", "/Bot", "[bot]"])
def test_an_empty_identity_is_refused(bad):
    with pytest.raises(ValueError):
        identity.parse(bad)


def test_is_login_checks_the_type_only_when_given():
    assert identity.is_login("owner-a", "owner-a") and identity.is_login("OWNER-A/User", "owner-a", "User")
    assert not identity.is_login("owner-a/Bot", "owner-a", "User")
