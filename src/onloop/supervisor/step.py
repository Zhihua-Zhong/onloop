"""One role step: build the exact `claude -p` command line from the role table, and run it within its budget.

    run_step(role, item, ...) -> StepResult

is a plain function: it takes its input, returns a result and keeps no state; `onloop step` (onestep.py) calls it.
`progress` is called with phase `started` when the process starts, `output` whenever it has written more (stream-json
writes one line per message, so this is real progress), and `running` on the ticks in between. `deadline` is a hard end in epoch seconds that the wall budget cannot extend.

Two limits stop a step, both by its own pid: the wall budget (`overrun`) and `silence`, the longest stretch with no
output (`stalled`).

**Dry run is the default.** Without `live=True` the command is built and returned, and nothing is started: `popen` is
never touched. A live run refuses to start when `ANTHROPIC_API_KEY` is in its environment, because the agents run on
the subscription and a metered key must never be reached by accident.

The command, for one role (flags as `claude --help` lists them; `--max-turns` is accepted though not listed):

    claude -p --output-format stream-json --verbose --max-turns N --permission-mode MODE
           --tools Read,Grep,Bash --allowedTools Read,Grep,Bash(gh pr view *)
           --strict-mcp-config --setting-sources user --session-id UUID [--json-schema SCHEMA] [--model M]

The prompt goes on stdin, never on the command line, so no shell or `.cmd` shim ever parses it. `--tools` limits the
built-in tools the step can see to the base names of `allowed_tools`; `--allowedTools` pre-approves exactly those
patterns; `dontAsk` (the default mode) denies anything else instead of prompting, so a step that needs more fails and
is logged, and never waits on a person. `--strict-mcp-config` with no `--mcp-config` gives the step no MCP servers.
`--setting-sources` loads only the role's setting sources (`user` by default), so a settings file inside the working
directory cannot add allow rules that widen the role's tools. `--json-schema` asks for a structured result.

`result_text(path)` reads a step's final result object back from its output file, uncut, for a role's `result_file`.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import string
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from .. import events as ev
from .. import procs
from .config import RoleSpec
from .items import WorkItem

METERED_KEY = "ANTHROPIC_API_KEY"
# Credentials the CLI prefers to the subscription login (its precedence: a bearer token for a gateway, then an API
# key). Either would bill a metered account, so a live run refuses both.
METERED_KEYS = (METERED_KEY, "ANTHROPIC_AUTH_TOKEN")
# The subscription token `claude setup-token` prints, for a service with no browser login.
OAUTH_TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"
PROGRESS_EVERY = 5.0
OUTCOMES = (
    "ok",
    "failed",
    "max_turns",
    "overrun",
    "stalled",
    "interrupted",
    "dry_run",
    "spawn_failed",
    "lost",
    "after_failed",
)
SUCCESS = ("ok", "dry_run")

Progress = Callable[[dict[str, Any]], None]


class MeteredKeyRefused(RuntimeError):
    """A live run found a metered API key in its environment and will not start."""


class NotLoggedIn(RuntimeError):
    """`claude auth status` says the CLI has no login: every live step would fail before its first turn."""


def check_login(
    claude: tuple[str, ...] | list[str],
    environ: Mapping[str, str] | None = None,
    run: Callable[..., Any] = procs.run_text,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Ask the CLI whether it is logged in (`claude auth status`, which makes no model call), before a live step
    starts. Raises NotLoggedIn when it says it is not, or uses an API key; returns what it said otherwise. An answer
    that cannot be read is returned as {} and does not stop the step: the step itself will say what is wrong."""
    env = dict(os.environ if environ is None else environ)
    if env.get(OAUTH_TOKEN):
        # The setup-token credential: a subscription token by construction (Pro and above). `auth status` is not
        # documented to report it, so it is not asked; a bad token fails the first turn, before any model work.
        return {"loggedIn": True, "authMethod": "oauth_token_env"}
    try:
        p = run([*claude, "auth", "status"], timeout=timeout, env=env)
        got = json.loads((p.stdout or "").strip() or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    if not isinstance(got, dict):
        return {}
    if got.get("loggedIn") is False:
        raise NotLoggedIn(
            "refusing to run live: `claude auth status` says the CLI is not logged in, so every step "
            "would fail before its first turn. Log in with the subscription account on this host "
            "(`claude auth login`, or `claude setup-token` for a service) and start again."
        )
    if str(got.get("authMethod") or "").lower() in ("api_key", "apikey"):
        raise NotLoggedIn(
            "refusing to run live: the CLI is logged in with an API key; the agents run on the "
            "subscription only."
        )
    return got


def refuse_metered_key(environ: Mapping[str, str]) -> None:
    for key in METERED_KEYS:
        if key in environ:
            raise MeteredKeyRefused(
                f"refusing to run live: {key} is set in the environment. The agents run on "
                "the subscription only; unset it and start again."
            )


def step_id(role: str, item_id: str, version: str, round_: int = 0) -> str:
    """The step's identity: the same role, item, item version and timer round always give the same id."""
    h = hashlib.sha256("\x1f".join((role, item_id, version, str(round_))).encode("utf-8")).hexdigest()
    return f"s-{h[:20]}"


def new_session_id() -> str:
    """A fresh Claude Code session id for one attempt. Never derived from the step id: the CLI keeps every session it
    has seen, so an id that a step could produce twice (a state directory started again, a step run once more) would
    be refused as "already in use" before the step starts. The id is recorded in the step's records, which is how a
    transcript is joined to its step."""
    return str(uuid.uuid4())


def base_tools(allowed: tuple[str, ...]) -> list[str]:
    """`Bash(gh pr view *)` -> `Bash`; order kept, each once."""
    out: list[str] = []
    for t in allowed:
        name = t.split("(", 1)[0].strip()
        if name and name not in out:
            out.append(name)
    return out


def build_command(claude: tuple[str, ...] | list[str], role: RoleSpec, session_id: str) -> list[str]:
    """The exact argv of one step. The prompt is not in it: it is written to the process's stdin."""
    argv = [
        *claude,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        str(role.turns),
        "--permission-mode",
        role.permission_mode,
    ]
    tools = base_tools(role.allowed_tools)
    argv += ["--tools", ",".join(tools)]
    if role.allowed_tools:
        argv += ["--allowedTools", ",".join(role.allowed_tools)]
    argv += [
        "--strict-mcp-config",
        "--setting-sources",
        ",".join(role.setting_sources),
        "--session-id",
        session_id,
    ]
    if role.result_schema:
        argv += ["--json-schema", role.result_schema]
    if role.model:
        argv += ["--model", role.model]
    return argv


def build_resume_command(
    claude: tuple[str, ...] | list[str],
    *,
    session_id: str,
    tools: tuple[str, ...],
    allowed_tools: tuple[str, ...],
    max_turns: int,
    settings: str,
    model: str | None = None,
    create: bool = False,
) -> list[str]:
    """The exact argv of one carried turn (decision 0010): resume the role's session, read one stream-json message on
    stdin, write stream-json, replay the user message, and run restricted with no prompts. Refusals are denied, not
    asked (`dontAsk`, `--permission-prompts none`); only the role's project settings and the carrier's hook settings
    load; no MCP server. `create` starts a new session with that id instead of resuming one."""
    argv = [
        *claude,
        "-p",
        "--session-id" if create else "--resume",
        session_id,
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--replay-user-messages",
        "--tools",
        ",".join(base_tools(tools)),
    ]
    if allowed_tools:
        argv += ["--allowedTools", ",".join(allowed_tools)]
    argv += [
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--max-turns",
        str(max_turns),
        "--settings",
        settings,
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--restricted",
    ]
    if model:
        argv += ["--model", model]
    return argv


def prompt_vars(
    role: RoleSpec, item: WorkItem, *, sid: str, repo: str = "", queue_size: int = 1
) -> dict[str, str]:
    charter = ""
    if role.charter is not None:
        try:
            charter = role.charter.read_text(encoding="utf-8")
        except OSError as e:
            charter = f"(charter {role.charter.name} could not be read: {type(e).__name__})"
    return {
        "role": role.name,
        "repo": repo,
        "step_id": sid,
        "item_id": item.id,
        "item_kind": item.kind,
        "item_number": "" if item.number is None else str(item.number),
        "item_title": item.title,
        "item_url": item.url,
        "item_state": item.state,
        "item_head": item.head or "",
        "item_head_short": (item.head or "")[:8],
        "done_when": item.done_when or "",
        "queue_size": str(queue_size),
        "charter": charter,
    }


def render_prompt(role: RoleSpec, item: WorkItem, **kw: Any) -> str:
    return string.Template(role.prompt).substitute(prompt_vars(role, item, **kw))


def display(argv: list[str]) -> str:
    """The command line as a person would type it (POSIX quoting), for the log and the dry-run output."""
    import shlex

    return shlex.join(argv)


@dataclass
class StepResult:
    step_id: str
    attempt: int
    role: str
    item_id: str
    item_version: str
    mode: str  # dry_run | live
    outcome: str
    argv: list[str]
    prompt_sha256: str
    started_at: str
    ended_at: str
    duration_s: float
    pid: int | None = None
    exit_code: int | None = None
    session_id: str | None = None
    num_turns: int | None = None
    cost_usd: float | None = None  # as reported by the CLI; on a subscription it is notional
    agent_result: str | None = None  # the agent's final text, cut to 500 characters
    output_path: str | None = None
    error: str | None = None
    killed: str | None = None  # how an overrun or interrupted step was stopped
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ts(t: float) -> str:
    import datetime as dt

    return ev.norm_ts(dt.datetime.fromtimestamp(t, dt.UTC)) or ""


def parse_output(text: str) -> dict[str, Any]:
    """The CLI's result object: the last JSON object of its output (stream-json ends with it), or {}."""
    text = (text or "").strip()
    if not text:
        return {}
    for candidate in (text, *reversed(text.splitlines())):
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def result_object(output_path: str | None) -> dict[str, Any]:
    """The step's final result object (the last JSON object of its output file), or {}."""
    if not output_path:
        return {}
    try:
        return parse_output(pathlib.Path(output_path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


def classify(exit_code: int | None, out: dict[str, Any]) -> str:
    if out.get("subtype") == "error_max_turns":
        return "max_turns"
    if exit_code == 0 and not out.get("is_error"):
        return "ok"
    return "failed"


def run_step(
    role: RoleSpec,
    item: WorkItem,
    *,
    sid: str,
    attempt: int = 1,
    claude: tuple[str, ...] = ("claude",),
    live: bool = False,
    out_dir: pathlib.Path | None = None,
    repo: str = "",
    queue_size: int = 1,
    deadline: float | None = None,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.time,
    progress_every: float = PROGRESS_EVERY,
    silence: float | None = None,
    session_id: str | None = None,
) -> StepResult:
    """Run one step of `role` on `item`, or in dry run only build it. Never raises for a failure of the step itself:
    a failure is an outcome. Raises `MeteredKeyRefused` before anything starts when a live run finds a metered key."""
    env = dict(os.environ if environ is None else environ)
    session = session_id or new_session_id()
    argv = build_command(claude, role, session)
    prompt = render_prompt(role, item, sid=sid, repo=repo, queue_size=queue_size)
    phash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    t0 = clock()
    base = dict(
        step_id=sid,
        attempt=attempt,
        role=role.name,
        item_id=item.id,
        item_version=item.version,
        argv=argv,
        prompt_sha256=phash,
        started_at=_ts(t0),
        session_id=session,
    )
    if not live:
        return StepResult(**base, mode="dry_run", outcome="dry_run", ended_at=_ts(t0), duration_s=0.0)
    refuse_metered_key(env)
    env["ONLOOP_STEP_ID"], env["ONLOOP_ROLE"] = sid, role.name
    hard_end = t0 + role.wall if deadline is None else min(t0 + role.wall, deadline)
    out_path = err_path = None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{sid}-{attempt}.json"
        err_path = out_dir / f"{sid}-{attempt}.err"
    if not role.cwd.is_dir():
        return StepResult(
            **base,
            mode="live",
            outcome="spawn_failed",
            ended_at=_ts(clock()),
            duration_s=0.0,
            error=f"working directory {role.cwd} does not exist",
        )
    out_f = open(out_path, "wb") if out_path else subprocess.DEVNULL  # noqa: SIM115 - closed below
    err_f = open(err_path, "wb") if err_path else subprocess.DEVNULL  # noqa: SIM115
    try:
        try:
            proc = procs.start(
                argv,
                popen=popen,
                cwd=str(role.cwd),
                stdin=subprocess.PIPE,
                stdout=out_f,
                stderr=err_f,
                env=env,
            )
        except OSError as e:
            return StepResult(
                **base,
                mode="live",
                outcome="spawn_failed",
                ended_at=_ts(clock()),
                duration_s=round(clock() - t0, 3),
                error=f"{type(e).__name__}: {e}"[:300],
                output_path=str(out_path) if out_path else None,
            )

        def feed() -> None:
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                proc.stdin.close()
            except (OSError, ValueError):
                pass

        threading.Thread(target=feed, daemon=True).start()
        if progress:
            progress(
                {
                    "phase": "started",
                    "pid": proc.pid,
                    "step_id": sid,
                    "attempt": attempt,
                    "deadline": _ts(hard_end),
                }
            )
        outcome, killed = None, None
        quiet_limit = role.silence if silence is None else silence
        if out_path is None:
            quiet_limit = float("inf")  # no output file to watch: only the wall budget
        size, last_output = 0, clock()
        try:
            while True:
                now = clock()
                if cancel is not None and cancel.is_set():
                    killed, outcome = procs.kill_tree(proc), "interrupted"
                    break
                if now >= hard_end:
                    killed, outcome = procs.kill_tree(proc), "overrun"
                    break
                if now - last_output >= quiet_limit:
                    killed, outcome = procs.kill_tree(proc), "stalled"
                    break
                try:
                    proc.wait(
                        timeout=max(
                            0.05, min(progress_every, hard_end - now, quiet_limit - (now - last_output))
                        )
                    )
                    break
                except subprocess.TimeoutExpired:
                    grown = out_path.stat().st_size if out_path is not None and out_path.exists() else 0
                    phase = "output" if grown > size else "running"
                    if grown > size:
                        size, last_output = grown, clock()
                    if progress:
                        progress(
                            {
                                "phase": phase,
                                "pid": proc.pid,
                                "step_id": sid,
                                "attempt": attempt,
                                "elapsed_s": round(clock() - t0, 1),
                                "output_bytes": size,
                            }
                        )
        except BaseException:
            # Whatever ends the wait early (an exception raised into this thread by a cancellation, an interrupt) must
            # not leave the step running: stop it by its own pid, then let the error go on.
            try:
                procs.kill_tree(proc)
            except Exception:  # noqa: BLE001
                pass
            raise
    finally:
        for f in (out_f, err_f):
            if f is not subprocess.DEVNULL:
                f.close()
    t1 = clock()
    code = proc.returncode
    out = parse_output(out_path.read_text(encoding="utf-8", errors="replace")) if out_path else {}
    result_text = out.get("result")
    error = None
    if outcome is None:
        outcome = classify(code, out)
        if outcome != "ok" and err_path:
            error = err_path.read_text(encoding="utf-8", errors="replace").strip()[-300:] or None
    else:
        error = {
            "overrun": f"stopped after {round(t1 - t0, 1)}s: wall budget {role.wall:.0f}s",
            "stalled": f"stopped: no output for {quiet_limit:.0f}s",
            "interrupted": "stopped: the step was cancelled or the worker is shutting down",
        }[outcome]
    return StepResult(
        **base,
        mode="live",
        outcome=outcome,
        ended_at=_ts(t1),
        duration_s=round(t1 - t0, 3),
        pid=proc.pid,
        exit_code=code,
        num_turns=out.get("num_turns"),
        cost_usd=out.get("total_cost_usd"),
        agent_result=str(result_text)[:500] if result_text is not None else None,
        output_path=str(out_path) if out_path else None,
        error=error,
        killed=killed,
    )
