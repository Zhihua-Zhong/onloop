"""The constraint: open waits grouped by what they wait on, with the wait downstream of each, the largest first."""

import datetime as dt

from onloop import events as ev
from onloop.overlay.constraint import Wait, compute

NOW = "2026-11-09T12:00:00.000Z"


def wait(id, waiter, on_ref, minutes_ago, on_kind="resource", reason="running", cleared_at=None):
    now = dt.datetime.fromisoformat(NOW.replace("Z", "+00:00"))
    since = ev.norm_ts(now - dt.timedelta(minutes=minutes_ago))
    return Wait(
        id=id,
        waiter=waiter,
        on_kind=on_kind,
        on_ref=on_ref,
        reason=reason,
        since=since,
        cleared_at=cleared_at,
    )


def test_no_open_waits_means_no_constraint():
    c = compute([], NOW)
    assert c.top is None and c.as_dict() == {"now": NOW, "constraint": None, "bottlenecks": []}


def test_waits_are_grouped_by_what_they_wait_on_and_the_largest_total_is_the_constraint():
    c = compute(
        [
            wait("w1", "builder", "lock:db", 25, reason="conflict"),
            wait("w2", "reviewer", "lock:db", 5, reason="running"),
            wait("w3", "author", "human:lead", 20, on_kind="human", reason="needs_human"),
        ],
        NOW,
    )
    assert [b.on_ref for b in c.bottlenecks] == ["lock:db", "human:lead"]
    db = c.top
    assert db.direct_wait_s == 30 * 60 and db.downstream_wait_s == 30 * 60
    assert db.waiters == ["builder", "reviewer"] and db.reasons == {"conflict": 1, "running": 1}
    assert db.oldest_since == "2026-11-09T11:35:00.000Z"


def test_waits_on_a_waiter_count_downstream_of_what_it_waits_on():
    # The gate holds the builder; two roles wait on the builder. The gate is the constraint, not the builder.
    c = compute(
        [
            wait("w1", "builder", "gate", 10),
            wait("w2", "reviewer", "builder", 30, on_kind="role", reason="missing_verdict"),
            wait("w3", "operator", "builder", 30, on_kind="role", reason="missing_verdict"),
        ],
        NOW,
    )
    by_ref = {b.on_ref: b for b in c.bottlenecks}
    assert by_ref["builder"].direct_wait_s == 60 * 60 and by_ref["builder"].downstream_wait_s == 60 * 60
    assert by_ref["gate"].direct_wait_s == 10 * 60 and by_ref["gate"].downstream_wait_s == 70 * 60
    assert c.top.on_ref == "gate"


def test_a_cleared_wait_does_not_count():
    c = compute([wait("w1", "builder", "gate", 50, cleared_at=NOW), wait("w2", "author", "lock:x", 1)], NOW)
    assert [b.on_ref for b in c.bottlenecks] == ["lock:x"]


def test_a_cycle_is_counted_once_and_reported():
    c = compute(
        [
            wait("w1", "a", "b", 10, on_kind="role"),
            wait("w2", "b", "a", 20, on_kind="role"),
        ],
        NOW,
    )
    by_ref = {b.on_ref: b for b in c.bottlenecks}
    assert by_ref["a"].downstream_wait_s == 30 * 60 and by_ref["b"].downstream_wait_s == 30 * 60
    assert by_ref["a"].in_cycle and by_ref["b"].in_cycle


def test_ties_are_broken_deterministically():
    ws = [wait("w1", "x", "lock:b", 10), wait("w2", "y", "lock:a", 10)]
    assert [b.on_ref for b in compute(ws, NOW).bottlenecks] == ["lock:a", "lock:b"]
    assert [b.on_ref for b in compute(list(reversed(ws)), NOW).bottlenecks] == ["lock:a", "lock:b"]


def test_rows_from_the_waits_table_are_accepted():
    row = {
        "id": "w1",
        "item_id": "pr#12",
        "waiter": "builder",
        "on_kind": "resource",
        "on_ref": "gate",
        "reason": "running",
        "since": "2026-11-09T11:00:00.000Z",
        "cleared_at": None,
    }
    assert compute([row], NOW).top.direct_wait_s == 3600
