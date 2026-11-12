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
from importlib.metadata import entry_points
from typing import NamedTuple

from . import gitutil, identity, procs
from . import policy as pol
from .classify import Classification, classify
from .github import Facts

TITLE_ROLE = re.compile(r"^\s*\[([A-Za-z0-9_-]+)\]")
TRAILER_ROLE = re.compile(r"^Role:\s*([A-Za-z0-9_-]+)\s*$", re.M | re.I)
BASE_REF = gitutil.BASE_REF
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
    # the same sanitiser as the reviewer's acknowledgement: a line inside a fence, a comment or a quote is an example,
    # not a claim
    return {m.lower() for t in texts for line in live_lines(t or "") for m in CROSS_OWNER.findall(line)}


# -- reading the reviews ---------------------------------------------------------------------------------------


def is_reviewer(a: dict, policy: pol.Policy) -> bool:
    return a.get("type") == "Bot" and bool(a.get("login")) and identity.is_login(a["login"], policy.reviewer)


def is_human(a: dict, policy: pol.Policy) -> bool:
    return a.get("type") == "User" and bool(a.get("login")) and identity.is_login(a["login"], policy.human)


def trusted(a: dict, policy: pol.Policy) -> bool:
    return is_reviewer(a, policy) or is_human(a, policy)


def standing_reviews(
    facts: Facts, policy: pol.Policy, before: str | None = None
) -> dict[tuple[str, str], str]:
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
    blocking = [
        f"{login} requested changes on this head"
        for (login, _t), s in sorted(latest.items())
        if s == "CHANGES_REQUESTED"
    ]
    rev = any(
        s == "APPROVED" and is_reviewer({"login": lg, "type": t}, policy) for (lg, t), s in latest.items()
    )
    hum = any(s == "APPROVED" and is_human({"login": lg, "type": t}, policy) for (lg, t), s in latest.items())
    return Evidence(rev and not blocking, hum and not blocking, blocking)


# -- the decision ----------------------------------------------------------------------------------------------


class Refusal(NamedTuple):
    """One reason the gate says no: a stable `code` for counting and a sentence for people."""

    code: str
    text: str


# The codes. P-codes are about the form of a pull request, which its author can fix before the gate ever runs;
# pre-flight reports exactly these. G-codes are about substance (state, evidence, checks).
CODES = {
    "G010": "the pull request is not open",
    "G011": "the pull request is a draft",
    "G012": "the pull request is aimed at another branch",
    "G020": "a trusted identity requested changes on this head",
    "G021": "no reviewer approval stands on this head",
    "G022": "the human has not approved this head and the change needs it",
    "G030": "a required check is missing on this head",
    "G031": "a required check is not green on this head",
    "G090": "an extra check refused it (plugin, fetch problem, unusable policy)",
    "G091": "a project check failed, or could not be run",
    "P001": "no authoring role",
    "P002": "the authoring role is not a role in the policy",
    "P003": "a path owned by another role, without Cross-owner",
    "P004": "a changed file gained carriage-return line endings (only where enforce.line_endings is refuse)",
    "P005": "a changed path no policy path covers (only where enforce.uncovered_paths is refuse)",
}
FORM_CODES = frozenset(c for c in CODES if c.startswith("P"))


def crlf_paths_from_patches(patches: dict[str, str] | None) -> list[str]:
    """The paths whose unified diff (as a host shows it) adds a line ending in a carriage return."""
    return sorted(
        p
        for p, patch in (patches or {}).items()
        if any(line.startswith("+") and line.endswith("\r") for line in (patch or "").split("\n"))
    )


def enforcement_refusals(policy: pol.Policy, files: list[str], gained_cr: list[str] | None) -> list[Refusal]:
    """P004 and P005, which exist only where the policy's `enforce` block says `refuse`. With no such block this is
    empty, so a policy that does not opt in is judged exactly as it was before. P004 fires on a file that GAINED a
    carriage return, never on one merely touched; P005 on a path no policy path matches. An unknown finding (the diff
    could not be read) refuses rather than passes."""
    out: list[Refusal] = []
    if policy.enforce.get("line_endings") == "refuse":
        if gained_cr is None:
            out.append(Refusal("P004", "the line endings of the change could not be checked"))
        elif gained_cr:
            out.append(
                Refusal("P004", "carriage-return line endings were added to: " + ", ".join(sorted(gained_cr)))
            )
    if policy.enforce.get("uncovered_paths") == "refuse":
        unowned = sorted(p for p in files if policy.match(p) is None)
        if unowned:
            out.append(Refusal("P005", "no policy path covers (nobody owns): " + ", ".join(unowned)))
    return out


def refusal_texts(refusals: list[Refusal]) -> list[str]:
    return [r.text for r in refusals]


def decide_refusals(
    facts: Facts,
    policy: pol.Policy,
    classification: Classification,
    *,
    base_branch: str = "main",
    live: bool = True,
    before: str | None = None,
    extra_refusals: list[str] | None = None,
) -> list[Refusal]:
    """Why this pull request may not merge, each reason with its code. Empty means it may.

    `live=False` is the audit's reading of a merge that already happened: state, draft and base are moot, and
    `before` (the merge time) discards any review submitted at or after it.
    """
    no: list[Refusal] = []
    n = facts["number"]
    if live:
        if facts["state"] != "OPEN":
            no.append(Refusal("G010", f"#{n} is {facts['state'].lower()}, not open"))
        if facts.get("draft"):
            no.append(Refusal("G011", f"#{n} is a draft"))
        if facts["base"] != base_branch:
            no.append(Refusal("G012", f"#{n} is aimed at {facts['base']!r}, not {base_branch!r}"))
    ev = evidence(facts, policy, before)
    no.extend(Refusal("G020", b) for b in ev.blocking)
    if not ev.reviewer_approved and not ev.blocking:
        no.append(
            Refusal(
                "G021", f"no reviewer approval from {policy.reviewer!r} stands on head {facts['head'][:7]}"
            )
        )
    if classification.needs_human and not ev.human_approved and not ev.blocking:
        why = "; ".join(classification.reasons) or ", ".join(classification.classes)
        no.append(
            Refusal(
                "G022",
                f"the human ({policy.human!r}) has not approved head {facts['head'][:7]}, and this "
                f"change needs it: {why}",
            )
        )
    no.extend(check_refusals(facts, policy))
    no.extend(ownership_refusals(facts, policy, classification))
    no.extend(enforcement_refusals(policy, classification.files, classification.gained_cr))
    no.extend(x if isinstance(x, Refusal) else Refusal("G090", x) for x in extra_refusals or [])
    return no


def decide(
    facts: Facts,
    policy: pol.Policy,
    classification: Classification,
    *,
    base_branch: str = "main",
    live: bool = True,
    before: str | None = None,
    extra_refusals: list[str] | None = None,
) -> list[str]:
    """`decide_refusals` as sentences. Empty means it may merge."""
    return refusal_texts(
        decide_refusals(
            facts,
            policy,
            classification,
            base_branch=base_branch,
            live=live,
            before=before,
            extra_refusals=extra_refusals,
        )
    )


def check_refusals(facts: Facts, policy: pol.Policy) -> list[Refusal]:
    """Every required check must be green on this head: present, and every run of that name on the head successful.

    A check can run more than once on a head (a re-run, a matrix, two workflows with one job name). One green run
    does not hide a red one, whatever order GitHub lists them in.
    """
    runs: dict[str, list[str | None]] = {}
    for c in facts.get("checks") or []:
        runs.setdefault(c["name"], []).append(c.get("conclusion"))
    out = []
    for name in policy.required_checks:
        got = runs.get(name)
        if not got:
            out.append(Refusal("G030", f"required check {name!r} is missing on head {facts['head'][:7]}"))
        elif any(r != "SUCCESS" for r in got):
            bad = sorted({(r or "pending").lower() for r in got if r != "SUCCESS"})
            out.append(
                Refusal(
                    "G031",
                    f"required check {name!r} is not green on head {facts['head'][:7]} ({', '.join(bad)})",
                )
            )
    return out


def check_results(facts: Facts, policy: pol.Policy) -> list[str]:
    return refusal_texts(check_refusals(facts, policy))


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HTML_COMMENT = re.compile(
    r"<!--.*?(?:-->|\Z)", re.S
)  # an unterminated one runs to the end, as GitHub renders it


def live_lines(body: str) -> list[str]:
    """The lines of a review body that are the reviewer's own words: not inside a ``` or ~~~ fence or an HTML comment.
    (A blockquoted or indented line never matches the anchored `Cross-owner:` pattern, so those need no code here.)"""
    out: list[str] = []
    fence: str | None = None
    for line in _HTML_COMMENT.sub("", body or "").split("\n"):
        line = line.rstrip("\r")
        m = _FENCE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            continue
        if m:
            fence = m.group(1)
            continue
        out.append(line)
    return out


def reviewer_ack(facts: Facts, policy: pol.Policy, owner: str) -> bool:
    """Has the reviewer identity quoted `Cross-owner: <owner>` in its standing approval of this head?

    Only a review by the reviewer identity counts (a Bot with the policy's reviewer login: the same text from a User of
    that name, or from the agents App, does not), on the current head, whose body carries the line, and only while that
    identity's latest verdict on the head is still an approval. The body is read from the facts the caller just
    fetched, so an edit to it after the approval is seen.
    """
    standing = standing_reviews(facts, policy)
    if not any(
        s == "APPROVED" and is_reviewer({"login": lg, "type": t}, policy) for (lg, t), s in standing.items()
    ):
        return False
    mine = [
        r
        for r in facts.get("reviews") or []
        if is_reviewer(r.get("author") or {}, policy)
        and r.get("commit") == facts["head"]
        and r.get("state") == "APPROVED"
    ]
    if not mine:
        return False
    latest = max(mine, key=lambda r: (r.get("submitted_at") or "", r.get("id") or 0))  # the standing approval
    line = re.compile(rf"^Cross-owner:\s*{re.escape(owner)}(?![A-Za-z0-9_-])", re.I)
    return any(line.match(x) for x in live_lines(latest.get("body") or ""))


def ownership_refusals(facts: Facts, policy: pol.Policy, c: Classification) -> list[Refusal]:
    role = authoring_role(facts.get("title", ""), facts.get("commit_messages") or [])
    if role is None:
        return [
            Refusal(
                "P001",
                "no authoring role: name it as `[role]` at the start of the title or in a `Role:` trailer",
            )
        ]
    if role not in policy.roles:
        return [Refusal("P002", f"the authoring role {role!r} is not a role in the policy")]
    borrowed = borrowed_owners(facts.get("body", ""), facts.get("commit_messages") or [])
    out = []
    ack = policy.automation.get("cross_owner") == "reviewer_ack"
    for owner in c.owners:
        if owner == role:
            continue
        if owner not in borrowed:
            out.append(
                Refusal(
                    "P003",
                    f"a change by {role!r} touches paths owned by {owner!r}; add a "
                    f"`Cross-owner: {owner} - <reason>` line to the pull request",
                )
            )
        elif ack and not reviewer_ack(facts, policy, owner):
            out.append(
                Refusal(
                    "P003",
                    f"a change by {role!r} touches paths owned by {owner!r}; the reviewer has not "
                    f"quoted `Cross-owner: {owner}` in its approval of this head",
                )
            )
    return out


def ownership(facts: Facts, policy: pol.Policy, c: Classification) -> list[str]:
    return refusal_texts(ownership_refusals(facts, policy, c))


# -- the work ---------------------------------------------------------------------------------------------------

Shell = Callable[[str, str | None], object]


def run_shell(command: str, cwd: str | None):
    return procs.run_text(command, shell=True, cwd=cwd)


def run_commands(
    git, commands: list[dict[str, str]], rev: str, what: str, *, shell: Shell = run_shell
) -> list[str]:
    """Run each `{name, run}` command in a clean, detached worktree of `rev`. Each failure is a reason, never a traceback.

    The commands come from the base policy, the one already in force, never from the head being judged.
    """
    if not commands:
        return []
    out: list[str] = []
    try:
        with git.in_worktree(rev) as wt:
            for c in commands:
                p = shell(c["run"], wt.cwd)
                if p.returncode != 0:
                    out.append(f"{what} {c['name']!r} failed on {rev[:7]} (exit {p.returncode})")
    except Exception as e:  # a worktree that cannot be made is a refusal
        out.append(f"cannot run the {what}s: {type(e).__name__}: {str(e)[:200]}")
    return out


def run_project_checks(git, policy: pol.Policy, head: str, *, shell: Shell = run_shell) -> list[str]:
    return run_commands(git, policy.checks, head, "project check", shell=shell)


def project_check_refusals(git, policy: pol.Policy, head: str, *, shell: Shell = run_shell) -> list[Refusal]:
    """The project's checks as refusals with their own code, so a report can tell them from every other reason."""
    return [Refusal("G091", t) for t in run_project_checks(git, policy, head, shell=shell)]


def run_post_merge_checks(git, policy: pol.Policy, merge_sha: str, *, shell: Shell = run_shell) -> list[str]:
    """The base policy's `post_merge_checks`, run in a worktree of the merge commit itself."""
    return run_commands(
        git, policy.raw.get("post_merge_checks") or [], merge_sha, "post-merge check", shell=shell
    )


def plugin_refusals(facts: Facts, policy: pol.Policy, c: Classification) -> list[str]:
    """Refusals from `onloop.checks` entry points: callables taking (facts, policy, classification) -> list[str].

    A plugin that raises refuses the merge. A broken check must never read as a passing one.
    """
    out: list[str] = []
    for ep in entry_points(group="onloop.checks"):
        try:
            out.extend(ep.load()(facts, policy, c) or [])
        except Exception as e:
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


def fetch_head(git, facts: Facts, base_branch: str) -> str | None:
    """Make the pull request's head and the base available locally; None when they are, else why not.

    The head is fetched from the pull request's own ref, and must be exactly the commit the decision is about: a head
    that moved since the facts were read is refused here, before anything is judged.
    """
    n = facts["number"]
    ref = f"refs/onloop/pr/{n}"
    try:
        git.fetch_pr(n, base_branch)
    except Exception as e:
        return f"cannot fetch #{n}: {str(e)[:200]}"
    try:
        got = git.rev_parse(ref)
    except Exception as e:
        return f"cannot read the fetched head of #{n}: {str(e)[:200]}"
    if got != facts["head"]:
        return f"the head of #{n} moved from {facts['head'][:7]} to {got[:7]} while it was being judged"
    if not git.has_commit(facts["head"]):
        return f"the head {facts['head'][:7]} of #{n} is not available locally"
    return None


def plan(
    facts: Facts,
    git,
    *,
    base_rev: str,
    base_branch: str = "main",
    extra: Callable[[Facts, pol.Policy, Classification], list[str]] | None = None,
) -> tuple[list[Refusal], Classification | None, pol.Policy | None]:
    """Read the policy and the diff, classify, and decide. Returns (refusals, classification, base policy)."""
    base_text = git.show(base_rev, pol.POLICY_FILE)
    if base_text is None:
        return (
            [
                Refusal(
                    "G090", f"{pol.POLICY_FILE} does not exist on {base_rev}; there is no policy to judge by"
                )
            ],
            None,
            None,
        )
    try:
        base = pol.loads(base_text)
    except pol.PolicyError as e:
        return [Refusal("G090", f"the policy on {base_rev} is unusable: {e}")], None, None
    try:
        files = git.changed_files(base_rev, facts["head"])
    except Exception as e:
        return [Refusal("G090", f"cannot compute the diff of #{facts['number']}: {str(e)[:200]}")], None, None
    head_policy, problem = plan_policy_change(git, files, facts["head"])
    c = classify(files, base, head_policy, problem)
    if base.enforce.get("line_endings") == "refuse":  # read only when the policy asks: nothing else changes
        try:
            c.gained_cr = git.gained_cr(base_rev, facts["head"])
        except Exception as e:
            return (
                [Refusal("G090", f"cannot read the line endings of #{facts['number']}: {str(e)[:200]}")],
                None,
                None,
            )
    refusals = decide_refusals(
        facts, base, c, base_branch=base_branch, extra_refusals=(extra(facts, base, c) if extra else [])
    )
    return refusals, c, base


def revert_merge(git, merge_sha: str, number: int, base_branch: str) -> str:
    """Undo a merge: a new commit that restores the tree before it, carrying the trailer the audit verifies.

    Made in a temporary worktree of the freshly fetched tip, so the operator's own checkout is never touched. It
    refuses, rather than guess, when it is not told which merge to undo.
    """
    if not merge_sha:
        raise ValueError("no merge commit to revert")
    git.fetch_base(base_branch)
    with git.in_worktree(BASE_REF) as wt:
        wt._run("revert", "-m", "1", "--no-edit", merge_sha)
        msg = wt.message("HEAD").rstrip() + f"\n\nGate-Revert: #{number} {merge_sha}\n"
        wt._run("commit", "--amend", "-m", msg)
        wt._run("push", "origin", f"HEAD:{base_branch}")
        return wt.rev_parse("HEAD")


Record = Callable[[str, int, dict], None]


def _refused(number: int, head: str, refusals: list[Refusal], record: Record | None) -> tuple[int, list[str]]:
    if record:
        record(
            "gate.refused",
            number,
            {
                "head": head,
                "codes": sorted({r.code for r in refusals}),
                "refusals": [{"code": r.code, "text": r.text} for r in refusals],
            },
        )
    return 1, refusal_texts(refusals)


def run(
    number: int,
    client,
    git,
    *,
    base_branch: str = "main",
    extra: Callable | None = None,
    expect_head: str | None = None,
    merge_trailer: str | None = None,
    post_merge: Callable[[pol.Policy, str], list[str]] | None = None,
    dry_run: bool = False,
    record: Record | None = None,
) -> tuple[int, list[str]]:
    """Gate one pull request.

    (0, notes) merged, or would merge; (1, reasons) refused; (2, notes) merged, then reverted because a post-merge
    check failed; (3, notes) merged, but the merge could not be fetched or named, so nothing was checked or reverted.

    `record(kind, number, payload)`, when given, is told of every real decision (`gate.refused` with its codes,
    `gate.merged`), never of a dry run, so the event log can count refusals by code.
    """
    if dry_run:
        record = None
    facts = client.pr(number)
    if expect_head is not None and facts["head"] != expect_head:  # the caller decided about another head
        return _refused(
            number,
            facts["head"],
            [
                Refusal(
                    "G090",
                    f"the head of #{number} is {facts['head'][:7]}, not "
                    f"the {expect_head[:7]} that was decided about",
                )
            ],
            record,
        )
    problem = fetch_head(git, facts, base_branch)
    if problem:
        return _refused(number, facts["head"], [Refusal("G090", problem)], record)
    refusals, c, base = plan(facts, git, base_rev=BASE_REF, base_branch=base_branch, extra=extra)
    if refusals:
        if record and base is not None and facts.get("state") == "MERGED" and facts.get("merge_sha"):
            # The merge already happened, so this run is refused (G010). If the agents identity made it, a crash
            # between the merge and its post-merge checks may have left them undone: say so (resuming is the autogate's
            # job). A merge by anyone else is only recorded as already merged.
            ours = facts.get("merged_by") == {"login": base.agents, "type": "Bot"}
            record(
                "gate.postmerge_pending" if ours else "gate.already_merged",
                number,
                {"head": facts["head"], "merge_sha": facts["merge_sha"], "merged_by": facts.get("merged_by")},
            )
        return _refused(number, facts["head"], refusals, record)
    assert c is not None and base is not None
    if dry_run:
        return 0, [
            f"would merge #{number} at {facts['head'][:7]} (approvers needed: {', '.join(c.approvers)})"
        ]
    # the project's checks took time: read the pull request again right before merging, so a review withdrawn, a
    # check turned red or a push made meanwhile stops the merge
    fresh = client.pr(number)
    if fresh["head"] != facts["head"]:
        return _refused(
            number,
            facts["head"],
            [
                Refusal(
                    "G090",
                    f"the head of #{number} moved from "
                    f"{facts['head'][:7]} to {fresh['head'][:7]} before the merge",
                )
            ],
            record,
        )
    again = decide_refusals(fresh, base, c, base_branch=base_branch)
    if again:
        return _refused(number, facts["head"], again, record)
    if merge_trailer is None:
        client.merge(number, facts["head"])
    else:
        client.merge(number, facts["head"], merge_trailer)
    notes = [f"merged #{number} at {facts['head'][:7]}"]
    if record:
        record("gate.merged", number, {"head": facts["head"], "approvers": c.approvers})
    if not (base.raw.get("post_merge_checks") or []):
        return 0, notes  # nothing to run after the merge: no fetch, no second read
    merge_sha = client.pr(number).get("merge_sha") or ""
    return after_merge(number, merge_sha, git, base, base_branch, notes, post_merge, record, facts["head"])


def after_merge(
    number: int,
    merge_sha: str,
    git,
    base: pol.Policy,
    base_branch: str,
    notes: list[str],
    post_merge: Callable[[pol.Policy, str], list[str]] | None,
    record: Record | None,
    head: str,
    *,
    revert: Callable[[str], str] | None = None,
) -> tuple[int, list[str]]:
    """The post-merge phase: fetch the merge, run the checks, revert when one fails.

    `revert(merge_sha)` is how a failing merge is undone; the autogate passes one that goes through its effects ledger.
    Records `gate.postmerge_checked` once the checks have actually run, so a crash before it can be told apart.
    """
    if not merge_sha:
        return 3, notes + [
            "GitHub reported no merge commit, so the post-merge checks could not run and nothing "
            "was reverted: look at the branch by hand"
        ]
    # the merge was made by GitHub, not by this clone: fetch it before anything is checked out at it
    try:
        git.fetch_base(base_branch)
    except Exception as e:
        return 3, notes + [
            f"cannot fetch the merge {merge_sha[:7]}, so the post-merge checks could not run and "
            f"nothing was reverted: {str(e)[:200]}"
        ]
    if not git.has_commit(merge_sha):
        return 3, notes + [
            f"the merge {merge_sha[:7]} is not available locally after a fetch, so the post-merge "
            "checks could not run and nothing was reverted: look at the branch by hand"
        ]
    failed = post_merge(base, merge_sha) if post_merge else []
    if record:
        record("gate.postmerge_checked", number, {"head": head, "merge_sha": merge_sha, "ok": not failed})
    if failed:
        sha = (revert or (lambda s: revert_merge(git, s, number, base_branch)))(merge_sha)
        if record:
            record("gate.reverted", number, {"head": head, "merge_sha": merge_sha, "revert_sha": sha})
        return 2, notes + failed + [f"reverted as {sha[:7]}"]
    return 0, notes


# -- self-test ---------------------------------------------------------------------------------------------------


def _fixture() -> tuple[pol.Policy, Facts]:
    p = pol.from_dict(
        {
            "human": "owner-a",
            "reviewer": "rev-bot",
            "agents": "agent-bot",
            "roles": {"builder": {}, "lead": {}},
            "paths": {
                "src/": {"owner": "builder"},
                "docs/claims/": {"owner": "lead", "reserved": "claims"},
                "onloop.yaml": {"owner": "lead", "tier": "full", "reserved": "authority_widening"},
            },
            "required_checks": ["ci"],
        }
    )
    head = "a" * 40
    rev = {
        "id": 1,
        "author": {"login": "rev-bot", "type": "Bot"},
        "state": "APPROVED",
        "commit": head,
        "submitted_at": "2026-01-01T00:00:00Z",
    }
    facts = {
        "number": 7,
        "state": "OPEN",
        "draft": False,
        "base": "main",
        "head": head,
        "title": "[builder] fix",
        "body": "",
        "commit_messages": [],
        "reviews": [rev],
        "dismissals": [],
        "checks": [{"name": "ci", "conclusion": "SUCCESS"}],
    }
    return p, facts


def self_test() -> tuple[bool, list[str]]:
    """The gate must pass the clean case and refuse each way the head's evidence can fail to hold."""
    p, facts = _fixture()
    c_src = classify(["src/a.py"], p)
    c_claims = classify(["docs/claims/x.md"], p)
    stale = dict(facts["reviews"][0], commit="b" * 40)
    human = {
        "id": 2,
        "author": {"login": "owner-a", "type": "User"},
        "state": "APPROVED",
        "commit": facts["head"],
        "submitted_at": "2026-01-01T01:00:00Z",
    }
    cases = [
        ("a clean change merges", decide(facts, p, c_src), False),
        ("a stale-head reviewer approval is refused", decide(dict(facts, reviews=[stale]), p, c_src), True),
        (
            "an approval from the wrong identity is refused",
            decide(
                dict(facts, reviews=[dict(facts["reviews"][0], author={"login": "rev-bot", "type": "User"})]),
                p,
                c_src,
            ),
            True,
        ),
        (
            "a red required check is refused",
            decide(dict(facts, checks=[{"name": "ci", "conclusion": "FAILURE"}]), p, c_src),
            True,
        ),
        (
            "a red run hides behind a green one (red first)",
            decide(
                dict(
                    facts,
                    checks=[{"name": "ci", "conclusion": "FAILURE"}, {"name": "ci", "conclusion": "SUCCESS"}],
                ),
                p,
                c_src,
            ),
            True,
        ),
        (
            "a red run hides behind a green one (green first)",
            decide(
                dict(
                    facts,
                    checks=[{"name": "ci", "conclusion": "SUCCESS"}, {"name": "ci", "conclusion": "FAILURE"}],
                ),
                p,
                c_src,
            ),
            True,
        ),
        ("a missing required check is refused", decide(dict(facts, checks=[]), p, c_src), True),
        ("a missing authoring role is refused", decide(dict(facts, title="fix"), p, c_src), True),
        (
            "a foreign path without Cross-owner is refused",
            decide(dict(facts, title="[builder] x"), p, classify(["docs/claims/x.md"], p)),
            True,
        ),
        (
            "a reserved class without the human is refused",
            decide(dict(facts, title="[lead] x"), p, c_claims),
            True,
        ),
        (
            "a reserved class with the human on the head merges",
            decide(dict(facts, title="[lead] x", reviews=facts["reviews"] + [human]), p, c_claims),
            False,
        ),
        (
            "a human approval of an older head is refused",
            decide(
                dict(facts, title="[lead] x", reviews=facts["reviews"] + [dict(human, commit="c" * 40)]),
                p,
                c_claims,
            ),
            True,
        ),
        ("a draft is refused", decide(dict(facts, draft=True), p, c_src), True),
        (
            "a standing request for changes is refused",
            decide(
                dict(
                    facts,
                    reviews=facts["reviews"]
                    + [
                        dict(
                            facts["reviews"][0],
                            id=3,
                            state="CHANGES_REQUESTED",
                            submitted_at="2026-01-02T00:00:00Z",
                        )
                    ],
                ),
                p,
                c_src,
            ),
            True,
        ),
    ]
    ok, lines = True, []
    for label, refusals, want_refused in cases:
        right = bool(refusals) == want_refused
        ok &= right
        lines.append(f"  {'ok' if right else 'FAILED'} {label}" + ("" if right else f" (got: {refusals})"))
    return ok, lines
