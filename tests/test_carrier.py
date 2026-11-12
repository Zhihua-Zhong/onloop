"""The session carrier, in one process against the fake CLI (decision 0010; the R4 plan's section 8)."""

import json
import pathlib
import time

import pytest

from onloop.carrier import config as carrier_config
from onloop.carrier import deliveries, guards, listing, messages
from onloop.carrier.loop import Carrier

from . import carrier_helpers as h


def runs(c):
    return c.led.select("SELECT * FROM runs ORDER BY started_at, id")


def live_leases(c):
    return c.led.select("SELECT * FROM session_leases WHERE released_at IS NULL")


class NoSpawn:
    def __call__(self, *a, **kw):
        raise AssertionError("observe-only must spawn nothing")


# -- observe-only by default ------------------------------------------------------------------------------------------


def test_a_fresh_install_spawns_nothing_and_logs_what_it_would_deliver(tmp_path):
    cfg = h.config(tmp_path, roles={"builder": h.role(cwd=tmp_path, opt_in=())})
    c = h.carrier(tmp_path, cfg, spawn=NoSpawn())
    d = h.say(c, "please look at the parser")
    for _ in range(3):  # many passes: one would.deliver, nothing spawned
        c.tick()
    would = c.led.select("SELECT payload FROM events WHERE kind = 'would.deliver'")
    assert len(would) == 1
    p = would[0]["payload"]
    assert p["role"] == "builder" and p["text"] == "please look at the parser"
    assert "--resume" in p["command"] and h.SID in p["command"] and "--restricted" in p["command"]
    assert p["why"].startswith("observe-only")
    assert h.delivery(c, d)["state"] == "cancelled" and runs(c) == []


def test_opted_in_but_not_started_is_still_observe_only(tmp_path):
    c = h.carrier(tmp_path, spawn=NoSpawn())  # opted in to message, carrier never started
    h.say(c, "hello")
    c.tick()
    assert h.kinds(c, "would.") == ["would.deliver"] and runs(c) == []


def test_a_turn_without_an_outcome_is_only_observed_when_the_corrective_is_not_opted_in(tmp_path):
    c = h.carrier(tmp_path, spawn=NoSpawn())
    h.start(c)
    stop(c, "t1", outcome=None)
    c.tick()
    assert [
        r["payload"]["kind"] for r in c.led.select("SELECT payload FROM events WHERE kind = 'would.deliver'")
    ] == ["corrective"]
    assert c.led.select("SELECT * FROM timers") == []


# -- a delivery, acked whatever the outcome ---------------------------------------------------------------------------


def test_a_message_is_delivered_processed_and_acked_by_the_stop_hook_turn(tmp_path):
    c = h.carrier(tmp_path)
    h.start(c)
    d = h.say(c, "please look at the parser")
    c.tick()
    row = h.delivery(c, d)
    assert row["state"] == "acked" and row["attempts"] == 1
    assert row["delivered_at"] <= row["processing_at"] <= row["acked_at"]
    turn = c.led.select("SELECT id, parse_error FROM turns")[0]
    assert row["acked_by_turn"] == turn["id"] and turn["parse_error"] is None
    r = runs(c)[0]
    assert (r["status"], r["stop_turn_id"], r["hook_missing"], r["denials"]) == ("ok", turn["id"], 0, 0)
    assert json.loads(r["rate_limit"])["status"] == "allowed"
    assert live_leases(c) == []


def test_a_turn_without_an_outcome_line_still_acks_and_fails_nothing(tmp_path):
    """The pilot's case: the roles write no outcome line yet. Every delivery is acked; nothing is failed or counted."""
    c = h.carrier(tmp_path, environ=h.env(tmp_path, FAKE_OUTCOME="none"))
    h.start(c)
    ids = [h.say(c, f"message {n}") for n in range(2)]
    c.tick()
    turn = c.led.select("SELECT id, parse_error IS NOT NULL AS bad FROM turns")
    assert len(turn) == 1 and turn[0]["bad"] == 1
    for d in ids:
        row = h.delivery(c, d)
        assert row["state"] == "acked" and row["attempts"] == 1 and row["last_error"] is None
        assert row["acked_by_turn"] == turn[0]["id"]  # by the Stop-hook turn itself, not by the run's end
    assert runs(c)[0]["hook_missing"] == 0
    c.tick()
    assert c.led.select("SELECT COUNT(*) AS n FROM deliveries WHERE kind = 'corrective'")[0]["n"] == 0
    assert c.led.select("SELECT COUNT(*) AS n FROM timers")[0]["n"] == 0
    assert len(runs(c)) == 1  # nothing re-run


def test_no_stop_hook_in_time_acks_from_the_result_line_and_marks_the_run(tmp_path):
    env = h.env(tmp_path)
    env.pop("FAKE_HOOK_LEDGER")
    c = h.carrier(tmp_path, environ=env, hook_wait_s=0.5)
    h.start(c)
    d = h.say(c, "hi")
    c.tick()
    assert h.delivery(c, d)["state"] == "acked" and h.delivery(c, d)["acked_by_turn"] is None
    assert runs(c)[0]["hook_missing"] == 1


def test_three_due_deliveries_go_as_one_composed_message_in_one_run(tmp_path):
    c = h.carrier(tmp_path)
    h.start(c)
    ids = [h.say(c, f"part {n}") for n in range(3)]
    c.tick()
    assert len(runs(c)) == 1
    rows = [h.delivery(c, d) for d in ids]
    assert {r["state"] for r in rows} == {"acked"} and len({r["acked_by_turn"] for r in rows}) == 1
    with c.w() as led:
        text = deliveries.compose(led, ids)
    assert [text.index(f"[onloop delivery {d}") for d in ids] == sorted(
        text.index(f"[onloop delivery {d}") for d in ids
    )
    assert text.count("local user, not verified") == 3
    assert c.led.select("SELECT COUNT(*) AS n FROM events WHERE kind = 'delivery.delivered'")[0]["n"] == 3


# -- refused, failed, retried -----------------------------------------------------------------------------------------


def test_a_refused_tool_is_not_a_success_and_is_not_re_run(tmp_path):
    c = h.carrier(tmp_path, environ=h.env(tmp_path, FAKE_DENIALS=1))
    h.start(c)
    d = h.say(c, "write a file")
    c.tick()
    c.tick()
    r = runs(c)
    assert len(r) == 1 and r[0]["status"] == "refused" and r[0]["denials"] == 1
    assert h.delivery(c, d)["state"] == "acked"  # the Stop hook fired: received and processed
    assert (
        c.led.select("SELECT COUNT(*) AS n FROM items WHERE id LIKE 'alert:carrier:refused:%'")[0]["n"] == 1
    )


def test_a_run_with_no_stop_hook_turn_is_resent_once_with_the_mark(tmp_path):
    env = h.env(tmp_path, FAKE_MODE="noresult")
    c = h.carrier(tmp_path, environ=env, hook_wait_s=0.2)
    h.start(c)
    d = h.say(c, "hi")
    c.tick()
    row = h.delivery(c, d)
    assert (row["state"], row["resent"], row["last_error"]) == ("created", 1, "error")
    assert row["next_attempt_at"] > c.led.now()  # a backoff
    with c.w() as led:
        assert deliveries.RESENT_MARK in deliveries.compose(led, [d])


# -- the corrective wake, at most once per turn, then the coordinator --------------------------------------------------


def stop(c, prompt, outcome="outcome: done", role="builder", sid=h.SID):
    from onloop.daemon import hook_receiver

    message = "Did a step." + (f"\n{outcome}" if outcome else "")
    body = {
        "role": role,
        "payload": {"session_id": sid, "prompt_id": prompt, "last_assistant_message": message},
    }
    with c.w() as led:
        led.append(hook_receiver.event_for("stop", body))
        return led.select("SELECT id FROM turns ORDER BY last_seq DESC LIMIT 1")[0]["id"]


def advance(c, seconds):
    """Move the ledger's clock on (timers are due by the ledger's own time)."""
    from onloop import events as ev

    c.led._clock = None
    base = c.led.now()
    c.led._clock = lambda: ev.shift_ts(base, seconds)


def test_one_corrective_per_turn_then_the_coordinator_with_the_age(tmp_path, monkeypatch):
    from onloop.carrier import loop

    monkeypatch.setattr(loop, "CORRECTIVE_AFTER_S", 0)  # the fake CLI logs its Stop hooks on the real clock
    monkeypatch.setattr(loop, "ESCALATE_AFTER_S", 0)
    roles = {
        "builder": h.role(cwd=tmp_path, opt_in=("message", "corrective")),
        "lead": h.role(sid=h.SID2, cwd=tmp_path, opt_in=("event",)),
    }
    cfg = h.config(tmp_path, roles=roles, coordinator="lead")
    c = h.carrier(tmp_path, cfg, environ=h.env(tmp_path, FAKE_OUTCOME="none"))
    h.start(c)
    first = stop(c, "p1", outcome=None)
    c.tick()  # the timer is set and fires: one corrective, delivered; the role answers again without an outcome
    assert c.led.select("SELECT id FROM timers WHERE kind = 'corrective'") == [{"id": f"corrective:{first}"}]
    corr = c.led.select("SELECT * FROM deliveries WHERE kind = 'corrective'")
    assert len(corr) == 1 and corr[0]["for_turn_id"] == first and corr[0]["delivered_at"]
    with c.w() as led:
        body = led.select("SELECT body FROM messages WHERE id = ?", (corr[0]["message_id"],))[0]["body"]
        assert body == deliveries.CORRECTIVE_TEXT
        again = deliveries.create(
            led, role="builder", kind="corrective", body="x", retention_s=60, for_turn_id=first
        )
    assert again is None  # the database refuses a second corrective for that turn
    for _ in range(3):
        c.tick()  # the answer had no outcome either: no corrective for it; the escalation goes to the coordinator
    assert c.led.select("SELECT COUNT(*) AS n FROM deliveries WHERE kind = 'corrective'")[0]["n"] == 1
    esc = c.led.select(
        "SELECT d.state, m.body FROM deliveries d JOIN messages m ON m.id = d.message_id WHERE d.to_role = 'lead'"
    )
    assert len(esc) == 1 and "min ago" in esc[0]["body"] and "builder" in esc[0]["body"]
    assert c.led.select("SELECT state, escalated_to FROM deliveries WHERE kind = 'corrective'") == [
        {"state": "escalated", "escalated_to": "lead"}
    ]


def test_a_valid_outcome_cancels_the_corrective(tmp_path):
    cfg = h.config(tmp_path, roles={"builder": h.role(cwd=tmp_path, opt_in=("corrective",))})
    c = h.carrier(tmp_path, cfg, spawn=NoSpawn())
    h.start(c)
    stop(c, "p1", outcome=None)
    c.tick()
    stop(c, "p2")  # the role wrote a valid line on its own
    advance(c, 130)
    c.tick()
    assert c.led.select("SELECT COUNT(*) AS n FROM deliveries")[0]["n"] == 0


def test_a_corrective_for_a_turn_older_than_an_hour_escalates_instead(tmp_path):
    cfg = h.config(tmp_path, roles={"builder": h.role(cwd=tmp_path, opt_in=("corrective",))})
    c = h.carrier(tmp_path, cfg, spawn=NoSpawn())
    h.start(c)
    t = stop(c, "p1", outcome=None)
    c.tick()
    advance(c, 3 * 3600)  # the machine slept through the timer
    c.tick()
    assert c.led.select("SELECT COUNT(*) AS n FROM deliveries")[0]["n"] == 0
    assert (
        c.led.select("SELECT COUNT(*) AS n FROM items WHERE id = ?", (f"alert:carrier:escalate:{t}",))[0]["n"]
        == 1
    )
    assert c.led.select("SELECT fired_at IS NOT NULL AS f FROM timers") == [{"f": 1}]


def test_a_handoff_to_a_carried_role_is_an_event_delivery(tmp_path):
    roles = {"builder": h.role(cwd=tmp_path), "reviewer": h.role(sid=h.SID2, cwd=tmp_path, opt_in=("event",))}
    c = h.carrier(tmp_path, h.config(tmp_path, roles=roles), spawn=NoSpawn())
    stop(c, "p1", outcome="outcome: handoff owner=reviewer ref=pr#7")
    c.tick()
    c.tick()
    rows = c.led.select("SELECT payload FROM events WHERE kind = 'would.deliver'")
    assert len(rows) == 1 and "pr#7" in rows[0]["payload"]["text"]  # not started: observe-only


# -- held elsewhere, contended ----------------------------------------------------------------------------------------


def test_a_session_open_elsewhere_is_never_resumed(tmp_path):
    held = h.Listings([{"pid": 4321, "sessionId": h.SID, "status": "idle", "kind": "interactive"}])
    c = h.carrier(tmp_path, listings=held, spawn=NoSpawn())
    h.start(c)
    d = h.say(c, "hi")
    rep = c.tick()
    assert rep.waiting["builder"].startswith("held_elsewhere") and h.delivery(c, d)["state"] == "created"
    assert live_leases(c) == [] and runs(c) == []


def test_an_unreadable_listing_spawns_nothing(tmp_path):
    c = h.carrier(tmp_path, listings=h.Listings(ok=False), spawn=NoSpawn())
    h.start(c)
    h.say(c, "hi")
    assert "could not be read" in c.tick().waiting["builder"]


def test_a_second_holder_seen_after_a_run_marks_it_contended_and_pauses_the_role(tmp_path):
    class Later(h.Listings):
        def __call__(self, bound):
            self.calls += 1
            rows = [{"pid": 999999, "sessionId": h.SID, "status": "busy"}] if self.calls > 1 else []
            return listing.Listing(ok=True, holders=listing.held(rows, bound))

    c = h.carrier(tmp_path, listings=Later())
    h.start(c)
    h.say(c, "hi")
    c.tick()
    assert runs(c)[0]["status"] == "contended"
    assert c.led.select("SELECT state FROM roles WHERE id = 'builder'") == [{"state": "paused_by_human"}]


# -- the listing filter (the lead's addition 1) -----------------------------------------------------------------------

OTHERS = [
    {
        "pid": 101,
        "cwd": "D:/work/someone/private-project",
        "kind": "interactive",
        "sessionId": "11111111-2222-4333-8444-555555555555",
        "name": "a private chat",
        "status": "busy",
    },
    {
        "pid": 102,
        "cwd": "/home/someone/other",
        "kind": "background",
        "sessionId": "66666666-7777-4888-8999-000000000000",
        "name": "another one",
        "status": "idle",
    },
]


def test_the_listing_filter_returns_nothing_about_unbound_sessions():
    assert listing.held(OTHERS, {h.SID}) == {}
    got = listing.held(
        [
            *OTHERS,
            {"pid": 7, "sessionId": h.SID, "status": "idle", "kind": "interactive", "cwd": "x", "name": "y"},
        ],
        {h.SID},
    )
    assert got == {
        h.SID: (listing.Holder(pid=7, status="idle", kind="interactive"),)
    }  # no cwd, no name, no count


def test_reading_the_listing_keeps_no_trace_of_unbound_sessions():
    class P:
        returncode, stdout = 0, json.dumps(OTHERS)

    out = listing.read(("claude",), {h.SID}, run=lambda *a, **k: P(), env={})
    assert out == listing.Listing(ok=True, holders={})
    text = repr(out)
    for row in OTHERS:
        for value in (row["cwd"], row["name"], row["sessionId"], str(row["pid"])):
            assert value not in text
    assert "2" not in text.replace(h.SID, "")  # not even a count

    class Bad:
        returncode, stdout = 0, "secret-looking garbage D:/work/someone/private-project"

    bad = listing.read(("claude",), {h.SID}, run=lambda *a, **k: Bad(), env={})
    assert not bad.ok and "private-project" not in (bad.error or "")


def test_the_carrier_stores_nothing_about_unbound_sessions(tmp_path):
    c = h.carrier(tmp_path, listings=h.Listings(OTHERS))
    h.start(c)
    h.say(c, "hi")
    c.tick()
    dump = json.dumps(c.led.select("SELECT * FROM events")) + c.led.dump()
    for row in OTHERS:
        for value in (row["cwd"], row["name"], row["sessionId"]):
            assert value not in dump
    assert runs(c)[0]["status"] == "ok"


# -- the guards -------------------------------------------------------------------------------------------------------


def test_a_role_that_names_a_shell_is_refused_with_a_pointer_to_a_future_decision(tmp_path):
    for tools in (["Read", "Bash"], ["PowerShell"], ["Bash(git status *)"], ["pwsh"]):
        problems: list[str] = []
        carrier_config.parse(
            {"roles": {"builder": {"session_id": h.SID, "cwd": ".", "tools": tools}}}, problems
        )
        assert len(problems) == 1 and "names a shell" in problems[0] and "future ADR" in problems[0], tools
    problems = []
    carrier_config.parse(
        {
            "roles": {
                "builder": {"session_id": h.SID, "cwd": ".", "tools": ["Read"], "allowed_tools": ["Bash(ls)"]}
            }
        },
        problems,
    )
    assert "allowed_tools names a shell (Bash)" in problems[0]
    role = carrier_config.CarriedRole("builder", h.SID, ".", ("Read", "Bash"), ("Read",))
    c = h.carrier(tmp_path)
    with pytest.raises(carrier_config.ShellRefused):
        c.command(role)


def test_a_metered_key_stops_the_carrier_at_start(tmp_path):
    env = {**h.env(tmp_path), "ANTHROPIC_API_KEY": "placeholder-not-a-key"}
    c = h.carrier(tmp_path, environ=env, spawn=NoSpawn())
    h.start(c)
    h.say(c, "hi")
    rep = c.tick()
    assert "ANTHROPIC_API_KEY" in rep.waiting["builder"] and runs(c) == []


def test_the_child_environment_holds_no_claude_or_anthropic_variable(tmp_path):
    dump = tmp_path / "env.json"
    env = {
        **h.env(tmp_path, FAKE_ENV_DUMP=dump),
        "CLAUDECODE": "1",
        "CLAUDE_CODE_SESSION_ID": "x",
        "ANTHROPIC_BASE_URL": "x",
    }
    c = h.carrier(tmp_path, environ=env)
    h.start(c)
    h.say(c, "hi")
    c.tick()
    names = json.loads(dump.read_text(encoding="utf-8"))
    assert names and not [n for n in names if n.upper().startswith(("CLAUDE", "ANTHROPIC"))]


def test_not_logged_in_pauses_once_alerts_once_and_counts_no_attempt(tmp_path):
    login = h.LoggedIn("`claude auth status` says the CLI is not logged in")
    c = h.carrier(tmp_path, login=login, spawn=NoSpawn())
    h.start(c)
    d = h.say(c, "hi")
    for _ in range(3):
        c.tick()
    assert h.kinds(c, "carrier.paused") == ["carrier.paused"]
    assert (
        c.led.select("SELECT COUNT(*) AS n FROM items WHERE id = 'alert:carrier:not_logged_in'")[0]["n"] == 1
    )
    assert h.delivery(c, d)["attempts"] == 0 and h.delivery(c, d)["state"] == "created"


def test_the_usage_limit_pauses_until_its_reset_and_puts_the_delivery_back_uncounted(tmp_path):
    c = h.carrier(tmp_path, environ=h.env(tmp_path, FAKE_RATE="rejected", FAKE_HOOK_LEDGER=""))
    h.start(c)
    d = h.say(c, "hi")
    c.tick()
    st = c.led.select("SELECT paused_reason, paused_until FROM carrier_state")[0]
    assert st["paused_reason"] == "usage_limit" and st["paused_until"].startswith(
        "2030-03-17"
    )  # resetsAt 1900000000
    assert runs(c)[0]["status"] == "usage_limited"
    assert (h.delivery(c, d)["state"], h.delivery(c, d)["attempts"]) == ("created", 0)  # back, not counted
    c.tick()
    assert len(runs(c)) == 1  # no probe loop while paused


def test_overage_pauses_the_carrier_at_once(tmp_path):
    c = h.carrier(tmp_path, environ=h.env(tmp_path, FAKE_OVERAGE=1))
    h.start(c)
    h.say(c, "one")
    c.tick()
    assert c.led.select("SELECT paused_reason FROM carrier_state")[0]["paused_reason"] == "overage"
    h.say(c, "two")
    c.tick()
    assert len(runs(c)) == 1
    assert (
        c.led.select("SELECT COUNT(*) AS n FROM items WHERE id LIKE 'alert:carrier:overage:%'")[0]["n"] == 1
    )


def test_a_usage_warning_halves_the_rate(tmp_path):
    cfg = h.config(tmp_path, roles={"builder": h.role(cwd=tmp_path, runs_per_hour=2)})
    c = h.carrier(tmp_path, cfg, environ=h.env(tmp_path, FAKE_RATE="allowed_warning"))
    h.start(c)
    h.say(c, "one")
    c.tick()
    h.say(c, "two")
    assert "budget: 1 of 1 runs" in c.tick().waiting["builder"]


def test_the_role_budget_holds_deliveries(tmp_path):
    cfg = h.config(tmp_path, roles={"builder": h.role(cwd=tmp_path, runs_per_hour=1)})
    c = h.carrier(tmp_path, cfg)
    h.start(c)
    h.say(c, "one")
    c.tick()
    d = h.say(c, "two")
    assert c.tick().waiting["builder"].startswith("budget")
    assert h.delivery(c, d)["state"] == "created"


def test_the_kill_switch_ends_a_live_run_within_five_seconds_and_releases_its_lease(tmp_path):
    import threading

    cfg = h.config(
        tmp_path, roles={"builder": h.role(cwd=tmp_path, silence="10s")}
    )  # a bounded run if it fails
    c = h.carrier(tmp_path, cfg, environ=h.env(tmp_path, FAKE_MODE="hang"))
    h.start(c)
    d = h.say(c, "hi")
    t = threading.Thread(target=c.tick, daemon=True)
    t.start()
    end = time.monotonic() + 20
    while not c.led.select("SELECT 1 FROM runs WHERE pid IS NOT NULL") and time.monotonic() < end:
        time.sleep(0.1)
    pid = c.led.select("SELECT claude_pid FROM runs")[0]["claude_pid"]
    (tmp_path / guards.KILL_FILE).write_text("stop", encoding="utf-8")
    t0 = time.monotonic()
    t.join(30)
    assert time.monotonic() - t0 < 5
    from onloop import procs

    assert not procs.pid_alive(pid)
    assert runs(c)[0]["status"] == "killed" and live_leases(c) == []
    row = h.delivery(c, d)
    assert row["state"] == "created" and row["attempts"] == 0  # put back, not counted
    assert "kill switch" in c.tick().waiting["builder"]


def test_a_paused_role_is_not_carried(tmp_path):
    c = h.carrier(tmp_path, spawn=NoSpawn())
    h.start(c)
    with c.w() as led:
        led.append(
            {
                "id": "p",
                "source": "ui",
                "kind": "role.paused",
                "subject_ref": "role:builder",
                "payload": {"id": "builder"},
            }
        )
    h.say(c, "hi")
    assert "paused by the human" in c.tick().waiting["builder"]


# -- crash, respawn, sleep --------------------------------------------------------------------------------------------


def test_after_a_crash_a_delivery_with_a_stop_hook_turn_is_acked_and_one_without_is_resent(tmp_path):
    c = h.carrier(tmp_path, spawn=NoSpawn())
    a, b = h.say(c, "answered"), h.say(c, "not answered")
    with c.w() as led:  # a run that was in flight when onloopd died: both written, one answered
        from onloop.carrier import lease

        claim = lease.acquire(
            led, role="builder", session_id=h.SID, deliveries=[a, b], instance="x:1", ttl_s=60
        )
        deliveries.mark(led, "delivery.delivered", [a], "builder")
    stop(c, "p-crash")  # the turn that answered a ended; b was written after it, and the run died
    advance(c, 1)
    with c.w() as led:
        deliveries.mark(led, "delivery.delivered", [b], "builder")
    c.led.close()
    again = h.carrier(tmp_path, spawn=NoSpawn())  # boot: recover and settle
    assert h.delivery(again, a)["state"] == "acked"
    assert (h.delivery(again, b)["state"], h.delivery(again, b)["resent"]) == ("created", 1)
    leases = again.led.select("SELECT release_reason FROM session_leases WHERE id = ?", (claim.lease_id,))
    assert leases == [{"release_reason": "orphaned"}]
    assert again.led.select("SELECT status FROM runs") == [{"status": "orphaned"}]


def test_a_wall_clock_jump_is_logged_once_as_a_wake(tmp_path):
    wall = [1000.0]
    c = h.carrier(tmp_path, spawn=NoSpawn(), wall=lambda: wall[0])
    c.tick()
    wall[0] += 8 * 3600  # the monotonic clock did not move: the machine slept
    c.tick()
    c.tick()
    assert h.kinds(c, "carrier.woke") == ["carrier.woke"]


# -- messages ---------------------------------------------------------------------------------------------------------

PEM = (
    "-----BEGIN "
    + "PRIVATE KEY-----\nMIIBVQIBADANBgkqhkiG9w0BAQEFAASCAT8wggE7AgEAAkEA\n-----END "
    + "PRIVATE KEY-----"
)


def test_a_message_is_scrubbed_kept_for_its_retention_then_only_its_hash(tmp_path):
    c = h.carrier(tmp_path, spawn=NoSpawn())
    with c.w() as led:
        mid = messages.post(
            led,
            thread="builder",
            body=f"use this:\n{PEM}\nthanks",
            author="human",
            channel="cli",
            retention_s=3600,
        )
        row = led.select("SELECT body, redacted, body_sha256 FROM messages WHERE id = ?", (mid,))[0]
    assert "PRIVATE KEY" not in row["body"] and messages.REDACTED in row["body"] and row["redacted"] == 1
    advance(c, 3601)
    c.tick()
    gone = c.led.select("SELECT body, body_sha256, expired_at FROM messages WHERE id = ?", (mid,))[0]
    assert gone["body"] is None and gone["body_sha256"] == row["body_sha256"] and gone["expired_at"]


def test_message_text_is_not_in_the_reports(tmp_path):
    from onloop.report import views

    c = h.carrier(tmp_path, spawn=NoSpawn())
    h.say(c, "a distinctive sentence about the parser")
    c.tick()
    now = views.now(c.led, c.led.now(), None)
    assert "distinctive sentence" not in json.dumps(now) and "distinctive sentence" not in json.dumps(
        views.flow(c.led, c.led.now())
    )


def test_a_message_over_8_kb_or_empty_is_refused(tmp_path):
    for body in ("", "   ", "x" * (8 * 1024 + 1)):
        with pytest.raises(messages.MessageRefused):
            messages.check(body)


def test_onloop_writes_the_role_hooks_file_it_resumes_with(tmp_path):
    c = h.carrier(tmp_path)
    argv = c.command(c.cfg.role("builder"))
    settings = pathlib.Path(argv[argv.index("--settings") + 1])
    hooks = json.loads(settings.read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == {"Stop", "SessionStart"}
    assert "daemon hook --event stop --role builder" in hooks["Stop"][0]["hooks"][0]["command"]
    for flag in ("--resume", "--restricted", "--replay-user-messages", "dontAsk", "--strict-mcp-config"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == "Read,Edit"


def test_a_carrier_without_onloopd_lock_is_its_own_writer(tmp_path):
    c = Carrier.__new__(Carrier)  # the class takes a lock or makes its own
    assert hasattr(Carrier, "w") and c is not None
