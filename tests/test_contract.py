"""The data contract (decision 0017): the reader, its refusals, privacy, and the activity view over a replay."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from onloop import cli
from onloop.contract import reader
from onloop.daemon import hook_receiver
from onloop.detect import core, no_outcome
from onloop.overlay.ledger import Ledger
from onloop.report import activity

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("contract_example", ROOT / "tools" / "contract_example.py")
example = importlib.util.module_from_spec(_spec)
sys.modules["contract_example"] = example
_spec.loader.exec_module(example)

import datetime as dt  # noqa: E402

END = dt.datetime(2026, 11, 17, 2, 0, 0, tzinfo=dt.UTC)
NOW = "2026-11-17T03:00:00.000Z"
SECRET = "maintainer@example.org"  # a value that must never reach a report; scan:allow (a test fixture)


@pytest.fixture
def exchange(tmp_path):
    root = tmp_path / "exchange"
    example.write(root, END, days=2, n=400, seed=3)
    (root / "onloop").mkdir()
    return root


def _ledger(root):
    return root / "onloop" / "ledger.db"


def _snap(root, i=-1):
    return sorted((root / "snapshots").iterdir())[i]


def _rewrite(folder, edit):
    """Change one record of a snapshot's file and keep the manifest's hash and counts right, unless told otherwise."""
    path = folder / "events.jsonl"
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    edit(lines)
    body = ("\n".join(json.dumps(x) for x in lines) + "\n").encode("utf-8")
    path.write_bytes(body)
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    m["files"]["events.jsonl"]["sha256"] = hashlib.sha256(body).hexdigest()
    (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")


def test_snapshots_import_once_and_overlapping_windows_deduplicate(exchange):
    first = reader.import_snapshots(exchange, _ledger(exchange))
    assert [r.status for r in first] == ["imported", "imported"]
    with Ledger(_ledger(exchange), readonly=True) as led:
        turns = led.select("SELECT count(*) AS n FROM turns")[0]["n"]
        n_events = led.select("SELECT count(*) AS n FROM events WHERE source = 'import'")[0]["n"]
    assert turns == 400  # every generated turn once, though the two windows overlap by a day
    assert first[1].new_events < first[1].records  # the overlap was already there
    again = reader.import_snapshots(exchange, _ledger(exchange))
    assert [r.status for r in again] == ["already imported", "already imported"]
    with Ledger(_ledger(exchange), readonly=True) as led:
        assert led.select("SELECT count(*) AS n FROM events WHERE source = 'import'")[0]["n"] == n_events
    report = (exchange / "onloop" / reader.REPORT).read_text(encoding="utf-8").splitlines()
    assert len(report) == 4 and all(json.loads(x)["status"] for x in report)


def test_an_imported_turn_has_the_id_the_live_hook_would_give_it(exchange):
    rec = json.loads(next(x for x in (_snap(exchange, 0) / "events.jsonl").open() if "turn.ended" in x))
    ev = reader.to_event(rec, "example-team")
    d = rec["data"]
    live = hook_receiver.event_for(
        "stop", {"role": d["role"], "payload": {"session_id": d["session_id"], "prompt_id": d["prompt_id"]}}
    )
    assert ev["id"] == live["id"] and ev["kind"] == live["kind"] == "hook.stop"
    assert (
        ev["source"] == "import"
        and ev["at"] == rec["time"]
        and ev["payload"]["started_at"] == d["started_at"]
    )


def test_a_bad_record_refuses_its_whole_file_and_the_report_never_holds_the_value(exchange):
    def bad(lines):
        lines[3]["data"]["role"] = SECRET  # not a role name, and e-mail-shaped

    _rewrite(_snap(exchange), bad)
    results = reader.import_snapshots(exchange, _ledger(exchange))
    assert [r.status for r in results] == ["imported", "refused"]
    refused = results[1]
    assert (
        refused.problems and refused.problems[0]["line"] == 4 and refused.problems[0]["path"] == "/data/role"
    )
    text = (exchange / "onloop" / reader.REPORT).read_text(encoding="utf-8")
    assert SECRET not in text
    with Ledger(_ledger(exchange), readonly=True) as led:  # nothing of the refused file is in the ledger
        files = {k for k in led.ingest_state("import") if k.endswith(".jsonl")}
        assert files == {f"{_snap(exchange, 0).name}/events.jsonl"}


def test_a_privacy_hit_in_a_free_field_refuses_the_file_by_path_and_rule_only(exchange, capsys):
    def leak(lines):
        lines[5]["subject"] = f"role:x {SECRET}"

    _rewrite(_snap(exchange), leak)
    assert cli.main(["import", "snapshot", str(exchange), "--ledger", str(_ledger(exchange))]) == 2
    out = capsys.readouterr().out
    assert "/subject: an e-mail address-shaped string" in out and SECRET not in out


def test_the_producers_own_deny_list_refuses_a_file(exchange):
    (exchange / "deny.txt").write_text("# names we never show\nbuilder\n", encoding="utf-8")
    results = reader.import_snapshots(exchange, _ledger(exchange))
    assert {r.status for r in results} == {"refused"}
    assert any("deny list" in p["rule"] for p in results[0].problems)


@pytest.mark.parametrize(
    "spoil, rule",
    [
        (lambda m: m.update(contract="1.9"), "a newer contract"),
        (lambda m: m["files"]["events.jsonl"].update(sha256="0" * 64), "SHA-256 differs"),
        (
            lambda m: m["files"]["events.jsonl"]["counts"].update({"onloop.turn.ended": 1}),
            "counts per type differ",
        ),
        (lambda m: m.update(extra="x"), "not allowed"),
    ],
)
def test_a_manifest_that_disagrees_is_refused(exchange, spoil, rule):
    folder = _snap(exchange)
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    spoil(m)
    (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    results = reader.import_snapshots(exchange, _ledger(exchange))
    assert results[-1].status == "refused" and any(rule in p["rule"] for p in results[-1].problems)


def test_a_snapshot_without_its_manifest_is_still_being_written_and_is_skipped(exchange):
    (_snap(exchange) / "manifest.json").unlink()
    results = reader.import_snapshots(exchange, _ledger(exchange))
    assert [r.snapshot for r in results] == [_snap(exchange, 0).name]


def test_a_changed_file_under_the_same_name_is_refused(exchange):
    reader.import_snapshots(exchange, _ledger(exchange))
    _rewrite(_snap(exchange), lambda lines: lines.pop())
    m = json.loads((_snap(exchange) / "manifest.json").read_text(encoding="utf-8"))
    m["files"]["events.jsonl"]["counts"]["onloop.turn.ended"] -= 1
    (_snap(exchange) / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    results = reader.import_snapshots(exchange, _ledger(exchange))
    assert results[-1].status == "refused" and "another hash" in results[-1].problems[0]["rule"]


def test_the_ledger_never_lives_in_a_repository_the_snapshots_or_onloops_state(exchange, tmp_path):
    with pytest.raises(reader.ImportRefused, match="snapshots"):
        reader.import_snapshots(exchange, _snap(exchange) / "ledger.db")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(reader.ImportRefused, match="git work tree"):
        reader.import_snapshots(exchange, repo / "ledger.db")
    state = tmp_path / "state"
    state.mkdir()
    (state / "denylist.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(reader.ImportRefused, match="state"):
        reader.import_snapshots(exchange, state / "ledger.db")


def test_a_dry_run_writes_nothing(exchange):
    results = reader.import_snapshots(exchange, _ledger(exchange), dry_run=True)
    assert {r.status for r in results} == {"checked (dry run)"}
    assert not _ledger(exchange).exists() and not (exchange / "onloop" / reader.REPORT).exists()


def test_a_team_without_outcome_lines_gets_one_summary_not_one_stall_per_turn(exchange):
    reader.import_snapshots(exchange, _ledger(exchange))
    with Ledger(_ledger(exchange)) as led:
        got = core.run(led, [(no_outcome.NAME, no_outcome.detect)], NOW)
        alerts = led.alerts()
    assert len(got.opened) == 1 and got.would == []
    assert [a["title"] for a in alerts] == ["Outcome lines not adopted: 0 of 400 turns end with one"]


def test_activity_shows_lanes_side_sessions_silences_and_hours(exchange):
    reader.import_snapshots(exchange, _ledger(exchange))
    with Ledger(_ledger(exchange), readonly=True) as led:
        a = activity.activity(led, NOW, 48)
    assert (
        a["data_as_of"] == "2026-11-17T02:00:00.000Z" and a["to"] == a["data_as_of"]
    )  # the data's end, not now
    assert a["data_age_s"] == pytest.approx(60 * 60)
    assert a["roles_declared"] == 11 and a["outcome_lines"] == {"with": 0, "of": 400}
    lanes = {lane["role"]: lane for lane in a["lanes"]}
    assert lanes["lead"]["side_sessions"] == 2 and lanes["lead"]["sessions"][0]["main"] is True
    assert lanes["quiet-observer"]["turns"] == 0 and lanes["quiet-observer"]["last_turn_at"] is None
    assert sum(h["turns"] for h in a["per_hour"]) == a["turns"] == sum(lane["turns"] for lane in a["lanes"])
    assert all(h["at"].endswith(":00:00.000Z") for h in a["per_hour"])  # whole clock hours
    quiet = lanes["docs-writer"]["longest_silence"]
    assert quiet["seconds"] >= 6 * 3600  # the generator's long silence for this role
    for lane in a["lanes"]:
        for s in lane["sessions"]:
            assert all(m["start"] <= m["end"] for m in s["marks"])
    assert [lane["turns"] for lane in a["lanes"]] == sorted(
        (lane["turns"] for lane in a["lanes"]), reverse=True
    )


def test_pull_requests_goal_revisions_and_feedback_import_once_each(tmp_path):
    root = tmp_path / "exchange"
    example.write(root, END, days=2, n=120, seed=5)
    (root / "onloop").mkdir()
    reader.import_snapshots(root, _ledger(root))
    with Ledger(_ledger(root), readonly=True) as led:
        kinds = {
            r["kind"]: r["n"] for r in led.select("SELECT kind, count(*) AS n FROM events GROUP BY kind")
        }
        prs = led.select("SELECT state, count(*) AS n FROM items WHERE id LIKE 'pr#%' GROUP BY state")
        goal = led.select("SELECT count(*) AS n, sum(checked) AS ticked FROM goal_items")[0]
        dens = [r["count"] for r in led.select("SELECT count FROM goal_denominators ORDER BY at")]
    # a PR seen in two overlapping windows is one PR: keyed by producer, kind and number, never by its head commit
    assert kinds["pr.opened"] == 12 and kinds["pr.merged"] + kinds["pr.closed"] == 12
    assert prs == [{"state": "done", "n": 12}]
    # the latest goal edit repeated on a quiet day is one edit, not a new revision
    assert goal == {"n": 11, "ticked": 8} and dens == [10, 11] and kinds["goal.read"] == 2
    # each export's stamp of an item's declared stage is its own event; a link is one
    assert kinds["feedback.item.reported"] == 6 and kinds["feedback.link.reported"] == 3
    assert kinds["feedback.batch.reported"] == 1


def test_a_feedback_items_stage_is_reported_by_the_team_and_kept_per_export(tmp_path):
    root = tmp_path / "exchange"
    example.write(root, END, days=2, n=60, seed=5)
    (root / "onloop").mkdir()
    reader.import_snapshots(root, _ledger(root))
    with Ledger(_ledger(root), readonly=True) as led:
        stamps = led.select(
            "SELECT json_extract(payload, '$.stage_index') AS i, json_extract(payload, '$.declared_by') AS by "
            "FROM events WHERE kind = 'feedback.item.reported' AND subject_ref = 'feedback:2026-11-14/F1' ORDER BY at"
        )
    assert [s["i"] for s in stamps] == [1, 2] and {s["by"] for s in stamps} == {"team"}


@pytest.fixture
def exchange_1_1(tmp_path):
    root = tmp_path / "exchange"
    example.write(root, END, days=2, n=120, seed=5, contract="1.1")
    (root / "onloop").mkdir()
    return root


def test_contract_1_1_checks_reviews_and_waits_join_the_ledgers_wait_model(exchange_1_1):
    results = reader.import_snapshots(exchange_1_1, _ledger(exchange_1_1))
    assert {r.status for r in results} == {"imported"}
    with Ledger(_ledger(exchange_1_1), readonly=True) as led:
        kinds = {
            r["kind"]: r["n"] for r in led.select("SELECT kind, count(*) AS n FROM events GROUP BY kind")
        }
        waits = {r["id"]: r for r in led.select("SELECT * FROM waits")}
        tasks = led.select("SELECT origin, ref, effect, stage FROM human_tasks")
        con = led.constraint(NOW)
    assert kinds["check.completed"] == 12 and kinds["review.submitted"] == 12
    human = next(w for w in waits.values() if w["on_kind"] == "human")
    assert human["item_id"] == "pr#3" and human["cleared_at"] is None
    rebase = next(w for w in waits.values() if w["on_ref"] == "builder")
    assert rebase["cleared_at"] is not None  # cleared by its wait.cleared, and only by it
    team = next(w for w in waits.values() if w["on_ref"] == "team")
    assert team["cleared_at"] is None
    # the person's wait is a task on their list, saying what is asked
    assert tasks == [
        {
            "origin": "wait",
            "ref": "pr#3",
            "effect": "approve the change (pr#3), so it can move",
            "stage": "open",
        }
    ]
    assert any(b["on_kind"] == "human" for b in con["bottlenecks"])


def test_a_wait_missing_from_a_later_export_stays_open(exchange_1_1):
    """Only wait.cleared clears a wait: an export that no longer lists it says nothing about it."""
    first = _snap(exchange_1_1, 0)
    reader.import_snapshots(exchange_1_1, _ledger(exchange_1_1), only=first.name)

    def drop_waits(lines):
        lines[:] = [x for x in lines if not x["type"].startswith("onloop.wait.")]

    last = _snap(exchange_1_1)
    _rewrite(last, drop_waits)
    m = json.loads((last / "manifest.json").read_text(encoding="utf-8"))
    counts = m["files"]["events.jsonl"]["counts"]
    for t in ("onloop.wait.opened", "onloop.wait.cleared"):
        counts.pop(t, None)
    (last / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    assert reader.import_snapshots(exchange_1_1, _ledger(exchange_1_1))[-1].status == "imported"
    with Ledger(_ledger(exchange_1_1), readonly=True) as led:
        open_ = led.select("SELECT on_ref FROM waits WHERE cleared_at IS NULL ORDER BY on_ref")
    assert [w["on_ref"] for w in open_] == ["builder", "human", "team"]


def test_a_1_1_type_in_a_1_0_snapshot_is_refused(exchange_1_1):
    folder = _snap(exchange_1_1)
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    m["contract"] = "1.0"
    (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    result = reader.import_snapshots(exchange_1_1, _ledger(exchange_1_1))[-1]
    assert result.status == "refused"
    assert any("a contract 1.1 type in a 1.0 snapshot" in p["rule"] for p in result.problems)


def test_a_login_shaped_reviewer_is_refused_and_never_printed(exchange_1_1):
    """A login fits the role-name pattern; only the declared roles tell them apart."""

    def login(lines):
        r = next(x for x in lines if x["type"] == "onloop.pr.reviewed")
        r["data"]["reviewer"] = "octocat"

    _rewrite(_snap(exchange_1_1), login)
    result = reader.import_snapshots(exchange_1_1, _ledger(exchange_1_1))[-1]
    assert result.status == "refused"
    assert {p["path"] for p in result.problems} == {"/data/reviewer"}
    assert "octocat" not in (exchange_1_1 / "onloop" / reader.REPORT).read_text(encoding="utf-8")


def test_a_body_shaped_verdict_is_refused_and_never_printed(exchange_1_1):
    body = "Looks good to me, but please split the migration first."

    def chatty(lines):
        r = next(x for x in lines if x["type"] == "onloop.pr.reviewed")
        r["data"]["verdict"] = body

    _rewrite(_snap(exchange_1_1), chatty)
    result = reader.import_snapshots(exchange_1_1, _ledger(exchange_1_1))[-1]
    assert result.status == "refused" and result.problems[0]["path"] == "/data/verdict"
    assert "split the migration" not in (exchange_1_1 / "onloop" / reader.REPORT).read_text(encoding="utf-8")


def test_an_imported_verdict_never_resolves_the_persons_approval_at_the_merge_gate(exchange_1_1):
    reader.import_snapshots(exchange_1_1, _ledger(exchange_1_1))
    with Ledger(_ledger(exchange_1_1), readonly=True) as led:
        gate_done = led.select(
            "SELECT count(*) AS n FROM human_tasks WHERE origin = 'gate' AND resolution = 'done'"
        )
    assert gate_done[0]["n"] == 0
