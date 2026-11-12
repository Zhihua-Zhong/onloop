"""E3: KPI counters from the ledger. Fixed weeks give fixed counts; a baseline of the wrong shape is refused."""

import copy
import json
import pathlib

import pytest

from onloop import cli
from onloop.overlay.ledger import Ledger
from onloop.report import weekly

SCHEMA = pathlib.Path(__file__).resolve().parent.parent / "docs" / "schemas" / "kpi-baseline.schema.json"
EXAMPLE = SCHEMA.with_name("kpi-baseline.example.json")


def ev(eid, kind, subject, p):
    return {"id": eid, "source": "ui", "kind": kind, "subject_ref": subject, "payload": p}


@pytest.fixture
def path(tmp_path):
    clock = {"now": "2026-11-16T09:00:00.000Z"}  # Monday of 2026-W47
    led = Ledger(tmp_path / "ledger.db", clock=lambda: clock["now"])

    def wait(wid, since_hhmm_day, reason="permission"):
        led.append(
            ev(
                wid,
                "wait.opened",
                "human",
                {
                    "id": wid,
                    "waiter": "builder",
                    "on_kind": "human",
                    "on_ref": "human",
                    "reason": reason,
                    "since": since_hhmm_day,
                },
            )
        )

    wait("a1", "2026-11-16T09:00:00.000Z")
    clock["now"] = "2026-11-16T10:00:00.000Z"
    led.append(ev("c1", "wait.cleared", "human", {"id": "a1"}))  # waited 1 h
    wait("a2", "2026-11-18T12:00:00.000Z", reason="paused_by_human")
    clock["now"] = "2026-11-18T12:30:00.000Z"
    led.append(ev("c2", "wait.cleared", "human", {"id": "a2"}))  # 30 min
    wait("a3", "2026-11-23T08:00:00.000Z")  # W48, still open
    led.append(
        ev(
            "x1",
            "wait.opened",
            "lock:db",
            {
                "id": "x1",
                "waiter": "builder",
                "on_kind": "resource",
                "on_ref": "lock:db",
                "reason": "running",
                "since": "2026-11-16T09:00:00.000Z",
            },
        )
    )  # not an ask
    for n, origin in enumerate(["timer", "detector", "relay", "human", "ask", None]):
        clock["now"] = f"2026-11-17T0{n}:00:00.000Z"
        led.append(
            ev(f"d{n}", "delivery.created", "lead", {"id": f"d{n}", "to_role": "lead", "origin": origin})
        )
    led.append(ev("d9", "delivery.created", "builder", {"id": "d9", "to_role": "builder", "origin": "human"}))
    led.close()
    return tmp_path / "ledger.db"


def test_fixed_weeks_give_fixed_counts(path):
    with Ledger(path, readonly=True) as led:
        r = weekly.build(led, "lead", "2026-11-23T10:00:00.000Z")
    w47, w48 = r["weeks"]["2026-W47"], r["weeks"]["2026-W48"]
    assert w47["asks_to_human"] == {"count": 2, "open": 0, "wait_s_total": 5400, "wait_s_max": 3600}
    assert [a["reason"] for a in w47["asks"]] == ["permission", "paused_by_human"]
    assert w48["asks_to_human"] == {"count": 1, "open": 1, "wait_s_total": 7200, "wait_s_max": 7200}
    assert w47["coordinator_messages"] == {"mechanical": 3, "judgment": 2, "unclassified": 1}
    assert w48["coordinator_messages"] == {"mechanical": 0, "judgment": 0, "unclassified": 0}
    assert (
        weekly.iso_week("2026-11-16T00:00:00.000Z") == "2026-W47"
    )  # the first Monday of the customer's aggregates


def test_the_baseline_imports_once_and_shows_beside_the_measured_weeks(path, capsys):
    assert cli.main(["kpi", "baseline", "import", str(EXAMPLE), "--ledger", str(path)]) == 0
    assert "2 new or changed" in capsys.readouterr().out
    assert cli.main(["kpi", "baseline", "import", str(EXAMPLE), "--ledger", str(path)]) == 0
    assert "0 new or changed" in capsys.readouterr().out
    assert cli.main(["kpi", "weekly", "--json", "--ledger", str(path)]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["weeks"]["2026-W47"]["baseline"]["coordinator_messages"] == {"mechanical": 41, "judgment": 9}
    assert r["weeks"]["2026-W48"]["baseline"]["asks_to_human"] == {"count": 4, "wait_s_total": 5400}
    changed = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    changed["weeks"] = changed["weeks"][:1]
    changed["weeks"][0]["coordinator_messages"]["judgment"] = 10
    fixed = path.parent / "fixed.json"
    fixed.write_text(json.dumps(changed), encoding="utf-8")
    assert cli.main(["kpi", "baseline", "import", str(fixed), "--ledger", str(path)]) == 0
    capsys.readouterr()
    assert cli.main(["kpi", "weekly", "--ledger", str(path)]) == 0
    out = capsys.readouterr().out
    assert "msgs 41/10, asks 6 (15300 s)" in out  # the latest import of a week wins


GOOD = json.loads(EXAMPLE.read_text(encoding="utf-8"))


def bad(**change):
    obj = copy.deepcopy(GOOD)
    for dotted, value in change.items():
        keys = dotted.split("__")
        target = obj
        for k in keys[:-1]:
            target = target[int(k)] if k.isdigit() else target[k]
        if value is KeyError:
            del target[keys[-1]]
        else:
            target[keys[-1]] = value
    return obj


@pytest.mark.parametrize(
    ("obj", "problem"),
    [
        ([], "must be a JSON object"),
        (bad(schema="onloop.kpi.baseline/2"), "schema must be"),
        (bad(split_method="by feel"), "split_method must be one of"),
        (bad(weeks=[]), "weeks must be a non-empty list"),
        (bad(weeks__0__week="2026-47"), "must be an ISO week"),
        (bad(weeks__0__week="2025-W53"), "is not a week of that year"),
        (bad(weeks__1__week="2026-W47"), "is given twice"),
        (bad(weeks__0__coordinator_messages__judgment=-1), "must be a whole number"),
        (bad(weeks__0__coordinator_messages__judgment=2.5), "must be a whole number"),
        (bad(weeks__0__coordinator_messages__judgment=True), "must be a whole number"),
        (bad(weeks__0__asks_to_human__count=KeyError), "asks_to_human.count must be a whole number"),
        (bad(weeks__0__messages=[]), "unknown key 'messages'"),
        (bad(transcripts=["..."]), "unknown key 'transcripts'"),
    ],
)
def test_a_baseline_of_the_wrong_shape_is_refused(obj, problem):
    assert any(problem in p for p in weekly.validate_baseline(obj)), weekly.validate_baseline(obj)


def test_the_example_is_valid_and_the_schema_says_what_the_validator_checks():
    assert weekly.validate_baseline(GOOD) == []
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["properties"]["schema"]["const"] == weekly.BASELINE_SCHEMA
    assert schema["properties"]["split_method"]["enum"] == list(weekly.SPLIT_METHODS)
    week = schema["properties"]["weeks"]["items"]
    assert week["properties"]["week"]["pattern"] == weekly._WEEK.pattern
    for block, keys in weekly._COUNT_KEYS.items():
        assert week["properties"][block]["required"] == list(keys)
        assert set(week["properties"][block]["properties"]) == {*keys, *weekly._OPTIONAL.get(block, ())}
    assert schema["additionalProperties"] is False and week["additionalProperties"] is False


def test_a_file_that_is_not_utf8_json_is_refused(tmp_path, path, capsys):
    f = tmp_path / "b.json"
    f.write_bytes(b"\xff{}")
    assert cli.main(["kpi", "baseline", "import", str(f), "--ledger", str(path)]) == 2
    assert "not UTF-8 JSON" in capsys.readouterr().err
