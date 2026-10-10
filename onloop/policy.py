"""Policy as code: `onloop.yaml` maps paths to an owner role, a review tier and a reserved class.

The policy is the one place that says who may change what and who must approve it. CODEOWNERS is generated
from it (see `codeowners.py`); the classifier, the gate and the audit all read it and nothing else.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any

import yaml

POLICY_FILE = "onloop.yaml"
APPROVERS = ("human", "reviewer")
# The reserved classes onloop ships with. A project may add its own under `classes:`; these six always exist.
BUILTIN_CLASSES = {
    "claims": "human",
    "safety_widening": "human",
    "authority_widening": "human",
    "destructive": "human",
    "money_accounts": "human",
    "leaves_project": "human",
}
LOOSENING_CLASS = "authority_widening"
DEFAULT_TIERS = ["routine", "full"]


class PolicyError(ValueError):
    """The policy file is missing, unreadable, or does not say what a gate can safely act on."""


@dataclass(frozen=True)
class Rule:
    owner: str | None = None
    tier: str = "routine"
    reserved: str | None = None


@dataclass
class Policy:
    human: str
    reviewer: str
    agents: str
    roles: dict[str, dict[str, Any]]
    classes: dict[str, str]
    tiers: list[str]
    default_tier: str
    paths: dict[str, Rule]
    checks: list[dict[str, str]] = field(default_factory=list)
    required_checks: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    # -- lookup ----------------------------------------------------------------------------------------------

    def match(self, path: str) -> str | None:
        """The most specific policy key covering `path`: an exact file entry, else the longest directory prefix."""
        path = normalise(path)
        best = None
        for key in self.paths:
            k = normalise(key)
            hit = path == k.rstrip("/") if not k.endswith("/") else path.startswith(k)
            if hit and (best is None or len(k) > len(normalise(best))):
                best = key
        return best

    def rule_for(self, path: str) -> Rule:
        key = self.match(path)
        if key is not None:
            return self.paths[key]
        return Rule(owner=None, tier=self.default_tier, reserved=None)

    def approver_of(self, reserved_class: str) -> str:
        return self.classes.get(reserved_class, "human")           # an unknown class is read as the strictest

    def tier_rank(self, tier: str) -> int:
        return self.tiers.index(tier) if tier in self.tiers else len(self.tiers)     # unknown reads as the highest


def normalise(path: str) -> str:
    p = path.replace("\\", "/")
    return p[2:] if p.startswith("./") else p


# -- loading and validation ----------------------------------------------------------------------------------

def validate(raw: Any) -> list[str]:
    """Every problem with a parsed policy, as plain sentences. Empty means the policy can be acted on."""
    problems: list[str] = []
    if not isinstance(raw, dict):
        return ["the policy is not a mapping"]
    for key in ("human", "reviewer", "agents"):
        if not isinstance(raw.get(key), str) or not raw.get(key):
            problems.append(f"`{key}` must name a GitHub login")
    if raw.get("human") and raw.get("human") in (raw.get("reviewer"), raw.get("agents")):
        problems.append("`human` must be a different account from `reviewer` and `agents`")
    if raw.get("reviewer") and raw.get("reviewer") == raw.get("agents"):
        problems.append("`reviewer` and `agents` must be different identities")
    roles = raw.get("roles")
    if not isinstance(roles, dict) or not roles:
        problems.append("`roles` must name at least one role")
        roles = {}
    for role, spec in roles.items():
        if not isinstance(spec, dict):
            problems.append(f"role `{role}` must map to a mapping (use `{role}: {{}}` for no settings)")
    classes = dict(BUILTIN_CLASSES)
    extra = raw.get("classes") or {}
    if not isinstance(extra, dict):
        problems.append("`classes` must be a mapping of class name to approver")
        extra = {}
    for name, approver in extra.items():
        if approver not in APPROVERS:
            problems.append(f"class `{name}` names approver {approver!r}; it must be one of {', '.join(APPROVERS)}")
        classes[name] = approver
    tiers = raw.get("tiers") or DEFAULT_TIERS
    if not isinstance(tiers, list) or not tiers or not all(isinstance(t, str) for t in tiers):
        problems.append("`tiers` must be a non-empty list of names, lowest first")
        tiers = DEFAULT_TIERS
    default_tier = (raw.get("defaults") or {}).get("tier", tiers[0])
    if default_tier not in tiers:
        problems.append(f"defaults.tier {default_tier!r} is not one of the tiers")
    paths = raw.get("paths")
    if not isinstance(paths, dict) or not paths:
        problems.append("`paths` must map at least one path to a rule")
        paths = {}
    for key, rule in paths.items():
        if not isinstance(rule, dict):
            problems.append(f"path `{key}` must map to a rule (owner, tier, reserved)")
            continue
        unknown = set(rule) - {"owner", "tier", "reserved"}
        if unknown:
            problems.append(f"path `{key}` has unknown field(s): {', '.join(sorted(unknown))}")
        owner = rule.get("owner")
        if owner is not None and owner not in roles:
            problems.append(f"path `{key}` is owned by {owner!r}, which is not a role")
        if rule.get("tier") is not None and rule["tier"] not in tiers:
            problems.append(f"path `{key}` has tier {rule['tier']!r}, which is not one of the tiers")
        if rule.get("reserved") is not None and rule["reserved"] not in classes:
            problems.append(f"path `{key}` is reserved to {rule['reserved']!r}, which is not a class")
    if POLICY_FILE not in paths:
        problems.append(f"`paths` must list `{POLICY_FILE}` itself, reserved to a class, so the policy cannot be "
                        "changed without the approval its own class asks for")
    elif (paths[POLICY_FILE] or {}).get("reserved") is None:
        problems.append(f"`{POLICY_FILE}` must carry a `reserved` class: the policy protects itself")
    checks = raw.get("checks") or []
    if not isinstance(checks, list) or not all(isinstance(c, dict) and c.get("name") and c.get("run") for c in checks):
        problems.append("`checks` must be a list of {name, run}")
    post = raw.get("post_merge_checks") or []
    if not isinstance(post, list) or not all(isinstance(c, dict) and c.get("name") and c.get("run") for c in post):
        problems.append("`post_merge_checks` must be a list of {name, run}")
    req = raw.get("required_checks") or []
    if not isinstance(req, list) or not all(isinstance(c, str) for c in req):
        problems.append("`required_checks` must be a list of check names")
    return problems


def from_dict(raw: dict[str, Any]) -> Policy:
    problems = validate(raw)
    if problems:
        raise PolicyError("; ".join(problems))
    classes = dict(BUILTIN_CLASSES)
    classes.update(raw.get("classes") or {})
    tiers = list(raw.get("tiers") or DEFAULT_TIERS)
    default_tier = (raw.get("defaults") or {}).get("tier", tiers[0])
    paths = {k: Rule(owner=v.get("owner"), tier=v.get("tier", default_tier), reserved=v.get("reserved"))
             for k, v in raw["paths"].items()}
    return Policy(human=raw["human"], reviewer=raw["reviewer"], agents=raw["agents"], roles=dict(raw["roles"]),
                  classes=classes, tiers=tiers, default_tier=default_tier, paths=paths,
                  checks=[dict(c) for c in raw.get("checks") or []],
                  required_checks=list(raw.get("required_checks") or []), raw=raw)


def loads(text: str) -> Policy:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PolicyError(f"the policy does not parse: {e}") from e
    return from_dict(raw)


def load(path: str | pathlib.Path = POLICY_FILE) -> Policy:
    p = pathlib.Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise PolicyError(f"cannot read {p}: {e}") from e
    return loads(text)


# -- the policy comparing with itself ------------------------------------------------------------------------

def loosening(base: Policy, head: Policy) -> list[str]:
    """What the head policy relaxes compared with the base policy, as sentences. Empty means it only holds or tightens.

    A pull request that changes the rules is not judged by the rules it changes: the gate compares the two and
    treats any relaxation as authority widening, whatever the files say.

    Paths are compared by what each policy *decides* for them, not by how the file spells it: for every key in either
    policy, `base.rule_for(key)` is compared with `head.rule_for(key)`. A rule can change at any key of either policy,
    so this catches a key that is removed, one whose reservation is dropped, and a new, more specific key that
    un-reserves part of a reserved directory. Relaxing means: a reservation is lost, moves to a class whose approver
    is not the human, or the tier falls; a class moves from human to reviewer or disappears; the human, reviewer or
    agent identity changes; a required check is dropped; or a check, by name and command, is dropped or its command
    changes.
    """
    out: list[str] = []
    if base.human != head.human:
        out.append(f"the human approver changes from {base.human!r} to {head.human!r}")
    if base.reviewer != head.reviewer:
        out.append(f"the reviewer identity changes from {base.reviewer!r} to {head.reviewer!r}")
    if base.agents != head.agents:
        out.append(f"the agent identity changes from {base.agents!r} to {head.agents!r}")
    for name in base.classes:
        new = head.classes.get(name)
        if new is None:
            out.append(f"class {name!r} is removed")
        elif base.approver_of(name) == "human" and new != "human":
            out.append(f"class {name!r} moves its approver from human to {new}")
    for key in sorted(set(base.paths) | set(head.paths)):
        was, now = base.rule_for(key), head.rule_for(key)
        if was.reserved and not now.reserved:
            out.append(f"{key!r} is no longer reserved (was {was.reserved!r})")
        elif was.reserved and now.reserved != was.reserved and base.approver_of(was.reserved) == "human"                 and head.approver_of(now.reserved) != "human":
            out.append(f"{key!r} moves from {was.reserved!r} to {now.reserved!r}, whose approver is not the human")
        if head.tier_rank(now.tier) < base.tier_rank(was.tier):
            out.append(f"{key!r} lowers its tier from {was.tier!r} to {now.tier!r}")
    for gone in sorted(set(base.required_checks) - set(head.required_checks)):
        out.append(f"the required check {gone!r} is dropped")
    for source, label in (("checks", "check"), ("post_merge_checks", "post-merge check")):
        kept = {(c["name"], c["run"]) for c in head.raw.get(source) or []}
        for c in base.raw.get(source) or []:
            if (c["name"], c["run"]) not in kept:
                names = {n for n, _ in kept}
                what = "changes its command" if c["name"] in names else "is dropped"
                out.append(f"the {label} {c['name']!r} {what}")
    return out
