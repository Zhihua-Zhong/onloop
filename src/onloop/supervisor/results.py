"""A step's result file and the role's after_result command, with no execution engine behind them.

Both are used by `onloop step`, which runs one step from a CI job.
"""

from __future__ import annotations

import json
import pathlib
import string
import subprocess
import time
from collections.abc import Mapping
from typing import Any

from .. import fsutil, procs
from . import step, steplog
from .items import WorkItem


class ResultPathError(ValueError):
    """The role's result_file would land outside its folder."""


def names(role: Any, item: WorkItem, sid: str) -> dict[str, str]:
    """What a result_file path, or an after_result argument, may name: identifiers only, never tracker text."""
    return {
        "role": role.name,
        "step_id": sid,
        "item_kind": item.kind,
        "item_number": "" if item.number is None else str(item.number),
        "item_head": item.head or "",
        "item_head_short": (item.head or "")[:8],
    }


def write_result(role: Any, item: WorkItem, sid: str, rd: dict[str, Any], default_base: pathlib.Path) -> str:
    """Write the step's result where the role says, from the role's result folder (else `default_base`): the outcome,
    the numbers, the structured result if the CLI returned one, and the final text, uncut. Beside it, `<file>.json`
    holds the same as data. Returns the path written."""
    base = role.result_base or default_base
    path = (base / string.Template(role.result_file).substitute(names(role, item, sid))).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ResultPathError(f"result_file {path} is outside {base}")
    res = step.result_object(rd.get("output_path"))
    structured = res.get("structured_output")
    head = {
        "step_id": sid,
        "attempt": rd.get("attempt"),
        "role": role.name,
        "item": item.id,
        "head": item.head,
        "outcome": rd.get("outcome"),
        "duration_s": rd.get("duration_s"),
        "num_turns": rd.get("num_turns"),
        "session_id": rd.get("session_id"),
        "cost_usd_notional": rd.get("cost_usd"),
        "permission_denials": res.get("permission_denials") or [],
        "error": rd.get("error"),
    }
    body = ["# Step result", "", "```json", json.dumps(head, indent=2, ensure_ascii=False), "```", ""]
    if structured is not None:
        body += [
            "## Structured result",
            "",
            "```json",
            json.dumps(structured, indent=2, ensure_ascii=False),
            "```",
            "",
        ]
    body += ["## Final text", "", str(res.get("result") or "(none)"), ""]
    steplog.write_json_atomic(
        path.with_suffix(path.suffix + ".json"),
        {**head, "structured": structured, "result": res.get("result")},
    )
    fsutil.write_atomic(path, "\n".join(body))
    return str(path)


def run_after(
    role: Any,
    item: WorkItem,
    sid: str,
    result_file: str,
    *,
    environ: Mapping[str, str],
    default_base: pathlib.Path,
) -> dict[str, Any]:
    """Run the role's after_result command on the result just written: from the role's folder, with `environ`, no
    shell, its output beside the result. Past its timeout it is stopped by its own pid. Returns what the step record
    keeps about it; `exit_code` 0 is the only success."""
    argv = [
        string.Template(a).substitute(
            {**names(role, item, sid), "result_file": result_file, "result_json": result_file + ".json"}
        )
        for a in role.after_result
    ]
    output = result_file + ".after.txt"
    t0 = time.monotonic()
    code, killed = None, None
    with open(output, "wb") as out:
        try:
            proc = procs.start(
                argv,
                cwd=str(role.result_base or default_base),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                env=dict(environ),
            )
        except OSError as e:
            return {
                "command": argv,
                "exit_code": None,
                "killed": None,
                "output": output,
                "duration_s": 0.0,
                "error": f"{type(e).__name__}: {e}",
            }
        end = t0 + role.after_timeout
        while code is None:
            try:
                code = proc.wait(timeout=min(1.0, max(0.0, end - time.monotonic())))
            except subprocess.TimeoutExpired:
                if time.monotonic() >= end:
                    killed = procs.kill_tree(proc)
                    break
    return {
        "command": argv,
        "exit_code": code,
        "killed": killed,
        "output": output,
        "duration_s": round(time.monotonic() - t0, 3),
    }
