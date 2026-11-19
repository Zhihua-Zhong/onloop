"""Generic daily snapshots in the data contract's shape (decision 0017), for the tests, the docs and the hands-on pass.

    python tools/contract_example.py OUT [--days 2] [--turns 1000] [--end 2026-11-17T02:00:00Z] [--seed 7]

Writes OUT/snapshots/<UTC time>/manifest.json and events.jsonl, one snapshot per day, each covering two days so
that consecutive snapshots overlap by one, as a producer is asked to export them. Every name is made up: 11 roles,
some with names of 20 characters, so a layout is tested at real lengths; the lead also has side sessions (forks).
Nothing here comes from any team's data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import random

ROLES = (
    ("lead", "coordinator"),
    ("builder", "agent"),
    ("reviewer", "reviewer"),
    ("research-analyst", "agent"),
    ("data-pipeline-owner", "agent"),
    ("frontend-builder", "agent"),
    ("release-coordinator", "coordinator"),
    ("docs-writer", "agent"),
    ("test-runner-nightly", "agent"),
    ("schema-steward", "agent"),
    ("quiet-observer", "agent"),  # declared, never takes a turn
)
WEIGHT = {"lead": 6, "builder": 9, "reviewer": 5, "research-analyst": 3, "data-pipeline-owner": 4}
PRODUCER = "example-team"


def _ts(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _sid(rng: random.Random) -> str:
    a, b, c, d, e = (rng.getrandbits(n) for n in (32, 16, 16, 16, 48))
    return f"{a:08x}-{b:04x}-{c:04x}-{d:04x}-{e:012x}"


def turns(rng: random.Random, start: dt.datetime, end: dt.datetime, n: int) -> list[dict]:
    """About n turns between start and end: work in bursts, with quiet hours at night and one long silence."""
    actors = [r for r, _ in ROLES if r != "quiet-observer"]
    weights = [WEIGHT.get(r, 2) for r in actors]
    sessions = {r: [_sid(rng)] for r in actors}
    sessions["lead"] += [_sid(rng), _sid(rng)]  # the lead's side sessions (forks)
    span = (end - start).total_seconds()
    out = []
    while len(out) < n:
        at = start + dt.timedelta(seconds=rng.random() * span)
        hour = at.hour
        if 15 <= hour < 21 and rng.random() < 0.85:  # the team's night (UTC): mostly quiet
            continue
        role = rng.choices(actors, weights)[0]
        if (
            role == "docs-writer"
            and at > start + dt.timedelta(hours=10)
            and at < start + dt.timedelta(hours=30)
        ):
            continue  # one long silence, for the "longest silence" mark
        sess = sessions[role][0] if role != "lead" or rng.random() < 0.7 else rng.choice(sessions["lead"][1:])
        dur = dt.timedelta(seconds=rng.randint(20, 900))
        out.append(
            {"role": role, "session_id": sess, "prompt_id": _sid(rng), "started_at": at - dur, "time": at}
        )
    return sorted(out, key=lambda t: t["time"])


def write(
    out: pathlib.Path, end: dt.datetime, days: int, n: int, seed: int, contract: str = "1.0"
) -> list[pathlib.Path]:
    rng = random.Random(seed)
    first = end - dt.timedelta(days=days + 1)
    all_turns = turns(rng, first, end, n)
    folders = []
    for d in range(days):
        # contract 1.2: a producer that upgrades mid-run sends its earlier exports as 1.1, without `by`, and its last
        # one as 1.2, so a PR first seen without its role gets it from a later copy
        this = "1.1" if contract == "1.2" and d < days - 1 else contract
        export = end - dt.timedelta(days=days - 1 - d)
        frm, to = export - dt.timedelta(days=2), export
        lines = []
        for role, kind in ROLES:
            lines.append(
                _record("onloop.role.declared", f"role-{role}", frm, export, {"role": role, "kind": kind})
            )
        for t in all_turns:
            if frm <= t["time"] < to:
                data = {k: t[k] for k in ("role", "session_id", "prompt_id")}
                data["started_at"] = _ts(t["started_at"])
                lines.append(_record("onloop.turn.ended", t["prompt_id"], t["time"], export, data))
        lines += _work(first, frm, to, export, d)
        if this != "1.0":
            lines += _work_1_1(first, frm, to, export)
        if this == "1.2":
            _with_by(lines)
        body = ("\n".join(json.dumps(x, sort_keys=True) for x in lines) + "\n").encode("utf-8")
        counts: dict[str, int] = {}
        for x in lines:
            counts[x["type"]] = counts.get(x["type"], 0) + 1
        name = export.strftime("%Y-%m-%dT%H%M%SZ")
        folder = out / "snapshots" / name
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "events.jsonl").write_bytes(body)
        manifest = {
            "contract": this,
            "exporttime": _ts(export),
            "producer": PRODUCER,
            "window": {"from": _ts(frm), "to": _ts(to)},
            "timezone": "Asia/Tokyo",
            "files": {"events.jsonl": {"sha256": hashlib.sha256(body).hexdigest(), "counts": counts}},
            "complete": {t: True for t in counts},
        }
        (folder / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        folders.append(folder)
    return folders


BY = {
    0: "team",
    1: "builder",
    2: "frontend-builder",
}  # made up: who opened PR n, by n % 3; PR 3 is the person's


def _with_by(lines: list[dict]) -> None:
    """Contract 1.2: each opened PR says which role opened it, as a role name, `team` or `human`."""
    for x in lines:
        if x["type"] == "onloop.pr.opened":
            n = x["data"]["number"]
            x["data"] = {
                **x["data"],
                "by": "human" if n == 3 else BY[n % 3],
            }  # its own copy: merged and closed share it
            x["dataschema"] = "onloop-contract/1.2/pr.opened"


def _work_1_1(first: dt.datetime, frm: dt.datetime, to: dt.datetime, export: dt.datetime) -> list[dict]:
    """Contract 1.1, made up: check runs and verdicts on PRs 1-12, and who three PRs wait on. PR 3 waits on the person
    to approve, from the first day, and is never cleared; PR 4 waits on the builder for a rebase, cleared on the second
    day; PR 5 waits on the team for a verdict."""
    out = []
    for n in range(1, 13):
        opened = first + dt.timedelta(hours=5 * n)
        sha = hashlib.sha256(f"pr{n}".encode()).hexdigest()[:40]
        ran = opened + dt.timedelta(minutes=20)
        if frm <= ran < to:
            check = {
                "number": n,
                "head_sha": sha,
                "check": "unit tests",
                "conclusion": "failure" if n % 5 == 0 else "success",
                "started_at": _ts(opened + dt.timedelta(minutes=5)),
                "completed_at": _ts(ran),
            }
            out.append(_record("onloop.pr.checks", f"pr-{n}-check-{_ts(ran)}", ran, export, check, f"pr-{n}"))
        said = opened + dt.timedelta(hours=1)
        if frm <= said < to:
            verdict = {
                "number": n,
                "head_sha": sha,
                "reviewer": "reviewer",
                "verdict": "READY",
                "at": _ts(said),
            }
            out.append(
                _record("onloop.pr.reviewed", f"pr-{n}-review-{_ts(said)}", said, export, verdict, f"pr-{n}")
            )
    waits = [
        (3, "human", "approve", first + dt.timedelta(hours=16), None),
        (4, "builder", "rebase", first + dt.timedelta(hours=21), first + dt.timedelta(days=2, hours=1)),
        (5, "team", "verdict", first + dt.timedelta(hours=26), None),
    ]
    for n, who, why, since, cleared in waits:
        if since < to:  # an open wait is repeated in every export while it is open
            data = {"on": f"pr:{n}", "waits_on": who, "waits_for": why, "since": _ts(since)}
            out.append(_record("onloop.wait.opened", f"wait-{n}-{who}", since, export, data, f"pr-{n}"))
        if cleared and frm <= cleared < to:
            data = {"on": f"pr:{n}", "waits_on": who, "since": _ts(since), "cleared_at": _ts(cleared)}
            out.append(
                _record("onloop.wait.cleared", f"wait-{n}-{who}-cleared", cleared, export, data, f"pr-{n}")
            )
    return out


STAGES = ["researched", "specified", "spec accepted", "built", "shown"]
ITEMS = [("F1", "Keep the source's timestamps"), ("F2", "Add a CSV export"), ("F3", "Load the report faster")]


def _work(first: dt.datetime, frm: dt.datetime, to: dt.datetime, export: dt.datetime, day: int) -> list[dict]:
    """Pull requests, a goal checklist and a feedback batch, made up, in the shape a producer exports them."""
    out = []
    for n in range(1, 13):  # PRs 1-12: opened through the days, most merged, one closed without merging
        opened = first + dt.timedelta(hours=5 * n)
        sha = hashlib.sha256(f"pr{n}".encode()).hexdigest()[:40]
        pr = {"number": n, "head_sha": sha, "title": f"Example change {n}"}
        if frm <= opened < to:
            out.append(_record("onloop.pr.opened", f"pr-{n}-opened", opened, export, pr, f"pr-{n}"))
        done = opened + dt.timedelta(hours=3)
        if frm <= done < to and n % 6:
            out.append(_record("onloop.pr.merged", f"pr-{n}-merged", done, export, pr, f"pr-{n}"))
        elif frm <= done < to:
            out.append(_record("onloop.pr.closed", f"pr-{n}-closed", done, export, pr, f"pr-{n}"))
    # the release plan: 10 items; an edit ticks one more each half day, and a quiet day repeats the latest edit
    for k in range(6):
        edit = first + dt.timedelta(hours=12 * k + 1)
        if frm <= edit < to or k == 5 and edit < to:
            items = [{"id": i, "ticked": i <= 3 + k} for i in range(1, 11 + (k >= 4))]
            data = {"goal": "release-plan", "items": items}
            out.append(
                _record(
                    "onloop.goal.revision", f"revision-{_ts(edit)}", edit, export, data, "goal:release-plan"
                )
            )
    batch = "2026-11-14"
    out.append(
        _record(
            "onloop.feedback.batch",
            f"batch-{batch}",
            first,
            export,
            {"batch": batch, "from": "human", "stages": STAGES, "items": len(ITEMS)},
            f"feedback-batch:{batch}",
        )
    )
    for i, (item, label) in enumerate(ITEMS):
        done = min(len(STAGES), i + day + 1)  # each export, every item one stage further, until accepted
        stage = STAGES[done] if done < len(STAGES) else "accepted"
        data = {
            "batch": batch,
            "item": item,
            "label": label,
            "stage_index": done,
            "stage": stage,
            "skipped_stages": [],
            "waits_on": "human" if stage in ("spec accepted", "shown") else "team",
        }
        out.append(
            _record(
                "onloop.feedback.item",
                f"item-{batch}-{item}-{_ts(export)}",
                export,
                export,
                data,
                f"feedback:{batch}/{item}",
            )
        )
        out.append(
            _record(
                "onloop.feedback.link",
                f"link-{batch}-{item}-{40 + i}",
                first,
                export,
                {"batch": batch, "item": item, "issue": 40 + i},
                f"feedback:{batch}/{item}",
            )
        )
    return out


def _record(
    kind: str, rid: str, at: dt.datetime, export: dt.datetime, data: dict, subject: str | None = None
) -> dict:
    return {
        "specversion": "1.0",
        "id": rid,
        "source": f"{PRODUCER}/export",
        "type": kind,
        "time": _ts(at),
        "exporttime": _ts(export),
        "subject": subject or f"role:{data['role']}",
        "dataschema": f"onloop-contract/1.0/{kind.removeprefix('onloop.')}",
        "data": data,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--turns", type=int, default=1000)
    ap.add_argument("--end", default="2026-11-17T02:00:00Z")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--contract", default="1.0", choices=("1.0", "1.1", "1.2"))
    a = ap.parse_args(argv)
    end = dt.datetime.fromisoformat(a.end.replace("Z", "+00:00"))
    for f in write(pathlib.Path(a.out), end, a.days, a.turns, a.seed, a.contract):
        print(f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
