"""`onloop gate` records each decision in the overlay ledger (source `gate`), apart from deciding it: no ledger, or an
append that fails, leaves the decision and its exit code as they were, with one warning line. It never creates a
ledger."""

import pytest

from onloop import cli, gate
from onloop.overlay.ledger import Ledger

T0 = "2026-11-09T09:00:00.000Z"


class _Client:
    repo = "o/r"

    def __init__(self, *_a, **_k):
        pass


@pytest.fixture
def fake_gate(monkeypatch):
    """`gate.run` replaced by one that refuses (exit 1) and records it, so the command's own wiring is what is tested."""
    seen = {}

    def run(number, client, git, *, dry_run=False, record=None, **_kw):
        seen["record"] = record
        if record and not dry_run:
            record("gate.refused", number, {"head": "h" * 40, "codes": ["G021"], "refusals": []})
        return 1, ["no reviewer approval stands on this head"]

    monkeypatch.setattr(cli, "GitHub", _Client)
    monkeypatch.setattr(cli, "Git", lambda *a, **k: object())
    monkeypatch.setattr(gate, "run", run)
    return seen


def test_with_a_ledger_the_decision_is_recorded_as_the_gates_own(tmp_path, monkeypatch, capsys, fake_gate):
    path = tmp_path / "ledger.db"
    Ledger(path, clock=lambda: T0).close()
    monkeypatch.setenv("ONLOOP_LEDGER", str(path))
    assert cli.main(["gate", "7", "--repo", "o/r"]) == 1
    err = capsys.readouterr().err
    assert "refused: no reviewer approval" in err and "not recorded" not in err
    with Ledger(path, readonly=True) as led:
        rows = led.select("SELECT source, kind, subject_ref, actor, payload FROM events")
    assert [(r["source"], r["kind"], r["subject_ref"], r["actor"]) for r in rows] == [
        ("gate", "gate.refused", "pr#7", "onloop")
    ]
    assert rows[0]["payload"]["codes"] == ["G021"]


def test_without_a_ledger_the_gate_decides_as_before_and_says_it_did_not_record(
    tmp_path, monkeypatch, capsys, fake_gate
):
    path = tmp_path / "none" / "ledger.db"
    monkeypatch.setenv("ONLOOP_LEDGER", str(path))
    assert cli.main(["gate", "7", "--repo", "o/r"]) == 1  # the same exit code
    err = capsys.readouterr().err.strip().splitlines()
    assert err[-1].startswith("onloop: warning: the gate's decision was not recorded (no ledger at")
    assert fake_gate["record"] is None
    assert not path.exists() and not path.parent.exists()  # it never creates a ledger


def test_an_append_that_fails_leaves_the_decision_and_warns(tmp_path, monkeypatch, capsys, fake_gate):
    path = tmp_path / "ledger.db"
    Ledger(path, clock=lambda: T0).close()
    monkeypatch.setenv("ONLOOP_LEDGER", str(path))

    def broken(self, event):
        raise OSError("disk full")

    monkeypatch.setattr(Ledger, "append", broken)
    assert cli.main(["gate", "7", "--repo", "o/r"]) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert err[-1].startswith("onloop: warning: the gate's decision was not recorded (gate.refused for pr#7")
    assert "disk full" in err[-1] and len([x for x in err if "warning" in x]) == 1


def test_a_dry_run_records_nothing_and_warns_about_nothing(tmp_path, monkeypatch, capsys, fake_gate):
    monkeypatch.setenv("ONLOOP_LEDGER", str(tmp_path / "missing.db"))
    assert cli.main(["gate", "7", "--repo", "o/r", "--dry-run"]) == 1
    assert "not recorded" not in capsys.readouterr().err and fake_gate["record"] is None
    assert not (tmp_path / "missing.db").exists()


def test_the_recorder_writes_every_decision_kind_the_kpis_count(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: T0) as led:
        failed: list[str] = []
        record = cli._recorder(led, "o/r", failed)
        for kind in ("gate.merged", "gate.refused", "gate.postmerge_checked", "gate.reverted"):
            record(kind, 3, {"head": "h"})
        record("gate.merged", 3, {"head": "h"})  # the same decision at the same time is logged once
        kinds = [r["kind"] for r in led.select("SELECT kind FROM events WHERE source = 'gate' ORDER BY seq")]
    assert (
        kinds == ["gate.merged", "gate.refused", "gate.postmerge_checked", "gate.reverted"] and failed == []
    )
