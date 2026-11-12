"""The supervisor's commands: `onloop supervise`, `onloop digest` and `onloop attention`.

supervise --once   the dry-run planner: look at the source once and print the exact command of every step that would
                   start now. It starts nothing and writes nothing.
digest             what moved, what is done, what waits on the person, from the last snapshot (or, with --refresh,
                   from the source); it writes nothing unless --mark is given
attention          the outside check: every attention rule, the heartbeat's included, from the state directory. It
                   prints and writes nothing unless --notify is given; then it sends and logs the notices in the
                   alert log, so a quiet period holds across runs.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from typing import Any

from .. import events as ev
from . import asks as asks_mod
from . import attention, notify, planner, schedule, sources, step, steplog
from .config import ConfigError, load

DIGEST_MARK = "digest.json"


def _cfg(path: str):
    try:
        return load(path)
    except ConfigError as e:
        raise SystemExit(f"onloop: {e}") from e


def cmd_plan_once(cfg: Any) -> int:
    """Print the steps that would start now, each with its exact command line. Reads only."""
    try:
        snap = sources.make(cfg.source, cfg.asks, lambda: dt.datetime.now(dt.UTC)).look()
    except sources.SourceError as e:
        print(f"onloop: {e}", file=sys.stderr)
        return 1
    records = steplog.StepLog(cfg.state_dir / planner.STEPS).records
    plan = planner.plan_once(cfg, snap, records, "dry_run", ev.now_ts())
    for d in plan.due:
        role = cfg.role(d.role)
        argv = step.build_command(cfg.claude, role, "<a new session id per attempt>")
        prompt = step.render_prompt(
            role,
            d.item,
            sid=d.sid,
            repo=cfg.source.repo,
            queue_size=plan.pending.get(d.role, {}).get("count", 1),
        )
        print(
            f"would run  {d.role}  {d.item.id}  {d.sid}\n  $ {step.display(argv)}\n"
            f"  (prompt on stdin, {len(prompt)} characters)"
        )
    held = f", {len(plan.held)} held by a WIP limit or max_parallel" if plan.held else ""
    print(f"plan: {len(plan.due)} step(s) due{held}; nothing was started")
    return 0


def cmd_supervise(args: Any) -> int:
    return cmd_plan_once(_cfg(args.config))


def cmd_digest(args: Any) -> int:
    cfg = _cfg(args.config)
    now_dt = dt.datetime.now(dt.UTC)
    now = ev.norm_ts(args.now) if args.now else ev.norm_ts(now_dt)
    mark = steplog.read_json(cfg.state_dir / DIGEST_MARK) or {}
    since = (
        ev.norm_ts(args.since)
        if args.since
        else (mark.get("at") or asks_mod.default_since(now_dt, args.hours))
    )
    try:
        snap = (
            sources.make(cfg.source, cfg.asks, lambda: now_dt).look()
            if args.refresh
            else planner.read_snapshot(cfg)
        )
    except sources.SourceError as e:
        print(f"onloop: {e}", file=sys.stderr)
        return 1
    if snap is None:
        print("onloop: no snapshot yet; pass --refresh to read the source", file=sys.stderr)
        return 1
    d = asks_mod.digest(snap.asks, since or "", now or "", snap.open_ids)
    origin = f"the snapshot read at {snap.read_at}" if snap.read_at else "the source"
    if args.json:
        print(json.dumps(d.as_dict(), indent=2, ensure_ascii=False))
    else:
        for line in asks_mod.digest_text(d):
            print(line)
        print(f"(from {origin})")
    if args.mark:
        steplog.write_json_atomic(cfg.state_dir / DIGEST_MARK, {"at": now})
    return 0


def cmd_attention(args: Any) -> int:
    cfg = _cfg(args.config)
    now = ev.now_ts()
    hb = steplog.read_json(cfg.state_dir / planner.HEARTBEAT)
    snap = planner.read_snapshot(cfg) or sources.Snapshot()
    mode = (hb or {}).get("mode") or "dry_run"
    view = steplog.step_view(steplog.StepLog(cfg.state_dir / planner.STEPS).records, mode)
    plan = schedule.plan_steps(
        cfg.roles, planner.queues_of(cfg, snap), view, now, max_parallel=cfg.max_parallel
    )
    # A heartbeat older than the threshold is stale whatever the reason.
    state = planner.attention_state(cfg, snap, plan, view, hb)
    alerts_log = steplog.StepLog(cfg.state_dir / planner.ALERTS)
    got = attention.evaluate_attention(state, now, cfg.attention, alerts_log.records)
    notifier = notify.make(cfg.notifier) if args.notify else None
    fired = 0
    for a in got:
        if a.status == "fire":
            fired += 1
            print(f"{a.rule}: {a.message}")
        if notifier is not None:
            rec = a.as_dict()
            if a.status == "fire":
                rec["sent"] = notifier.send(f"onloop: {a.rule.replace('_', ' ')}", a.message, rec)
            alerts_log.write(rec)
    if not fired:
        print("attention: nothing to tell")
    return 1 if fired else 0
