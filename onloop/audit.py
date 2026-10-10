"""The main-branch audit: every commit on the base branch must be one the policy would have allowed.

The gate decides before a merge; the audit checks afterwards, from the record, and alarms on anything that got
onto the branch some other way (a direct push, a merge no pull request explains, a merge of something other than the
pull request's head, a merge the policy of its own time would have refused).

It accepts, on the first-parent history since a starting commit, only:
  - a merge by the agents' identity whose second parent is the pull request's head and which the gate's own
    decision, re-run on the policy as it read at the merge's first parent, would have allowed (a review submitted
    at or after the merge does not count);
  - a merge by the human's account, whose second parent is the pull request's head;
  - a gate revert: a commit carrying `Gate-Revert: #N <sha>`, whose parent is that merge, where that merge is itself
    accepted, and whose tree equals the tree before it.
Anything else is an alarm. The policy is read at the merge's first parent, never at the branch tip, so a policy
changed later neither excuses nor condemns an earlier merge.
"""
from __future__ import annotations

from . import policy as pol
from .classify import classify
from .gate import REVERT_TRAILER, decide, plan_policy_change
from .testing import FakeGit, FakeGitHub


def history(git, client, *, since: str | None, base_branch: str = "main", limit: int | None = None) -> list[dict]:
    """One record per first-parent commit since `since` (the last `limit` of them, when given), in order.

    A record is `{sha, kind, ok, why, pr, title}`: `kind` is "merge", "human merge", "revert" or "direct push";
    `ok` says whether the policy of its own time would have allowed it, and `why` says why not. The dashboard shows
    these; `audit()` keeps the ones that are not ok. A revert is checked against the merge it names, which is
    judged on demand, so a window that starts after that merge still gives the right answer.
    """
    shas = git.first_parent_commits(since, base_branch)
    if limit:
        shas = shas[-limit:]
    memo: dict[str, dict] = {}

    def judge(sha: str) -> dict:
        if sha in memo:
            return memo[sha]
        rec = {"sha": sha, "kind": "merge", "ok": False, "why": "", "pr": None, "title": ""}
        memo[sha] = rec
        parents = git.parents(sha)
        m = REVERT_TRAILER.search(git.message(sha) or "")
        if m:
            rec.update(kind="revert", pr=int(m.group(1)), title="Gate revert")
            why = _revert(git, sha, parents, m, judge)
            rec.update(ok=not why, why=why)
            return rec
        if len(parents) != 2:
            rec.update(kind="direct push", why="a commit pushed straight to the branch, not a merge",
                       title=((git.message(sha) or "").splitlines() or [""])[0])
            return rec
        n = client.pr_for_commit(sha)
        if n is None:
            rec.update(why="a merge commit that no pull request explains")
            return rec
        facts = client.pr(n)
        rec.update(pr=n, title=facts.get("title") or "")
        if facts["head"] != parents[1]:
            rec.update(why=f"it merged {parents[1][:7]}, not the head of #{n} ({facts['head'][:7]})")
            return rec
        by = facts.get("merged_by") or {}
        why = _judge(git, facts, parents, by, base_branch)
        if not why and by.get("type") == "User":
            rec["kind"] = "human merge"
        rec.update(ok=not why, why=why or "")
        return rec

    return [judge(s) for s in shas]


def audit(git, client, *, since: str | None, base_branch: str = "main") -> list[tuple[str, str]]:
    """[(sha, reason)] for every commit that should not be on the branch. Empty means the history holds."""
    return [(r["sha"], r["why"]) for r in history(git, client, since=since, base_branch=base_branch) if not r["ok"]]


def _judge(git, facts, parents, by, base_branch) -> str | None:
    base_text = git.show(parents[0], pol.POLICY_FILE)
    if base_text is None:
        return f"#{facts['number']}: there was no policy at the merge's first parent {parents[0][:7]}"
    try:
        base = pol.loads(base_text)
    except pol.PolicyError as e:
        return f"#{facts['number']}: the policy at its first parent is unusable: {e}"
    if by.get("type") == "User" and by.get("login") == base.human:
        return None
    if not (by.get("type") == "Bot" and by.get("login") == base.agents):
        return f"#{facts['number']} was merged by {by.get('login')!r}, neither the gate's identity nor the human"
    files = git.changed_files(parents[0], parents[1])
    head_policy, problem = plan_policy_change(git, files, parents[1])
    c = classify(files, base, head_policy, problem)
    refusals = decide(facts, base, c, base_branch=base_branch, live=False, before=facts.get("merged_at"))
    if refusals:
        return f"the gate merged #{facts['number']}, but its own checks fail: {'; '.join(refusals)}"
    return None


def _revert(git, sha, parents, m, judge) -> str:
    """Why a Gate-Revert is not genuine, or "" when it is. `judge(sha)` returns the record of a commit."""
    num, named = m.group(1), m.group(2)
    if len(parents) != 1 or not (parents[0].startswith(named) or named.startswith(parents[0])):
        return f"Gate-Revert names #{num} {named[:7]}, which is not this commit's parent"
    target = judge(parents[0])
    if not target["ok"] or target["kind"] not in ("merge", "human merge"):
        return f"Gate-Revert names {named[:7]}, which is not an accepted merge in this audit"
    before = git.parents(parents[0])[0]
    if git.tree(sha) != git.tree(before):
        return f"Gate-Revert names {named[:7]} but its tree is not what that merge started from"
    return ""


# -- self-test ---------------------------------------------------------------------------------------------------

_POLICY = """
human: owner-a
reviewer: rev-bot
agents: agent-bot
roles: {builder: {}, lead: {}}
paths:
  src/: {owner: builder}
  docs/claims/: {owner: lead, reserved: claims}
  onloop.yaml: {owner: lead, tier: full, reserved: authority_widening}
required_checks: [ci]
"""


def _world(**over):
    head, merge, root = "b" * 40, "c" * 40, "a" * 40
    commits = {root: {"parents": [], "message": "root", "tree": "T0", "files": {pol.POLICY_FILE: _POLICY}},
               merge: {"parents": [root, head], "message": "Merge pull request #7", "tree": "T1", "files": {}}}
    rev = {"id": 1, "author": {"login": "rev-bot", "type": "Bot"}, "state": "APPROVED", "commit": head,
           "submitted_at": "2026-01-01T00:00:00Z"}
    facts = {"number": 7, "state": "MERGED", "draft": False, "base": "main", "head": head, "title": "[builder] fix",
             "body": "", "commit_messages": [], "reviews": [rev], "dismissals": [],
             "checks": [{"name": "ci", "conclusion": "SUCCESS"}], "merged_by": {"login": "agent-bot", "type": "Bot"},
             "merged_at": "2026-01-02T00:00:00Z", "merge_sha": merge}
    facts.update(over.pop("facts", {}))
    changed = {(root, head): over.pop("files", ["src/a.py"])}
    git = FakeGit(commits, changed, order=[root, merge])
    return git, FakeGitHub({7: facts}, {merge: 7}), root, merge


def self_test() -> tuple[bool, list[str]]:
    """The audit must accept a clean gate merge and alarm on each way a merge can fail to be one."""
    late = {"id": 9, "author": {"login": "rev-bot", "type": "Bot"}, "state": "APPROVED", "commit": "b" * 40,
            "submitted_at": "2026-01-03T00:00:00Z"}
    cases = [
        ("a clean gate merge is accepted", {}, False),
        ("a merge by someone else alarms", {"facts": {"merged_by": {"login": "someone", "type": "User"}}}, True),
        ("a merge by a User with the agents' name alarms", {"facts": {"merged_by": {"login": "agent-bot",
                                                                                     "type": "User"}}}, True),
        ("a merge of something other than the head alarms (even if that head was approved)",
         {"facts": {"head": "d" * 40, "reviews": [{"id": 1, "author": {"login": "rev-bot", "type": "Bot"},
                                                   "state": "APPROVED", "commit": "d" * 40,
                                                   "submitted_at": "2026-01-01T00:00:00Z"}]}}, True),
        ("a gate merge with no reviewer approval alarms", {"facts": {"reviews": []}}, True),
        ("a gate merge approved only after the merge alarms", {"facts": {"reviews": [late]}}, True),
        ("a gate merge with a red check alarms", {"facts": {"checks": [{"name": "ci", "conclusion": "FAILURE"}]}},
         True),
        ("a gate merge of a reserved path without the human alarms", {"files": ["docs/claims/x.md"],
                                                                      "facts": {"title": "[lead] x"}}, True),
    ]
    ok, lines = True, []
    for label, over, want_alarm in cases:
        git, client, root, _merge = _world(**over)
        alarms = audit(git, client, since=root)
        right = bool(alarms) == want_alarm
        ok &= right
        lines.append(f"  {'ok' if right else 'FAILED'} {label}" + ("" if right else f" (got: {alarms})"))
    git, client, root, merge = _world()
    git.commits["e" * 40] = {"parents": [merge], "message": "fix", "tree": "T2", "files": {}}
    git.order.append("e" * 40)
    right = any("straight" in why for _s, why in audit(git, client, since=root))
    ok &= right
    lines.append(f"  {'ok' if right else 'FAILED'} a direct push alarms")
    git, client, root, merge = _world()
    git.commits["e" * 40] = {"parents": [merge], "message": f"Revert\n\nGate-Revert: #7 {merge}\n", "tree": "T0",
                             "files": {}}
    git.order.append("e" * 40)
    right = not audit(git, client, since=root)
    ok &= right
    lines.append(f"  {'ok' if right else 'FAILED'} a genuine gate revert is accepted")
    git.commits["e" * 40]["tree"] = "T9"
    right = bool(audit(git, client, since=root))
    ok &= right
    lines.append(f"  {'ok' if right else 'FAILED'} a gate revert whose tree does not match alarms")
    return ok, lines
