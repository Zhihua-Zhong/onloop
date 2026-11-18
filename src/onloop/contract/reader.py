"""`onloop import snapshot`: replay a team's daily snapshots into a ledger beside them (decision 0017).

    import_snapshots(root, ledger_path, *, only=None, dry_run=False) -> [FileResult]

- A snapshot is a folder named by its UTC export time, holding `manifest.json` (written last: without it the folder is
  not read) and the JSON-lines files the manifest lists, each with its SHA-256 and its counts per record type.
- Each file is imported whole or not at all: a bad record, a privacy hit, a hash or a count that disagrees with the
  manifest, or a newer contract refuses the file, and the report says where and which rule, never the value.
- Idempotent: a file already imported (same snapshot, file and hash) adds nothing; the same name with another hash is
  refused, because a snapshot does not change. Events carry the live ingest's ids, so overlapping windows and a
  later live install deduplicate.
- It writes only the ledger and its report, `import-report.jsonl` next to the ledger. The ledger may not be inside a
  git work tree, inside the snapshots, or in a folder that holds a confidentiality deny list (onloop's own state).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any

from .. import disposition, verdicts
from .. import events as ev
from ..overlay.ledger import Ledger
from . import CONTRACT, OWNERS, SINCE, TYPES, Problem, check, privacy, schema

SOURCE = "import"
REPORT = "import-report.jsonl"
_SNAPSHOT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{6}Z$")


class ImportRefused(Exception):
    """The whole run is refused (a ledger path in the wrong place, a folder that is not a snapshot root)."""


@dataclass
class FileResult:
    snapshot: str
    file: str
    status: str  # imported | already imported | refused
    records: int = 0
    new_events: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    problems: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot,
            "file": self.file,
            "status": self.status,
            "records": self.records,
            "new_events": self.new_events,
            "counts": self.counts,
            "problems": self.problems[:50],
            "problems_total": len(self.problems),
        }


def check_ledger_place(ledger_path: pathlib.Path, root: pathlib.Path) -> None:
    lp = ledger_path.resolve()
    snaps = root.resolve() / "snapshots"
    if snaps in lp.parents:
        raise ImportRefused("the ledger may not be inside the snapshots: onloop only reads them")
    for d in lp.parents:
        if (d / ".git").exists():
            raise ImportRefused(
                "the ledger may not be inside a git work tree: a customer's data never enters a repository"
            )
        if (d / "denylist.txt").exists():
            raise ImportRefused("the ledger may not be in onloop's own state folder (it holds a deny list)")


def _deny(root: pathlib.Path) -> tuple[re.Pattern[str], ...]:
    path = root / "deny.txt"
    if not path.exists():
        return ()
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(re.compile(line))
        except re.error as e:
            raise ImportRefused(f"deny.txt line {i} is not a regular expression") from e
    return tuple(out)


def _norm(ts: str) -> str:
    return ev.norm_ts(ts) or ts


def to_event(rec: dict[str, Any], producer: str) -> dict[str, Any]:
    """One checked record as the ledger event the live ingest would have logged for the same occurrence."""
    d, at, kind = rec["data"], _norm(rec["time"]), rec["type"]
    imported = {"exporttime": _norm(rec["exporttime"]), "producer": producer}
    if kind == "onloop.role.declared":
        payload = {"id": d["role"], "imported": imported}
        if d.get("kind"):
            payload["kind"] = d["kind"]
        return {
            "id": ev.event_id(producer, "role.declared", f"role:{d['role']}", ""),
            "source": SOURCE,
            "kind": "role.declared",
            "subject_ref": f"role:{d['role']}",
            "actor": None,
            "at": at,
            "payload": payload,
        }
    if kind in ("onloop.pr.opened", "onloop.pr.merged", "onloop.pr.closed"):
        # One repository per producer: the natural key is (producer, kind, number). The head commit is the PR's head
        # now, not at the event, so it is kept as a fact about the PR and never keys the event.
        pk = kind.removeprefix("onloop.")
        subject = f"pr#{d['number']}"
        payload = {"number": d["number"], "imported": imported}
        if d.get("title") is not None:
            payload["title"] = d["title"]
        if d.get("head_sha"):
            payload["head"] = d["head_sha"]
        return {
            "id": ev.event_id(producer, pk, subject, ""),
            "source": SOURCE,
            "kind": pk,
            "subject_ref": subject,
            "actor": None,
            "at": at,
            "payload": payload,
        }
    if kind.startswith("onloop.feedback."):
        return _feedback_event(kind, d, at, imported, producer)
    if kind in ("onloop.pr.checks", "onloop.pr.reviewed"):
        return _check_or_review_event(kind, d, at, imported, producer)
    if kind.startswith("onloop.wait."):
        return _wait_event(kind, d, at, imported, producer)
    role, session, prompt = d["role"], d["session_id"], d["prompt_id"]
    out: dict[str, Any] = {"role": role, "session_id": session, "prompt_id": prompt, "imported": imported}
    out["grammar"] = disposition.GRAMMAR
    if d.get("started_at"):
        out["started_at"] = _norm(d["started_at"])
    got = disposition.parse(d.get("outcome_line") or "")
    if isinstance(got, disposition.Outcome):
        out["outcome"] = {"value": got.value, "ref": got.ref, "owner": got.owner, "waker": got.waker}
        if got.goal is not None and got.value in ("done", "continue"):
            out["outcome"]["goal"] = got.goal
        if got.resolves is not None:
            out["outcome"]["resolves"] = got.resolves
        if got.value in disposition.ASK_VALUES:
            ask = disposition.frozen_ask(got, at)
            if ask:
                out["ask"] = ask
        out["line"] = got.line
    else:
        out["parse_error"] = got.reason
        out["line"] = got.line if got.line and got.line.startswith("outcome:") else None
    key = "\x1f".join(
        ("hook", "stop", role, session, prompt)
    )  # the hook receiver's key: the same turn, the same id
    return {
        "id": hashlib.sha256(key.encode("utf-8")).hexdigest(),
        "source": SOURCE,
        "kind": "hook.stop",
        "subject_ref": f"role:{role}",
        "actor": role,
        "at": at,
        "payload": out,
    }


def _feedback_event(kind: str, d: dict[str, Any], at: str, imported: dict, producer: str) -> dict[str, Any]:
    """Feedback as the team reports it. Kept as events until the overview's tables exist (R6), which project them.

    An item's stage is declared by the team ("reported by the team"), not derived by onloop; each export's stamp is its
    own event, so a stage's age (and "stuck") can be computed from the days it did not move."""
    short = kind.removeprefix("onloop.")
    if short == "feedback.batch":
        subject, key = f"feedback-batch:{d['batch']}", ""
    elif short == "feedback.item":
        subject, key = f"feedback:{d['batch']}/{d['item']}", imported["exporttime"]  # one stamp per export
    else:
        subject, key = f"feedback:{d['batch']}/{d['item']}", f"issue#{d['issue']}"
    return {
        "id": ev.event_id(producer, short, subject, key),
        "source": SOURCE,
        "kind": f"{short}.reported",
        "subject_ref": subject,
        "actor": None,
        "at": at,
        "payload": {**d, "declared_by": "team", "imported": imported},
    }


def _check_or_review_event(
    kind: str, d: dict[str, Any], at: str, imported: dict, producer: str
) -> dict[str, Any]:
    """A check run or a verdict, as the live GitHub ingest's kinds. An imported verdict is the team's code (READY,
    HOLD), not the code host's APPROVED, so it never resolves the person's approval at the merge gate."""
    subject = f"pr#{d['number']}"
    if kind == "onloop.pr.checks":
        payload = {
            "name": d["check"],
            "conclusion": d["conclusion"],
            "head": d["head_sha"],
            "started_at": _norm(d["started_at"]),
            "completed_at": _norm(d["completed_at"]),
            "imported": imported,
        }
        key = f"{d['check']}/{d['head_sha']}/{_norm(d['completed_at'])}"
        return {
            "id": ev.event_id(producer, "check.completed", subject, key),
            "source": SOURCE,
            "kind": "check.completed",
            "subject_ref": subject,
            "actor": None,
            "at": at,
            "payload": payload,
        }
    payload = {
        "author": f"{d['reviewer']}/role",
        "reviewer_role": d["reviewer"],
        "state": d["verdict"],
        "head": d["head_sha"],
        "commit": d["head_sha"],
        "imported": imported,
    }
    key = f"{d['reviewer']}/{d['head_sha']}/{_norm(d['at'])}"
    return {
        "id": ev.event_id(producer, verdicts.KIND, subject, key),
        "source": SOURCE,
        "kind": verdicts.KIND,
        "subject_ref": subject,
        "actor": d["reviewer"],
        "at": at,
        "payload": payload,
    }


WAITS_FOR = {
    "approve": "approve",
    "walk": "walk through",
    "verdict": "give a verdict on",
    "rebase": "rebase",
    "deploy-go": "give the go to deploy",
    "other": "answer on",
}


def _wait_event(kind: str, d: dict[str, Any], at: str, imported: dict, producer: str) -> dict[str, Any]:
    """Who a pull request waits on, into the ledger's one wait model (the constraint, Now, the task list, the map).

    A wait on the person opens a task on their list. It ends only with `wait.cleared`; a wait missing from a later
    export stays open. `team` is held as a pseudo-role named "team" until the overview's owner classes (schema 9)."""
    pr = "pr#" + d["on"].split(":", 1)[1]
    since = _norm(d["since"])
    wid = f"import:{pr}/{d['waits_on']}/{since}"
    if kind == "onloop.wait.cleared":
        return {
            "id": ev.event_id(producer, "wait.cleared", pr, f"{d['waits_on']}/{since}"),
            "source": SOURCE,
            "kind": "wait.cleared",
            "subject_ref": pr,
            "actor": None,
            "at": at,
            "payload": {"id": wid, "imported": imported},
        }
    human = d["waits_on"] == "human"
    payload = {
        "id": wid,
        "item_id": pr,
        "waiter": pr,
        "on_kind": "human" if human else "role",
        "on_ref": "human" if human else d["waits_on"],
        "reason": "needs_human" if human else "blocked",
        "ref": pr,
        "since": since,
        "waits_for": d.get("waits_for"),
        "imported": imported,
    }
    if human:
        verb = WAITS_FOR.get(d.get("waits_for") or "other", "answer on")
        payload["effect"] = f"{verb} the change ({pr}), so it can move"
    return {
        "id": ev.event_id(producer, "wait.opened", pr, f"{d['waits_on']}/{since}"),
        "source": SOURCE,
        "kind": "wait.opened",
        "subject_ref": pr,
        "actor": None,
        "at": at,
        "payload": payload,
    }


def goal_events(
    led, records: list[dict[str, Any]], producer: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Goal revisions as R5's own goal events (decision 0014), diffed against the last revision already imported.

    A revision is keyed by its edit time: the same edit exported again (a quiet day repeats the latest) adds nothing.
    Returns the events and each goal's new state for the ingest cursor."""
    from .. import goals
    from ..adapters import Edit, GoalSnapshot

    out: list[dict[str, Any]] = []
    states: dict[str, Any] = {}
    by_goal: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_goal.setdefault(r["data"]["goal"], []).append(r)
    for gid, recs in sorted(by_goal.items()):
        state = (led.ingest_state(SOURCE).get(f"goal:{gid}") if led is not None else None) or {}
        last_at = state.get("edit_at")
        edits, seen = [], set()
        if last_at:
            prior = tuple(
                (int(n), "", bool(t))
                for n, t in sorted(state.get("items", {}).items(), key=lambda x: int(x[0]))
            )
            edits.append(Edit(edit_id=last_at, at=last_at, items=prior))
        for r in sorted(recs, key=lambda r: _norm(r["time"])):
            at = _norm(r["time"])
            if (last_at and at <= last_at) or at in seen:
                continue  # an edit already imported, or the same edit repeated in this file
            seen.add(at)
            items = tuple(
                (i["id"], "", i["ticked"]) for i in sorted(r["data"]["items"], key=lambda i: i["id"])
            )
            edits.append(Edit(edit_id=at, at=at, items=items))
        if not edits or (last_at and len(edits) == 1):
            continue
        if not last_at:
            out.append(
                {
                    "id": f"goal:{gid}:declared:import:{producer}",
                    "source": SOURCE,
                    "kind": "goal.declared",
                    "subject_ref": f"goal:{gid}",
                    "actor": None,
                    "payload": {"goal_id": gid, "adapter": "import", "locator": producer},
                }
            )
        for e in goals.edit_events(gid, GoalSnapshot(title=None, edits=tuple(edits)), last_at):
            out.append({**e, "source": SOURCE})
        newest = edits[-1]
        out.append(
            {
                "id": f"goal:{gid}:read:import:{newest.edit_id}",
                "source": SOURCE,
                "kind": "goal.read",
                "subject_ref": f"goal:{gid}",
                "actor": None,
                "at": newest.at,
                "payload": {"goal_id": gid, "last_edit_at": newest.at},
            }
        )
        states[f"goal:{gid}"] = {"edit_at": newest.at, "items": {str(n): t for n, _, t in newest.items}}
    return out, states


def _check_record(rec: Any) -> list[Problem]:
    found = check(rec, schema("envelope"))
    if found:
        return found
    if rec["type"] not in TYPES:
        return [Problem("/type", "a record type this onloop does not read yet")]
    if rec["dataschema"].rsplit("/", 1)[-1] != rec["type"].removeprefix("onloop."):
        return [Problem("/dataschema", "names another type than /type")]
    return [Problem(f"/data{p.path}", p.rule) for p in check(rec["data"], schema(f"data/{rec['type']}"))]


def _read_file(
    path: pathlib.Path, want: dict[str, Any], deny, minor: int = 0, known_roles: frozenset[str] = frozenset()
) -> tuple[list[dict[str, Any]], list[dict], dict, int]:
    raw = path.read_bytes()  # read-only; the file is never opened for writing
    problems: list[dict[str, Any]] = []
    if hashlib.sha256(raw).hexdigest() != want["sha256"]:
        problems.append({"line": None, "path": "/", "rule": "its SHA-256 differs from the manifest's"})
    records, counts = [], {}
    lines = raw.decode("utf-8", errors="strict").splitlines()
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            problems.append({"line": n, "path": "/", "rule": "not JSON"})
            continue
        found = _check_record(rec) or privacy(rec, deny)
        if not found and SINCE.get(rec["type"], 0) > minor:
            found = [Problem("/type", f"a contract 1.{SINCE[rec['type']]} type in a 1.{minor} snapshot")]
        if found:
            kind = rec.get("type") if isinstance(rec, dict) and rec.get("type") in TYPES else None
            problems.extend({"line": n, "type": kind, **p.as_dict()} for p in found)
            continue
        records.append(rec)
        counts[rec["type"]] = counts.get(rec["type"], 0) + 1
    if counts != {k: v for k, v in want["counts"].items() if v}:
        problems.append({"line": None, "path": "/", "rule": "its counts per type differ from the manifest's"})
    problems.extend(_owner_problems(records, known_roles))
    return records, problems, counts, len(lines)


def _owner_problems(records: list[dict[str, Any]], known: frozenset[str]) -> list[dict[str, Any]]:
    """A reviewer or a waiter is a declared role, `human` or `team`: a login fits the role-name pattern, so only the
    declared roles tell them apart. Roles declared in the same file count."""
    roles = (
        set(known) | {r["data"]["role"] for r in records if r["type"] == "onloop.role.declared"} | set(OWNERS)
    )
    out = []
    for r in records:
        field = {
            "onloop.pr.reviewed": "reviewer",
            "onloop.wait.opened": "waits_on",
            "onloop.wait.cleared": "waits_on",
        }
        key = field.get(r["type"])
        if key and r["data"][key] not in roles:
            out.append(
                {
                    "line": None,
                    "type": r["type"],
                    "path": f"/data/{key}",
                    "rule": "not a declared role, human or team (a login is never sent)",
                }
            )
    return out


def import_snapshots(
    root: pathlib.Path, ledger_path: pathlib.Path, *, only: str | None = None, dry_run: bool = False
) -> list[FileResult]:
    root = pathlib.Path(root)
    snaps = root / "snapshots"
    if not snaps.is_dir():
        raise ImportRefused(f"{root} has no snapshots folder")
    check_ledger_place(pathlib.Path(ledger_path), root)
    deny = _deny(root)
    names = sorted(p.name for p in snaps.iterdir() if p.is_dir() and _SNAPSHOT.match(p.name))
    if only:
        names = [n for n in names if n == only]
    results: list[FileResult] = []
    led = None if dry_run else Ledger(ledger_path)
    try:
        for name in names:
            folder = snaps / name
            mpath = folder / "manifest.json"
            if not mpath.exists():
                continue  # still being written: the manifest comes last
            try:
                manifest = json.loads(mpath.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                results.append(
                    FileResult(
                        name,
                        "manifest.json",
                        "refused",
                        problems=[{"line": None, "path": "/", "rule": "not JSON"}],
                    )
                )
                continue
            found = check(manifest, schema("manifest"))
            if not found and int(manifest["contract"].split(".")[1]) > int(CONTRACT.split(".")[1]):
                found = [Problem("/contract", "a newer contract than this onloop reads: upgrade onloop")]
            if found:
                results.append(
                    FileResult(
                        name,
                        "manifest.json",
                        "refused",
                        problems=[{"line": None, **p.as_dict()} for p in found],
                    )
                )
                continue
            for fname, want in sorted(manifest["files"].items()):
                minor = int(manifest["contract"].split(".")[1])
                results.append(
                    _import_file(led, folder, name, fname, want, manifest["producer"], deny, minor)
                )
    finally:
        if led is not None:
            led.close()
    if not dry_run:
        report = pathlib.Path(ledger_path).parent / REPORT
        with report.open("a", encoding="utf-8", newline="\n") as f:
            at = ev.now_ts()
            for r in results:
                f.write(json.dumps({"at": at, **r.as_dict()}, sort_keys=True) + "\n")
    return results


def _import_file(led, folder, name, fname, want, producer, deny, minor: int = 0) -> FileResult:
    path = folder / fname
    key = f"{name}/{fname}"
    if not path.exists():
        return FileResult(
            name,
            fname,
            "refused",
            problems=[{"line": None, "path": "/", "rule": "listed in the manifest but missing"}],
        )
    if led is not None:
        seen = led.ingest_state(SOURCE).get(key)
        if seen is not None:
            if seen.get("sha256") == want["sha256"]:
                return FileResult(name, fname, "already imported", records=seen.get("records", 0))
            return FileResult(
                name,
                fname,
                "refused",
                problems=[
                    {
                        "line": None,
                        "path": "/",
                        "rule": "already imported with another hash: a snapshot does not change",
                    }
                ],
            )
    known = frozenset(r["id"] for r in led.select("SELECT id FROM roles")) if led is not None else frozenset()
    records, problems, counts, _ = _read_file(path, want, deny, minor, known)
    if problems:
        return FileResult(name, fname, "refused", records=len(records), counts=counts, problems=problems)
    # roles first, then by the time each thing happened: a turn's role exists before the turn
    records.sort(key=lambda r: (r["type"] != "onloop.role.declared", _norm(r["time"])))
    revisions = [r for r in records if r["type"] == "onloop.goal.revision"]
    events = [to_event(r, producer) for r in records if r["type"] != "onloop.goal.revision"]
    if led is None:
        goal_events(None, revisions, producer)  # the diff runs, so a dry run fails where a real one would
        return FileResult(name, fname, "checked (dry run)", records=len(records), counts=counts)
    with led.tx():
        g_events, g_states = goal_events(led, revisions, producer)
        new = [s for s in (led._append_one(e) for e in events + g_events) if s is not None]
        led.put_ingest_state(SOURCE, {key: {"sha256": want["sha256"], "records": len(records)}, **g_states})
    return FileResult(name, fname, "imported", records=len(records), new_events=len(new), counts=counts)
