"""The `onloop` command line."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Sequence

from . import __version__, asapp, audit, codeowners, gate, ledger, watchdog
from . import classify as classify_mod
from . import init as init_mod
from . import policy as pol
from .github import GitHub
from .gitutil import Git


def _say(msg: str = "") -> None:
    print(msg)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _repo(args) -> str:
    repo = getattr(args, "repo", None) or os.environ.get("GITHUB_REPOSITORY")
    if repo:
        return repo
    p = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                       capture_output=True, text=True)
    if p.returncode != 0 or not p.stdout.strip():
        raise SystemExit("onloop: say which repository with --repo OWNER/NAME (or set GITHUB_REPOSITORY)")
    return p.stdout.strip()


def _policy(args) -> pol.Policy:
    try:
        return pol.load(args.policy)
    except pol.PolicyError as e:
        raise SystemExit(f"onloop: {e}") from e


# -- commands ----------------------------------------------------------------------------------------------------

def cmd_init(args) -> int:
    written, skipped = init_mod.init(args.dir, human=args.human, reviewer=args.reviewer, agents=args.agents,
                                     force=args.force)
    for w in written:
        _say(f"wrote   {w}")
    for s in skipped:
        _say(f"kept    {s} (exists; --force overwrites)")
    _say("")
    _say("Next: edit onloop.yaml (the three logins), then `onloop policy check` and `onloop codeowners --check`.")
    return 0


def cmd_policy_check(args) -> int:
    try:
        raw = pol.yaml.safe_load(pathlib.Path(args.policy).read_text(encoding="utf-8"))
    except (OSError, pol.yaml.YAMLError) as e:
        _err(f"onloop: cannot read {args.policy}: {e}")
        return 1
    problems = pol.validate(raw)
    if problems:
        for p in problems:
            _err(f"policy: {p}")
        return 1
    _say(f"policy ok: {len(raw['paths'])} paths, {len(raw['roles'])} roles")
    return 0


def cmd_codeowners(args) -> int:
    policy = _policy(args)
    path = pathlib.Path(args.file)
    if args.write:
        path.write_text(codeowners.generate(policy), encoding="utf-8", newline="\n")
        _say(f"wrote {path}")
        return 0
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    problems = codeowners.check(policy, existing)
    for p in problems:
        _err(p)
    if not problems:
        _say("CODEOWNERS matches the policy")
    return 1 if problems else 0


def cmd_classify(args) -> int:
    git = Git()
    head_policy, problem = None, None
    if args.paths:
        base = _policy(args)
        files = list(args.paths)
        if args.head_policy:
            try:
                head_policy = pol.load(args.head_policy)
            except pol.PolicyError as e:
                problem = str(e)
    else:
        base_text = git.show(args.base, pol.POLICY_FILE)
        if base_text is None:
            raise SystemExit(f"onloop: {pol.POLICY_FILE} does not exist at {args.base}")
        base = pol.loads(base_text)
        files = git.changed_files(args.base, args.head)
        head_policy, problem = gate.plan_policy_change(git, files, args.head)
    c = classify_mod.classify(files, base, head_policy, problem)
    if args.json:
        _say(json.dumps(c.as_dict(), indent=2))
    else:
        _say(f"approvers needed: {', '.join(c.approvers)}")
        _say(f"tier: {c.tier}")
        _say(f"reserved classes: {', '.join(c.classes) or 'none'}")
        for r in c.reasons:
            _say(f"  - {r}")
    return 0


def cmd_gate(args) -> int:
    policy = _policy(args)
    client = GitHub(_repo(args))
    git = Git()

    def extra(facts, base, c):
        out = gate.run_project_checks(base, facts["head"])
        out += gate.plugin_refusals(facts, base, c)
        return out

    def post_merge(base):
        return [f"post-merge check {c['name']!r} failed" for c in (policy.raw.get("post_merge_checks") or [])
                if gate.run_shell(c["run"], ".").returncode != 0]

    code, notes = gate.run(args.number, client, git, base_branch=args.base, extra=extra, post_merge=post_merge,
                           dry_run=args.dry_run)
    for n in notes:
        (_say if code == 0 else _err)(("" if code == 0 else "refused: ") + n if code == 1 else n)
    return code


def _since(git: Git, policy: pol.Policy, args) -> str | None:
    if args.since:
        return args.since
    configured = (policy.raw.get("audit") or {}).get("since")
    if configured:
        return str(configured)
    p = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "--", pol.POLICY_FILE], capture_output=True,
                       text=True)
    first = p.stdout.split()[-1] if p.stdout.split() else None
    return first


def cmd_audit(args) -> int:
    policy = _policy(args)
    git = Git()
    alarms = audit.audit(git, GitHub(_repo(args)), since=_since(git, policy, args), base_branch=args.base)
    for sha, why in alarms:
        _err(f"ALARM {sha[:7]}: {why}")
    if not alarms:
        _say("audit ok: every merge on the branch is one the policy allows")
    return 1 if alarms else 0


def cmd_as_app(args) -> int:
    cmd = list(args.command)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    policy = _policy(args)
    try:
        return asapp.run(cmd, args.identity, policy.raw, _repo(args))
    except asapp.AppError as e:
        _err(f"as-app: {e}")
        return 2


def cmd_ledger(args) -> int:
    try:
        data = ledger.load(args.file)
    except ledger.LedgerError as e:
        _err(f"ledger: {e}")
        return 1
    if args.action == "render":
        _say(ledger.render_html(data) if args.html else ledger.render_markdown(data))
        return 0
    if args.action == "next":
        it = ledger.next_item(data, args.role)
        if it is None:
            _say(f"{args.role}: the queue is empty; tell the lead")
            return 1
        _say(f"{it['id']} [{it['state']}] {it['title']}")
        return 0
    roles, previous = None, None
    try:
        roles = list(pol.load(args.policy).roles)
    except pol.PolicyError:
        pass
    if args.base:
        text = Git().show(args.base, ledger.LEDGER_FILE)
        previous = pol.yaml.safe_load(text) if text else None
    problems = ledger.check(data, roles, previous)
    for p in problems:
        _err(f"ledger: {p}")
    if not problems:
        _say("ledger ok")
    return 1 if problems else 0


def cmd_watchdog(args) -> int:
    policy = _policy(args)
    data = ledger.load(args.file)
    client = GitHub(_repo(args)) if not args.no_github else None
    prs = []
    if client is not None:
        for n in client.open_prs():
            f = client.pr(n)
            prs.append(f)
    found = watchdog.scan(data, prs, dt.datetime.now(dt.UTC), roles=list(policy.roles),
                          reviewer=policy.reviewer)
    for f in found:
        _say(f"- {f}")
    if not found:
        _say("nothing is stuck")
    return 1 if found else 0


def cmd_selftest(_args) -> int:
    ok = True
    for name, mod in (("policy loosening and approvers", classify_mod), ("merge gate", gate), ("main audit", audit),
                      ("agent identity", asapp), ("CODEOWNERS", codeowners), ("ledger", ledger),
                      ("watchdog", watchdog)):
        good, lines = mod.self_test()
        ok &= good
        _say(f"{name}: {'bites' if good else 'FAILED'}")
        for line in lines:
            _say(line)
    _say("verdict: every checker still bites" if ok else "verdict: a checker no longer bites")
    return 0 if ok else 1


# -- parser ---------------------------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="onloop", description="Run a team of AI agents on a GitHub repository with a "
                                 "human on the loop, not in it.")
    ap.add_argument("--version", action="version", version=f"onloop {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="<command>")

    def add(name, fn, help_, policy=True, repo=False):
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(fn=fn)
        if policy:
            sp.add_argument("--policy", default=pol.POLICY_FILE, help="path to onloop.yaml")
        if repo:
            sp.add_argument("--repo", help="OWNER/NAME (default: $GITHUB_REPOSITORY, else gh's current repository)")
        return sp

    sp = add("init", cmd_init, "write a starter policy, charters, ledger, CODEOWNERS and workflows", policy=False)
    sp.add_argument("--dir", default=".")
    sp.add_argument("--human", default="your-login", help="the human's GitHub login")
    sp.add_argument("--reviewer", default="your-reviewer-app", help="the reviewer App's bare login")
    sp.add_argument("--agents", default="your-agents-app", help="the agents App's bare login")
    sp.add_argument("--force", action="store_true", help="overwrite files that exist")

    pp = sub.add_parser("policy", help="work with onloop.yaml")
    psub = pp.add_subparsers(dest="action", metavar="<action>")
    sp = psub.add_parser("check", help="validate the policy")
    sp.add_argument("--policy", default=pol.POLICY_FILE)
    sp.set_defaults(fn=cmd_policy_check)

    sp = add("codeowners", cmd_codeowners, "check or write CODEOWNERS, generated from the policy")
    sp.add_argument("--file", default="CODEOWNERS")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="fail if CODEOWNERS differs from the policy (default)")
    g.add_argument("--write", action="store_true", help="write CODEOWNERS")

    sp = add("classify", cmd_classify, "say who must approve a change")
    sp.add_argument("--base", default="origin/main")
    sp.add_argument("--head", default="HEAD")
    sp.add_argument("--paths", nargs="+", help="classify these paths instead of a git diff")
    sp.add_argument("--head-policy", help="with --paths: the policy as the head leaves it")
    sp.add_argument("--json", action="store_true")

    sp = add("gate", cmd_gate, "merge a pull request if every condition holds on its exact head", repo=True)
    sp.add_argument("number", type=int)
    sp.add_argument("--base", default="main")
    sp.add_argument("--dry-run", action="store_true", help="decide, but do not merge")

    sp = add("audit", cmd_audit, "re-judge every merge on the main branch against the policy of its own time", repo=True)
    sp.add_argument("--since", help="first commit to audit (exclusive); default: where the policy first appeared")
    sp.add_argument("--base", default="main")

    sp = add("as-app", cmd_as_app, "run one git or gh command as a GitHub App identity", repo=True)
    sp.add_argument("--identity", choices=asapp.IDENTITIES, default="agents")
    sp.add_argument("command", nargs=argparse.REMAINDER, help="-- git ... or -- gh ...")

    lp = sub.add_parser("ledger", help="check, render or pull from ledger.yaml")
    lsub = lp.add_subparsers(dest="action", metavar="<action>")
    for action, help_ in (("check", "validate the ledger"), ("render", "print a status page"),
                          ("next", "what a role does next")):
        sp = lsub.add_parser(action, help=help_)
        sp.add_argument("--file", default=ledger.LEDGER_FILE)
        sp.add_argument("--policy", default=pol.POLICY_FILE)
        sp.set_defaults(fn=cmd_ledger, action=action)
        if action == "check":
            sp.add_argument("--base", help="a git ref whose ledger.yaml the changelog must still be a prefix of")
        if action == "render":
            sp.add_argument("--html", action="store_true")
        if action == "next":
            sp.add_argument("--role", required=True)

    sp = add("watchdog", cmd_watchdog, "flag stuck items, idle roles and pull requests waiting for a verdict", repo=True)
    sp.add_argument("--file", default=ledger.LEDGER_FILE)
    sp.add_argument("--no-github", action="store_true", help="read the ledger only")

    add("selftest", cmd_selftest, "prove every checker still bites", policy=False)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 0 if not argv else 2
    try:
        return int(args.fn(args))
    except SystemExit as e:
        if isinstance(e.code, str):
            _err(e.code)
            return 2
        return int(e.code or 0)


if __name__ == "__main__":
    sys.exit(main())
