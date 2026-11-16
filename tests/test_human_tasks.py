"""The human task list's record (decision 0013): opened by asks, never closed by a wait clearing, closed only with a
resolution and its evidence, and dated, never counted (decision 0012)."""

import datetime as dt
import json
import pathlib
import sqlite3

import pytest

from onloop import localday
from onloop.daemon import hook_receiver as hr
from onloop.overlay import human_tasks
from onloop.overlay.ledger import Ledger

FIX = pathlib.Path(__file__).parent / "fixtures" / "hooks"
JST = dt.timezone(dt.timedelta(hours=9), "+09:00")


class Clock:
    def __init__(self, ts="2026-11-16T05:00:00.000Z"):
        self.ts = ts

    def __call__(self):
        return self.ts


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def led(tmp_path, clock):
    localday.set_zone(JST)
    led = Ledger(tmp_path / "ledger.db", clock=clock)
    yield led
    led.close()
    localday.set_zone(None)


_n = iter(range(10_000))


def stop(led, line, role="builder"):
    p = json.loads((FIX / "stop.json").read_text(encoding="utf-8"))
    p.update(last_assistant_message=f"Some work.\n\n{line}", prompt_id=f"p{next(_n)}")
    return hr.handle(led, "stop", {"role": role, "payload": p})


def tasks(led, open_only=False):
    where = " WHERE stage <> 'resolved'" if open_only else ""
    return led._rows(f"SELECT * FROM human_tasks{where} ORDER BY opened_at, id")


ASK = (
    "outcome: blocked owner=human ref=pr#45 goal=demo/14 weight=critical days=2 "
    'effect="approve the schema change, so the import can run" holds="the import demo waits"'
)


def test_an_ask_to_the_person_opens_a_task_with_its_fields(led):
    stop(led, ASK)
    (t,) = tasks(led)
    assert (t["origin"], t["author_role"], t["owner"], t["ref"]) == ("outcome", "builder", "human", "pr#45")
    assert (t["goal_id"], t["goal_item"], t["weight"]) == ("demo", 14, "critical")
    assert t["effect"].startswith("approve") and t["holds"] == "the import demo waits"
    assert t["incomplete"] == 0 and t["stage"] == "open"
    # 05:00Z is 14:00 in +09:00 on Nov 16: days=2 is Nov 18, stored as a date with the key as written
    assert (t["cost_from"], t["cost_given"]) == ("2026-11-18", "days=2")
    assert "days" not in t  # no count is stored anywhere
    assert t["return_author_at"] == "2026-11-23T05:00:00.000Z"
    assert t["return_coordinator_at"] == "2026-11-30T05:00:00.000Z"


def test_days_are_dated_by_the_calendar_day_of_the_ledger_time_in_kpi_timezone(led, clock):
    clock.ts = "2026-11-16T14:30:00.000Z"  # 23:30 on Nov 16 in +09:00
    stop(led, 'outcome: blocked owner=human ref=#1 days=3 effect="a, so b" holds=x')
    clock.ts = "2026-11-16T15:30:00.000Z"  # 00:30 on Nov 17 in +09:00
    stop(led, 'outcome: blocked owner=human ref=#2 days=3 effect="a, so b" holds=x')
    assert [t["cost_from"] for t in tasks(led)] == ["2026-11-19", "2026-11-20"]


def test_the_date_is_frozen_in_the_event_so_replay_and_a_new_zone_do_not_move_it(led, clock):
    stop(led, 'outcome: blocked owner=human ref=#1 days=3 effect="a, so b" holds=x')
    before = led.dump()
    localday.set_zone(dt.timezone(dt.timedelta(hours=-11)))
    clock.ts = "2027-01-01T00:00:00.000Z"
    assert led.replay() == before
    ok, _, _ = led.rebuild_matches()
    assert ok


def test_a_wait_clearing_never_closes_the_task(led):
    stop(led, ASK)
    stop(led, "outcome: idle")  # the role's next turn clears its blocked wait (decision 0007)
    assert led._rows("SELECT * FROM waits WHERE reason = 'blocked' AND cleared_at IS NULL") == []
    (t,) = tasks(led)
    assert t["stage"] == "open" and t["resolution"] is None


def test_a_restated_ask_amends_the_same_task(led):
    stop(led, "outcome: blocked owner=human ref=pr#45")
    stop(led, ASK)
    (t,) = tasks(led)
    assert t["incomplete"] == 0 and t["weight"] == "critical"


def test_the_author_closing_the_ref_without_the_person_is_resolved_without_you(led):
    stop(led, ASK)
    stop(led, "outcome: done ref=pr#45")
    (t,) = tasks(led)
    assert (t["stage"], t["resolution"], t["resolved_by"]) == ("resolved", "superseded", "builder")
    assert t["evidence"].startswith("turn:") and t["superseded_by"] == t["evidence"]


def test_done_resolves_closes_a_returned_task_as_expired(led):
    stop(led, ASK)
    (t,) = tasks(led)
    led.append(
        {
            "id": "r1",
            "source": "detector",
            "kind": "task.returned",
            "subject_ref": f"task:{t['id']}",
            "payload": {"id": t["id"], "stage": "author"},
        }
    )
    stop(led, f"outcome: done resolves={t['id']}")
    (t,) = tasks(led)
    assert (t["resolution"], t["stage"]) == ("expired", "resolved")


@pytest.mark.parametrize(
    ("line", "reasons", "flags"),
    [
        ("outcome: blocked owner=human ref=#1", ["no_effect"], ["no_weight", "no_default", "no_goal"]),
        ('outcome: blocked owner=human ref=#1 effect="a, so b" days=2', ["no_holds"], ["no_weight"]),
        ('outcome: blocked owner=human ref=#1 effect="a, so b" days=?', ["no_holds"], ["no_weight"]),
        ('outcome: blocked owner=human ref=#1 effect="a, so b" cost_from=2026-11-20', ["no_holds"], []),
        ('outcome: blocked owner=human ref=#1 effect="a, so b"', [], ["no_weight"]),
        (
            "outcome: blocked owner=human ref=#1 weight=critical days=1",
            ["no_effect", "no_holds"],
            ["critical_incomplete"],
        ),
    ],
)
def test_what_makes_an_ask_incomplete(led, line, reasons, flags):
    stop(led, line)
    (t,) = tasks(led)
    assert json.loads(t["incomplete_reasons"]) == reasons
    assert t["incomplete"] == (1 if reasons else 0)
    assert set(flags) <= set(json.loads(t["flags"]))


def test_an_ask_to_another_role_makes_no_task(led):
    stop(led, 'outcome: blocked owner=lead ref=#1 effect="a, so b"')
    assert tasks(led) == []


def test_a_task_is_never_closed_without_a_resolution_and_evidence(led):
    stop(led, ASK)
    (t,) = tasks(led)
    with pytest.raises(sqlite3.IntegrityError):
        led.conn.execute("UPDATE human_tasks SET stage = 'resolved' WHERE id = ?", (t["id"],))
    with pytest.raises(sqlite3.IntegrityError):
        led.conn.execute(
            "UPDATE human_tasks SET stage = 'resolved', resolution = 'done', resolved_at = ?, resolved_by = 'x' "
            "WHERE id = ?",
            (t["opened_at"], t["id"]),
        )
    with pytest.raises(sqlite3.IntegrityError):  # no event deletes a row of the log
        led.conn.execute("DELETE FROM events")


def test_a_resolved_task_stays_resolved_and_the_first_resolution_stands(led):
    stop(led, ASK)
    (t,) = tasks(led)
    for n, res in enumerate(("done", "declined")):
        led.append(
            {
                "id": f"res{n}",
                "source": "ui",
                "kind": "task.resolved",
                "subject_ref": f"task:{t['id']}",
                "payload": {"id": t["id"], "resolution": res, "evidence": "human:ui", "by": "human"},
            }
        )
    (t,) = tasks(led)
    assert t["resolution"] == "done"
    with pytest.raises(sqlite3.IntegrityError):
        led.conn.execute("UPDATE human_tasks SET stage = 'open', resolution = NULL WHERE id = ?", (t["id"],))


def test_one_open_task_per_ask_is_the_database_s(led):
    stop(led, ASK)
    (t,) = tasks(led)
    row = dict(t)
    row.update(id="t-other", origin_ref="other")
    cols = ", ".join(row)
    with pytest.raises(sqlite3.IntegrityError):
        led.conn.execute(
            f"INSERT INTO human_tasks ({cols}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values())
        )


def test_a_kept_task_restarts_its_clocks(led, clock):
    stop(led, ASK)
    (t,) = tasks(led)
    led.append(
        {
            "id": "ret",
            "source": "detector",
            "kind": "task.returned",
            "subject_ref": "x",
            "payload": {"id": t["id"], "stage": "coordinator"},
        }
    )
    clock.ts = "2026-12-01T00:00:00.000Z"
    led.append(
        {
            "id": "keep",
            "source": "cli",
            "kind": "task.kept",
            "subject_ref": "x",
            "payload": {"id": t["id"], "reason": "the demo moved", "by": "lead"},
        }
    )
    (t,) = tasks(led)
    assert (t["stage"], t["kept_reason"], t["return_author_at"]) == (
        "open",
        "the demo moved",
        "2026-12-08T00:00:00.000Z",
    )


def test_onloop_ask_then_a_line_that_names_the_task_makes_one_task(led):
    led.append(
        {
            "id": "ask1",
            "source": "cli",
            "kind": "task.opened",
            "subject_ref": "task:t-abc",
            "payload": {
                "id": "t-abc",
                "origin": "cli",
                "origin_ref": "ask1",
                "author_role": "builder",
                "ref": "pr#9",
                "ask": {"effect": "a, so b"},
            },
        }
    )
    stop(led, "outcome: blocked owner=human ref=task:t-abc")
    (t,) = tasks(led)
    assert t["id"] == "t-abc" and t["wait_id"].endswith("/blocked")


def test_a_blocking_prompt_is_a_task_until_the_session_goes_on(led):
    p = json.loads((FIX / "notification.json").read_text(encoding="utf-8"))
    p.update(notification_type="permission_prompt", message="Claude needs your permission to use Bash")
    hr.handle(led, "notification", {"role": "builder", "payload": p})
    (t,) = tasks(led)
    assert t["origin"] == "notification" and t["incomplete"] == 0
    stop(led, "outcome: idle")
    (t,) = tasks(led)
    assert (t["resolution"], t["resolved_by"]) == ("done", "session")


def test_a_paused_role_is_a_task_until_resumed(led):
    led.append(
        {
            "id": "pz",
            "source": "ui",
            "kind": "role.paused",
            "subject_ref": "role:reviewer",
            "payload": {"id": "reviewer"},
        }
    )
    (t,) = tasks(led)
    assert t["origin"] == "pause" and "resume reviewer" in t["effect"]
    led.append(
        {
            "id": "rs",
            "source": "ui",
            "kind": "role.resumed",
            "subject_ref": "role:reviewer",
            "payload": {"id": "reviewer"},
        }
    )
    assert tasks(led, open_only=True) == []


def test_a_version_1_event_keeps_version_1_meaning_on_replay(led):
    """An ask logged before decision 0012 (no `grammar` in the payload) ends when its wait clears."""
    led.append(
        {
            "id": "old1",
            "source": "hook",
            "kind": "hook.stop",
            "subject_ref": "role:builder",
            "payload": {
                "role": "builder",
                "session_id": "s",
                "line": "outcome: blocked owner=human ref=#1",
                "outcome": {"value": "blocked", "ref": "#1", "owner": "human", "waker": None},
            },
        }
    )
    (t,) = tasks(led)
    assert "legacy" in json.loads(t["flags"]) and t["incomplete"] == 1
    led.append(
        {
            "id": "old2",
            "source": "hook",
            "kind": "hook.stop",
            "subject_ref": "role:builder",
            "payload": {
                "role": "builder",
                "session_id": "s",
                "line": "outcome: idle",
                "outcome": {"value": "idle", "ref": None, "owner": None, "waker": None},
            },
        }
    )
    (t,) = tasks(led)
    assert (t["resolution"], t["evidence"]) == ("superseded", "turn:old2")


def test_the_task_projection_replays_byte_for_byte(led, clock):
    stop(led, ASK)
    stop(led, 'outcome: blocked owner=human ref=#2 days=? effect="a, so b"')
    clock.ts = "2026-11-17T00:00:00.000Z"
    stop(led, "outcome: done ref=pr#45")
    ok, live, rebuilt = led.rebuild_matches()
    assert ok, (live, rebuilt)


def test_task_ids_are_deterministic():
    assert human_tasks.task_id("outcome", "e1") == human_tasks.task_id("outcome", "e1")
    assert human_tasks.task_id("outcome", "e1") != human_tasks.task_id("cli", "e1")
