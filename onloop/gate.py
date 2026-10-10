"""The merge gate: merge a pull request only when every condition holds on the exact head SHA.

`decide()` is a pure function of facts. It returns the reasons a pull request may not merge; an empty list means
it may. Every piece of evidence it accepts is bound to the head: a review of an older commit, a check on another
commit and a verdict for a different head count for nothing.

The gate checks, on the head:
  1. the pull request is open, not a draft, and aimed at the base branch;
  2. a reviewer approval, from the reviewer identity, on this head, with no standing request for changes;
  3. the human's approval on this head when the classification asks for it;
  4. every required check green on this head;
  5. an authoring role (`[role]` title prefix, else a `Role:` trailer) that owns every path it changes, or a
     `Cross-owner: <role>` line naming each owner it borrows from;
  6. the project's own checks, which are configuration or plugins and arrive as `extra_refusals`.

`run()` reads the facts, decides, and merges with `--match-head-commit`, then runs the post-merge checks and reverts
the merge when one fails.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from . import policy as pol
from .classify import Classification, classify
from .github import Facts

TITLE_ROLE = re.compile(r"^\s*\[([A-Za-z0-9_-]+)\]")
TRAILER_ROLE = re.compile(r"^Role:\s*([A-Za-z0-9_-]+)\s*$", re.M | re.I)
CROSS_OWNER = re.compile(r"^Cross-owner:\s*([A-Za-z0-9_-]+)\b", re.M | re.I)
REVERT_TRAILER = re.compile(r"^Gate-Revert:\s*#(\d+)\s+([0-9a-f]{7,40})\s*$", re.M)


def authoring_role(title: str, commit_messages: list[str]) -> str | None:
    m = TITLE_ROLE.match(title or "")
    if m:
        return m.group(1).lower()
    found = [r for msg in commit_messages for r in TRAILER_ROLE.findall(msg or "")]
    return found[-1].lower() if found else None


def borrowed_owners(body: str, commit_messages: list[str]) -> set[str]:
    texts = [body or "", *commit_messages]
    return {m.lower() for t in texts for m in CROSS_OWNER.findall(t or "")}


# -- reading the reviews ---------------------------------------------------------------------------------------

def is_reviewer(a: dict, policy: pol.Policy) -> bool:
    return a.get("type") == "Bot" and a.get("login") == policy.reviewer


def is_human(a: dict, policy: pol.Policy) -> bool:
    return a.get("type") == "User" and a.get("login") == policy.human


def trusted(a: dict, policy: pol.Policy) -> bool:
    return is_reviewer(a, policy) or is_human(a, policy)


def standing_reviews(facts: Facts, policy: pol.Policy, before: str | None = None) -> dict[tuple[str, str], str]:
    """The latest effective review state of each trusted identity, on this head only.

    A review counts only when it is bound to the head commit and (for the audit) submitted before `before`. COMMENTED
    and PENDING reviews assert nothing. A DISMISSED review is a withdrawal. A CHANGES_REQUESTED that someone other
    than the reviewer or the human dismissed still blocks: only they may lift it.
    """
    head = facts["head"]
    dismissals: dict[int, list[dict]] = {}
    for d in facts.get("dismissals") or []:
        dismissals.setdefault(d.get("review_id"), []).append(d)
    latest: dict[tuple[str, str], str] = {}
    for r in sorted(facts.get("reviews") or [], key=lambda x: x.get("submitted_at") or ""):
        a = r.get("author") or {}
        if not trusted(a, policy) or r.get("commit") != head:
            continue
        if before and (r.get("submitted_at") or "") >= before:
            continue
        state = r.get("state")
        if state == "DISMISSED":
            by = (dismissals.get(r.get("id")) or [{}])[-1]
            if trusted(by.get("actor") or {}, policy):
                state = None
            elif by.get("previous_state") == "CHANGES_REQUESTED":
                state = "CHANGES_REQUESTED"
            else:
                state = None
        elif state not in ("APPROVED", "CHANGES_REQUESTED"):
            state = None
        if state:
            latest[(a["login"], a["type"])] = state
    return latest


@dataclass
class Evidence:
    reviewer_approved: bool
    human_approved: bool
    blocking: list[str]


def evidence(facts: Facts, policy: pol.Policy, before: str | None = None) -> Evidence:
    latest = standing_reviews(facts, policy, before)
    blocking = [f"{login} requested changes on this head" for (login, _t), s in sorted(latest.items())
                if s == "CHANGES_REQUESTED"]
    rev = any(s == "APPROVED" and t == "Bot" and lg == policy.reviewer for (lg, t), s in latest.items())
    hum = any(s == "APPROVED" and t == "User" and lg == policy.human for (lg, t), s in latest.items())
    return Evidence(rev and not blocking, hum and not blocking, blocking)


# -- the decision ----------------------------------------------------------------------------------------------

def decide(facts: Facts, policy: pol.Policy, classification: Classification, *, base_branch: str = "main",
           live: bool = True, before: str | None = None, extra_refusals: list[str] | None = None) -> list[str]:
    """Why this pull request may not merge, as sentences. Empty means it may.

    `live=False` is the audit's reading of a merge that already happened: state, draft and base are moot, and
    `before` (the merge time) discards any review submitted at or after it.
    """
    no: list[str] = []
    n = facts["number"]
    if live:
        if facts["state"] != "OPEN":
            no.append(f"#{n} is {facts['state'].lower()}, not open")
        if facts.get("draft"):
            no.append(f"#{n} is a draft")
        if facts["base"] != base_branch:
            no.append(f"#{n} is aimed at {facts['base']!r}, not {base_branch!r}")
    ev = evidence(facts, policy, before)
    no.extend(ev.blocking)
    if not ev.reviewer_approved and not ev.blocking:
        no.append(f"no reviewer approval from {policy.reviewer!r} stands on head {facts['head'][:7]}")
    if classification.needs_human and not ev.human_approved and not ev.blocking:
        why = "; ".join(classification.reasons) or ", ".join(classification.classes)
        no.append(f"the human ({policy.human!r}) has not approved head {facts['head'][:7]}, and this change "
                  f"needs it: {why}")
    no.extend(check_results(facts, policy))
    no.extend(ownership(facts, policy, classification))
    no.extend(extra_refusals or [])
    return no


def check_results(facts: Facts, policy: pol.Policy) -> list[str]:
    """Every required check must be green on this head. A missing check is a failing check."""
    by_name: dict[str, str | None] = {}
    for c in facts.get("checks") or []:
        by_name[c["name"]] = c.get("conclusion")
    out = []
    for name in policy.required_checks:
        got = by_name.get(name)
        if got != "SUCCESS":
            out.append(f"required check {name!r} is {'missing' if name not in by_name else (got or 'pending').lower()} "
                       f"on head {facts['head'][:7]}")
    return out


def ownership(facts: Facts, policy: pol.Policy, c: Classification) -> list[str]:
    role = authoring_role(facts.get("title", ""), facts.get("commit_messages") or [])
    if role is None:
        return ["no authoring role: name it as `[role]` at the start of the title or in a `Role:` trailer"]
    if role not in policy.roles:
        return [f"the authoring role {role!r} is not a role in the policy"]
    borrowed = borrowed_owners(facts.get("body", ""), facts.get("commit_messages") or [])
    out = []
    for owner in c.owners:
        if owner != role and owner not in borrowed:
            out.append(f"a change by {role!r} touches paths owned by {owner!r}; add a "
                       f"`Cross-owner: {owner} - <reason>` line to the pull request")
    return out


# -- the work ---------------------------------------------------------------------------------------------------

CheckRunner = Callable[[pol.Policy, str], list[str]]


def run_project_checks(policy: pol.Policy, head: str, *, runner=None, git_runner=None) -> list[str]:
    """Run the project's own `checks` in a temporary worktree of the head; each failure is a refusal."""
    from .gitutil import run as _run
    run = runner or _run
    refusals: list[str] = []
    if not policy.checks:
        return refusals
    import tempfile
    with tempfile.TemporaryDirectory(prefix="onloop-gate-") as tmp:
        added = run(["git", "worktree", "add", "--detach", tmp, head])
        if added.returncode != 0:
            return [f"cannot check out the head to run the project's checks: {(added.stderr or '').strip()[:200]}"]
        try:
            for chk in policy.checks:
                p = run_shell(chk["run"], tmp)
                if p.returncode != 0:
                    refusals.append(f"project check {chk['name']!r} failed on the head (exit {p.returncode})")
        finally:
            run(["git", "worktree", "remove", "--force", tmp])
    return refusals


def run_shell(command: str, cwd: str):
    import subprocess
    return subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True)   # noqa: S602


def plugin_refusals(facts: Facts, policy: pol.Policy, c: Classification) -> list[str]:
    """Refusals from `onloop.checks` entry points: callables taking (facts, policy, classification) -> list[str]."""
    from importlib.metadata import entry_points
    out: list[str] = []
    for ep in entry_points(group="onloop.checks"):
        try:
            out.extend(ep.load()(facts, policy, c) or [])
        except Exception as e:                                                  # a broken plugin refuses, never passes
            out.append(f"plugin {ep.name!r} failed: {type(e).__name__}: {e}")
    return out


def plan_policy_change(git, files: list[str], head_rev: str) -> tuple[pol.Policy | None, str | None]:
    """The policy as the head leaves it, when the change touches the policy file: (policy, None), or (None, problem)."""
    if pol.POLICY_FILE not in files:
        return None, None
    text = git.show(head_rev, pol.POLICY_FILE)
    if text is None:
        return None, "the head deletes the policy file"
    try:
        return pol.loads(text), None
    except pol.PolicyError as e:
        return None, str(e)


def plan(facts: Facts, git, *, base_rev: str, base_branch: str = "main", extra: Callable[[Facts, pol.Policy,
         Classification], list[str]] | None = None) -> tuple[list[str], Classification | None, pol.Policy | None]:
    """Read the policy and the diff, classify, and decide. Returns (refusals, classification, base policy)."""
    base_text = git.show(base_rev, pol.POLICY_FILE)
    if base_text is None:
        return [f"{pol.POLICY_FILE} does not exist on {base_rev}; there is no policy to judge by"], None, None
    try:
        base = pol.loads(base_text)
    except pol.PolicyError as e:
        return [f"the policy on {base_rev} is unusable: {e}"], None, None
    files = git.changed_files(base_rev, facts["head"])
    head_policy, problem = plan_policy_change(git, files, facts["head"])
    c = classify(files, base, head_policy, problem)
    refusals = decide(facts, base, c, base_branch=base_branch, extra_refusals=(extra(facts, base, c) if extra else []))
    return refusals, c, base


def revert_merge(git, merge_sha: str, number: int, base_branch: str) -> str:
    """Undo a merge: a new commit that restores the tree before it, carrying the trailer the audit verifies."""
    git._run("checkout", base_branch)
    git._run("revert", "-m", "1", "--no-edit", merge_sha)
    msg = git.message("HEAD").rstrip() + f"\n\nGate-Revert: #{number} {merge_sha}\n"
    git._run("commit", "--amend", "-m", msg)
    git._run("push", "origin", f"HEAD:{base_branch}")
    return git.rev_parse("HEAD")


def run(number: int, client, git, *, base_branch: str = "main", extra: Callable | None = None,
        post_merge: Callable[[pol.Policy], list[str]] | None = None, dry_run: bool = False) -> tuple[int, list[str]]:
    """Gate one pull request. (0, notes) merged or would merge; (1, reasons) refused; (2, notes) merged then reverted."""
    facts = client.pr(number)
    refusals, c, base = plan(facts, git, base_rev=f"origin/{base_branch}", base_branch=base_branch, extra=extra)
    if refusals:
        return 1, refusals
    assert c is not None and base is not None
    if dry_run:
        return 0, [f"would merge #{number} at {facts['head'][:7]} (approvers needed: {', '.join(c.approvers)})"]
    client.merge(number, facts["head"])
    merged = client.pr(number)
    notes = [f"merged #{number} at {facts['head'][:7]}"]
    failed = post_merge(base) if post_merge else []
    if failed:
        sha = revert_merge(git, merged.get("merge_sha") or "", number, base_branch)
        return 2, notes + failed + [f"reverted as {sha[:7]}"]
    return 0, notes


# -- self-test ---------------------------------------------------------------------------------------------------

def _fixture() -> tuple[pol.Policy, Facts]:
    p = pol.from_dict({
        "human": "owner-a", "reviewer": "rev-bot", "agents": "agent-bot",
        "roles": {"builder": {}, "lead": {}},
        "paths": {"src/": {"owner": "builder"}, "docs/claims/": {"owner": "lead", "reserved": "claims"},
                  "onloop.yaml": {"owner": "lead", "tier": "full", "reserved": "authority_widening"}},
        "required_checks": ["ci"],
    })
    head = "a" * 40
    rev = {"id": 1, "author": {"login": "rev-bot", "type": "Bot"}, "state": "APPROVED", "commit": head,
           "submitted_at": "2026-01-01T00:00:00Z"}
    facts = {"number": 7, "state": "OPEN", "draft": False, "base": "main", "head": head, "title": "[builder] fix",
             "body": "", "commit_messages": [], "reviews": [rev], "dismissals": [],
             "checks": [{"name": "ci", "conclusion": "SUCCESS"}]}
    return p, facts


def self_test() -> tuple[bool, list[str]]:
    """The gate must pass the clean case and refuse each way the head's evidence can fail to hold."""
    p, facts = _fixture()
    c_src = classify(["src/a.py"], p)
    c_claims = classify(["docs/claims/x.md"], p)
    stale = dict(facts["reviews"][0], commit="b" * 40)
    human = {"id": 2, "author": {"login": "owner-a", "type": "User"}, "state": "APPROVED", "commit": facts["head"],
             "submitted_at": "2026-01-01T01:00:00Z"}
    cases = [
        ("a clean change merges", decide(facts, p, c_src), False),
        ("a stale-head reviewer approval is refused", decide(dict(facts, reviews=[stale]), p, c_src), True),
        ("an approval from the wrong identity is refused", decide(dict(facts, reviews=[dict(
            facts["reviews"][0], author={"login": "rev-bot", "type": "User"})]), p, c_src), True),
        ("a red required check is refused", decide(dict(facts, checks=[{"name": "ci", "conclusion": "FAILURE"}]),
                                                    p, c_src), True),
        ("a missing required check is refused", decide(dict(facts, checks=[]), p, c_src), True),
        ("a missing authoring role is refused", decide(dict(facts, title="fix"), p, c_src), True),
        ("a foreign path without Cross-owner is refused", decide(dict(facts, title="[builder] x"), p,
                                                                  classify(["docs/claims/x.md"], p)), True),
        ("a reserved class without the human is refused", decide(dict(facts, title="[lead] x"), p, c_claims), True),
        ("a reserved class with the human on the head merges", decide(dict(facts, title="[lead] x",
                                                                           reviews=facts["reviews"] + [human]),
                                                                       p, c_claims), False),
        ("a human approval of an older head is refused", decide(dict(facts, title="[lead] x", reviews=facts[
            "reviews"] + [dict(human, commit="c" * 40)]), p, c_claims), True),
        ("a draft is refused", decide(dict(facts, draft=True), p, c_src), True),
        ("a standing request for changes is refused", decide(dict(facts, reviews=facts["reviews"] + [dict(
            facts["reviews"][0], id=3, state="CHANGES_REQUESTED", submitted_at="2026-01-02T00:00:00Z")]), p,
            c_src), True),
    ]
    ok, lines = True, []
    for label, refusals, want_refused in cases:
        right = bool(refusals) == want_refused
        ok &= right
        lines.append(f"  {'ok' if right else 'FAILED'} {label}" + ("" if right else f" (got: {refusals})"))
    return ok, lines
