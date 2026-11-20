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
