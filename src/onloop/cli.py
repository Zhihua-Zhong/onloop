"""The `onloop` command line."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from collections.abc import Sequence

from . import (
    __version__,
    asapp,
    audit,
    codeowners,
    gate,
    gitutil,
    intake,
    kpi,
    ledger,
    watchdog,
)
from . import anchor as anchor_mod
from . import classify as classify_mod
from . import events as events_mod
from . import github as github_mod
from . import hooks as hooks_mod
from . import init as init_mod
from . import liveness as liveness_mod
from . import policy as pol
from . import preflight as preflight_mod
from . import slots as slots_mod
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
    found = github_mod.current_repo()
    if not found:
        raise SystemExit("onloop: say which repository with --repo OWNER/NAME (or set GITHUB_REPOSITORY)")
    return found


def _policy(args) -> pol.Policy:
    try:
        return pol.load(args.policy)
    except pol.PolicyError as e:
        raise SystemExit(f"onloop: {e}") from e


# -- commands ----------------------------------------------------------------------------------------------------


def cmd_init(args) -> int:
    written, skipped = init_mod.init(
        args.dir,
        human=args.human,
        reviewer=args.reviewer,
        agents=args.agents,
        force=args.force,
        install_spec=args.install_spec,
    )
    for w in written:
        _say(f"wrote   {w}")
    for s in skipped:
        _say(f"kept    {s} (exists; --force overwrites)")
    _say("")
    _say(
        "Next: edit onloop.yaml (the three logins), then `onloop policy check` and `onloop codeowners --check`."
    )
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
        for line in classify_mod.summary_lines(c):
            _say(line)
    return 0


def _recorder(log, repo: str, failed: list[str]):
    """A `record` callback for `gate.run` that appends each decision (`gate.merged`, `gate.refused`,
    `gate.postmerge_checked`, `gate.reverted`) to the overlay ledger as onloop's own (source `gate`). Recording is
    apart from deciding: an append that fails is noted in `failed`, never raised, so the gate decides as it would
    have without a log."""

    def record(kind: str, number: int, payload: dict) -> None:
        subject = f"pr#{number}"
        try:
            log.append(
                {
                    "id": events_mod.event_id(repo, kind, subject, f"{payload.get('head')}:{log.now()}"),
                    "source": "gate",
                    "kind": kind,
                    "subject_ref": subject,
                    "actor": "onloop",
                    "payload": payload,
                }
            )
        except Exception as e:  # noqa: BLE001 - a log that cannot take the decision must not change it
            failed.append(f"{kind} for {subject}: {type(e).__name__}: {e}"[:300])

    return record


def _gate_log():
    """The ledger the gate records into: the existing overlay ledger file, opened for writing, or why there is none.
    It never creates one: a gate run in a checkout without onloopd's ledger records nothing, and says so."""
    from .overlay.ledger import Ledger, LedgerError, default_path

    path = default_path()
    if not path.exists():
        return None, f"no ledger at {path}"
    try:
        return Ledger(path), None
    except (LedgerError, sqlite3.Error, OSError) as e:
        return None, f"the ledger at {path} could not be opened: {e}"


def cmd_gate(args) -> int:
    client = GitHub(_repo(args))
    git = Git()

    def extra(facts, base, c):
        return gate.project_check_refusals(git, base, facts["head"]) + gate.plugin_refusals(facts, base, c)

    def post_merge(base, merge_sha):
        return gate.run_post_merge_checks(git, base, merge_sha)

    log, missing = (
        (None, None) if args.dry_run else _gate_log()
    )  # a dry run decides nothing, so records nothing
    failed: list[str] = []
    try:
        code, notes = gate.run(
            args.number,
            client,
            git,
            base_branch=args.base,
            extra=extra,
            post_merge=post_merge,
            dry_run=args.dry_run,
            record=_recorder(log, client.repo, failed) if log else None,
        )
    finally:
        if log:
            log.close()
    for n in notes:
        if code == 0:
            _say(n)
        else:
            _err(("refused: " if code == 1 else "") + n)
    if not args.dry_run and (missing or failed):
        # Never silent: the decision stands, and the line says it is not in the log the KPIs count from.
        _err(f"onloop: warning: the gate's decision was not recorded ({missing or '; '.join(failed)})")
    return code


def _since(git: Git, policy: pol.Policy, args) -> str | None:
    if args.since:
        return args.since
    configured = (policy.raw.get("audit") or {}).get("since")
    if configured:
        return str(configured)
    p = subprocess.run(
        ["git", "log", "--diff-filter=A", "--format=%H", "--", pol.POLICY_FILE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
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
    roles, previous, human = None, None, None
    try:
        loaded = pol.load(args.policy)
        roles, human = list(loaded.roles), loaded.human
    except pol.PolicyError:
        pass
    if args.base:
        text = Git().show(args.base, ledger.LEDGER_FILE)
        previous = pol.yaml.safe_load(text) if text else None
    problems = ledger.check(data, roles, previous, human=human)
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
    found = watchdog.scan(
        data,
        prs,
        dt.datetime.now(dt.UTC),
        roles=list(policy.roles),
        reviewer=policy.reviewer,
    )
    for f in found:
        _say(f"- {f}")
    if not found:
        _say("nothing is stuck")
    return 1 if found else 0


def _approver_roles(policy: pol.Policy) -> dict[str, str]:
    """Which role each slot's requests wait on: the approver."""
    out = {}
    for name, spec in policy.slots.items():
        a = spec["approver"]
        out[name] = "onloop" if a == "auto" else policy.human if a == "human" else a.removeprefix("role:")
    return out


def cmd_kpi_ledger(args) -> int:
    """`onloop kpi weekly` and `onloop kpi baseline import`: the counters over the overlay ledger."""
    from .overlay.ledger import Ledger, LedgerError
    from .report import weekly

    try:
        if args.kpi_action == "weekly":
            with Ledger(_ledger_path(args), readonly=True) as led:
                report = weekly.build(led, args.coordinator, led.now())
            if args.json:
                _say(json.dumps(report, indent=2, sort_keys=True))
            else:
                for line in weekly.text(report):
                    _say(line)
            return 0
        if args.baseline_action != "import":
            _err("onloop: say which baseline action: import")
            return 2
        raw = pathlib.Path(args.file).read_bytes()
        try:
            obj = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError) as e:
            _err(f"onloop: {args.file} is not UTF-8 JSON: {e}")
            return 2
        problems = weekly.validate_baseline(obj)
        if problems:
            _err(f"onloop: {args.file} is not a KPI baseline (docs/schemas/kpi-baseline.schema.json):")
            for p in problems:
                _err(f"  - {p}")
            return 2
        with Ledger(_ledger_path(args)) as led:
            seqs = led.append_batch(weekly.baseline_events(obj))
        _say(f"imported {len(obj['weeks'])} week(s) from {args.file}: {len(seqs)} new or changed")
        return 0
    except OSError as e:
        _err(f"onloop: {e}")
        return 2
    except LedgerError as e:
        _err(f"onloop: {e}")
        return 2


def cmd_kpi(args) -> int:
    approvers, reviewer = {}, None
    if args.policy:
        try:
            loaded = pol.load(args.policy)
            approvers, reviewer = _approver_roles(loaded), loaded.reviewer
        except pol.PolicyError as e:
            _err(f"onloop: {e}")
            return 2
    from .overlay.ledger import Ledger, LedgerError

    try:
        with Ledger(_ledger_path(args), readonly=True) as led:
            report = kpi.kpi_report(
                kpi.LedgerEvents(led),
                since=args.since,
                until=args.until,
                slot_approvers=approvers,
                reviewer=reviewer,
            )
    except (LedgerError, ValueError, OSError, gitutil.GitError) as e:
        _err(f"onloop: {e}")
        return 1
    if args.json:
        _say(json.dumps(report, indent=2))
    else:
        for line in kpi.kpi_text(report):
            _say(line)
    return 0


def _ledger(path: str) -> dict:
    try:
        return ledger.load(path)
    except ledger.LedgerError as e:
        raise SystemExit(f"onloop: {e}") from e


def cmd_anchor(args) -> int:
    """Print an anchor composed from the policy and the ledger. Reads two files and nothing else."""
    try:
        policy = pol.load(args.policy)
        _say(anchor_mod.compose(args.kind, policy, _ledger(args.ledger), args.role))
    except (pol.PolicyError, KeyError) as e:
        _err(f"onloop: {e}")
        return 2
    return 0


def cmd_hooks(args) -> int:
    """Print the hooks configuration for a role, or with --liveness the state-recording hooks. It writes no file."""
    if args.liveness:
        _say(
            json.dumps(
                hooks_mod.liveness_config(
                    command=args.command, directory=args.liveness_dir, only_under=args.only_under
                ),
                indent=2,
            )
        )
        return 0
    if not args.role:
        _err("onloop: --role is required unless --liveness is given")
        return 2
    try:
        policy = pol.load(args.policy)
        cfg = hooks_mod.config(
            policy, args.role, command=args.command, policy_path=args.policy, ledger_path=args.ledger
        )
    except (pol.PolicyError, KeyError, ValueError) as e:
        _err(f"onloop: {e}")
        return 2
    _say(json.dumps(cfg, indent=2))
    return 0


def cmd_liveness(args) -> int:
    """Record a session's state from a hook payload on stdin. Always exits 0 (see liveness.py)."""
    return liveness_mod.main_record(args.event, args.dir, args.only_under)


def cmd_events(args) -> int:
    """List the ledger's events, with the filters `/api/events` takes. Opens the ledger read-only."""
    from .overlay.ledger import Ledger, LedgerError
    from .report import views

    params = {
        k: [str(v)]
        for k, v in (
            ("kind", args.kind),
            ("subject", args.subject),
            ("source", args.source),
            ("actor", args.actor),
            ("since", args.since),
            ("until", args.until),
            ("after", args.after),
            ("limit", args.limit),
        )
        if v is not None
    }
    try:
        with Ledger(_ledger_path(args), readonly=True) as led:
            rows = views.events_query(led, params)["events"]
    except (LedgerError, views.QueryError) as e:
        _err(f"onloop: {e}")
        return 1
    if args.json:
        _say(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        _say(
            f"{r['seq']:>5} {r['ts_system']} {r['source']:<12} {r['kind']:<17} {r['subject_ref']:<14} "
            f"{r['actor'] or '-':<16} {json.dumps(r['payload'], sort_keys=True)}"
        )
    if not rows:
        _say("no events")
    return 0


def cmd_preflight(args) -> int:
    """Report a pull request's form problems and the slots it needs. With --post, keep the one pre-flight comment current."""
    policy = _policy(args)
    client = GitHub(_repo(args))
    try:
        facts = client.pr(args.number)
        rep = preflight_mod.report(
            facts, policy, client.pr_files(args.number), client.pr_patches(args.number)
        )
        if args.json:
            _say(json.dumps(rep, indent=2))
        else:
            for line in preflight_mod.text(rep):
                _say(line)
        if args.post:
            body = preflight_mod.comment_body(rep)
            existing = client.find_comment(
                args.number, preflight_mod.MARKER, preflight_mod.comment_authors(policy)
            )
            if existing is not None:
                if not existing["body"].strip().startswith(body.strip()):
                    client.update_comment(existing["id"], body)
            elif not (preflight_mod.clean(rep) and not rep["slots"] and not rep["warnings"]):
                client.comment(args.number, body)
    except Exception as e:  # a GitHub error must not read as "no problems"
        _err(f"onloop: {e}")
        return 2
    return 0 if preflight_mod.clean(rep) else 1


def cmd_slot(args) -> int:
    """Slots: need, request, wait, grant, renew, release, status. Only `need` and `status` leave the ledger alone."""
    if getattr(args, "db", None):
        _err(
            "onloop: the slots live in the overlay ledger since 0.3.0: use --ledger FILE (default .onloop/ledger.db)"
        )
        return 2
    policy = _policy(args)
    if args.action == "need":
        for name in slots_mod.need(policy, args.paths):
            _say(name)
        return 0
    from .overlay.ledger import Ledger, LedgerError

    try:
        if args.action == "status":
            with Ledger(_ledger_path(args), readonly=True) as st:
                state = slots_mod.status(st, policy)
            if args.json:
                _say(json.dumps(state, indent=2))
            else:
                for line in slots_mod.status_text(state):
                    _say(line)
            return 0
        repo = _repo(args)
        with Ledger(_ledger_path(args)) as st:
            if args.action == "request":
                res = slots_mod.request(
                    st,
                    repo,
                    policy,
                    args.slot,
                    holder=args.holder,
                    role=args.role,
                    reason=args.reason,
                    ref=args.ref,
                    paths=args.paths,
                )
                _say(json.dumps(res))
                return 0
            if args.action == "wait":
                code, g = slots_mod.wait(
                    st,
                    repo,
                    policy,
                    args.slot,
                    holder=args.holder,
                    ref=args.ref,
                    timeout=args.timeout,
                    poll=args.poll,
                )
                if g:
                    for line in slots_mod.grant_text(g):
                        _say(line)
                elif code == slots_mod.EXIT_EXPIRED:
                    _err("onloop: the request or its lease expired")
                else:
                    _err("onloop: timed out waiting for the slot")
                return code
            if args.action == "grant":
                anchor = None
                if args.anchor:
                    anchor = slots_mod.verify_anchor(
                        GitHub(repo),
                        repo,
                        policy,
                        slots_mod.request_row(st, args.request_id),
                        args.anchor,
                        st.instance(),
                    )
                _say(
                    f"granted {slots_mod.grant(st, repo, policy, args.request_id, by=args.by, by_role=args.as_role, anchor=anchor)}"
                )
                return 0
            if args.action == "renew":
                _say(json.dumps(slots_mod.renew(st, repo, policy, args.grant_id, holder=args.holder)))
                return 0
            if args.action == "release":
                nxt = slots_mod.release(
                    st, repo, policy, args.grant_id, reason=args.reason, holder=args.holder, by=args.by
                )
                _say(f"released; next grant: {nxt}" if nxt else "released")
                return 0
    except (slots_mod.SlotError, LedgerError) as e:
        _err(f"onloop: {e}")
        return 1
    _err("onloop: say which slot action: need, request, wait, grant, renew, release or status")
    return 2


def _ledger_path(args) -> pathlib.Path:
    from .overlay.ledger import default_path

    return pathlib.Path(args.ledger) if getattr(args, "ledger", None) else default_path()


def cmd_overlay(args) -> int:
    """The overlay ledger: import the legacy stores into it, replay it, or print its state."""
    import hashlib

    from .overlay import importer
    from .overlay.ledger import Ledger, LedgerError

    if args.action not in ("import", "replay", "status"):
        _err("onloop: say which overlay action: import, replay or status")
        return 2
    path = _ledger_path(args)
    try:
        if args.action == "status":
            with Ledger(path, readonly=True) as led:
                _say(json.dumps(led.health(), indent=2, sort_keys=True))
            return 0
        with Ledger(path) as led:
            if args.action == "import":
                if not (args.store or args.workgraph):
                    _err("onloop: name a legacy store to import: --store PATH and/or --workgraph PATH")
                    return 2
                failed = False
                for given, fn in (
                    (args.store, importer.import_poll_store),
                    (args.workgraph, importer.import_work_graph),
                ):
                    if not given:
                        continue
                    try:
                        r = fn(led, given)
                    except (OSError, sqlite3.Error) as e:
                        _err(f"{given}: not imported: {e}")
                        failed = True
                        continue
                    _say(f"{r.source}: {r.read} read, {r.appended} new, cursor at {r.cursor}")
                return 1 if failed else 0
            if args.check:
                equal, live, rebuilt = led.rebuild_matches()
                digest = hashlib.sha256(rebuilt.encode("utf-8")).hexdigest()
                _say(
                    f"rebuilt {led.last_seq()} events into a scratch copy: projections "
                    f"{'byte-identical to the live ones' if equal else 'DIFFERENT from the live ones'} (sha256 {digest})"
                )
                return 0 if equal else 1
            before = led.dump()
            after = led.replay()
            digest = hashlib.sha256(after.encode("utf-8")).hexdigest()
            _say(
                f"replayed {led.last_seq()} events; projections "
                f"{'byte-identical to the ones before' if after == before else 'CHANGED by the replay'} (sha256 {digest})"
            )
            return 0 if after == before else 1
    except LedgerError as e:
        _err(f"onloop: {e}")
        return 2


def cmd_item(args) -> int:
    """An item in the overlay ledger: attach the evidence a follow-up needs."""
    import uuid

    from .overlay.ledger import Ledger, LedgerError

    if args.action != "evidence":
        _err("onloop: say which item action: evidence")
        return 2
    ref = (args.ref or "").strip()
    if not ref or len(ref) > 200 or any(c in ref for c in "\r\n"):
        _err("onloop: the evidence is a reference of at most 200 characters on one line (a URL, a PR, a log)")
        return 2
    try:
        with Ledger(_ledger_path(args)) as led:
            rows = led.select("SELECT * FROM items WHERE id = ?", (args.id,))
            if not rows:
                _err(f"onloop: no item {args.id!r} in the ledger")
                return 1
            if not rows[0]["evidence_required"]:
                _err(f"onloop: item {args.id!r} does not ask for evidence")
                return 1
            if rows[0]["evidence_ref"]:
                _err(f"onloop: item {args.id!r} already has evidence: {rows[0]['evidence_ref']}")
                return 1
            led.append(
                {
                    "id": f"ui-{uuid.uuid4().hex}",
                    "source": "ui",
                    "kind": "item.evidence",
                    "subject_ref": args.id,
                    "actor": "cli",
                    "payload": {"id": args.id, "evidence_ref": ref},
                }
            )
            parent = rows[0]["parent_id"]
            state = led.select("SELECT state FROM items WHERE id = ?", (parent,)) if parent else []
    except LedgerError as e:
        _err(f"onloop: {e}")
        return 2
    _say(f"{args.id}: evidence {ref}; done" + (f"; {parent} is {state[0]['state']}" if state else ""))
    return 0


def cmd_report(args) -> int:
    """Reports over the overlay ledger."""
    from . import events as ev_mod
    from .overlay.ledger import Ledger, LedgerError
    from .report import observe

    if args.action != "observe":
        _err("onloop: say which report: observe")
        return 2
    try:
        since = ev_mod.norm_ts(args.since)
        until = ev_mod.norm_ts(args.until) if args.until else None
    except ev_mod.EventError as e:
        _err(f"onloop: --since or --until: {e}")
        return 2
    try:
        with Ledger(_ledger_path(args), readonly=True) as led:
            report = observe.build(led, since, until or led.now())
    except LedgerError as e:
        _err(f"onloop: {e}")
        return 2
    if args.json:
        _say(json.dumps(report, indent=2, sort_keys=True))
    else:
        for line in observe.text(report):
            _say(line)
    return 0


def cmd_ingest(args) -> int:
    """`onloop ingest github --once`: one read of a repository into the overlay ledger, by GETs only."""
    from . import github as github_mod
    from .daemon import config as daemon_config
    from .daemon import main as daemon
    from .overlay.ledger import Ledger, LedgerError

    if args.source != "github" or not args.once:
        _err(
            "onloop: say `onloop ingest github --once --repo owner/name`; onloopd runs the ingest continuously"
        )
        return 2
    try:
        watch = daemon_config.parse(
            {
                "github": {
                    k: v
                    for k, v in (
                        ("repo", args.repo),
                        ("base", args.base),
                        ("workdir", args.workdir),
                        ("policy", args.policy),
                    )
                    if v is not None
                }
            }
        ).github
    except daemon_config.ConfigError as e:
        _err(f"onloop: {'; '.join(e.problems)}")
        return 2
    try:
        source = daemon.GitHubSource(watch, client=github_mod.GitHub(watch.repo))
    except pol.PolicyError as e:
        _err(f"onloop: {e}")
        return 2
    try:
        with Ledger(_ledger_path(args)) as led:
            got = source(led)
            cursor = led.ingest_state(f"github:{watch.repo}")
    except (github_mod.GitHubError, LedgerError, OSError) as e:
        _err(f"onloop: the read failed and nothing was written: {e}")
        return 1
    snaps = sum(1 for k in cursor if k.startswith("snap:"))
    _say(
        f"github:{watch.repo}: {got['appended']} new event(s); "
        f"{'list unchanged (304)' if got['not_modified'] else 'list read'}; {snaps} pull request(s) known"
    )
    return 0


def cmd_role(args) -> int:
    """Pause or resume a role (source `ui`): a paused role's items wait on the human, with reason paused_by_human."""
    import re
    import uuid

    from .overlay.ledger import Ledger, LedgerError

    if args.action not in ("pause", "resume"):
        _err("onloop: say which role action: pause or resume")
        return 2
    if not re.match(r"^[a-z][a-z0-9_-]{0,39}$", args.role or ""):
        _err("onloop: a role name is lowercase letters, digits, '_' or '-'")
        return 2
    try:
        with Ledger(_ledger_path(args)) as led:
            rows = led.select("SELECT state FROM roles WHERE id = ?", (args.role,))
            paused = bool(rows) and rows[0]["state"] == "paused_by_human"
            if (args.action == "pause") == paused:
                _say(f"{args.role} is already {'paused' if paused else 'not paused'}; nothing changed")
                return 0
            led.append(
                {
                    "id": f"ui-{uuid.uuid4().hex}",
                    "source": "ui",
                    "kind": f"role.{'paused' if args.action == 'pause' else 'resumed'}",
                    "subject_ref": f"role:{args.role}",
                    "actor": "cli",
                    "payload": {"id": args.role},
                }
            )
            held = led.select(
                "SELECT COUNT(*) AS n FROM waits WHERE id LIKE ? AND cleared_at IS NULL",
                (f"pause:{args.role}:%",),
            )[0]["n"]
    except LedgerError as e:
        _err(f"onloop: {e}")
        return 2
    if args.action == "pause":
        _say(f"{args.role} paused; {held} item(s) now wait on the human")
    else:
        _say(f"{args.role} resumed")
    return 0


def cmd_daemon(args) -> int:
    """onloopd: run it in the foreground, ask it to stop, or print its state."""
    import urllib.request

    from .daemon import main as daemon
    from .daemon import server as daemon_server

    if args.action not in ("run", "stop", "status", "hook"):
        _err("onloop: say which daemon action: run, stop, status or hook")
        return 2
    if args.action == "hook":
        from .daemon import hook_client

        return hook_client.main(args.event, args.role, _ledger_path(args), args.timeout)
    ledger = _ledger_path(args)
    state = ledger.resolve().parent
    if args.action == "run":
        from .daemon import config as daemon_config

        config_path = pathlib.Path(args.config) if args.config else state / daemon_config.CONFIG_FILE
        try:
            watch = (
                daemon_config.load(config_path)
                if args.config or config_path.exists()
                else daemon_config.OnloopdConfig()
            )
        except daemon_config.ConfigError as e:
            _err(f"onloop: onloopd does not start: {config_path}:")
            for problem in e.problems:
                _err(f"  - {problem}")
            return 2
        cfg = daemon.DaemonConfig(
            watch=watch,
            ledger=ledger,
            port=args.port if args.port is not None else (watch.port or daemon_server.DEFAULT_PORT),
            host=args.host or daemon_server.HOST,
            tick_s=args.tick,
        )
        try:
            return daemon.run(
                cfg, say=lambda m: print(m, flush=True)
            )  # a redirected log shows each line at once
        except daemon_server.NotLoopback as e:
            _err(f"onloop: {e}")
            return 2
    if args.action == "stop":
        code, message = daemon.request_stop(state, timeout_s=args.timeout)
        (_say if code in (0, 1) else _err)(message)
        return code
    beat_path = state / daemon.HEARTBEAT_FILE
    beat = json.loads(beat_path.read_text(encoding="utf-8")) if beat_path.exists() else None
    if not beat:
        _say(f"onloopd: no heartbeat in {state}")
        return 1
    try:
        url = f"http://{daemon_server.HOST}:{beat['port']}/health"
        with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 - the loopback address only
            health = json.loads(r.read().decode("utf-8"))
    except OSError as e:
        _say(f"onloopd: last heartbeat {beat['at']} from pid {beat['pid']}, but /health did not answer: {e}")
        return 1
    _say(json.dumps({"heartbeat": beat, "health": health}, indent=2, sort_keys=True))
    return 0


def cmd_supervise(args) -> int:
    from .supervisor import commands as sv

    return sv.cmd_supervise(args)


def cmd_step(args) -> int:
    from .supervisor import commands as sv
    from .supervisor import onestep

    return onestep.cmd_step(args, sv._cfg)


def cmd_digest(args) -> int:
    from .supervisor import commands as sv

    return sv.cmd_digest(args)


def cmd_attention(args) -> int:
    from .supervisor import commands as sv

    return sv.cmd_attention(args)


def cmd_selftest(_args) -> int:
    from . import disposition as disposition_mod
    from .detect import no_outcome as detect_no_outcome
    from .detect import schedules as detect_schedules
    from .detect import wakers as detect_wakers
    from .supervisor import attention as sv_attention

    ok = True
    for name, mod in (
        ("policy loosening and approvers", classify_mod),
        ("merge gate", gate),
        ("main audit", audit),
        ("agent identity", asapp),
        ("CODEOWNERS", codeowners),
        ("ledger", ledger),
        ("watchdog", watchdog),
        ("slots", slots_mod),
        ("pre-flight", preflight_mod),
        ("KPIs", kpi),
        ("anchors", anchor_mod),
        ("intake", intake),
        ("supervisor attention", sv_attention),
        ("turn outcome line", disposition_mod),
        ("detector: a turn without a valid outcome", detect_no_outcome),
        ("detector: a continue whose waker is not alive", detect_wakers),
        ("detector: a scheduled job that failed or did not run", detect_schedules),
    ):
        good, lines = mod.self_test()
        ok &= good
        _say(f"{name}: {'bites' if good else 'FAILED'}")
        for line in lines:
            _say(line)
    _say("verdict: every checker still bites" if ok else "verdict: a checker no longer bites")
    return 0 if ok else 1


# -- parser ---------------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="onloop",
        description="Run a team of AI agents on a GitHub repository with a human on the loop, not in it.",
    )
    ap.add_argument("--version", action="version", version=f"onloop {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="<command>")

    def add(name, fn, help_, policy=True, repo=False):
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(fn=fn)
        if policy:
            sp.add_argument("--policy", default=pol.POLICY_FILE, help="path to onloop.yaml")
        if repo:
            sp.add_argument(
                "--repo", help="OWNER/NAME (default: $GITHUB_REPOSITORY, else gh's current repository)"
            )
        return sp

    sp = add(
        "init", cmd_init, "write a starter policy, charters, ledger, CODEOWNERS and workflows", policy=False
    )
    sp.add_argument("--dir", default=".")
    sp.add_argument("--human", default="your-login", help="the human's GitHub login")
    sp.add_argument("--reviewer", default="your-reviewer-app", help="the reviewer App's bare login")
    sp.add_argument("--agents", default="your-agents-app", help="the agents App's bare login")
    sp.add_argument(
        "--install-spec",
        default=init_mod.DEFAULT_INSTALL_SPEC,
        help="what the workflows `pip install` (default: the tagged release on GitHub)",
    )
    sp.add_argument("--force", action="store_true", help="overwrite files that exist")

    pp = sub.add_parser("policy", help="work with onloop.yaml")
    psub = pp.add_subparsers(dest="action", metavar="<action>")
    sp = psub.add_parser("check", help="validate the policy")
    sp.add_argument("--policy", default=pol.POLICY_FILE)
    sp.set_defaults(fn=cmd_policy_check)

    sp = add("codeowners", cmd_codeowners, "check or write CODEOWNERS, generated from the policy")
    sp.add_argument("--file", default="CODEOWNERS")
    g = sp.add_mutually_exclusive_group()
    g.add_argument(
        "--check", action="store_true", help="fail if CODEOWNERS differs from the policy (default)"
    )
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

    sp = add(
        "audit",
        cmd_audit,
        "re-judge every merge on the main branch against the policy of its own time",
        repo=True,
    )
    sp.add_argument(
        "--since", help="first commit to audit (exclusive); default: where the policy first appeared"
    )
    sp.add_argument("--base", default="main")

    sp = add("as-app", cmd_as_app, "run one git or gh command as a GitHub App identity", repo=True)
    sp.add_argument("--identity", choices=asapp.IDENTITIES, default="agents")
    sp.add_argument("command", nargs=argparse.REMAINDER, help="-- git ... or -- gh ...")

    lp = sub.add_parser("ledger", help="check, render or pull from ledger.yaml")
    lsub = lp.add_subparsers(dest="action", metavar="<action>")
    for action, help_ in (
        ("check", "validate the ledger"),
        ("render", "print a status page"),
        ("next", "what a role does next"),
    ):
        sp = lsub.add_parser(action, help=help_)
        sp.add_argument("--file", default=ledger.LEDGER_FILE)
        sp.add_argument("--policy", default=pol.POLICY_FILE)
        sp.set_defaults(fn=cmd_ledger, action=action)
        if action == "check":
            sp.add_argument(
                "--base", help="a git ref whose ledger.yaml the changelog must still be a prefix of"
            )
        if action == "render":
            sp.add_argument("--html", action="store_true")
        if action == "next":
            sp.add_argument("--role", required=True)

    sp = add(
        "watchdog",
        cmd_watchdog,
        "flag stuck items, idle roles and pull requests waiting for a verdict",
        repo=True,
    )
    sp.add_argument("--file", default=ledger.LEDGER_FILE)
    sp.add_argument("--no-github", action="store_true", help="read the ledger only")

    sp = add("kpi", cmd_kpi, "measurements over the event log (read-only)", policy=False)
    ksub = sp.add_subparsers(dest="kpi_action", metavar="[weekly | baseline]")
    ksub.required = False
    kw = ksub.add_parser(
        "weekly",
        help="asks to the human and messages to the coordinator per ISO week, next to the imported baseline",
        description="asks to the human and messages to the coordinator per ISO week, next to the imported baseline",
    )
    kw.set_defaults(fn=cmd_kpi_ledger)
    kw.add_argument("--coordinator", default="lead", help="the coordinating role (default: lead)")
    kw.add_argument("--json", action="store_true")
    kw.add_argument("--ledger", help="the ledger file (default: $ONLOOP_LEDGER, else .onloop/ledger.db)")
    kb = ksub.add_parser("baseline", help="the team's own weekly aggregates")
    kbsub = kb.add_subparsers(dest="baseline_action", metavar="<action>")
    kbi = kbsub.add_parser(
        "import",
        help="import a baseline file (docs/schemas/kpi-baseline.schema.json) into the ledger",
        description="import a baseline file (docs/schemas/kpi-baseline.schema.json) into the ledger",
    )
    kbi.set_defaults(fn=cmd_kpi_ledger)
    kbi.add_argument("file", help="the baseline, a JSON file")
    kbi.add_argument("--ledger", help="the ledger file (default: $ONLOOP_LEDGER, else .onloop/ledger.db)")
    sp.add_argument(
        "--ledger", help="the ledger file, read-only (default: $ONLOOP_LEDGER, else .onloop/ledger.db)"
    )
    sp.add_argument("--since", help="only events at or after this time (YYYY-MM-DDTHH:MM:SS.mmmZ)")
    sp.add_argument("--until", help="stop at this time")
    sp.add_argument("--policy", help="names the role each slot waits on")
    sp.add_argument("--json", action="store_true")

    sp = add(
        "anchor", cmd_anchor, "print an anchor composed from the policy and the ledger (reads files only)"
    )
    sp.add_argument("--role", required=True)
    sp.add_argument("--kind", choices=anchor_mod.KINDS, default="full")
    sp.add_argument("--ledger", default=ledger.LEDGER_FILE)

    sp = add(
        "hooks",
        cmd_hooks,
        "print the hooks configuration that weaves the anchors into a session (writes nothing)",
    )
    sp.add_argument("--role")
    sp.add_argument("--ledger", default=ledger.LEDGER_FILE)
    sp.add_argument("--command", default="onloop", help="how the hook should call onloop")
    sp.add_argument(
        "--liveness",
        action="store_true",
        help="print the hooks that record session state instead of the anchors",
    )
    sp.add_argument("--liveness-dir", default=hooks_mod.LIVENESS_DIR, help="where the records are written")
    sp.add_argument(
        "--only-under",
        action="append",
        help="with --liveness: record only sessions whose cwd is under this path (repeatable)",
    )

    sp = add(
        "liveness",
        cmd_liveness,
        "record a session's state from a hook payload on stdin (never fails the session)",
        policy=False,
    )
    sp.add_argument("action", choices=["record"])
    sp.add_argument("--event", required=True)
    sp.add_argument(
        "--only-under",
        action="append",
        help="record only when the payload's cwd is under this path (repeatable)",
    )
    sp.add_argument(
        "--dir",
        default=os.environ.get("ONLOOP_LIVENESS_DIR", hooks_mod.LIVENESS_DIR),
        help="where the per-session records are written (default: $ONLOOP_LIVENESS_DIR, else ./liveness)",
    )

    sp = add("events", cmd_events, "list the ledger's events, oldest first (read-only)", policy=False)
    sp.add_argument("--ledger", help="the ledger file (default: $ONLOOP_LEDGER, else .onloop/ledger.db)")
    sp.add_argument("--kind", help="for example pr.merged")
    sp.add_argument("--subject", help="for example pr#12")
    sp.add_argument("--source", help="github, hook, healthchecks, ui, detector, legacy or config")
    sp.add_argument("--actor", help="who logged it")
    sp.add_argument("--since", help="at or after this time, the ledger's own (YYYY-MM-DDTHH:MM:SSZ)")
    sp.add_argument("--until", help="before this time, the ledger's own")
    sp.add_argument("--after", type=int, help="only events after this sequence number")
    sp.add_argument("--limit", type=int, help="at most this many (up to 500)")
    sp.add_argument("--json", action="store_true")

    sp = add(
        "preflight", cmd_preflight, "report a pull request's form problems and the slots it needs", repo=True
    )
    sp.add_argument("number", type=int)
    sp.add_argument(
        "--post", action="store_true", help="create or edit the one pre-flight comment on the pull request"
    )
    sp.add_argument("--json", action="store_true")

    slp = sub.add_parser("slot", help="scarce things one holder uses at a time: ask, wait, renew, release")
    slsub = slp.add_subparsers(dest="action", metavar="<action>")

    def slot_cmd(name, help_, db=True, repo=True):
        sp = slsub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(fn=cmd_slot)
        sp.add_argument("--policy", default=pol.POLICY_FILE, help="path to onloop.yaml")
        if db:
            sp.add_argument(
                "--ledger", help="the ledger file (default: $ONLOOP_LEDGER, else .onloop/ledger.db)"
            )
            sp.add_argument(
                "--db", help=argparse.SUPPRESS
            )  # before 0.3.0; refused with a pointer to --ledger
        if repo:
            sp.add_argument(
                "--repo", help="OWNER/NAME (default: $GITHUB_REPOSITORY, else gh's current repository)"
            )
        return sp

    sp = slot_cmd(
        "need", "print the slots a change to these paths needs (nothing for a docs-only change)", False, False
    )
    sp.add_argument("--paths", nargs="+", required=True)
    sp = slot_cmd(
        "request", "join the queue for a slot; granted at once if it is free and the approver is auto"
    )
    sp.add_argument("slot")
    sp.add_argument("--holder", required=True)
    sp.add_argument("--role", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--ref", required=True, help="the pull request or ticket this is for, for example pr#12")
    sp.add_argument("--paths", nargs="*", default=[])
    sp = slot_cmd("wait", "block until the request is granted; exit 0 granted, 3 expired, 4 timed out")
    sp.add_argument("slot")
    sp.add_argument("--holder", required=True)
    sp.add_argument("--ref", required=True)
    sp.add_argument("--timeout", type=float, help="seconds to wait (default: until the request expires)")
    sp.add_argument("--poll", type=float, default=2.0)
    sp = slot_cmd(
        "grant", "grant the request at the head of its queue (for a slot whose approver is a person)"
    )
    sp.add_argument("request_id", type=int)
    sp.add_argument(
        "--by",
        help="who grants it, recorded in the log (attested, not authenticated); an anchored slot refuses it",
    )
    sp.add_argument(
        "--anchor",
        help="the URL of the human's `/grant <request id>` comment on the request's pull request or "
        "issue, checked against GitHub (required for a slot with human_grant: anchored)",
    )
    sp.add_argument(
        "--as-role", help="the role the granter acts as, for a slot whose approver is role:<name>"
    )
    sp = slot_cmd("renew", "extend a live lease (only its holder may)")
    sp.add_argument("grant_id", type=int)
    sp.add_argument("--holder", required=True)
    sp = slot_cmd("release", "end a lease and hand the slot to the next in the queue")
    sp.add_argument("grant_id", type=int)
    sp.add_argument("--reason", default="released")
    sp.add_argument("--holder", help="the holder, releasing its own lease")
    sp.add_argument("--by", help="anyone else releasing it; recorded under this identity")
    sp = slot_cmd("status", "who holds each slot and who waits (read-only)", True, False)
    sp.add_argument("--json", action="store_true")

    op = sub.add_parser(
        "overlay", help="the overlay ledger: import the legacy stores, replay it, print its state"
    )
    osub = op.add_subparsers(dest="action", metavar="<action>")
    for action, help_ in (
        ("import", "append what is new in the legacy stores to the ledger, by cursor (read-only on them)"),
        ("replay", "rebuild the ledger's projections from its event log and compare them byte for byte"),
        ("status", "print the ledger's schema version, last event and ingest cursors"),
    ):
        sp = osub.add_parser(action, help=help_, description=help_)
        sp.set_defaults(fn=cmd_overlay)
        sp.add_argument("--ledger", help="the ledger file (default: $ONLOOP_LEDGER, else .onloop/ledger.db)")
        if action == "import":
            sp.add_argument("--store", help="the legacy onloop.db to import (the upgrade path)")
            sp.add_argument("--workgraph", help="the legacy work-graph store to import (the upgrade path)")
        if action == "replay":
            sp.add_argument(
                "--check",
                action="store_true",
                help="rebuild into a scratch copy and compare, leaving the ledger unchanged",
            )

    ing = sub.add_parser(
        "ingest", help="read a source into the overlay ledger once (onloopd does it continuously)"
    )
    ingsub = ing.add_subparsers(dest="source", metavar="<source>")
    sp = ingsub.add_parser(
        "github",
        help="read a GitHub repository into the ledger, by GETs only (`gh api`); never writes to the remote",
        description="read a GitHub repository into the ledger, by GETs only (`gh api`); never writes to the remote",
    )
    sp.set_defaults(fn=cmd_ingest)
    sp.add_argument("--once", action="store_true", required=True, help="one read, then exit")
    sp.add_argument("--repo", required=True, help="owner/name, any repository the gh login can read")
    sp.add_argument("--base", help="the base branch (default main)")
    sp.add_argument(
        "--workdir", help="a local clone for patch ids (default none: every push counts as substantive)"
    )
    sp.add_argument(
        "--policy", help="an onloop.yaml for required checks, identities and the post-merge pattern"
    )
    sp.add_argument("--ledger", help="the ledger file (default: $ONLOOP_LEDGER, else .onloop/ledger.db)")

    rep = sub.add_parser("report", help="reports over the overlay ledger")
    repsub = rep.add_subparsers(dest="action", metavar="<report>")
    sp = repsub.add_parser(
        "observe",
        help="what onloop would have done since a time, next to what happened then; and each role's turns",
        description="what onloop would have done since a time, next to what happened then; and each role's turns",
    )
    sp.set_defaults(fn=cmd_report)
    sp.add_argument(
        "--since", required=True, help="a date or time, as 2026-11-09 or 2026-11-09T09:00:00Z (UTC)"
    )
    sp.add_argument("--until", help="the end, in the same form (default: now)")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--ledger", help="the ledger file (default: $ONLOOP_LEDGER, else .onloop/ledger.db)")

    rp = sub.add_parser("role", help="pause or resume a role in the overlay ledger")
    rsub = rp.add_subparsers(dest="action", metavar="<action>")
    for action, help_ in (
        (
            "pause",
            "pause a role: every item it owns waits on the human (paused_by_human) until it is resumed",
        ),
        ("resume", "resume a paused role: its items stop waiting on the human"),
    ):
        sp = rsub.add_parser(action, help=help_, description=help_)
        sp.set_defaults(fn=cmd_role)
        sp.add_argument("role", help="the role name")
        sp.add_argument("--ledger", help="the ledger file (default: $ONLOOP_LEDGER, else .onloop/ledger.db)")

    ip = sub.add_parser("item", help="an item in the overlay ledger")
    isub = ip.add_subparsers(dest="action", metavar="<action>")
    sp = isub.add_parser(
        "evidence",
        help="attach the evidence a follow-up needs (a post-merge step); it is then done",
        description="attach the evidence a follow-up needs (a post-merge step); it is then done",
    )
    sp.set_defaults(fn=cmd_item)
    sp.add_argument("id", help="the item, as `pr#12/post-merge/1`")
    sp.add_argument("ref", help="the evidence: a URL, a pull request, a log line")
    sp.add_argument("--ledger", help="the ledger file (default: $ONLOOP_LEDGER, else .onloop/ledger.db)")

    dp = sub.add_parser("daemon", help="onloopd, the resident service on the loopback address")
    dsub = dp.add_subparsers(dest="action", metavar="<action>")
    for action, help_ in (
        ("run", "run onloopd in the foreground: the HTTP API and the ingest tick"),
        ("stop", "ask the running onloopd to stop, and wait for its pid to exit"),
        ("status", "print onloopd's last heartbeat and its /health answer"),
        (
            "hook",
            "post a Claude Code hook's payload (stdin) to onloopd; for a role's hook settings; never fails",
        ),
    ):
        sp = dsub.add_parser(action, help=help_, description=help_)
        sp.set_defaults(fn=cmd_daemon)
        sp.add_argument("--ledger", help="the ledger file (default: $ONLOOP_LEDGER, else .onloop/ledger.db)")
        if action == "run":
            sp.add_argument(
                "--port",
                type=int,
                default=None,
                help="the port (default: onloopd.yaml's `port`, else 8790; 0 picks a free one). A port in use is "
                "an error; onloopd never falls back to another",
            )
            sp.add_argument("--host", help="the loopback address (the default); any other is refused")
            sp.add_argument(
                "--tick", type=float, default=10.0, help="seconds between ingest ticks (default 10)"
            )
            sp.add_argument(
                "--config",
                help="onloopd.yaml, what onloopd watches (default: onloopd.yaml next to the ledger, if there is one)",
            )
        if action == "hook":
            sp.add_argument("--event", required=True, choices=("stop", "session-start", "notification"))
            sp.add_argument("--role", required=True, help="the role whose session this hook belongs to")
            sp.add_argument("--timeout", type=float, default=2.0, help="seconds before giving up (default 2)")
        if action == "stop":
            sp.add_argument(
                "--timeout", type=float, default=30.0, help="seconds to wait before stopping it by pid"
            )

    sp = add(
        "supervise",
        cmd_supervise,
        "the supervisor's planner: print the command of every role step that would start now",
        policy=False,
    )
    sp.add_argument(
        "--config", required=True, help="the supervisor configuration (YAML): roles, source, attention"
    )
    sp.add_argument(
        "--once",
        action="store_true",
        required=True,
        help="look once: print the command of every step that would start now, start nothing, write nothing",
    )

    sp = add(
        "step",
        cmd_step,
        "one governed step for one pull request, if its role's triggers say it is due: for a "
        "CI job (dry run unless --live)",
        policy=False,
    )
    sp.add_argument("--config", required=True, help="the supervisor configuration (YAML) holding the role")
    sp.add_argument("--role", required=True)
    sp.add_argument("--pr", required=True, type=int, help="the pull request's number")
    sp.add_argument(
        "--live",
        action="store_true",
        help="run the step; refused with a metered credential or an unlogged CLI (default: print it)",
    )
    sp.add_argument(
        "--state", help="the folder for the step's output and last-step.json (default: the config's)"
    )
    sp.add_argument("--out", help="the folder result_file is written under (default: the config's folder)")

    sp = add(
        "digest",
        cmd_digest,
        "what moved, what is done and what waits on the person, from the last snapshot",
        policy=False,
    )
    sp.add_argument("--config", required=True)
    sp.add_argument("--since", help="start of the digest (default: the last --mark, else --hours ago)")
    sp.add_argument("--hours", type=float, default=24.0)
    sp.add_argument("--now", help=argparse.SUPPRESS)
    sp.add_argument(
        "--refresh", action="store_true", help="read the source now (GETs only) instead of the snapshot"
    )
    sp.add_argument(
        "--mark", action="store_true", help="record this digest's time, so the next one starts there"
    )
    sp.add_argument("--json", action="store_true")

    sp = add(
        "attention",
        cmd_attention,
        "evaluate the attention rules from the state directory, the supervisor's "
        "heartbeat included (an outside check; exit 1 when a rule fires)",
        policy=False,
    )
    sp.add_argument("--config", required=True)
    sp.add_argument(
        "--notify", action="store_true", help="send the notices and log them (default: print only)"
    )

    add("selftest", cmd_selftest, "prove every checker still bites", policy=False)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = build_parser()
    given = list(sys.argv[1:]) if argv is None else list(argv)
    if given[:2] == ["daemon", "hook"]:
        # a hook must never fail a session, not even on arguments it cannot read
        try:
            args = ap.parse_args(argv)
        except SystemExit:
            return 0
        return int(args.fn(args))
    if given[:1] == ["liveness"]:
        # a hook must never fail a session, not even on arguments it cannot read
        try:
            args = ap.parse_args(argv)
        except SystemExit:
            return 0
        return liveness_mod.main_record(args.event, args.dir, args.only_under)
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
