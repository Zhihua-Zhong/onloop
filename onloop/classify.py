"""Approval classification: who must approve a change, computed from the change itself.

Input is the list of paths a pull request changes (from `git diff base...head`, never from the host's file list),
the policy in force on the base, and, when the change touches the policy file, the policy as the head would leave it.
Output says which reserved classes the change touches, which approvers are therefore required, the review tier,
and which roles own the touched paths.

A change to the rules is never judged by the rules it changes. When the head policy relaxes the base policy, the
change is classed as authority widening and needs the human, whatever the head policy says about itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import policy as pol


@dataclass
class Classification:
    files: list[str]
    classes: list[str] = field(default_factory=list)
    approvers: list[str] = field(default_factory=list)       # "reviewer" is always present; "human" when a class asks
    tier: str = "routine"
    owners: list[str] = field(default_factory=list)
    unowned: list[str] = field(default_factory=list)
    loosening: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def needs_human(self) -> bool:
        return "human" in self.approvers

    def as_dict(self) -> dict:
        return {"files": self.files, "classes": self.classes, "approvers": self.approvers, "tier": self.tier,
                "owners": self.owners, "unowned": self.unowned, "loosening": self.loosening,
                "reasons": self.reasons}


def classify(files: list[str], base: pol.Policy, head: pol.Policy | None = None,
             head_policy_problem: str | None = None) -> Classification:
    """Classify a change. `head` is the policy as the head would leave it (None when the change does not touch it).

    `head_policy_problem` says the head's policy file could not be read or does not validate; that is itself a
    change of authority, and is classed as such.
    """
    c = Classification(files=sorted(files), tier=base.default_tier)
    classes: set[str] = set()
    owners: set[str] = set()
    for f in c.files:
        rule = base.rule_for(f)
        if rule.reserved:
            classes.add(rule.reserved)
            c.reasons.append(f"{f} is reserved to {rule.reserved!r}")
        if base.tier_rank(rule.tier) > base.tier_rank(c.tier):
            c.tier = rule.tier
        if rule.owner:
            owners.add(rule.owner)
        else:
            c.unowned.append(f)
    if head_policy_problem:
        c.loosening.append(f"the policy in the head cannot be read: {head_policy_problem}")
    elif head is not None:
        c.loosening = pol.loosening(base, head)
    if c.loosening:
        classes.add(pol.LOOSENING_CLASS)
        c.reasons.extend(f"loosens the policy: {x}" for x in c.loosening)
    c.classes = sorted(classes)
    c.owners = sorted(owners)
    approvers = {"reviewer"}
    for cls in c.classes:
        # a loosening needs the human whatever approver the class names; every other class says who it needs
        approvers.add("human" if cls == pol.LOOSENING_CLASS and c.loosening else base.approver_of(cls))
    c.approvers = sorted(approvers)           # alphabetical: human, then reviewer
    return c


def self_test() -> tuple[bool, list[str]]:
    """The classifier must catch a loosening and ask the right approver for each class."""
    base = pol.from_dict({
        "human": "owner-a", "reviewer": "rev-bot", "agents": "agent-bot",
        "roles": {"builder": {}, "lead": {}},
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
    })
    loosened = dict(base.raw, paths=dict(base.raw["paths"], **{"docs/claims/": {"owner": "lead"}}))
    tightened = dict(base.raw, paths=dict(base.raw["paths"], **{"src/": {"owner": "builder", "tier": "full"}}))
    cases = [
        ("plain change needs the reviewer only", classify(["src/a.py"], base), ["reviewer"], False),
        ("a reserved human class needs the human", classify(["docs/claims/x.md"], base), ["human", "reviewer"], False),
        ("a class the reviewer approves stays with the reviewer", classify(["docs/copy/x.md"], base), ["reviewer"],
         False),
        ("a loosened policy is authority widening", classify(["onloop.yaml"], base, pol.from_dict(loosened)),
         ["human", "reviewer"], True),
        ("a tightened policy still touches the reserved policy file", classify(["onloop.yaml"], base,
                                                                              pol.from_dict(tightened)),
         ["human", "reviewer"], False),
        ("an unreadable head policy is authority widening", classify(["onloop.yaml"], base, None, "does not parse"),
         ["human", "reviewer"], True),
    ]
    ok, lines = True, []
    for label, got, want_approvers, want_loose in cases:
        right = got.approvers == want_approvers and bool(got.loosening) == want_loose
        ok &= right
        lines.append(f"  {'ok' if right else 'FAILED'} {label}" + ("" if right else f" (got {got.approvers})"))
    # the loosening must still need the human when the class it lands in names the reviewer
    soft = pol.from_dict(dict(base.raw, classes={"copy_edits": "reviewer", "authority_widening": "reviewer"}))
    got = classify(["onloop.yaml"], soft, pol.from_dict(dict(soft.raw, paths=dict(soft.raw["paths"], **{
        "docs/claims/": {"owner": "lead"}}))))
    right = got.needs_human
    ok &= right
    lines.append(f"  {'ok' if right else 'FAILED'} a loosening needs the human even if its class names the reviewer")
    return ok, lines
