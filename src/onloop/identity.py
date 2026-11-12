"""One form for who someone is.

GitHub names an actor by a login and a type (`octocat/User`, `some-app/Bot`). People type a bare login, and an agent
session may be only a role name. Comparing such strings with `==` is how an identity check gets bypassed (`agent-x/Bot`
asking, `agent-x` granting). So an identity is parsed once, at the boundary where it comes in, stored in its canonical
form, and compared only with `same_identity`.

`login` is compared without case (GitHub logins are case-insensitive). The type is optional: an identity without one
matches any type, an identity with one matches only that type. That is the safe direction for the checks that use this:
a self-grant, a renewal by the holder, a release by the holder.
"""

from __future__ import annotations

from typing import NamedTuple


class Identity(NamedTuple):
    login: str
    type: str | None = None


def parse(who: str) -> Identity:
    """`octocat`, `octocat/User` or `app[bot]/Bot` -> an Identity with a lowercase login and a capitalised type."""
    text = (who or "").strip()
    if not text:
        raise ValueError("an identity cannot be empty")
    login, _, kind = text.partition("/")
    login = login.strip().lower().removesuffix("[bot]")
    if not login:
        raise ValueError(f"an identity needs a login: {who!r}")
    kind = kind.strip()
    return Identity(login, kind[:1].upper() + kind[1:].lower() if kind else None)


def canonical(who: str) -> str:
    """The stored form: `login` or `login/Type`."""
    i = parse(who)
    return f"{i.login}/{i.type}" if i.type else i.login


def same_identity(a: str | Identity, b: str | Identity) -> bool:
    x = parse(a) if isinstance(a, str) else a
    y = parse(b) if isinstance(b, str) else b
    return x.login == y.login and (x.type is None or y.type is None or x.type == y.type)


def is_login(who: str, login: str, type_: str | None = None) -> bool:
    """Is `who` the account `login` (of `type_` when given)?"""
    return same_identity(who, Identity(parse(login).login, type_))


def same_role(a: object, b: object) -> bool:
    """Whether two role names are the same role. The one place roles are compared, so a change to how (case, spacing)
    reaches every comparison at once."""
    return isinstance(a, str) and isinstance(b, str) and bool(a.strip()) and a.strip() == b.strip()
