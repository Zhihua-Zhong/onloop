"""`cross_owner: reviewer_ack`: a path borrowed from another role needs the reviewer to quote the Cross-owner line."""

import copy

import pytest

from onloop import gate
from onloop import policy as pol
from onloop.classify import classify

from .conftest import BASE, HEAD, review

LINE = "Cross-owner: lead"
OLD = "b" * 40


def policy_for(mode):
    raw = copy.deepcopy(BASE)
    raw["automation"] = {"cross_owner": mode}
    return pol.from_dict(raw)


def facts_with(reviews, body=f"{LINE} - the copy needs a new example", **over):
    f = {
        "number": 7,
        "state": "OPEN",
        "draft": False,
        "base": "main",
        "head": HEAD,
        "title": "[builder] example",
        "body": body,
        "commit_messages": [],
        "reviews": reviews,
        "dismissals": [],
        "checks": [{"name": "ci", "conclusion": "SUCCESS"}],
    }
    f.update(over)
    return f


def p003(facts, mode="reviewer_ack"):
    p = policy_for(mode)
    got = gate.decide_refusals(facts, p, classify(["docs/copy/x.md"], p))
    return [r for r in got if r.code == "P003"]


def quoted(**over):
    r = review(**over)
    r["body"] = f"Approved.\n{LINE} - agreed\n"
    return r


def test_the_reviewers_quote_on_the_current_head_acknowledges_it():
    assert p003(facts_with([quoted()])) == []


def test_without_the_quote_it_is_refused_in_ack_mode_but_not_in_author_mode():
    f = facts_with([review()])
    assert len(p003(f)) == 1 and "has not quoted" in p003(f)[0].text
    assert p003(f, mode="author") == []


def test_the_pull_request_must_still_carry_the_line_itself():
    f = facts_with([quoted()], body="")
    assert len(p003(f)) == 1 and "add a" in p003(f)[0].text


# -- the three negative tests: a forged acknowledgement ----------------------------------------------------------------


def test_the_same_text_from_a_user_named_like_the_reviewer_is_refused():
    f = facts_with([review(), quoted(login="rev-bot", type_="User", id_=2)])
    assert len(p003(f)) == 1
    f2 = facts_with([quoted(login="rev-bot", type_="User")])
    assert len(p003(f2)) == 1


def test_the_same_text_from_the_agents_app_is_refused():
    f = facts_with([review(), quoted(login="agent-bot", type_="Bot", id_=2)])
    assert len(p003(f)) == 1


def test_a_quote_on_an_older_head_is_refused():
    f = facts_with([quoted(commit=OLD)])
    assert len(p003(f)) == 1
    f2 = facts_with([review(), quoted(commit=OLD, id_=2)])
    assert len(p003(f2)) == 1


# -- the acknowledgement is read live ---------------------------------------------------------------------------------


def test_a_body_edited_afterwards_to_drop_the_line_is_seen():
    before = facts_with([quoted()])
    after = facts_with([{**quoted(), "body": "Approved."}])
    assert p003(before) == [] and len(p003(after)) == 1


def test_a_later_change_request_by_the_reviewer_withdraws_it():
    withdrawn = quoted()
    changes = review(state="CHANGES_REQUESTED", id_=2, at="2026-01-02T00:00:00Z")
    assert len(p003(facts_with([withdrawn, changes]))) == 1


def test_the_owner_name_must_match_whole():
    f = facts_with([{**review(), "body": "Cross-owner: leader - not the same role"}])
    assert len(p003(f)) == 1


def test_the_borrower_who_owns_the_path_needs_nothing():
    p = policy_for("reviewer_ack")
    f = facts_with([review()], title="[lead] copy", body="")
    assert [r for r in gate.decide_refusals(f, p, classify(["docs/copy/x.md"], p)) if r.code == "P003"] == []


@pytest.mark.parametrize("mode", ["author", "reviewer_ack"])
def test_the_gate_applies_the_same_rule_to_a_whole_run(mode):
    from .test_gate import _world

    raw = copy.deepcopy(BASE)
    raw["automation"] = {"cross_owner": mode}
    git, client = _world(
        raw, ["docs/copy/x.md"], title="[builder] example", body=f"{LINE} - because", reviews=[review()]
    )
    code, notes = gate.run(7, client, git)
    assert (code == 1) == (mode == "reviewer_ack")


def test_the_reviewers_login_is_compared_without_case_in_every_rule():
    raw = copy.deepcopy(BASE)
    raw["reviewer"] = "Rev-Bot"  # the policy spells it differently from what GitHub reports
    raw["human"] = "Owner-A"
    raw["automation"] = {"cross_owner": "reviewer_ack"}
    p = pol.from_dict(raw)
    f = facts_with([quoted()])
    assert [
        r.code for r in gate.decide_refusals(f, p, classify(["docs/copy/x.md"], p)) if r.code == "P003"
    ] == []
    assert gate.is_human({"login": "owner-a", "type": "User"}, p) and not gate.is_human(
        {"login": "owner-a", "type": "Bot"}, p
    )
    assert gate.evidence(f, p).reviewer_approved


# -- only the standing verdict speaks ---------------------------------------------------------------------------------


def test_a_later_approval_without_the_line_withdraws_it():
    early = quoted()
    later = {**review(id_=2, at="2026-01-02T00:00:00Z"), "body": "Approved again, nothing else."}
    assert len(p003(facts_with([early, later]))) == 1
    assert p003(facts_with([later, early])) != []  # order in the list does not matter, the date does


def test_a_later_approval_that_has_the_line_acknowledges_it():
    early = {**review(), "body": "Approved."}
    later = {**quoted(), "id": 2, "submitted_at": "2026-01-02T00:00:00Z"}
    assert p003(facts_with([early, later])) == []


@pytest.mark.parametrize(
    "body",
    [
        f"```\n{LINE}\n```",
        f"~~~\n{LINE}\n~~~",
        f"````text\n```\n{LINE}\n````",
        f"> {LINE}",
        f"<!--\n{LINE}\n-->",
        f"    {LINE}",
        f"Approved. {LINE}",
    ],
)
def test_a_quoted_line_is_not_an_acknowledgement(body):
    f = facts_with([{**review(), "body": body}])
    assert len(p003(f)) == 1


def test_a_line_after_a_closed_fence_counts():
    f = facts_with([{**review(), "body": f"```\nquoted\n```\n{LINE} - ok"}])
    assert p003(f) == []


def test_an_unclosed_fence_swallows_the_rest():
    f = facts_with([{**review(), "body": f"```\n{LINE}"}])
    assert len(p003(f)) == 1


# -- the author's line is read the same way ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        f"```\n{LINE} - example\n```",
        f"~~~\n{LINE}\n~~~",
        f"> {LINE}",
        f"<!-- {LINE} -->",
        f"<!--\n{LINE}\n-->",
    ],
)
@pytest.mark.parametrize("mode", ["author", "reviewer_ack"])
def test_a_fenced_or_quoted_cross_owner_line_in_the_body_does_not_waive_ownership(body, mode):
    f = facts_with([quoted()], body=body)
    assert len(p003(f, mode=mode)) == 1 and "add a" in p003(f, mode=mode)[0].text


def test_a_fenced_line_in_a_commit_message_does_not_waive_ownership():
    f = facts_with([quoted()], body="", commit_messages=[f"fix\n\n```\n{LINE}\n```"])
    assert len(p003(f)) == 1
    g = facts_with([quoted()], body="", commit_messages=[f"fix\n\n{LINE} - real"])
    assert p003(g) == []


def test_a_real_line_after_a_fenced_example_still_counts():
    f = facts_with([quoted()], body=f"```\n{LINE}\n```\n{LINE} - the real one")
    assert p003(f) == []


def test_an_unterminated_html_comment_runs_to_the_end():
    f = facts_with([{**review(), "body": f"Approved.\n<!-- note\n{LINE} - hidden by GitHub"}])
    assert len(p003(f)) == 1
    g = facts_with([{**review(), "body": f"<!-- closed -->\n{LINE} - visible"}])
    assert p003(g) == []
    assert gate.live_lines("a\n<!-- never closed\nb") == ["a", ""]
