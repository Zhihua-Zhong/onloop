"""Contract 1.2: pr.opened says which role opened the change (`by`), and the map holds a change by that role. All data
here is made up."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from onloop import derive
from onloop.contract import CONTRACT, reader
from onloop.overlay.ledger import Ledger

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("contract_example_12", ROOT / "tools" / "contract_example.py")
example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(example)
END = dt.datetime(2026, 11, 17, 2, 0, 0, tzinfo=dt.UTC)
NOW = "2026-11-17T03:00:00.000Z"


def _exchange(tmp_path, contract="1.2"):
    root = tmp_path / "exchange"
    example.write(root, END, days=2, n=120, seed=5, contract=contract)
    (root / "onloop").mkdir()
    return root


def _snap(root, i=-1):
    return sorted((root / "snapshots").iterdir())[i]


def _rewrite(folder, edit):
    path = folder / "events.jsonl"
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    edit(lines)
    body = ("\n".join(json.dumps(x) for x in lines) + "\n").encode("utf-8")
    path.write_bytes(body)
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    m["files"]["events.jsonl"]["sha256"] = hashlib.sha256(body).hexdigest()
    (folder / "manifest.json").write_text(json.dumps(m), encoding="utf-8")


def _roles(path):
    with Ledger(path, readonly=True) as led:
        return {
            r["id"]: r["owner_role"]
            for r in led.select("SELECT id, owner_role FROM items WHERE id LIKE 'pr#%'")
        }


def test_the_reader_knows_1_2():
    assert CONTRACT == "1.2"


def test_a_1_2_export_gives_each_change_the_role_that_opened_it(tmp_path):
    root = _exchange(tmp_path)
    results = reader.import_snapshots(root, root / "onloop" / "ledger.db")
    assert [r.status for r in results] == ["imported", "imported"], [r.as_dict() for r in results]
    roles = _roles(root / "onloop" / "ledger.db")
    assert roles["pr#7"] == "builder" and roles["pr#8"] == "frontend-builder" and roles["pr#6"] == "team"


def test_a_change_first_seen_without_by_gets_its_role_from_a_later_copy_with_the_same_id(tmp_path):
    """The first export is 1.1, without `by`; the second repeats pr.opened for PR 5 with the same envelope id, now
    with `by`. The opening is a duplicate, but its role is not lost."""
    root = _exchange(tmp_path)
    first, second = sorted((root / "snapshots").iterdir())
    one = [json.loads(x) for x in (first / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    two = [json.loads(x) for x in (second / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    a = next(r for r in one if r["type"] == "onloop.pr.opened" and r["data"]["number"] == 5)
    b = next(r for r in two if r["type"] == "onloop.pr.opened" and r["data"]["number"] == 5)
    assert a["id"] == b["id"] and "by" not in a["data"] and b["data"]["by"] == "frontend-builder"
    reader.import_snapshots(root, root / "onloop" / "ledger.db", only=first.name)
    assert _roles(root / "onloop" / "ledger.db")["pr#5"] is None
    reader.import_snapshots(root, root / "onloop" / "ledger.db")
    assert _roles(root / "onloop" / "ledger.db")["pr#5"] == "frontend-builder"
    with Ledger(root / "onloop" / "ledger.db") as led:
        same, *_ = led.rebuild_matches()
    assert same


def test_a_by_that_is_not_a_declared_role_refuses_the_file_and_is_never_printed(tmp_path):
    root = _exchange(tmp_path)

    def login(lines):
        r = next(x for x in lines if x["type"] == "onloop.pr.opened" and "by" in x["data"])
        r["data"]["by"] = "someone-elses-account"

    _rewrite(_snap(root), login)
    results = reader.import_snapshots(root, root / "onloop" / "ledger.db")
    refused = results[-1]
    assert refused.status == "refused"
    assert any(p["path"] == "/data/by" and "not a declared role" in p["rule"] for p in refused.problems)
    assert "someone-elses-account" not in json.dumps(refused.as_dict())


@pytest.mark.parametrize("who", ["team", "human"])
def test_team_and_human_are_allowed_as_by(tmp_path, who):
    root = _exchange(tmp_path)

    def set_by(lines):
        for x in lines:
            if x["type"] == "onloop.pr.opened" and "by" in x["data"]:
                x["data"]["by"] = who

    _rewrite(_snap(root), set_by)
    assert [r.status for r in reader.import_snapshots(root, root / "onloop" / "ledger.db")] == [
        "imported"
    ] * 2


@pytest.mark.parametrize("schema_minor", ["1.0", "1.2"])
def test_a_1_1_snapshot_with_by_is_refused(tmp_path, schema_minor):
    root = _exchange(tmp_path, contract="1.1")

    def add_by(lines):
        r = next(x for x in lines if x["type"] == "onloop.pr.opened")
        r["data"]["by"] = "builder"
        r["dataschema"] = f"onloop-contract/{schema_minor}/pr.opened"

    _rewrite(_snap(root), add_by)
    refused = reader.import_snapshots(root, root / "onloop" / "ledger.db")[-1]
    assert refused.status == "refused" and refused.problems


def test_a_1_1_snapshot_with_by_says_why(tmp_path):
    root = _exchange(tmp_path, contract="1.1")

    def add_by(lines):
        r = next(x for x in lines if x["type"] == "onloop.pr.opened")
        r["data"]["by"] = "builder"
        r["dataschema"] = "onloop-contract/1.1/pr.opened"

    _rewrite(_snap(root), add_by)
    refused = reader.import_snapshots(root, root / "onloop" / "ledger.db")[-1]
    assert any(p["rule"] == "a contract 1.2 field in a 1.1 snapshot" for p in refused.problems), (
        refused.problems
    )


def test_by_on_a_merged_or_closed_change_is_refused(tmp_path):
    root = _exchange(tmp_path)

    def on_merged(lines):
        r = next(x for x in lines if x["type"] == "onloop.pr.merged")
        r["data"] = {**r["data"], "by": "builder"}

    _rewrite(_snap(root), on_merged)
    assert reader.import_snapshots(root, root / "onloop" / "ledger.db")[-1].status == "refused"


def _open_change(tmp_path, by):
    """An open change, no wait on it, no move for 5 days: red at zoom 0, held by whoever opened it."""
    path = tmp_path / "l.db"
    with Ledger(path, clock=lambda: NOW) as led:
        led.append(
            {
                "id": "o9",
                "source": "import",
                "kind": "pr.opened",
                "subject_ref": "pr#9",
                "at": "2026-11-12T03:00:00.000Z",
                "payload": {"number": 9, "title": "Example change 9"},
            }
        )
        if by is not None:
            led.append(
                {
                    "id": "a9",
                    "source": "import",
                    "kind": "pr.author",
                    "subject_ref": "pr#9",
                    "at": "2026-11-12T03:00:00.000Z",
                    "payload": {"number": 9, "by": by},
                }
            )
    return path


@pytest.mark.parametrize(
    ("by", "holder"),
    [("docs-writer", "docs-writer"), (None, "the team"), ("team", "the team"), ("human", "you (opened it)")],
)
def test_a_change_that_waits_on_nobody_is_held_by_the_role_that_opened_it(tmp_path, by, holder):
    path = _open_change(tmp_path, by)
    with Ledger(path, readonly=True) as led:
        z0 = derive.zoom0(led, NOW)
        z1 = derive.zoom1(led, NOW, "changes")
        z2 = derive.zoom2(led, NOW, "changes", "opened")
    changes = next(b for b in z0["blocks"] if b["kind"] == "changes")
    assert changes["colour"] == "red"
    assert changes["reason"]["text"] == f"{holder}: no move on pr#9 for 5 days"  # names the role, then why
    opened = next(s for s in z1["stages"] if s["stage"] == "opened")
    assert opened["reason"]["text"].startswith(f"{holder}: ")
    assert [h["holder"] for h in z2["holders"]] == [holder]


def test_the_map_never_invents_a_role(tmp_path):
    """No `by`, no role: the holder is the team, whatever roles are declared."""
    path = _open_change(tmp_path, None)
    with Ledger(path, clock=lambda: NOW) as led:
        led.append(
            {
                "id": "r1",
                "source": "import",
                "kind": "role.declared",
                "subject_ref": "role:builder",
                "payload": {"id": "builder"},
            }
        )
        z2 = derive.zoom2(led, NOW, "changes", "opened")
    assert [h["holder"] for h in z2["holders"]] == ["the team"]
