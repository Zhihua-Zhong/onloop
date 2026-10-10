import copy

import pytest

from onloop import policy as pol


def test_a_valid_policy_loads_and_matches_the_longest_path(policy):
    assert policy.match("src/a/b.py") == "src/"
    assert policy.match("core/x.py") == "core/"
    assert policy.match("onloop.yaml") == "onloop.yaml"
    assert policy.match("onloop.yaml.bak") is None
    assert policy.rule_for("README.md").owner is None
    assert policy.rule_for("core/x.py").tier == "full"


def test_the_six_builtin_classes_exist_and_a_project_may_add_one(policy):
    for name in ("claims", "safety_widening", "authority_widening", "destructive", "money_accounts", "leaves_project"):
        assert policy.approver_of(name) == "human"
    assert policy.approver_of("copy_edits") == "reviewer"
    assert policy.approver_of("never_heard_of_it") == "human"           # unknown reads as the strictest


@pytest.mark.parametrize("mutate,needle", [
    (lambda r: r.pop("human"), "`human`"),
    (lambda r: r.update(reviewer="owner-a"), "different account"),
    (lambda r: r.update(agents="rev-bot"), "different identities"),
    (lambda r: r.update(roles={}), "`roles`"),
    (lambda r: r["paths"].pop("onloop.yaml"), "itself"),
    (lambda r: r["paths"]["onloop.yaml"].pop("reserved"), "protects itself"),
    (lambda r: r["paths"].update({"x/": {"owner": "ghost"}}), "not a role"),
    (lambda r: r["paths"].update({"x/": {"tier": "gold"}}), "not one of the tiers"),
    (lambda r: r["paths"].update({"x/": {"reserved": "nope"}}), "not a class"),
    (lambda r: r["paths"].update({"x/": {"colour": "red"}}), "unknown field"),
    (lambda r: r.update(classes={"c": "robot"}), "approver"),
    (lambda r: r.update(checks=[{"name": "t"}]), "`checks`"),
])
def test_every_way_a_policy_can_be_unusable_is_named(raw, mutate, needle):
    mutate(raw)
    problems = pol.validate(raw)
    assert any(needle in p for p in problems), problems
    with pytest.raises(pol.PolicyError):
        pol.from_dict(raw)


def test_unparseable_and_missing_files_fail_closed(tmp_path):
    (tmp_path / "bad.yaml").write_text("a: [unclosed", encoding="utf-8")
    with pytest.raises(pol.PolicyError):
        pol.load(tmp_path / "bad.yaml")
    with pytest.raises(pol.PolicyError):
        pol.load(tmp_path / "nothing.yaml")


def _loosen(raw, fn):
    r = copy.deepcopy(raw)
    fn(r)
    return pol.from_dict(r)


@pytest.mark.parametrize("fn,needle", [
    (lambda r: r["paths"]["docs/claims/"].pop("reserved"), "no longer reserved"),
    (lambda r: r["paths"].pop("docs/claims/"), "removed"),
    (lambda r: r["paths"].update({"core/": {"owner": "builder", "tier": "routine"}}), "lowers its tier"),
    (lambda r: r["paths"]["docs/claims/"].update(reserved="copy_edits"), "not the human"),
    (lambda r: r.update(human="someone-else"), "human approver changes"),
    (lambda r: r.update(reviewer="other-bot"), "reviewer identity changes"),
    (lambda r: r.update(required_checks=[]), "dropped"),
    (lambda r: r.update(classes={"copy_edits": "reviewer", "claims": "reviewer"}), "from human to reviewer"),
])
def test_every_relaxation_of_the_policy_is_caught(policy, raw, fn, needle):
    found = pol.loosening(policy, _loosen(raw, fn))
    assert any(needle in f for f in found), found


def test_a_policy_that_only_tightens_or_holds_is_not_a_loosening(policy, raw):
    tighter = _loosen(raw, lambda r: r["paths"].update({"src/": {"owner": "builder", "tier": "full"}}))
    assert pol.loosening(policy, tighter) == []
    assert pol.loosening(policy, policy) == []


def test_roles_and_post_merge_checks_must_have_the_shape_the_code_reads(raw):
    raw["roles"]["empty"] = None
    assert any("role `empty`" in p for p in pol.validate(raw))
    raw["roles"]["empty"] = {}
    raw["post_merge_checks"] = [{"name": "smoke"}]
    assert any("post_merge_checks" in p for p in pol.validate(raw))
