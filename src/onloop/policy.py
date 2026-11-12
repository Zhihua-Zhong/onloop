"""Policy as code: `onloop.yaml` maps paths to an owner role, a review tier and a reserved class.

The policy is the one place that says who may change what and who must approve it. CODEOWNERS is generated
from it (see `codeowners.py`); the classifier, the gate and the audit all read it and nothing else.
"""

from __future__ import annotations

import pathlib
import re
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
AUTOMATION_DEFAULTS = {
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
INDENT_SENSITIVE = [".py", ".yaml", ".yml", ".md", ".mk", "Makefile"]
ENFORCE_MODES = ("warn", "refuse")
ENFORCE_DEFAULTS = {"line_endings": "warn", "uncovered_paths": "warn"}
WORKBENCH_DEFAULTS = {
    "stale_days": 7,
    "allowed_origins": [],
    "human_roles": [],
    "indent_sensitive": INDENT_SENSITIVE,
}
AUTOMATION_MODES = ("observe", "act")
CROSS_OWNER_MODES = ("author", "reviewer_ack")
DEFAULT_TIERS = ["routine", "full"]
SLOT_FIELDS = {
    "paths",
    "timeout_minutes",
    "wait_minutes",
    "approver",
    "conditions",
    "commands",
    "max_renewals",
    "capacity",
    "human_grant",
}
HUMAN_GRANTS = ("attested", "anchored")
MAX_CAPACITY = 8
ONE_SEAT_SLOTS = ("gate",)  # the slot that serialises merges never has two holders
SLOT_DEFAULTS = {
    "timeout_minutes": 60,
    "wait_minutes": 1440,
    "approver": "human",
    "conditions": [],
    "commands": [],
    "max_renewals": 3,
    "capacity": 1,
    "human_grant": "attested",
}


# The post-merge step convention (story 10): a line in the pull request's body. A team can point the pattern at its
# own template section; it must name the groups `role` and `step`.
POST_MERGE_PATTERN = r"^Post-merge:\s*(?P<role>[a-z][a-z0-9_-]{0,39}):\s*(?P<step>\S.*?)\s*$"
POST_MERGE_MAX_STEPS = 20
POST_MERGE_MAX_STEP = 300
_ROLE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


def post_merge_steps(body: str | None, pattern: str = POST_MERGE_PATTERN) -> list[dict[str, str]]:
    """The post-merge steps a pull request's body names, in order: `[{role, step}]`. Lines whose role is not a role
    name are skipped; at most POST_MERGE_MAX_STEPS, each step cut to POST_MERGE_MAX_STEP characters."""
    rx = re.compile(pattern)
    out = []
    for line in (body or "").splitlines():
        m = rx.match(line)
        if not m:
            continue
        role, step = (m.group("role") or "").strip().lower(), (m.group("step") or "").strip()
        if _ROLE_NAME.match(role) and step:
            out.append({"role": role, "step": step[:POST_MERGE_MAX_STEP]})
        if len(out) == POST_MERGE_MAX_STEPS:
            break
    return out


def validate_post_merge(block: Any) -> list[str]:
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["`post_merge` must be a mapping with `pattern`"]
    out = []
    unknown = set(block) - {"pattern"}
    if unknown:
        out.append(f"`post_merge` has unknown field(s): {', '.join(sorted(unknown))}")
    pattern = block.get("pattern", POST_MERGE_PATTERN)
    try:
        rx = re.compile(pattern) if isinstance(pattern, str) else None
    except re.error as e:
        return [*out, f"post_merge.pattern is not a regular expression: {e}"]
    if rx is None or not {"role", "step"} <= set(rx.groupindex):
        out.append("post_merge.pattern must be a regular expression with the named groups `role` and `step`")
    return out


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
    automation: dict[str, Any] = field(default_factory=lambda: dict(AUTOMATION_DEFAULTS))
    slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    workbench: dict[str, Any] = field(
        default_factory=lambda: {
            **WORKBENCH_DEFAULTS,
            "allowed_origins": [],
            "human_roles": [],
            "indent_sensitive": list(INDENT_SENSITIVE),
        }
    )
    enforce: dict[str, str] = field(default_factory=lambda: dict(ENFORCE_DEFAULTS))
    post_merge_pattern: str = POST_MERGE_PATTERN
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
        return self.classes.get(reserved_class, "human")  # an unknown class is read as the strictest

    def tier_rank(self, tier: str) -> int:
        return (
            self.tiers.index(tier) if tier in self.tiers else len(self.tiers)
        )  # unknown reads as the highest


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
            continue
        if spec.get("anchor", "session") not in ("session", "every_message"):
            problems.append(f"role `{role}` has anchor {spec.get('anchor')!r}; use session or every_message")
        leads = spec.get("leads", [])
        if not isinstance(leads, list) or any(r not in roles for r in leads):
            problems.append(f"role `{role}` leads {leads!r}: `leads` must list roles that exist")
        kpis = spec.get("kpis", [])
        if not (isinstance(kpis, dict) or (isinstance(kpis, list) and all(isinstance(k, str) for k in kpis))):
            problems.append(
                f"role `{role}` has `kpis` that is neither a list of names nor a mapping of name to target"
            )
    classes = dict(BUILTIN_CLASSES)
    extra = raw.get("classes") or {}
    if not isinstance(extra, dict):
        problems.append("`classes` must be a mapping of class name to approver")
        extra = {}
    for name, approver in extra.items():
        if approver not in APPROVERS:
            problems.append(
                f"class `{name}` names approver {approver!r}; it must be one of {', '.join(APPROVERS)}"
            )
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
        problems.append(
            f"`paths` must list `{POLICY_FILE}` itself, reserved to a class, so the policy cannot be "
            "changed without the approval its own class asks for"
        )
    elif (paths[POLICY_FILE] or {}).get("reserved") is None:
        problems.append(f"`{POLICY_FILE}` must carry a `reserved` class: the policy protects itself")
    checks = raw.get("checks") or []
    if not isinstance(checks, list) or not all(
        isinstance(c, dict) and c.get("name") and c.get("run") for c in checks
    ):
        problems.append("`checks` must be a list of {name, run}")
    post = raw.get("post_merge_checks") or []
    if not isinstance(post, list) or not all(
        isinstance(c, dict) and c.get("name") and c.get("run") for c in post
    ):
        problems.append("`post_merge_checks` must be a list of {name, run}")
    problems.extend(validate_automation(raw.get("automation")))
    problems.extend(validate_slots(raw.get("slots"), roles))
    problems.extend(validate_workbench(raw.get("workbench"), roles))
    problems.extend(validate_enforce(raw.get("enforce")))
    problems.extend(validate_post_merge(raw.get("post_merge")))
    req = raw.get("required_checks") or []
    if not isinstance(req, list) or not all(isinstance(c, str) for c in req):
        problems.append("`required_checks` must be a list of check names")
    return problems


def validate_slots(slots: Any, roles: Any) -> list[str]:
    """Problems with the `slots` block: each slot a scarce thing with a path scope, a lease and an approver."""
    if slots is None:
        return []
    if not isinstance(slots, dict):
        return ["`slots` must be a mapping of slot name to its settings"]
    out: list[str] = []
    for name, spec in slots.items():
        if not isinstance(spec, dict):
            out.append(f"slot `{name}` must be a mapping")
            continue
        unknown = set(spec) - SLOT_FIELDS
        if unknown:
            out.append(f"slot `{name}` has unknown field(s): {', '.join(sorted(unknown))}")
        paths = spec.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
            out.append(
                f"slot `{name}` needs `paths`: a non-empty list of path patterns (`**` for everything)"
            )
        for key in ("timeout_minutes", "wait_minutes", "max_renewals"):
            v = spec.get(key, SLOT_DEFAULTS[key])
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                out.append(f"slot `{name}`: {key} must be a whole number, at least 1")
        hg = spec.get("human_grant", SLOT_DEFAULTS["human_grant"])
        if hg not in HUMAN_GRANTS:
            out.append(f"slot `{name}`: human_grant must be one of {', '.join(HUMAN_GRANTS)}")
        elif hg == "anchored" and spec.get("approver", SLOT_DEFAULTS["approver"]) != "human":
            out.append(f"slot `{name}`: human_grant: anchored needs approver: human")
        cap = spec.get("capacity", SLOT_DEFAULTS["capacity"])
        if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= MAX_CAPACITY:
            out.append(f"slot `{name}`: capacity must be a whole number from 1 to {MAX_CAPACITY}")
        elif name in ONE_SEAT_SLOTS and cap != 1:
            out.append(f"slot `{name}` has one seat: its capacity cannot be {cap}")
        approver = spec.get("approver", SLOT_DEFAULTS["approver"])
        role = approver[5:] if isinstance(approver, str) and approver.startswith("role:") else None
        if approver not in ("auto", "human") and role is None:
            out.append(f"slot `{name}`: approver must be auto, human or role:<name>")
        elif role is not None and (not isinstance(roles, dict) or role not in roles):
            out.append(f"slot `{name}`: approver names role {role!r}, which is not a role")
        for key in ("conditions", "commands"):
            v = spec.get(key, [])
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                out.append(f"slot `{name}`: {key} must be a list of strings")
    return out


def validate_enforce(block: Any) -> list[str]:
    """Problems with the `enforce` block: which of the two warnings pre-flight already gives the gate also refuses for."""
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["`enforce` must be a mapping"]
    out = []
    unknown = set(block) - set(ENFORCE_DEFAULTS)
    if unknown:
        out.append(f"`enforce` has unknown field(s): {', '.join(sorted(unknown))}")
    for name in ENFORCE_DEFAULTS:
        if block.get(name, "warn") not in ENFORCE_MODES:
            out.append(f"enforce.{name} must be one of {', '.join(ENFORCE_MODES)}")
    return out


def enforce_loosening(base: dict[str, str], head: dict[str, str]) -> list[str]:
    """What the head's `enforce` block relaxes: a refusal that becomes a warning, or a field removed (the default warns)."""
    return [
        f"enforce.{name} goes from refuse to {head.get(name, 'warn')}: the gate stops refusing for it"
        for name in ENFORCE_DEFAULTS
        if base.get(name) == "refuse" and head.get(name) != "refuse"
    ]


def validate_workbench(block: Any, roles: Any = None) -> list[str]:
    """Problems with the `workbench` block: how stale a verification may be, the origins a page may link to, and the
    roles whose rulings are the human's own."""
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["`workbench` must be a mapping"]
    out = []
    unknown = set(block) - set(WORKBENCH_DEFAULTS)
    if unknown:
        out.append(f"`workbench` has unknown field(s): {', '.join(sorted(unknown))}")
    days = block.get("stale_days", WORKBENCH_DEFAULTS["stale_days"])
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        out.append("workbench.stale_days must be a whole number of days, at least 1")
    origins = block.get("allowed_origins", [])
    if not isinstance(origins, list) or not all(
        isinstance(o, str) and re.fullmatch(r"https://[a-z0-9.-]+(:[0-9]+)?", o) for o in origins
    ):
        out.append(
            "workbench.allowed_origins must be a list of origins such as https://github.com (https, a host, "
            "no path, lower case)"
        )
    sensitive = block.get("indent_sensitive", INDENT_SENSITIVE)
    if not isinstance(sensitive, list) or not all(
        isinstance(s, str) and s and "/" not in s for s in sensitive
    ):
        out.append(
            "workbench.indent_sensitive must be a list of extensions such as .py or file names such as Makefile"
        )
    human_roles = block.get("human_roles", [])
    if not isinstance(human_roles, list) or not all(isinstance(r, str) for r in human_roles):
        out.append("workbench.human_roles must be a list of role names")
    elif roles is not None:
        for r in human_roles:
            if r not in roles:
                out.append(f"workbench.human_roles names {r!r}, which is not a role")
    return out


def workbench_loosening(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    """What the head's `workbench` block relaxes: a verification may be older before it turns amber, or a page may
    link to a new origin."""
    out = []
    if head["stale_days"] > base["stale_days"]:
        out.append(f"workbench.stale_days is raised from {base['stale_days']} to {head['stale_days']}")
    for origin in sorted(set(head["allowed_origins"]) - set(base["allowed_origins"])):
        out.append(f"workbench.allowed_origins gains {origin}: pages may link there")
    for s in sorted(set(base["indent_sensitive"]) - set(head["indent_sensitive"])):
        out.append(
            f"workbench.indent_sensitive drops {s}: an indentation-only push there is no longer flagged"
        )
    for role in sorted(set(head["human_roles"]) - set(base["human_roles"])):
        out.append(f"workbench.human_roles gains {role}: its decisions leave the lead's overturnable list")
    return out


def slot_specs(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each slot's settings with the defaults filled in."""
    return {name: {**SLOT_DEFAULTS, **spec} for name, spec in (raw.get("slots") or {}).items()}


def validate_automation(auto: Any) -> list[str]:
    if auto is None:
        return []
    if not isinstance(auto, dict):
        return ["`automation` must be a mapping"]
    out = []
    unknown = set(auto) - set(AUTOMATION_DEFAULTS)
    if unknown:
        out.append(f"`automation` has unknown field(s): {', '.join(sorted(unknown))}")
    if auto.get("mode", "observe") not in AUTOMATION_MODES:
        out.append(f"automation.mode must be one of {', '.join(AUTOMATION_MODES)}")
    secs = auto.get("intake_seconds", 60)
    if isinstance(secs, bool) or not isinstance(secs, int) or secs < 10:
        out.append("automation.intake_seconds must be a whole number of seconds, at least 10")
    for flag in ("autogate", "autoready", "allow_working_tree"):
        if not isinstance(auto.get(flag, False), bool):
            out.append(f"automation.{flag} must be true or false")
    for name, low in (("max_stop_minutes", 0), ("max_defer_hours", 1)):
        v = auto.get(name, AUTOMATION_DEFAULTS[name])
        if isinstance(v, bool) or not isinstance(v, int) or v < low:
            out.append(f"automation.{name} must be a whole number, at least {low}")
    gate_check = auto.get("external_gate_check")
    if gate_check is not None and (not isinstance(gate_check, str) or not gate_check.strip()):
        out.append("automation.external_gate_check must be the name of a check run, or left out")
    if auto.get("cross_owner", "author") not in CROSS_OWNER_MODES:
        out.append(f"automation.cross_owner must be one of {', '.join(CROSS_OWNER_MODES)}")
    return out


def from_dict(raw: dict[str, Any]) -> Policy:
    problems = validate(raw)
    if problems:
        raise PolicyError("; ".join(problems))
    classes = dict(BUILTIN_CLASSES)
    classes.update(raw.get("classes") or {})
    tiers = list(raw.get("tiers") or DEFAULT_TIERS)
    default_tier = (raw.get("defaults") or {}).get("tier", tiers[0])
    paths = {
        k: Rule(owner=v.get("owner"), tier=v.get("tier", default_tier), reserved=v.get("reserved"))
        for k, v in raw["paths"].items()
    }
    return Policy(
        human=raw["human"],
        reviewer=raw["reviewer"],
        agents=raw["agents"],
        roles=dict(raw["roles"]),
        classes=classes,
        tiers=tiers,
        default_tier=default_tier,
        paths=paths,
        checks=[dict(c) for c in raw.get("checks") or []],
        required_checks=list(raw.get("required_checks") or []),
        automation={**AUTOMATION_DEFAULTS, **(raw.get("automation") or {})},
        slots=slot_specs(raw),
        workbench={**WORKBENCH_DEFAULTS, **(raw.get("workbench") or {})},
        enforce={**ENFORCE_DEFAULTS, **(raw.get("enforce") or {})},
        post_merge_pattern=(raw.get("post_merge") or {}).get("pattern", POST_MERGE_PATTERN),
        raw=raw,
    )


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
        elif (
            was.reserved
            and now.reserved != was.reserved
            and base.approver_of(was.reserved) == "human"
            and head.approver_of(now.reserved) != "human"
        ):
            out.append(
                f"{key!r} moves from {was.reserved!r} to {now.reserved!r}, whose approver is not the human"
            )
        if head.tier_rank(now.tier) < base.tier_rank(was.tier):
            out.append(f"{key!r} lowers its tier from {was.tier!r} to {now.tier!r}")
    out.extend(automation_loosening(base.automation, head.automation))
    out.extend(slot_loosening(base.slots, head.slots))
    out.extend(workbench_loosening(base.workbench, head.workbench))
    out.extend(enforce_loosening(base.enforce, head.enforce))
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


APPROVER_RANK = {"human": 0, "auto": 2}  # role:* sits between the two


def _approver_rank(approver: str) -> int:
    return APPROVER_RANK.get(approver, 1)


def slot_loosening(base: dict[str, dict[str, Any]], head: dict[str, dict[str, Any]]) -> list[str]:
    """What the head's `slots` relax: a slot that is removed, covers fewer paths, hands itself out more freely, or lets
    a lease run longer. Adding a slot, or tightening one, is not a loosening."""
    out = []
    for name, was in sorted(base.items()):
        now = head.get(name)
        if now is None:
            out.append(f"slot {name!r} is removed")
            continue
        if _approver_rank(now["approver"]) > _approver_rank(was["approver"]) or (
            was["approver"] != now["approver"]
            and was["approver"].startswith("role:")
            and now["approver"].startswith("role:")
        ):
            out.append(f"slot {name!r} moves its approver from {was['approver']} to {now['approver']}")
        lost = sorted(set(was["paths"]) - set(now["paths"]))
        if lost:
            out.append(f"slot {name!r} no longer covers {', '.join(lost)}")
        for key in ("conditions", "commands"):
            dropped = [x for x in was[key] if x not in now[key]]
            if dropped:
                out.append(f"slot {name!r} drops or changes {key}: {'; '.join(dropped)}")
        if was["human_grant"] == "anchored" and now["human_grant"] != "anchored":
            out.append(f"slot {name!r} stops requiring the human's grant to be anchored on a GitHub comment")
        if now["capacity"] > was["capacity"]:
            out.append(f"slot {name!r} raises its capacity from {was['capacity']} to {now['capacity']}")
        if now["max_renewals"] > was["max_renewals"]:
            out.append(
                f"slot {name!r} raises max_renewals from {was['max_renewals']} to {now['max_renewals']}"
            )
        if now["timeout_minutes"] > was["timeout_minutes"]:
            out.append(
                f"slot {name!r} raises its lease from {was['timeout_minutes']} to {now['timeout_minutes']} minutes"
            )
    return out


def automation_loosening(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    """What the head's `automation` block relaxes: onloop doing more by itself, or checking less."""
    out = []
    for flag in ("autogate", "autoready"):
        if head.get(flag) and not base.get(flag):
            out.append(f"automation.{flag} is turned on")
    for name in ("max_stop_minutes", "max_defer_hours"):
        was, now = base.get(name, AUTOMATION_DEFAULTS[name]), head.get(name, AUTOMATION_DEFAULTS[name])
        if isinstance(was, int) and isinstance(now, int) and now > was:
            out.append(f"automation.{name} is raised from {was} to {now}")
    if head.get("external_gate_check") and head.get("external_gate_check") != base.get("external_gate_check"):
        out.append("automation.external_gate_check is set or changed: it explains away shadow disagreements")
    if head.get("allow_working_tree") and not base.get("allow_working_tree"):
        out.append(
            "automation.allow_working_tree is turned on: the runner may work in a checkout someone else uses"
        )
    if base.get("mode") == "observe" and head.get("mode") == "act":
        out.append("automation.mode moves from observe to act")
    if base.get("cross_owner") == "reviewer_ack" and head.get("cross_owner") == "author":
        out.append("automation.cross_owner moves from reviewer_ack to author")
    return out
