"""The replay's timeline (decision 0019, derive.timeline): each second of a lane counts once, in the first of the four
kinds that covers it; a request's tokens go to the step recorded on its session, else between steps."""

import random

import pytest

from onloop import derive
from onloop import timeline as tl
from onloop.overlay.ledger import Ledger

D = "2026-11-02T"


def at(hm: str) -> str:
    return f"{D}{hm}:00.000Z"


def row(led, table, **cols):
    cols.setdefault("last_seq", 0)
    keys = ", ".join(cols)
    led.conn.execute(
        f"INSERT INTO {table} ({keys}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values())
    )


class Clock:
    def __init__(self):
        self.t = at("09:00")

    def __call__(self):
        return self.t


def otel(led, clock, when, eid, session, **tokens):
    clock.t = when
    led.append(
        {
            "id": eid,
            "source": "otel",
            "kind": "cost.request",
            "subject_ref": f"session:{session}",
            "actor": "builder",
            "payload": {"session_id": session, "role": "builder", **tokens},
        }
    )


def step(led, clock, when, which, session, ref):
    clock.t = when
    led.append(
        {
            "id": f"{which}-{ref}-{session}",
            "source": "carrier",
            "kind": f"step.{which}",
            "subject_ref": f"step:task:{ref}",
            "actor": "builder",
            "payload": {"session_id": session, "role": "builder", "step": {"kind": "task", "ref": ref}},
        }
    )


@pytest.fixture
def led(tmp_path):
    clock = Clock()
    with Ledger(tmp_path / "l.db", clock=clock) as led:
        row(
            led,
            "deliveries",
            id="d1",
            to_role="builder",
            created_at=at("10:00"),
            delivered_at=at("10:02"),
            processing_at=at("10:03"),
            run_id="r1",
            state="acked",
            kind="message",
        )
        row(
            led,
            "deliveries",
            id="d2",
            to_role="builder",
            created_at=at("10:40"),
            state="cancelled",
            kind="message",
        )
        row(
            led,
            "runs",
            id="r1",
            role="builder",
            session_id="s1",
            lease_id="l1",
            started_at=at("10:01"),
            ended_at=at("10:10"),
            status="ok",
        )
        task = dict(
            origin="outcome",
            clock_from=at("10:20"),
            return_author_at=at("12:00"),
            return_coordinator_at=at("13:00"),
            incomplete=1,
            updated_at=at("10:20"),
            owner="human",
        )
        row(
            led, "human_tasks", id="t1", origin_ref="x1", author_role="builder", opened_at=at("10:20"), **task
        )
        row(
            led,
            "human_tasks",
            id="t0",
            origin_ref="x0",
            author_role="builder",
            opened_at=at("09:00"),
            resolved_at=at("09:30"),
            **task,
        )
        step(led, clock, at("10:04"), "started", "s1", "t-12")
        otel(
            led,
            clock,
            at("10:05"),
            "c1",
            "s1",
            input_tokens=10,
            output_tokens=20,
            cache_read_tokens=300,
            cache_creation_tokens=40,
            duration_ms=2000,
        )
        step(led, clock, at("10:08"), "ended", "s1", "t-12")
        otel(
            led,
            clock,
            at("10:30"),
            "c2",
            "s1",
            input_tokens=1,
            output_tokens=2,
            cache_read_tokens=3,
            cache_creation_tokens=4,
            duration_ms=60000,
        )
        yield led


def test_each_second_counts_once_in_the_first_kind_that_covers_it(led):
    t = derive.timeline(led, at("10:00"), at("11:00"), now=at("10:50"))
    (b,) = [L for L in t["lanes"] if L["lane"] == "builder"]
    # queued 10:00-10:01 (the run's harness wins from 10:01); harness 10:01-10:03; working 10:03-10:10 (the message
    # is processed) and 10:29-10:30 (a request's own minute, over the wait); waiting 10:20-10:50 less that minute
    assert b["time_s"] == {"working": 480.0, "harness": 120.0, "waiting": 1740.0, "queued": 60.0}
    assert [s[2] for s in b["segments"]] == ["queued", "harness", "working", "waiting", "working", "waiting"]
    assert b["segments"][-1][1] == at("10:50")  # still open: runs to now, not to the end of the window


def test_a_requests_tokens_go_to_its_recorded_step_else_between_steps(led):
    t = derive.timeline(led, at("10:00"), at("11:00"), now=at("10:50"))
    (b,) = [L for L in t["lanes"] if L["lane"] == "builder"]
    assert b["steps"] == [
        {
            "step": "step:task:t-12",
            "attributed": "recorded",
            "tokens": {
                "input": 10,
                "output": 20,
                "cache_creation": 40,
                "cache_read": 300,
                "total": 70,
                "requests": 1,
            },
        }
    ]
    assert b["between_steps"] == {
        "input": 1,
        "output": 2,
        "cache_creation": 4,
        "cache_read": 3,
        "total": 7,
        "requests": 1,
    }
    assert b["tokens"]["total"] == 77 and t["totals"]["tokens"]["total"] == 77


def test_a_window_clips_every_interval(led):
    t = derive.timeline(led, at("10:02"), at("10:06"), now=at("10:50"))
    (b,) = [L for L in t["lanes"] if L["lane"] == "builder"]
    assert b["time_s"] == {"working": 180.0, "harness": 60.0, "waiting": 0.0, "queued": 0.0}
    assert b["between_steps"]["requests"] == 0 and b["steps"][0]["tokens"]["requests"] == 1


def test_the_sweep_gives_the_same_segments_as_checking_every_instant():
    r = random.Random(7)
    raw = []
    for _ in range(300):
        a = r.randrange(0, 86_400_000)
        raw.append((a, a + r.randrange(1, 3_600_000), r.choice(tl.KINDS)))
    cuts = sorted({x for a, b, _ in raw for x in (a, b)})
    want: list[tuple[int, int, str]] = []
    for a, b in zip(cuts, cuts[1:], strict=False):
        here = {k for x, y, k in raw if x <= a and y >= b}
        kind = next((k for k in tl.KINDS if k in here), None)
        if kind is None:
            continue
        if want and want[-1][1] == a and want[-1][2] == kind:
            want[-1] = (want[-1][0], b, kind)
        else:
            want.append((a, b, kind))
    assert tl.segments(raw) == want


# -- rule E's clause 2: the next commit's Node: trailer on the session's branch (decision 0019) ------------------------


def git_repo(path, commits):
    """A repository with one commit per (hh:mm, node or None), at those committer times, on branch `work`."""
    import os
    import subprocess

    path.mkdir()

    def git(*a, when=None):
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t",
        }
        if when:
            env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = when
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True, env=env)

    git("init", "-q", "-b", "work")
    for i, (hm, node) in enumerate(commits):
        (path / "f.txt").write_text(str(i), encoding="utf-8")
        git("add", "f.txt")
        msg = f"change {i}" + (f"\n\nNode: {node}" if node else "")
        git("commit", "-q", "-m", msg, when=at(hm).replace(".000Z", "+00:00"))
    return path


def test_a_request_no_step_covers_goes_to_the_next_commits_node_attributed_by_rule(tmp_path):
    repo = git_repo(tmp_path / "repo", [("10:10", "a1"), ("10:20", None), ("10:40", "a2")])
    clock = Clock()
    with Ledger(tmp_path / "l.db", clock=clock) as led:
        clock.t = at("10:00")
        led.append(
            {
                "id": "open",
                "source": "cli",
                "kind": "session.opened",
                "subject_ref": "session:s9",
                "actor": "builder",
                "payload": {"session_id": "s9", "role": "builder", "repo": str(repo), "branch": "work"},
            }
        )
        otel(led, clock, at("10:05"), "r1", "s9", input_tokens=1, output_tokens=1)  # before a1's commit
        otel(led, clock, at("10:15"), "r2", "s9", input_tokens=2, output_tokens=2)  # next commit has no Node
        otel(led, clock, at("10:30"), "r3", "s9", input_tokens=3, output_tokens=3)  # before a2's commit
        step(led, clock, at("10:31"), "started", "s9", "t-9")
        otel(
            led, clock, at("10:32"), "r4", "s9", input_tokens=4, output_tokens=4
        )  # a recorded step wins over rule
        step(led, clock, at("10:33"), "ended", "s9", "t-9")
        otel(led, clock, at("10:50"), "r5", "s9", input_tokens=5, output_tokens=5)  # no commit after it
        t = derive.timeline(led, at("10:00"), at("11:00"), now=at("11:00"))
    (b,) = [L for L in t["lanes"] if L["lane"] == "builder"]
    got = {(s["step"], s["attributed"]): s["tokens"]["total"] for s in b["steps"]}
    assert got == {("step:node:a1", "rule"): 2, ("step:node:a2", "rule"): 6, ("step:task:t-9", "recorded"): 8}
    assert b["between_steps"]["total"] == 4 + 10 and b["between_steps"]["requests"] == 2


def test_rule_e_reads_nothing_without_a_known_branch(tmp_path):
    clock = Clock()
    with Ledger(tmp_path / "l.db", clock=clock) as led:
        otel(led, clock, at("10:05"), "r1", "s8", input_tokens=1, output_tokens=1)
        t = derive.timeline(led, at("10:00"), at("11:00"), now=at("11:00"))
    (b,) = [L for L in t["lanes"] if L["lane"] == "builder"]
    assert b["steps"] == [] and b["between_steps"]["requests"] == 1


def test_a_session_records_only_its_repository_and_branch(tmp_path):
    from onloop import telemetry

    repo = git_repo(tmp_path / "repo", [("10:10", "a1")])
    where = telemetry.place(repo)
    assert where == {"repo": where["repo"], "branch": "work"} and where["repo"].replace("\\", "/").endswith(
        "/repo"
    )
    assert telemetry.place(tmp_path / "not-a-repo-xyz") == {"repo": None, "branch": None}
    e = telemetry.opened_event(source="cli", role="builder", session_id="s1", cwd=str(repo), **where)
    assert set(e["payload"]) == {"role", "session_id", "cwd", "repo", "branch"}


def test_working_now_is_each_open_recorded_step_with_its_tokens_so_far(led):
    assert [(w["lane"], w["step"], w["tokens"]["total"]) for w in tl.working_now(led, at("10:06"))] == [
        ("builder", "step:task:t-12", 70)
    ]
    assert tl.working_now(led, at("10:20")) == []  # the step ended at 10:08
    assert tl.working_now(led, at("10:03")) == []  # and began at 10:04
