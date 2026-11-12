"""A human grant anchored on a GitHub comment is checked against GitHub, live, and every way to forge one is refused."""

import copy

import pytest

from onloop import policy as pol
from onloop import slots

from .conftest import BASE
from .conftest import SlotLedger as Store
from .test_slot_capacity import REPO, Clock

HUMAN = "owner-a"
URL = "https://github.com/o/r/pull/12#issuecomment-555"
BASE_COMMENT = {
    "id": 555,
    "issue_url": "https://api.github.com/repos/o/r/issues/12",
    "user": {"login": HUMAN, "type": "User"},
    "body": "/grant @:1",
    "created_at": "2026-10-12T00:10:00Z",
    "updated_at": "2026-10-12T00:10:00Z",
}


class Client:
    def __init__(self, comment=None, error=None):
        self.comment, self.error, self.asked = comment, error, []

    def issue_comment(self, comment_id):
        self.asked.append(comment_id)
        if self.error:
            raise self.error
        return self.comment


def comment(**over):
    return {**BASE_COMMENT, **over}


def policy(human_grant="anchored"):
    raw = copy.deepcopy(BASE)
    raw["slots"] = {
        "db": {"paths": ["db/"], "approver": "human", "human_grant": human_grant},
        "host": {"paths": ["src/"], "approver": "human"},
    }
    return pol.from_dict(raw)


@pytest.fixture
def st(tmp_path):
    with Store(tmp_path / "a.db", clock=Clock()) as s:
        yield s


def asked(st, p, slot="db", ref="pr#12", holder="builder-1"):
    r = slots.request(st, REPO, p, slot, holder=holder, role="builder", reason="migrate", ref=ref)
    return r["request"]


def verify(st, p, c, url=URL, rid=1, repo=REPO):
    """`@` in the comment body stands for this database's own instance id."""
    c = c and {**c, "body": (c.get("body") or "").replace("@:", st.instance() + ":")}
    return slots.verify_anchor(Client(c), repo, p, slots.request_row(st, rid), url, st.instance())


# -- the one that works --------------------------------------------------------------------------------------------------------------------


def test_the_humans_own_grant_comment_on_the_requests_pull_request_is_accepted_and_recorded(st):
    p = policy()
    asked(st, p)
    evidence = verify(st, p, comment())
    gid = slots.grant(st, REPO, p, 1, anchor=evidence)
    payload = st.events(kind="slot.granted")[0]["payload"]
    assert (
        payload["anchor"]["comment"] == 555 and payload["anchor"]["updated_at"] == "2026-10-12T00:10:00.000Z"
    )
    assert payload["granted_by"] == f"{HUMAN}/User" and gid == payload["grant"]


def test_an_issue_ref_works_the_same(st):
    p = policy()
    asked(st, p, ref="issue#12")
    assert verify(st, p, comment())["comment"] == 555


def test_the_repository_name_is_compared_without_case(st):
    p = policy()
    asked(st, p)
    assert verify(st, p, comment(issue_url="https://api.github.com/repos/O/R/issues/12"))["comment"] == 555


# -- every way to forge one ------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "over,why",
    [
        ({"user": {"login": HUMAN, "type": "Bot"}}, "not by the human"),
        ({"user": {"login": "agent-bot", "type": "Bot"}}, "not by the human"),
        ({"user": {"login": "someone-else", "type": "User"}}, "not by the human"),
        ({"user": {"login": f"{HUMAN}-2", "type": "User"}}, "not by the human"),
        ({"user": None}, "not by the human"),
        ({"body": "> /grant @:1"}, "no line"),
        ({"body": "```\n/grant @:1\n```"}, "no line"),
        ({"body": "~~~\n/grant @:1\n~~~"}, "no line"),
        ({"body": "<!-- /grant @:1 -->"}, "no line"),
        ({"body": "  /grant @:1"}, "no line"),
        ({"body": "please /grant @:1"}, "no line"),
        ({"body": "/grant @:2"}, "no line"),
        ({"body": "/grant @:11"}, "no line"),
        ({"body": ""}, "no line"),
        (
            {"created_at": "2026-10-11T23:59:59Z", "updated_at": "2026-10-11T23:59:59Z"},
            "older than the request",
        ),
        ({"updated_at": "2026-10-12T00:20:00Z"}, "edited"),
        (
            {"issue_url": "https://api.github.com/repos/o/other/issues/12"},
            "not on a pull request or issue of o/r",
        ),
        ({"issue_url": "https://api.github.com/repos/o/r/issues/13"}, "on #13"),
        ({"issue_url": "https://api.github.com/repos/o/r/pulls/12"}, "not on a pull request or issue"),
        ({"issue_url": "https://example.test/repos/o/r/issues/12"}, "not on a pull request or issue"),
        ({"issue_url": None}, "not on a pull request or issue"),
        ({"id": 556}, "different comment"),
    ],
)
def test_a_comment_that_is_not_the_humans_grant_for_this_request_is_refused(st, over, why):
    p = policy()
    asked(st, p)
    with pytest.raises(slots.SlotError, match=why):
        verify(st, p, comment(**over))


def test_the_url_names_the_comment_and_nothing_else(st):
    p = policy()
    asked(st, p)
    other_pr_url = "https://github.com/o/r/pull/99#issuecomment-555"
    assert (
        verify(st, p, comment(), url=other_pr_url)["comment"] == 555
    )  # the URL's own path is not what is judged
    with pytest.raises(slots.SlotError, match="on #13"):
        verify(st, p, comment(issue_url="https://api.github.com/repos/o/r/issues/13"), url=URL)
    for bad in (
        "https://github.com/o/r/pull/12",
        "",
        "https://github.com/o/r/pull/12#issuecomment-",
        "#issuecomment-5x",
    ):
        with pytest.raises(slots.SlotError, match="URL of a comment"):
            verify(st, p, comment(), url=bad)


@pytest.mark.parametrize("ref", ["main", "pr#", "pr#x", "PR#12", "branch#12", "pr#12 ", "task-3"])
def test_an_anchored_slot_refuses_any_ref_that_is_not_a_pull_request_or_issue(st, ref):
    p = policy()
    asked(st, p, ref=ref)
    with pytest.raises(slots.SlotError, match="pr#N or issue#N"):
        verify(st, p, comment())


def test_a_comment_that_cannot_be_read_is_not_an_anchor(st):
    p = policy()
    asked(st, p)
    with pytest.raises(slots.SlotError, match="could not read"):
        slots.verify_anchor(
            Client(error=RuntimeError("404")), REPO, p, slots.request_row(st, 1), URL, st.instance()
        )


# -- which database the line is for -------------------------------------------------------------------------------------------------------------


def test_a_comment_for_another_database_is_refused_even_with_the_right_request_number(st, tmp_path):
    p = policy()
    asked(st, p)
    with Store(tmp_path / "other.db", clock=Clock()) as other:
        assert other.instance() != st.instance()
        theirs = f"/grant {other.instance()}:1"
    with pytest.raises(slots.SlotError, match="another database"):
        slots.verify_anchor(
            Client(comment(body=theirs)), REPO, p, slots.request_row(st, 1), URL, st.instance()
        )


def test_the_old_form_of_the_line_is_refused_and_the_message_says_what_to_write(st):
    p = policy()
    asked(st, p)
    with pytest.raises(slots.SlotError, match="old form") as e:
        slots.verify_anchor(
            Client(comment(body="/grant 1")), REPO, p, slots.request_row(st, 1), URL, st.instance()
        )
    assert f"/grant {st.instance()}:1" in str(e.value)


def test_the_instance_id_is_sixteen_hex_digits_and_survives_reopening(tmp_path):
    with Store(tmp_path / "i.db") as s:
        first = s.instance()
    assert len(first) == 16 and set(first) <= set("0123456789abcdef")
    with Store(tmp_path / "i.db") as s:
        assert s.instance() == first


def test_a_new_request_on_an_anchored_slot_says_the_line_to_write(st):
    p = policy()
    r = slots.request(st, REPO, p, "db", holder="b", role="builder", reason="m", ref="pr#12")
    assert r["grant_line"] == f"/grant {st.instance()}:1"
    r2 = slots.request(st, REPO, p, "db", holder="d", role="builder", reason="m", ref="pr#13")
    assert r2["grant_line"] == f"/grant {st.instance()}:2"
    h = slots.request(st, REPO, p, "host", holder="c", role="builder", reason="m", ref="pr#12")
    assert "grant_line" not in h


def test_an_anchor_from_another_database_does_not_grant(st):
    p = policy()
    asked(st, p)
    evidence = {**verify(st, p, comment()), "instance": "0" * 16}
    with pytest.raises(slots.SlotError, match="use --anchor"):
        slots.grant(st, REPO, p, 1, anchor=evidence)


# -- the grant itself ---------------------------------------------------------------------------------------------------------------------------


def test_an_anchored_slot_refuses_a_bare_by_and_a_missing_anchor(st):
    p = policy()
    asked(st, p)
    with pytest.raises(slots.SlotError, match="not --by"):
        slots.grant(st, REPO, p, 1, by=HUMAN)
    with pytest.raises(slots.SlotError, match="use --anchor"):
        slots.grant(st, REPO, p, 1)
    assert st.conn.execute("SELECT count(*) FROM slot_grants").fetchone()[0] == 0


def test_an_anchor_made_for_another_request_does_not_grant_this_one(st):
    p = policy()
    asked(st, p)
    asked(st, p, ref="pr#13", holder="builder-2")
    evidence = verify(st, p, comment())
    with pytest.raises(slots.SlotError, match="use --anchor"):
        slots.grant(st, REPO, p, 2, anchor=evidence)


def test_an_attested_slot_still_takes_a_bare_by_and_needs_one(st):
    p = policy()
    asked(st, p, slot="host", ref="whatever")
    with pytest.raises(slots.SlotError, match="--by"):
        slots.grant(st, REPO, p, 1)
    assert slots.grant(st, REPO, p, 1, by=HUMAN) == 1
    assert "anchor" not in st.events(kind="slot.granted")[0]["payload"]


def test_a_default_slot_is_attested():
    assert policy().slots["host"]["human_grant"] == "attested"


# -- the policy ----------------------------------------------------------------------------------------------------------------------------------


def test_the_policy_checks_the_field():
    raw = copy.deepcopy(BASE)
    raw["slots"] = {"x": {"paths": ["x/"], "approver": "human", "human_grant": "maybe"}}
    assert any("human_grant must be" in p for p in pol.validate(raw))
    raw["slots"] = {"x": {"paths": ["x/"], "approver": "auto", "human_grant": "anchored"}}
    assert any("needs approver: human" in p for p in pol.validate(raw))
    raw["slots"] = {"x": {"paths": ["x/"], "approver": "role:lead", "human_grant": "anchored"}}
    assert any("needs approver: human" in p for p in pol.validate(raw))


def test_stopping_the_anchor_is_a_loosening_and_adding_it_is_not():
    a, b = policy("anchored"), policy("attested")
    assert any("anchored" in g for g in pol.loosening(a, b))
    assert not pol.loosening(b, a) and not pol.loosening(a, a)
