"""`onloop step`: one governed step for one pull request, with no execution engine. Made for a CI job.

A CI system already gives the event, the timeout, the concurrency group and the run history. What it does not give is
what this runs, from the same role table as the supervisor:

  1. whether the step is due: the role's triggers decide, on a fresh read of the source (the head, its required checks,
     whether the answering login has already answered this head). Not due is exit 0 with the reason, so an event that
     arrives early (checks still running) costs one read and nothing else;
  2. the governed command: `claude -p` with the role's tools under `dontAsk`, its settings sources, its schema, the
     prompt on stdin (step.build_command), stopped by its own pid on the wall budget or on `silence`;
  3. the result-file contract: the outcome, the numbers, the structured result and the final text, written by this
     process, so the step needs no write tool; then the role's after_result command, if it has one.

Dry run unless --live. A live run refuses a metered credential and an unlogged CLI before anything starts.
Exit codes: 0 ran ok, or not due; 1 the step failed (its outcome says how); 2 refused, or the configuration is wrong.
The summary is printed as one JSON line and written to `<state>/last-step.json`.

Under GitHub Actions (GITHUB_ACTIONS=true) it also says what happened where a person looks: a line in the job summary,
and a notice annotation when the step did not run. A step that was not due exits 0 like one that ran, so without this a
skipped review looks like a passed one in the run list.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import pathlib
from collections.abc import Callable, Mapping
from typing import Any

from . import items, results, sources, step, steplog
from .config import SupervisorConfig
from .items import WorkItem


def why_not(item: WorkItem) -> str:
    """Why an open pull request is not in the role's queue, in the terms of its fields."""
    out = []
    if item.draft:
        out.append("it is a draft")
    if item.checks not in (None, "green"):
        out.append(f"the required checks at the head are {item.checks}")
    if item.answered:
        out.append("the head is already answered")
    if not item.head:
        out.append("its head was not read")
    return "; ".join(out) or "no trigger of the role matches it"


def with_dirs(
    cfg: SupervisorConfig, state: pathlib.Path | None, out: pathlib.Path | None
) -> SupervisorConfig:
    """The configuration with the state folder and the roles' result folder moved, for a CI job's own folders."""
    if out is not None:
        cfg = dataclasses.replace(
            cfg, roles=tuple(dataclasses.replace(r, result_base=out) for r in cfg.roles)
        )
    if state is not None:
        cfg = dataclasses.replace(cfg, state_dir=state)
    return cfg


def run_one(
    cfg: SupervisorConfig,
    role_name: str,
    number: int,
    *,
    live: bool,
    environ: Mapping[str, str] | None = None,
    source: Any = None,
    popen: Any = None,
    say: Callable[[str], None] = print,
) -> tuple[int, dict[str, Any]]:
    env = dict(os.environ if environ is None else environ)
    try:
        role = cfg.role(role_name)
    except KeyError:
        return 2, {"due": False, "error": f"no role {role_name!r} in {cfg.path}"}
    if live:
        try:
            step.refuse_metered_key(env)
            step.check_login(cfg.claude, env)
        except (step.MeteredKeyRefused, step.NotLoggedIn) as e:
            return 2, {"due": False, "error": str(e)}
    src = source or sources.make(cfg.source, cfg.asks, lambda: dt.datetime.now(dt.UTC))
    try:
        snap = src.look()
    except sources.SourceError as e:
        return 1, {"due": False, "error": f"the source could not be read: {e}"}
    found = next((i for i in snap.items if i.kind == "pr" and i.number == number), None)
    if found is None:
        return 0, {"due": False, "reason": f"pull request #{number} is not open"}
    item = next((i for i in items.queue_for(role, list(snap.items), []) if i.id == found.id), None)
    if item is None:
        return 0, {"due": False, "item": found.id, "head": found.head, "reason": why_not(found)}
    sid = step.step_id(role.name, item.id, item.version)
    kw = {"popen": popen} if popen is not None else {}
    rd = step.run_step(
        role,
        item,
        sid=sid,
        claude=cfg.claude,
        live=live,
        out_dir=cfg.state_dir / "steps",
        repo=cfg.source.repo,
        environ=env,
        silence=role.silence,
        **kw,
    ).as_dict()
    summary: dict[str, Any] = {
        "due": True,
        "item": item.id,
        "head": item.head,
        "step_id": sid,
        **{
            k: rd.get(k)
            for k in ("outcome", "duration_s", "num_turns", "session_id", "exit_code", "error", "output_path")
        },
    }
    if not live:
        say(f"dry-run  {role.name}  {item.id}  {sid}  $ {step.display(rd['argv'])}")
    ok = rd["outcome"] in step.SUCCESS
    if live and role.result_file:
        try:
            summary["result_file"] = results.write_result(role, item, sid, rd, cfg.path.parent)
        except results.ResultPathError as e:
            return 2, {**summary, "error": str(e)}
        summary["result_json"] = summary["result_file"] + ".json"
        if ok and role.after_result:
            after = results.run_after(
                role, item, sid, summary["result_file"], environ=env, default_base=cfg.path.parent
            )
            summary["after_result"] = after
            if after["exit_code"] != 0:
                ok = False
                summary["outcome"] = "after_failed"
    return (0 if ok else 1), summary


def cmd_step(args: Any, load: Callable[[str], SupervisorConfig]) -> int:
    cfg = with_dirs(
        load(args.config),
        pathlib.Path(args.state).resolve() if args.state else None,
        pathlib.Path(args.out).resolve() if args.out else None,
    )
    code, summary = run_one(cfg, args.role, args.pr, live=args.live)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    steplog.write_json_atomic(cfg.state_dir / "last-step.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    ci_report(args.role, summary, os.environ, say=print)
    return code


def _escape(s: str) -> str:
    """A workflow command's message: GitHub reads %, CR and LF as escapes."""
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_prop(s: str) -> str:
    """A workflow command's property value, such as its title: also `:` and `,`."""
    return _escape(s).replace(":", "%3A").replace(",", "%2C")


def ci_line(role: str, summary: Mapping[str, Any]) -> str:
    """One line on what the step did, for a person reading the run."""
    item = summary.get("item") or ""
    if not summary.get("due"):
        why = summary.get("reason") or summary.get("error") or "unknown"
        return f"{role}: not run ({'not due' if summary.get('reason') else 'refused'}): {why} {item}".rstrip()
    return (
        f"{role}: ran {item} at {str(summary.get('head') or '')[:8]}: {summary.get('outcome')}, "
        f"{summary.get('num_turns')} turns, {summary.get('duration_s')} s"
    )


def ci_report(
    role: str, summary: Mapping[str, Any], environ: Mapping[str, str], say: Callable[[str], None]
) -> None:
    """Under GitHub Actions: the line in the step log and the job summary, and a notice annotation when the step did not
    run. The log is what the API exposes; the job summary shows only on the run page."""
    if environ.get("GITHUB_ACTIONS") != "true":
        return
    # One physical line, so a reason cannot start a workflow command of its own on the next line of the log.
    line = " ".join(ci_line(role, summary).splitlines())
    say(line)
    if not summary.get("due"):
        say(f"::notice title={_escape_prop(role + ': not run')}::{_escape(line)}")
    path = environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
