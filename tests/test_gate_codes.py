"""Refusal codes: each reason has a stable code, the sentences are unchanged, and real decisions are recorded."""

import copy

import pytest

from onloop import audit, gate
from onloop import policy as pol
from onloop.classify import classify

from .conftest import HEAD, review
from .test_gate import _world


def codes(facts, policy, paths, **kw):
    return {r.code for r in gate.decide_refusals(facts, policy, classify(paths, policy), **kw)}


def test_every_code_is_reachable_and_documented(facts, policy):
    f = facts
    seen: set[str] = set()
    cases = [
        (dict(f, state="CLOSED"), ["src/a.py"], {}),
        (dict(f, draft=True), ["src/a.py"], {}),
        (dict(f, base="other"), ["src/a.py"], {}),
        (
            dict(f, reviews=[review(), review(state="CHANGES_REQUESTED", id_=2, at="2026-01-02T00:00:00Z")]),
            ["src/a.py"],
            {},
        ),
        (dict(f, reviews=[]), ["src/a.py"], {}),
        (dict(f, title="[lead] x"), ["docs/claims/x.md"], {}),
        (dict(f, checks=[]), ["src/a.py"], {}),
        (dict(f, checks=[{"name": "ci", "conclusion": "FAILURE"}]), ["src/a.py"], {}),
        (f, ["src/a.py"], {"extra_refusals": ["plugin 'x' failed"]}),
        (f, ["src/a.py"], {"extra_refusals": [gate.Refusal("G091", "project check 'x' failed")]}),
        (dict(f, title="fix"), ["src/a.py"], {}),
        (dict(f, title="[ghost] fix"), ["src/a.py"], {}),
        (dict(f, title="[builder] x"), ["docs/copy/x.md"], {}),
    ]
    for facts, paths, kw in cases:
        seen |= codes(facts, policy, paths, **kw)
    strict = pol.from_dict(dict(policy.raw, enforce={"line_endings": "refuse", "uncovered_paths": "refuse"}))
    seen |= {r.code for r in gate.enforcement_refusals(strict, ["src/a.py", "stray.txt"], ["src/a.py"])}
    assert seen == set(gate.CODES)


def test_the_sentences_are_the_same_as_before_the_codes(facts, policy):
    c = classify(["src/a.py"], policy)
    stale = dict(facts, reviews=[review(commit="b" * 40)])
    assert gate.decide(stale, policy, c) == gate.refusal_texts(gate.decide_refusals(stale, policy, c))
    assert gate.decide(stale, policy, c)[0].startswith("no reviewer approval")


def test_form_codes_are_exactly_the_ones_an_author_can_fix_before_the_gate_runs():
    assert gate.FORM_CODES == {"P001", "P002", "P003", "P004", "P005"}
    assert all(c in gate.CODES for c in gate.FORM_CODES)


def test_a_red_check_is_substance_not_form(facts, policy):
    got = codes(dict(facts, checks=[{"name": "ci", "conclusion": "FAILURE"}]), policy, ["src/a.py"])
    assert got == {"G031"} and not (got & gate.FORM_CODES)


# -- the log of decisions ------------------------------------------------------------------------------------------------


@pytest.fixture
def recorder():
    got = []
    return got, lambda kind, number, payload: got.append((kind, number, payload))


def test_a_refusal_is_recorded_with_its_codes(raw, recorder):
    got, record = recorder
    git, client = _world(raw, ["src/a.py"], title="fix", reviews=[])
    code, _ = gate.run(7, client, git, record=record)
    assert code == 1 and len(got) == 1 and got[0][0] == "gate.refused"
    assert got[0][2]["codes"] == ["G021", "P001"] and got[0][2]["head"] == HEAD
    assert {r["code"] for r in got[0][2]["refusals"]} == {"G021", "P001"}


def test_a_merge_is_recorded(raw, recorder):
    got, record = recorder
    git, client = _world(raw, ["src/a.py"])
    assert gate.run(7, client, git, record=record)[0] == 0
    assert [g[0] for g in got] == ["gate.merged"] and got[0][2]["head"] == HEAD


def test_a_replay_that_finds_the_agents_own_merge_records_the_post_merge_checks_as_pending(raw, recorder):
    got, record = recorder
    git, client = _world(
        raw, ["src/a.py"], state="MERGED", merge_sha="c" * 40, merged_by={"login": "agent-bot", "type": "Bot"}
    )
    code, _ = gate.run(7, client, git, record=record)
    assert code == 1
    assert [g[0] for g in got] == ["gate.postmerge_pending", "gate.refused"]
    assert got[0][2]["merge_sha"] == "c" * 40 and got[0][2]["head"] == HEAD


def test_a_merge_by_a_person_is_already_merged_not_pending(raw, recorder):
    got, record = recorder
    git, client = _world(
        raw, ["src/a.py"], state="MERGED", merge_sha="c" * 40, merged_by={"login": "owner-a", "type": "User"}
    )
    gate.run(7, client, git, record=record)
    assert [g[0] for g in got] == ["gate.already_merged", "gate.refused"]
    got.clear()
    git, client = _world(raw, ["src/a.py"], state="CLOSED")  # closed, never merged: nothing to record
    gate.run(7, client, git, record=record)
    assert [g[0] for g in got] == ["gate.refused"]


def test_a_dry_run_records_nothing(raw, recorder):
    got, record = recorder
    git, client = _world(raw, ["src/a.py"])
    gate.run(7, client, git, dry_run=True, record=record)
    git, client = _world(raw, ["src/a.py"], reviews=[])
    gate.run(7, client, git, dry_run=True, record=record)
    assert got == []


def test_a_head_that_moved_before_the_merge_is_recorded_as_a_refusal(raw, recorder):
    got, record = recorder
    git, client = _world(raw, ["src/a.py"])
    real = client.pr
    calls = {"n": 0}

    def moving(n):
        calls["n"] += 1
        f = copy.deepcopy(real(n))
        if calls["n"] >= 2:
            f["head"] = "f" * 40
        return f

    client.pr = moving
    assert gate.run(7, client, git, record=record)[0] == 1
    assert got[0][0] == "gate.refused" and got[0][2]["codes"] == ["G090"]


def test_the_recorded_decisions_land_in_the_ledger(tmp_path, raw):
    from onloop import cli
    from onloop.overlay.ledger import Ledger

    git, client = _world(raw, ["src/a.py"], reviews=[])
    failed: list[str] = []
    with Ledger(tmp_path / "t.db") as led:
        gate.run(7, client, git, record=cli._recorder(led, "o/r", failed))
        e = led.select("SELECT * FROM events WHERE kind = 'gate.refused'")
    assert len(e) == 1 and e[0]["subject_ref"] == "pr#7" and e[0]["payload"]["codes"] == ["G021"]
    assert e[0]["source"] == "gate" and failed == []


def test_the_audit_names_the_codes_of_a_merge_the_gate_should_not_have_made():
    from onloop.audit import _world as audit_world

    git, client, root, _merge = audit_world(facts={"reviews": []})
    why = audit.audit(git, client, since=root)[0][1]
    assert "[G021]" in why


def test_the_policy_keeps_the_old_function_names(policy, facts):
    c = classify(["src/a.py"], policy)
    assert gate.check_results(facts, policy) == [] and gate.ownership(facts, policy, c) == []
    assert isinstance(pol.AUTOMATION_DEFAULTS, dict)
