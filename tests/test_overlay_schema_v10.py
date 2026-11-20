"""Ledger schema 10, replay, cost and live monitoring (decision 0019): the migration keeps every event, the `otel`
source arrives with it, a window on an event's own time is served by an index, and each new kind is the OCEL 2.0
event type decision 0019's §5 names."""

import pathlib
import re
import sqlite3

import pytest

from onloop.overlay import schema
from onloop.overlay.ledger import Ledger

from .test_overlay_ledger import T0, event, story

ROOT = pathlib.Path(__file__).resolve().parent.parent


def otel(eid, kind, subject, payload, at=None):
    return event(eid, kind, subject, payload, source="otel", at=at)


def test_a_version_9_ledger_migrates_to_version_10_and_keeps_every_event(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    with monkeypatch.context() as m:
        m.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:9])
        with Ledger(path, clock=lambda: T0) as led:
            story(led)
            led.append(event("v9i", "pr.opened", "pr#7", {"number": 7}, source="import"))
            with pytest.raises(sqlite3.IntegrityError):  # version 9 has no `otel` source
                led.append(otel("v9o", "cost.request", "session:s1", {"output_tokens": 4}))
            before = [tuple(r) for r in led.conn.execute("SELECT * FROM events ORDER BY seq")]
            uid = led.instance()
    with Ledger(path, clock=lambda: T0) as led:
        assert led.version() == len(schema.MIGRATIONS) == 10
        assert [tuple(r) for r in led.conn.execute("SELECT * FROM events ORDER BY seq")] == before
        assert led.instance() == uid
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            led.conn.execute("DELETE FROM events")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            led.conn.execute("UPDATE events SET kind = 'x'")
        seq = led.append(otel("v10o", "cost.request", "session:s1", {"output_tokens": 4, "prompt_id": "p1"}))
        assert seq == before[-1][0] + 1
        assert led.rebuild_matches()[0]


def test_a_replay_window_on_an_events_own_time_uses_the_time_index(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: T0) as led:
        plan = " ".join(
            str(r[-1])
            for r in led.conn.execute(
                "EXPLAIN QUERY PLAN SELECT seq FROM events WHERE COALESCE(at, ts_system) >= ? AND COALESCE(at, ts_system) < ? "
                "ORDER BY COALESCE(at, ts_system), seq",
                ("2026-11-01T00:00:00.000Z", "2026-11-02T00:00:00.000Z"),
            )
        )
    assert "events_time" in plan, plan


def test_version_10_keeps_the_earlier_source_sets():
    assert "otel" not in schema.SOURCES_8 and "otel" in schema.SOURCES
    assert all("'otel'" not in m for m in schema.MIGRATIONS[:9])
    assert "'import', 'otel'" in schema.SCHEMA_10


def test_each_new_kind_is_the_ocel_event_type_decision_0019_names():
    adr = (ROOT / "docs" / "decisions" / "0019-replay-cost-and-live-monitoring.md").read_text(
        encoding="utf-8"
    )
    rows = {}
    for kinds, ocel in re.findall(r"^\| ((?:`[a-z.]+`(?:, )?)+) \| ((?:`[a-z ]+`(?:, )?)+) \|", adr, re.M):
        for k, o in zip(re.findall(r"`([a-z.]+)`", kinds), re.findall(r"`([a-z ]+)`", ocel), strict=True):
            rows[k] = o
    assert rows == schema.KINDS_10, (rows, schema.KINDS_10)
