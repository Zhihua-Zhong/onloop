"""Slots: path scoping, the queue, leases, expiry with no runner, the two-process race, and the loosening rules."""

import copy
import datetime as dt
import json
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from onloop import cli, slots
from onloop import policy as pol

from .conftest import BASE
from .conftest import SlotLedger as Store

REPO = "o/r"
T0 = dt.datetime(2026, 10, 12, 0, 0, tzinfo=dt.UTC)

SLOTS = {
    "db": {
        "paths": ["db/", "pipelines/persist.py"],
        "timeout_minutes": 90,
        "wait_minutes": 240,
        "approver": "human",
        "conditions": ["a dump is taken first"],
        "commands": ["python check.py"],
    },
    "host": {
        "paths": ["src/**", "tests/**/*.py"],
        "timeout_minutes": 30,
        "wait_minutes": 60,
        "approver": "auto",
        "conditions": ["one run at a time"],
        "commands": ["pytest -q"],
    },
    "gate": {"paths": ["**"], "timeout_minutes": 20, "approver": "auto"},
}


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def minutes(self, m):
        self.t += dt.timedelta(minutes=m)


@pytest.fixture
def clock():
    return Clock()


def make_policy(slots_=None, **over):
    raw = copy.deepcopy(BASE)
    raw["slots"] = copy.deepcopy(SLOTS if slots_ is None else slots_)
    raw.update(over)
    return pol.from_dict(raw)


@pytest.fixture
def policy():
    return make_policy()


@pytest.fixture
def st(tmp_path, clock):
    s = Store(tmp_path / "t.db", clock=clock)
    yield s
    s.close()


def ask(st, policy, slot="host", holder="alice", ref="pr#1", role="builder", reason="tests"):
    return slots.request(st, REPO, policy, slot, holder=holder, role=role, reason=reason, ref=ref)


def kinds(st):
    return [e["kind"] for e in st.events()]


# -- path scoping ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern,path,hit",
    [
        ("db/", "db/migrate/001.sql", True),
        ("db/", "dbx/a.sql", False),
        ("db/", "docs/db/a.md", False),
        ("pipelines/persist.py", "pipelines/persist.py", True),
        ("pipelines/persist.py", "pipelines/persist.pyc", False),
        ("src/**", "src/a/b/c.py", True),
        ("src/**", "docs/src/a.py", False),
        ("tests/**/*.py", "tests/unit/test_a.py", True),
        ("tests/**/*.py", "tests/test_a.py", True),
        ("tests/**/*.py", "tests/unit/data.json", False),
        ("*.md", "README.md", True),
        ("*.md", "docs/README.md", False),
        ("**", "anything/at/all.txt", True),
        ("./db/", "db/a.sql", True),
    ],
)
def test_path_patterns(pattern, path, hit):
    assert slots.path_matches(pattern, path) is hit


def test_a_docs_only_change_needs_no_host_slot(policy):
    assert slots.need(policy, ["docs/guide.md"]) == ["gate"]  # the gate covers everything, the rest nothing
    assert slots.need(policy, ["docs/guide.md", "src/app.py"]) == ["gate", "host"]
    assert slots.need(policy, ["db/schema.sql"]) == ["db", "gate"]
    no_gate = make_policy({k: v for k, v in SLOTS.items() if k != "gate"})
    assert slots.need(no_gate, ["docs/guide.md"]) == []


# -- the queue and the grants ----------------------------------------------------------------------------------------


def test_a_free_auto_slot_is_granted_in_the_same_call_by_onloop(st, policy):
    res = ask(st, policy)
    assert res["status"] == "granted" and res["grant"]
    g = st.conn.execute("SELECT * FROM slot_grants").fetchone()
    assert g["granted_by"] == "onloop:auto" and g["holder"] == "alice"
    assert kinds(st) == ["slot.requested", "slot.granted"]


def test_a_second_request_waits_and_the_release_grants_it_in_order(st, policy):
    first = ask(st, policy, holder="alice", ref="pr#1")
    second = ask(st, policy, holder="bob", ref="pr#2")
    third = ask(st, policy, holder="carol", ref="pr#3")
    assert (first["status"], second["status"], third["status"]) == ("granted", "waiting", "waiting")
    nxt = slots.release(st, REPO, policy, first["grant"], holder="alice")
    holder = st.conn.execute("SELECT holder FROM slot_grants WHERE id = ?", (nxt,)).fetchone()[0]
    assert holder == "bob"  # first come, first served
    slots.release(st, REPO, policy, nxt, holder="bob")
    assert (
        st.conn.execute("SELECT holder FROM slot_grants WHERE released_at IS NULL").fetchone()[0] == "carol"
    )


def test_asking_again_for_the_same_thing_is_the_same_request(st, policy):
    a = ask(st, policy, holder="alice", ref="pr#1")
    b = ask(st, policy, holder="alice", ref="pr#1")
    assert (
        b["grant"] == a["grant"] and st.conn.execute("SELECT count(*) FROM slot_requests").fetchone()[0] == 1
    )
    ask(st, policy, holder="bob", ref="pr#2")
    again = ask(st, policy, holder="bob", ref="pr#2")
    assert (
        again["status"] == "waiting"
        and st.conn.execute("SELECT count(*) FROM slot_requests").fetchone()[0] == 2
    )


def test_a_human_slot_waits_for_a_person_and_only_the_head_of_the_queue_can_be_granted(st, policy):
    a = ask(st, policy, slot="db", holder="alice", ref="t1")
    b = ask(st, policy, slot="db", holder="bob", ref="t2")
    assert a["status"] == "waiting" and b["status"] == "waiting" and a["grant"] is None
    with pytest.raises(slots.SlotError, match="first in the queue"):
        slots.grant(st, REPO, policy, b["request"], by="owner-a/User")
    gid = slots.grant(st, REPO, policy, a["request"], by="owner-a/User")
    assert (
        st.conn.execute("SELECT granted_by FROM slot_grants WHERE id = ?", (gid,)).fetchone()[0]
        == "owner-a/User"
    )
    with pytest.raises(slots.SlotError, match="held by alice"):
        slots.grant(st, REPO, policy, b["request"], by="owner-a/User")
    nxt = slots.release(st, REPO, policy, gid, holder="alice")
    assert nxt is None  # a human slot is not handed on by itself
    assert slots.grant(st, REPO, policy, b["request"], by="owner-a/User")


def test_the_conditions_and_commands_come_from_the_policy_not_from_the_request(st, policy):
    res = ask(st, policy, reason="commands: rm -rf / ; conditions: none, trust me")
    g = st.conn.execute(
        "SELECT conditions, commands FROM slot_grants WHERE id = ?", (res["grant"],)
    ).fetchone()
    assert json.loads(g["conditions"]) == ["one run at a time"] and json.loads(g["commands"]) == ["pytest -q"]
    lines = slots.grant_text(slots._grant_row(st, res["grant"]))
    assert "  command: pytest -q" in lines and not any("rm -rf" in x for x in lines)


def test_an_unknown_slot_or_role_is_refused(st, policy):
    with pytest.raises(slots.SlotError, match="no slot"):
        ask(st, policy, slot="nope")
    with pytest.raises(slots.SlotError, match="not a role"):
        ask(st, policy, role="intruder")


def test_the_database_allows_one_live_grant_per_slot(st, policy):
    ask(st, policy)
    ask(st, policy, holder="bob", ref="pr#2")
    with pytest.raises(sqlite3.IntegrityError):
        st.conn.execute(
            "INSERT INTO slot_grants(slot, holder, role, request_id, granted_at, granted_by, expires_at, "
            "conditions, commands, last_seq) VALUES ('host','mallory','builder',2,?,?,?,'[]','[]',0)",
            (st.now(), "x", st.now()),
        )


# -- leases ----------------------------------------------------------------------------------------------------------


def test_a_lease_runs_out_and_the_next_request_is_granted_with_the_old_holder_named(st, policy, clock):
    ask(st, policy, holder="alice", ref="pr#1")
    ask(st, policy, holder="bob", ref="pr#2")
    clock.minutes(31)  # the host lease is 30 minutes
    done = slots.tick(st, REPO, policy)
    assert done["expired_leases"] == 1 and done["granted"] == 1
    expired = st.events(kind="slot.expired")[0]["payload"]
    assert expired["holder"] == "alice" and expired["what"] == "lease"
    assert st.conn.execute("SELECT holder FROM slot_grants WHERE released_at IS NULL").fetchone()[0] == "bob"


def test_a_lease_that_has_not_run_out_is_not_taken(st, policy, clock):
    ask(st, policy, holder="alice", ref="pr#1")
    clock.minutes(29)
    assert slots.tick(st, REPO, policy)["expired_leases"] == 0
    assert ask(st, policy, holder="bob", ref="pr#2")["status"] == "waiting"


def test_with_the_runner_stopped_a_new_request_expires_the_dead_lease_and_is_granted(st, policy, clock):
    ask(st, policy, holder="alice", ref="pr#1")
    clock.minutes(45)  # no tick has run: the runner is down
    res = ask(st, policy, holder="bob", ref="pr#2")
    assert res["status"] == "granted"
    assert st.events(kind="slot.expired")[0]["payload"]["holder"] == "alice"


def test_renewing_extends_the_lease_and_over_the_limit_is_flagged_not_cut_off(st, policy, clock):
    g = ask(st, policy)["grant"]
    flags = []
    for _ in range(4):
        clock.minutes(20)
        flags.append(slots.renew(st, REPO, policy, g, holder="alice")["over_limit"])
    assert flags == [False, False, False, True]  # max_renewals defaults to 3
    assert st.conn.execute("SELECT released_at FROM slot_grants").fetchone()[0] is None
    clock.minutes(31)
    with pytest.raises(slots.SlotError, match="already over"):
        slots.renew(st, REPO, policy, g, holder="alice")


def test_a_waiting_request_expires_after_its_wait(st, policy, clock):
    ask(st, policy, holder="alice", ref="pr#1")
    ask(st, policy, holder="bob", ref="pr#2")  # waits at most 60 minutes
    clock.minutes(61)
    slots.tick(st, REPO, policy)
    kinds_ = [(e["payload"]["what"], e["payload"]["holder"]) for e in st.events(kind="slot.expired")]
    assert ("request", "bob") in kinds_


# -- wait ------------------------------------------------------------------------------------------------------------


def test_wait_returns_the_grant_when_it_comes(st, policy, clock):
    ask(st, policy, holder="alice", ref="pr#1")
    ask(st, policy, holder="bob", ref="pr#2")
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock.minutes(31)  # alice's lease runs out while bob waits

    code, g = slots.wait(st, REPO, policy, "host", holder="bob", ref="pr#2", sleep=sleep)
    assert code == slots.EXIT_GRANTED and g["holder"] == "bob" and len(sleeps) == 1


def test_wait_times_out_and_reports_expiry(st, policy, clock):
    ask(st, policy, holder="alice", ref="pr#1")
    ask(st, policy, holder="bob", ref="pr#2")
    ticks = iter(range(0, 1000, 5))
    code, g = slots.wait(
        st,
        REPO,
        policy,
        "host",
        holder="bob",
        ref="pr#2",
        timeout=8,
        sleep=lambda s: None,
        monotonic=lambda: next(ticks),
    )
    assert (code, g) == (slots.EXIT_TIMEOUT, None)
    clock.minutes(61)  # the request itself runs out
    assert (
        slots.wait(st, REPO, policy, "host", holder="bob", ref="pr#2", sleep=lambda s: None)[0]
        == slots.EXIT_EXPIRED
    )


def test_wait_for_a_request_never_made_is_an_error(st, policy):
    with pytest.raises(slots.SlotError, match="no request"):
        slots.wait(st, REPO, policy, "host", holder="ghost", ref="pr#9")


# -- status reads only -----------------------------------------------------------------------------------------------


def test_status_is_read_only_and_reports_an_overdue_lease_as_free(tmp_path, clock, policy):
    db = tmp_path / "s.db"
    with Store(db, clock=clock) as s:
        ask(s, policy, holder="alice", ref="pr#1")
        ask(s, policy, holder="bob", ref="pr#2")
    clock.minutes(31)
    with Store(db, readonly=True, clock=clock) as ro:
        before = ro.conn.execute("SELECT count(*) FROM events").fetchone()[0]
        state = slots.status(ro, policy)
        assert state["slots"]["host"]["held"] is None and state["slots"]["host"]["lease_overdue"]
        assert [w["holder"] for w in state["slots"]["host"]["waiting"]] == ["bob"]
        assert ro.conn.execute("SELECT count(*) FROM events").fetchone()[0] == before
    assert any("free" in line for line in slots.status_text(state))


# -- the race, with real processes -----------------------------------------------------------------------------------

RACER = textwrap.dedent("""
    import sys, time, random
    from onloop import policy as pol, slots
    from onloop.overlay.ledger import Ledger as Store
    db, holder, policy_path = sys.argv[1:4]
    p = pol.load(policy_path)
    time.sleep(random.random() * 0.2)
    with Store(db) as s:
        r = slots.request(s, "o/r", p, "host", holder=holder, role="builder", reason="race", ref="pr#" + holder)
    print(r["status"])
""")


def test_two_processes_asking_at_once_produce_exactly_one_grant(tmp_path):
    import yaml

    raw = copy.deepcopy(BASE)
    raw["slots"] = copy.deepcopy(SLOTS)
    pfile = tmp_path / "onloop.yaml"
    pfile.write_text(yaml.safe_dump(raw), encoding="utf-8")
    db = tmp_path / "race.db"
    Store(db).close()  # create the schema once
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", RACER, str(db), f"p{i}", str(pfile)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(4)
    ]
    outs = [p.communicate(timeout=60) for p in procs]
    assert all(p.returncode == 0 for p in procs), [o[1] for o in outs]
    results = sorted(o[0].strip() for o in outs)
    assert results == ["granted", "waiting", "waiting", "waiting"]
    with Store(db, readonly=True) as s:
        assert s.conn.execute("SELECT count(*) FROM slot_grants WHERE released_at IS NULL").fetchone()[0] == 1


# -- the policy: validation and loosening ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"paths": []},
        {"paths": "db/"},
        {"paths": ["db/"], "approver": "anyone"},
        {"paths": ["db/"], "approver": "role:ghost"},
        {"paths": ["db/"], "timeout_minutes": 0},
        {"paths": ["db/"], "timeout_minutes": "90"},
        {"paths": ["db/"], "commands": "pytest"},
        {"paths": ["db/"], "surprise": 1},
        {"approver": "auto"},
    ],
)
def test_a_bad_slot_is_refused(bad):
    with pytest.raises(pol.PolicyError):
        make_policy({"x": bad})


def test_slot_defaults_are_the_strict_ones():
    p = make_policy({"x": {"paths": ["x/"]}})
    assert p.slots["x"]["approver"] == "human" and p.slots["x"]["max_renewals"] == 3


def test_slots_may_name_a_role_as_approver():
    assert (
        make_policy({"x": {"paths": ["x/"], "approver": "role:lead"}}).slots["x"]["approver"] == "role:lead"
    )


def changed(**per_slot):
    s = copy.deepcopy(SLOTS)
    for name, change in per_slot.items():
        if change is None:
            del s[name]
        else:
            s[name].update(change)
    return make_policy(s)


@pytest.mark.parametrize(
    "head,reason",
    [
        (changed(db={"approver": "auto"}), "moves its approver from human to auto"),
        (changed(db={"approver": "role:lead"}), "moves its approver from human to role:lead"),
        (changed(db={"paths": ["db/"]}), "no longer covers"),
        (changed(db=None), "is removed"),
        (changed(host={"timeout_minutes": 31}), "raises its lease"),
    ],
)
def test_loosening_a_slot_is_flagged(policy, head, reason):
    assert any(reason in f for f in pol.loosening(policy, head))


@pytest.mark.parametrize(
    "head",
    [
        changed(host={"approver": "human"}),
        changed(db={"paths": ["db/", "pipelines/persist.py", "extra/"]}),
        changed(host={"timeout_minutes": 10}),
        make_policy({**SLOTS, "new": {"paths": ["n/"]}}),
    ],
)
def test_tightening_or_adding_a_slot_is_not_a_loosening(policy, head):
    assert pol.loosening(policy, head) == []


def test_role_approver_to_auto_is_a_loosening_but_human_to_role_is_too():
    base = make_policy({"x": {"paths": ["x/"], "approver": "role:lead"}})
    assert any(
        "auto" in f for f in pol.loosening(base, make_policy({"x": {"paths": ["x/"], "approver": "auto"}}))
    )
    assert pol.loosening(base, make_policy({"x": {"paths": ["x/"], "approver": "human"}})) == []


# -- the command line ------------------------------------------------------------------------------------------------


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    import yaml

    raw = copy.deepcopy(BASE)
    raw["slots"] = copy.deepcopy(SLOTS)
    pfile = tmp_path / "onloop.yaml"
    pfile.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setenv("ONLOOP_LEDGER", str(tmp_path / "cli.db"))
    return ["--policy", str(pfile)], ["--repo", REPO]


def test_the_commands_end_to_end(cli_env, capsys):
    pol_arg, repo_arg = cli_env
    assert cli.main(["slot", "need", *pol_arg, "--paths", "docs/a.md", "src/x.py"]) == 0
    assert capsys.readouterr().out.split() == ["gate", "host"]
    assert (
        cli.main(
            [
                "slot",
                "request",
                "host",
                *pol_arg,
                *repo_arg,
                "--holder",
                "alice",
                "--role",
                "builder",
                "--reason",
                "t",
                "--ref",
                "pr#1",
            ]
        )
        == 0
    )
    res = json.loads(capsys.readouterr().out)
    assert res["status"] == "granted"
    assert cli.main(["slot", "wait", "host", *pol_arg, *repo_arg, "--holder", "alice", "--ref", "pr#1"]) == 0
    out = capsys.readouterr().out
    assert "granted: slot host to alice" in out and "command: pytest -q" in out
    assert cli.main(["slot", "status", *pol_arg, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["slots"]["host"]["held"]["holder"] == "alice"
    assert cli.main(["slot", "renew", str(res["grant"]), *pol_arg, *repo_arg, "--holder", "alice"]) == 0
    capsys.readouterr()
    assert cli.main(["slot", "release", str(res["grant"]), *pol_arg, *repo_arg, "--holder", "alice"]) == 0
    assert "released" in capsys.readouterr().out


def test_wait_exits_4_on_timeout_and_the_error_exits_1(cli_env, capsys):
    pol_arg, repo_arg = cli_env
    for who, ref in (("alice", "pr#1"), ("bob", "pr#2")):
        cli.main(
            [
                "slot",
                "request",
                "host",
                *pol_arg,
                *repo_arg,
                "--holder",
                who,
                "--role",
                "builder",
                "--reason",
                "t",
                "--ref",
                ref,
            ]
        )
    capsys.readouterr()
    assert (
        cli.main(
            [
                "slot",
                "wait",
                "host",
                *pol_arg,
                *repo_arg,
                "--holder",
                "bob",
                "--ref",
                "pr#2",
                "--timeout",
                "0",
            ]
        )
        == 4
    )
    assert (
        cli.main(
            [
                "slot",
                "request",
                "nope",
                *pol_arg,
                *repo_arg,
                "--holder",
                "a",
                "--role",
                "builder",
                "--reason",
                "t",
                "--ref",
                "r",
            ]
        )
        == 1
    )
    assert "no slot" in capsys.readouterr().err


# -- who may grant, renew and release (the self-grant review) --------------------------------------------------------


def test_a_holder_cannot_grant_its_own_request(st, policy):
    r = ask(st, policy, slot="db", holder="agent-x", ref="t1")
    with pytest.raises(slots.SlotError, match="own request"):
        slots.grant(st, REPO, policy, r["request"], by="agent-x")
    with pytest.raises(slots.SlotError, match="own request"):
        slots.grant(st, REPO, policy, r["request"], by="agent-x/Bot")


@pytest.mark.parametrize("by", ["agent-x", "rev-bot/Bot", "someone-else/User", "owner-a-impostor"])
def test_a_human_slot_is_granted_only_by_the_policys_human(st, policy, by):
    r = ask(st, policy, slot="db", holder="builder-1", ref="t1")
    with pytest.raises(slots.SlotError, match="granted by the human"):
        slots.grant(st, REPO, policy, r["request"], by=by)
    assert slots.grant(st, REPO, policy, r["request"], by="owner-a")  # the human's own login, bare or not


def test_a_role_slot_needs_a_granter_who_states_that_role(st):
    p = make_policy({"x": {"paths": ["x/"], "approver": "role:lead", "timeout_minutes": 10}})
    r = ask(st, p, slot="x", holder="builder-1", ref="t1")
    with pytest.raises(slots.SlotError, match="role 'lead'"):
        slots.grant(st, REPO, p, r["request"], by="operator-1")
    with pytest.raises(slots.SlotError, match="role 'lead'"):
        slots.grant(st, REPO, p, r["request"], by="operator-1", by_role="builder")
    assert slots.grant(st, REPO, p, r["request"], by="operator-1", by_role="lead")


def test_only_the_holder_renews(st, policy):
    g = ask(st, policy, holder="alice")["grant"]
    with pytest.raises(slots.SlotError, match="only its holder"):
        slots.renew(st, REPO, policy, g, holder="mallory")
    assert slots.renew(st, REPO, policy, g, holder="alice")["renewals"] == 1


def test_a_release_by_someone_else_needs_by_and_is_recorded_under_that_identity(st, policy):
    g = ask(st, policy, holder="alice")["grant"]
    with pytest.raises(slots.SlotError, match="needs --by"):
        slots.release(st, REPO, policy, g, holder="mallory")
    with pytest.raises(slots.SlotError, match="needs --by"):
        slots.release(st, REPO, policy, g)
    slots.release(st, REPO, policy, g, by="operator-1", reason="session died")
    e = st.events(kind="slot.released")[0]
    assert (
        e["actor"] == "operator-1"
        and e["payload"]["released_by"] == "operator-1"
        and e["payload"]["holder"] == "alice"
    )


def test_the_holder_releasing_its_own_lease_is_recorded_as_such(st, policy):
    g = ask(st, policy, holder="alice")["grant"]
    slots.release(st, REPO, policy, g, holder="alice")
    assert st.events(kind="slot.released")[0]["payload"]["released_by"] == "alice"


# -- more ways to loosen a slot ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"conditions": []}, "drops or changes conditions"),
        ({"conditions": ["a different condition"]}, "drops or changes conditions"),
        ({"commands": []}, "drops or changes commands"),
        ({"commands": ["pytest -q --lf"]}, "drops or changes commands"),
        ({"max_renewals": 4}, "raises max_renewals"),
    ],
)
def test_loosening_what_a_grant_says_or_how_long_it_is_flagged(policy, change, reason):
    assert any(reason in f for f in pol.loosening(policy, changed(host=change)))


@pytest.mark.parametrize(
    "change",
    [
        {"conditions": ["one run at a time", "and a dump first"]},
        {"commands": ["pytest -q", "ruff check ."]},
        {"max_renewals": 2},
    ],
)
def test_adding_to_what_a_grant_says_or_lowering_renewals_is_not_a_loosening(policy, change):
    assert pol.loosening(policy, changed(host=change)) == []


def test_a_change_between_different_role_approvers_is_a_loosening():
    a = make_policy({"x": {"paths": ["x/"], "approver": "role:lead"}})
    b = make_policy({"x": {"paths": ["x/"], "approver": "role:builder"}})
    assert any("role:lead to role:builder" in f for f in pol.loosening(a, b))
    assert pol.loosening(a, a) == []


# -- one identity form -----------------------------------------------------------------------------------------------


def test_a_typed_holder_cannot_be_bypassed_by_the_bare_login_on_a_role_slot(st):
    p = make_policy({"x": {"paths": ["x/"], "approver": "role:lead", "timeout_minutes": 10}})
    r = ask(st, p, slot="x", holder="agent-x/Bot", ref="t1")
    with pytest.raises(slots.SlotError, match="own request"):
        slots.grant(st, REPO, p, r["request"], by="agent-x", by_role="lead")
    with pytest.raises(slots.SlotError, match="own request"):
        slots.grant(st, REPO, p, r["request"], by="AGENT-X/Bot", by_role="lead")


def test_a_typed_holder_cannot_grant_itself_the_human_slot_with_the_bare_login(st, policy):
    r = ask(st, policy, slot="db", holder="owner-a/User", ref="t1")
    with pytest.raises(slots.SlotError, match="own request"):
        slots.grant(st, REPO, policy, r["request"], by="owner-a")
    with pytest.raises(slots.SlotError, match="own request"):
        slots.grant(st, REPO, policy, r["request"], by="Owner-A/User")


def test_an_app_with_the_humans_login_is_not_the_human(st, policy):
    r = ask(st, policy, slot="db", holder="builder-1", ref="t1")
    with pytest.raises(slots.SlotError, match="granted by the human"):
        slots.grant(st, REPO, policy, r["request"], by="owner-a/Bot")
    assert slots.grant(st, REPO, policy, r["request"], by="Owner-A/User")


def test_the_holder_is_stored_in_one_canonical_form(st, policy):
    res = ask(st, policy, holder="Agent-X/bot", ref="pr#1")
    assert st.conn.execute("SELECT holder FROM slot_requests").fetchone()[0] == "agent-x/Bot"
    assert (
        st.conn.execute("SELECT holder FROM slot_grants WHERE id = ?", (res["grant"],)).fetchone()[0]
        == "agent-x/Bot"
    )


def test_asking_in_another_form_is_the_same_request(st, policy):
    a = ask(st, policy, holder="agent-x/Bot", ref="pr#1")
    b = ask(st, policy, holder="AGENT-X", ref="pr#1")
    assert (
        b["grant"] == a["grant"] and st.conn.execute("SELECT count(*) FROM slot_requests").fetchone()[0] == 1
    )
    ask(st, policy, holder="bob", ref="pr#2")
    again = ask(st, policy, holder="Bob/User", ref="pr#2")
    assert (
        again["status"] == "waiting"
        and st.conn.execute("SELECT count(*) FROM slot_requests").fetchone()[0] == 2
    )


@pytest.mark.parametrize(
    "stored,caller",
    [
        ("agent-x/Bot", "agent-x"),
        ("agent-x", "agent-x/Bot"),
        ("Alice/User", "alice"),
        ("alice", "ALICE/User"),
    ],
)
def test_the_real_holder_can_renew_and_release_in_any_form(st, policy, stored, caller):
    g = ask(st, policy, holder=stored)["grant"]
    assert slots.renew(st, REPO, policy, g, holder=caller)["renewals"] == 1
    slots.release(st, REPO, policy, g, holder=caller)
    assert st.conn.execute("SELECT released_at FROM slot_grants").fetchone()[0] is not None


def test_another_account_with_the_same_login_but_a_different_type_cannot(st, policy):
    g = ask(st, policy, holder="agent-x/Bot")["grant"]
    with pytest.raises(slots.SlotError, match="only its holder"):
        slots.renew(st, REPO, policy, g, holder="agent-x/User")
    with pytest.raises(slots.SlotError, match="needs --by"):
        slots.release(st, REPO, policy, g, holder="agent-x/User")


def test_wait_finds_the_request_made_in_another_form(st, policy):
    ask(st, policy, holder="agent-x/Bot", ref="pr#1")
    code, g = slots.wait(st, REPO, policy, "host", holder="AGENT-X", ref="pr#1", sleep=lambda s: None)
    assert code == slots.EXIT_GRANTED and g["holder"] == "agent-x/Bot"


def test_the_old_db_flag_says_where_the_slots_are_now(tmp_path, capsys):
    rc = cli.main(["slot", "status", "--db", str(tmp_path / "onloop.db")])
    assert rc == 2 and "use --ledger" in capsys.readouterr().err


def test_a_slot_event_is_a_ledger_event_and_replay_rebuilds_the_queue(st, policy):
    ask(st, policy, holder="alice", ref="pr#1")
    ask(st, policy, holder="bob", ref="pr#2")
    assert {e["source"] for e in st.events(kind="slot.requested")} == {"ui"}
    assert st.rebuild_matches()[0]
    top = st.constraint(st.now())["constraint"]
    assert top["on_ref"] == "slot:host" and top["reasons"] == {"running": 1}


def test_a_grant_event_for_a_taken_seat_is_refused_and_not_logged(st, policy):
    import sqlite3 as sq

    first = ask(st, policy, holder="alice", ref="pr#1")
    ask(st, policy, holder="bob", ref="pr#2")
    seq = st.last_seq()
    forged = {
        "id": "forged",
        "source": "ui",
        "kind": "slot.granted",
        "subject_ref": "slot:host",
        "payload": {
            "grant": 99,
            "request": 2,
            "holder": "bob",
            "role": "builder",
            "granted_by": "x",
            "expires_at": st.now(),
            "seat": 1,
        },
    }
    with pytest.raises(sq.IntegrityError):
        st.append(forged)
    assert st.last_seq() == seq
    holder = st.conn.execute("SELECT holder FROM slot_grants WHERE released_at IS NULL").fetchone()[0]
    assert holder == "alice" and first["status"] == "granted"  # the holder was not replaced
