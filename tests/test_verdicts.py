"""The reviewer's verdict is read in one place. Three readers of reviews each forgot the author filter once."""

import pathlib
import re

import pytest

from onloop import verdicts

H1, H2 = "a" * 40, "b" * 40


def rev(
    state,
    author="rev-bot/Bot",
    commit=H1,
    head=None,
    at="2026-10-12T08:00:00.000Z",
    actor="same",
    kind="review.submitted",
):
    who = author if actor == "same" else actor
    return {
        "kind": kind,
        "subject": "pr#1",
        "at": at,
        "actor": who,
        "payload": {"author": author, "state": state, "commit": commit, "head": head or commit},
    }


# -- who counts -------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "author,counts",
    [
        ("rev-bot/Bot", True),
        ("rev-bot", True),
        ("rev-bot/User", False),
        ("someone/User", False),
        ("someone/Bot", False),
        (None, False),
        ("", False),
    ],
)
def test_only_the_reviewer_app_as_a_bot_is_the_reviewer(author, counts):
    assert verdicts.by_reviewer(rev("APPROVED", author=author), "rev-bot") is counts


def test_an_author_with_no_login_is_not_the_reviewer_and_does_not_raise():
    assert verdicts.by_reviewer(rev("APPROVED", author="/Bot"), "rev-bot") is False


def test_with_no_reviewer_given_every_author_counts():
    assert verdicts.by_reviewer(rev("APPROVED", author="someone/User"), None) is True


def test_the_actor_is_read_before_the_payload_author():
    e = rev("APPROVED", author="someone/User", actor="rev-bot/Bot")
    assert verdicts.by_reviewer(e, "rev-bot") is True


@pytest.mark.parametrize(
    "state,want",
    [
        ("APPROVED", "APPROVED"),
        ("CHANGES_REQUESTED", "CHANGES_REQUESTED"),
        ("COMMENTED", None),
        ("DISMISSED", None),
        ("PENDING", None),
        (None, None),
    ],
)
def test_only_approved_and_changes_requested_are_verdicts(state, want):
    assert verdicts.verdict_state(rev(state), "rev-bot") == want


def test_another_kind_of_event_is_not_a_verdict():
    assert verdicts.verdict_state(rev("APPROVED", kind="pr.opened"), "rev-bot") is None


def test_a_drive_by_approval_is_not_a_verdict():
    assert verdicts.verdict_state(rev("APPROVED", author="someone/User"), "rev-bot") is None


# -- the readers -------------------------------------------------------------------------------------------------------------------


def test_a_current_verdict_is_bound_to_the_head_it_was_submitted_on():
    assert verdicts.current_verdict(rev("APPROVED", commit=H1, head=H1), "rev-bot") == "APPROVED"
    assert verdicts.current_verdict(rev("APPROVED", commit=H1, head=H2), "rev-bot") is None


def test_the_latest_verdict_is_the_last_one_by_the_reviewer_whatever_head_it_names():
    evs = [
        rev("CHANGES_REQUESTED", commit=H1),
        rev("APPROVED", commit=H2),
        rev("COMMENTED", author="x/User", commit=H2),
    ]
    got = verdicts.latest_verdict(evs, "rev-bot")
    assert got["state"] == "APPROVED" and got["commit"] == H2
    assert verdicts.latest_verdict([rev("COMMENTED")], "rev-bot") is None


def test_a_comment_on_the_new_head_is_not_a_standing_verdict_on_it():
    evs = [
        rev("APPROVED", commit=H1),
        rev("COMMENTED", author="someone/User", commit=H2),
        rev("APPROVED", author="someone/User", commit=H2),
    ]
    assert verdicts.standing_verdict(evs, "rev-bot", H2) is None
    assert verdicts.standing_verdict(evs, "rev-bot", H1)["state"] == "APPROVED"


def test_the_standing_verdict_is_the_latest_one_that_names_the_head():
    evs = [rev("CHANGES_REQUESTED", commit=H1), rev("APPROVED", commit=H1)]
    assert verdicts.standing_verdict(evs, "rev-bot", H1)["state"] == "APPROVED"


def test_other_reviews_are_everything_that_is_not_the_reviewers_verdict():
    evs = [
        rev("APPROVED"),
        rev("COMMENTED"),
        rev("APPROVED", author="someone/User"),
        rev("APPROVED", kind="pr.opened"),
    ]
    assert [(o["author"], o["state"]) for o in verdicts.other_reviews(evs, "rev-bot")] == [
        ("rev-bot/Bot", "COMMENTED"),
        ("someone/User", "APPROVED"),
    ]


def test_changes_requested_counts_only_from_the_reviewer_and_only_on_that_head():
    evs = [
        rev("CHANGES_REQUESTED", author="someone/User"),
        rev("CHANGES_REQUESTED", commit=H2),
        rev("CHANGES_REQUESTED", commit=H1),
        rev("APPROVED", commit=H1),
    ]
    assert len(verdicts.changes_requested_on(evs, "rev-bot", H1)) == 1
    assert len(verdicts.changes_requested_on(evs, None, H1)) == 2  # no policy: every author, as before


# -- the boundary ------------------------------------------------------------------------------------------------------------------

ALLOWED = {"verdicts.py", "events.py", "intake.py"}
SUBSCRIBES = {"autogate.py"}  # lists the kind it wakes on; it reads facts, not these payloads


def test_no_module_but_the_helper_reads_review_events_directly():
    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "onloop"
    offenders = []
    for f in sorted(root.rglob("*.py")):
        if f.name in ALLOWED | SUBSCRIBES:
            continue
        if re.search(r"""['"]review\.submitted['"]""", f.read_text(encoding="utf-8")):
            offenders.append(f.relative_to(root).as_posix())
    assert offenders == [], f"read review events through onloop/verdicts.py: {offenders}"


def test_no_second_author_filter_is_left_in_kpi():
    src = (pathlib.Path(__file__).resolve().parent.parent / "src" / "onloop" / "kpi.py").read_text(
        encoding="utf-8"
    )
    assert "def _by(" not in src and "identity.is_login" not in src
