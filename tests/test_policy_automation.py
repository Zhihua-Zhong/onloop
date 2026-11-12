"""The `automation` block: validated, defaulted, and covered by the loosening rule."""

import copy

import pytest

from onloop import policy as pol
from onloop.classify import classify


def with_auto(raw, **auto):
    r = copy.deepcopy(raw)
    r["automation"] = auto
    return r


def test_defaults_are_the_safe_ones(policy):
    assert policy.automation == {
        "mode": "observe",
        "intake_seconds": 60,
        "autogate": False,
        "autoready": False,
        "cross_owner": "author",
        "allow_working_tree": False,
        "max_stop_minutes": 60,
        "max_defer_hours": 4,
        "external_gate_check": None,
    }


def test_a_partial_block_keeps_the_other_defaults(raw):
    p = pol.from_dict(with_auto(raw, autogate=True))
    assert p.automation["autogate"] is True and p.automation["mode"] == "observe"


@pytest.mark.parametrize(
    "bad",
    [
        {"mode": "yolo"},
        {"intake_seconds": 5},
        {"intake_seconds": "60"},
        {"intake_seconds": True},
        {"autogate": "yes"},
        {"autoready": 1},
        {"cross_owner": "nobody"},
        {"surprise": 1},
        {"max_stop_minutes": -1},
        {"max_stop_minutes": "60"},
        {"max_stop_minutes": True},
        {"max_defer_hours": 0},
        {"external_gate_check": ""},
        {"external_gate_check": 3},
    ],
)
def test_a_bad_automation_field_is_refused(raw, bad):
    with pytest.raises(pol.PolicyError):
        pol.from_dict(with_auto(raw, **bad))


def test_automation_must_be_a_mapping(raw):
    r = copy.deepcopy(raw)
    r["automation"] = ["observe"]
    with pytest.raises(pol.PolicyError):
        pol.from_dict(r)


@pytest.mark.parametrize(
    "before,after,reason",
    [
        ({}, {"autogate": True}, "autogate is turned on"),
        ({}, {"autoready": True}, "autoready is turned on"),
        ({}, {"mode": "act"}, "observe to act"),
        ({}, {"max_stop_minutes": 600}, "max_stop_minutes is raised from 60 to 600"),
        ({}, {"max_defer_hours": 48}, "max_defer_hours is raised from 4 to 48"),
        ({}, {"external_gate_check": "merge-gate"}, "external_gate_check is set or changed"),
    ],
)
def test_loosening_the_automation_is_authority_widening(raw, before, after, reason):
    base, head = pol.from_dict(with_auto(raw, **before)), pol.from_dict(with_auto(raw, **after))
    found = pol.loosening(base, head)
    assert any(reason in f for f in found)
    c = classify(["onloop.yaml"], base, head)
    assert c.needs_human and pol.LOOSENING_CLASS in c.classes


def test_cross_owner_reviewer_ack_is_accepted_now_that_it_is_implemented(raw):
    assert (
        pol.from_dict(with_auto(raw, cross_owner="reviewer_ack")).automation["cross_owner"] == "reviewer_ack"
    )


def test_moving_cross_owner_back_to_author_is_a_loosening(raw):
    base, head = pol.from_dict(with_auto(raw, cross_owner="reviewer_ack")), pol.from_dict(with_auto(raw))
    assert any("reviewer_ack to author" in f for f in pol.loosening(base, head))
    assert pol.loosening(head, base) == []


@pytest.mark.parametrize(
    "before,after",
    [
        ({"autogate": True}, {}),
        ({"autoready": True}, {}),
        ({"mode": "act"}, {"mode": "observe"}),
        ({}, {"intake_seconds": 300}),
        ({"autogate": True}, {"autogate": True}),
    ],
)
def test_tightening_or_leaving_the_automation_alone_is_not_a_loosening(raw, before, after):
    base, head = pol.from_dict(with_auto(raw, **before)), pol.from_dict(with_auto(raw, **after))
    assert pol.loosening(base, head) == []
