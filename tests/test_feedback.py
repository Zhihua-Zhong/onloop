"""Schema 9: feedback from both channels (the overview plan's §2 and §12.6), with the team's declared stage."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from onloop import cli
from onloop.contract import reader
from onloop.overlay import feedback_proj, schema
from onloop.overlay.ledger import Ledger

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("contract_example_fb", ROOT / "tools" / "contract_example.py")
example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(example)
END = dt.datetime(2026, 11, 17, 2, 0, 0, tzinfo=dt.UTC)
NOW = "2026-11-17T03:00:00.000Z"


def _rows(led, sql, args=()):
    return [dict(r) for r in led.select(sql, args)]


def test_the_ledger_is_at_schema_9_with_the_feedback_tables(tmp_path):
    with Ledger(tmp_path / "l.db") as led:
        assert led.version() == len(schema.MIGRATIONS) == 9
        names = {r["name"] for r in led.select("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"feedback_batches", "feedback", "feedback_links"} <= names


def test_a_person_gives_a_batch_in_one_sitting_and_the_body_is_kept_only_as_a_hash(tmp_path, capsys):
    path = tmp_path / "l.db"
    body = "Keep the source's timestamps\nwith the token: abcdefgh12345678 in the import"  # scan:allow (a fixture)
    assert cli.main(["feedback", body, "Add a CSV export", "--by", "lead", "--ledger", str(path)]) == 0
    with Ledger(path, readonly=True) as led:
        batch = _rows(led, "SELECT * FROM feedback_batches")
        items = _rows(led, "SELECT * FROM feedback ORDER BY position")
        log = json.dumps(
            [dict(r) for r in led.select("SELECT payload FROM events WHERE kind = 'feedback.given'")]
        )
    assert len(batch) == 1 and batch[0]["origin"] == "cli" and batch[0]["given_by"] == "lead"
    assert batch[0]["items"] == 2 and [i["position"] for i in items] == [1, 2]
    assert items[0]["title"] == "Keep the source's timestamps" and len(items[0]["body_sha256"]) == 64
    assert items[0]["declared_stage"] is None and items[0]["declared_by"] is None
    assert "abcdefgh12345678" not in log and "with the token" not in log  # the body never reaches the ledger


def test_a_secret_in_a_title_is_scrubbed_before_it_is_logged(tmp_path):
    path = tmp_path / "l.db"
    assert cli.main(["feedback", "rotate password: hunter2hunter2 today", "--ledger", str(path)]) == 0
    with Ledger(path, readonly=True) as led:
        title = led.select("SELECT title FROM feedback")[0]["title"]
    assert "hunter2hunter2" not in title and "[redacted]" in title


def test_feedback_with_no_item_is_refused(tmp_path, capsys):
    assert cli.main(["feedback", "--ledger", str(tmp_path / "l.db")]) == 2
    assert "at least one item" in capsys.readouterr().err


@pytest.fixture
def exported(tmp_path):
    root = tmp_path / "exchange"
    example.write(root, END, days=2, n=200, seed=3)
    (root / "onloop").mkdir()
    results = reader.import_snapshots(root, root / "onloop" / "ledger.db")
    assert [r.status for r in results] == ["imported", "imported"]
    return root / "onloop" / "ledger.db"


def test_a_teams_export_gives_its_batch_with_each_items_declared_stage(exported):
    with Ledger(exported, readonly=True) as led:
        batches = _rows(led, "SELECT * FROM feedback_batches")
        items = _rows(led, "SELECT * FROM feedback ORDER BY position")
        links = _rows(led, "SELECT * FROM feedback_links ORDER BY ref")
    assert len(batches) == 1 and batches[0]["origin"] == "import" and batches[0]["items"] == 3
    assert json.loads(batches[0]["stages"]) == example.STAGES
    assert [i["origin_ref"] for i in items] == ["F1", "F2", "F3"]
    assert all(i["declared_by"] == "team" for i in items)
    # the second export moved every item one stage on, so each stage began at that export
    second = sorted(i["declared_at"] for i in items)[-1]
    assert all(i["declared_since"] == i["declared_at"] == second for i in items)
    assert [i["declared_stage_index"] for i in items] == [2, 3, 4]
    assert {lk["ref"] for lk in links} == {"issue#40", "issue#41", "issue#42"} and len(links) == 3


def test_a_stage_that_did_not_move_keeps_the_time_it_began(tmp_path):
    with Ledger(tmp_path / "l.db") as led:
        for n, (at, idx) in enumerate([("2026-11-15T02:00:00.000Z", 1), ("2026-11-16T02:00:00.000Z", 1)]):
            led.append(
                {
                    "id": f"stamp{n}",
                    "source": "import",
                    "kind": "feedback.item.reported",
                    "subject_ref": "feedback:b/F1",
                    "at": at,
                    "payload": {
                        "batch": "b",
                        "item": "F1",
                        "stage_index": idx,
                        "stage": "specified",
                        "waits_on": "team",
                        "declared_by": "team",
                        "imported": {"exporttime": at, "producer": "p"},
                    },
                }
            )
        row = led.select("SELECT declared_since, declared_at FROM feedback")[0]
    assert (
        row["declared_since"] == "2026-11-15T02:00:00.000Z"
        and row["declared_at"] == "2026-11-16T02:00:00.000Z"
    )


def test_the_feedback_tables_replay_byte_for_byte(exported, tmp_path):
    path = tmp_path / "l.db"
    cli.main(["feedback", "One more", "--ledger", str(path)])
    for p in (exported, path):
        with Ledger(p) as led:
            same, *_ = led.rebuild_matches()
        assert same


def test_ids_are_content_derived():
    assert (
        feedback_proj.batch_id("cli", "x")
        == feedback_proj.batch_id("cli", "x")
        != feedback_proj.batch_id("ui", "x")
    )
    assert feedback_proj.item_id("b-1", 1).startswith("f-") and len(feedback_proj.item_id("b-1", 1)) == 12


def test_feedback_is_imported_only_by_its_own_rules():
    from onloop.overlay import projections

    for kind in feedback_proj.RULES:
        assert projections.RULES[kind] is feedback_proj.RULES[kind]
    assert sys.modules["onloop.overlay.feedback_proj"] is feedback_proj
