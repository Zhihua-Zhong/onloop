"""One decision, one ask (0.7.0 plan, section 4): asks about one subject collapse into one decision, counted; the
person's answer is one of the recorded options, reaches the asker through its hooks, and stays withdrawable until it
has; an ask already open, or one that says nothing of what it changes, is sent back to the agent once."""

import datetime as dt
import io
import json
import pathlib

import pytest

from onloop import decisions, localday, tasks
from onloop.daemon import hook_client
from onloop.daemon import hook_receiver as hr
from onloop.overlay import human_tasks
from onloop.overlay.ledger import Ledger

FIX = pathlib.Path(__file__).parent / "fixtures" / "hooks"
JST = dt.timezone(dt.timedelta(hours=9), "+09:00")
T0 = "2026-11-16T05:00:00.000Z"


class Clock:
    def __init__(self, ts=T0):
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


def stop(led, line, role="builder", active=False):
    p = json.loads((FIX / "stop.json").read_text(encoding="utf-8"))
    p.update(
        last_assistant_message=f"Some work.\n\n{line}", prompt_id=f"p{next(_n)}", stop_hook_active=active
    )
    return hr.handle(led, "stop", {"role": role, "payload": p})


def prompt(led, role="builder", text="the person's words, never kept"):
    p = {
        "session_id": f"s-{role}",
        "prompt_id": f"q{next(_n)}",
        "prompt": text,
        "hook_event_name": "UserPromptSubmit",
    }
    return hr.handle(led, "prompt-submit", {"role": role, "payload": p})


def open_decisions(led):
    return led._rows("SELECT * FROM human_tasks WHERE stage <> 'resolved' ORDER BY opened_at, id")


ASK = 'outcome: blocked owner=human ref={ref} effect="merge the export changes, so the release can go" holds="the release"'


@pytest.mark.parametrize(
    ("ref", "key"),
    [
        ("pr#12", "pr#12"),
        ("PR#12", "pr#12"),
        ("pr:12", "pr#12"),
        ("#12", "pr#12"),
        ("issue:7", "issue#7"),
        ("feat/export-schema", "export-schema"),
        ("fix/Export-Schema", "export-schema"),
        ("docs/plan.md", "plan.md"),
        ("release-2.4", "release-2.4"),
        ("task:t-0123456789", None),
        (None, None),
        ("", None),
    ],
)
def test_a_subject_is_one_key_however_it_is_written(ref, key):
    assert human_tasks.subject_key(ref) == key


def test_asks_about_one_subject_from_two_roles_are_one_decision_with_its_first_author(led, clock):
    stop(led, ASK.format(ref="pr#12"), role="builder")
    clock.ts = "2026-11-16T06:00:00.000Z"
    stop(led, ASK.format(ref="#12"), role="reviewer")
    clock.ts = "2026-11-16T07:00:00.000Z"
    stop(led, ASK.format(ref="pr:12"), role="lead")
    (d,) = open_decisions(led)
    assert d["author_role"] == "builder" and d["asks"] == 3 and d["subject_key"] == "pr#12"
    log = led._rows("SELECT author_role, new_fact FROM task_asks WHERE task_id = ? ORDER BY at", (d["id"],))
    assert [(r["author_role"], r["new_fact"]) for r in log] == [("builder", 1), ("reviewer", 0), ("lead", 0)]
    v = decisions.view(led, clock.ts)
    assert (
        v["count"] == 1
        and v["open"][0]["asks"] == 3
        and v["open"][0]["askers"] == ["builder", "reviewer", "lead"]
    )
    assert v["asks_per_decision"] == 3.0


def test_a_restated_ask_with_a_new_fact_amends_the_decision_and_is_counted_as_new(led):
    stop(led, ASK.format(ref="pr#12"))
    stop(led, ASK.format(ref="pr#12") + ' default="nothing merges"', role="reviewer")
    (d,) = open_decisions(led)
    assert d["default_text"] == "nothing merges" and d["asks"] == 2
    assert [r["new_fact"] for r in led._rows("SELECT new_fact FROM task_asks ORDER BY at, id")] == [1, 1]


def test_options_are_recorded_and_the_answer_must_be_one_of_them(led):
    stop(led, ASK.format(ref="pr#12") + ' options="Merge both|Merge one|Wait"')
    (d,) = open_decisions(led)
    assert json.loads(d["options"]) == ["Merge both", "Merge one", "Wait"]
    with pytest.raises(tasks.Refused, match="one of the ask's options"):
        tasks.handle(
            led,
            "resolve",
            {"id": d["id"], "resolution": "done", "answer": "Merge all", "channel": "ui", "by": "human"},
        )
    tasks.handle(
        led,
        "resolve",
        {"id": d["id"], "resolution": "done", "answer": "Merge both", "channel": "ui", "by": "human"},
    )
    (r,) = led._rows("SELECT * FROM human_tasks WHERE id = ?", (d["id"],))
    assert r["resolution"] == "done" and r["answer"] == "Merge both" and r["delivered_at"] is None


def test_an_answer_reaches_the_asker_at_its_next_prompt_and_only_once(led, clock):
    stop(led, ASK.format(ref="pr#12") + ' options="Merge both|Wait"')
    (d,) = open_decisions(led)
    tasks.handle(
        led,
        "resolve",
        {"id": d["id"], "resolution": "done", "answer": "Merge both", "channel": "ui", "by": "human"},
    )
    clock.ts = "2026-11-16T06:00:00.000Z"
    told = prompt(led)["hook_output"]["hookSpecificOutput"]
    assert told["hookEventName"] == "UserPromptSubmit"
    assert f"{d['id']} answered by the person: Merge both" in told["additionalContext"]
    (r,) = led._rows("SELECT delivered_at, delivered_by FROM human_tasks WHERE id = ?", (d["id"],))
    assert r["delivered_at"] == clock.ts and r["delivered_by"].startswith("event:")
    assert "hook_output" not in prompt(led)  # nothing new to say
    (e,) = led._rows("SELECT payload FROM events WHERE kind = 'hook.prompt_submit' LIMIT 1")
    payload = e["payload"] if isinstance(e["payload"], dict) else json.loads(e["payload"])
    assert "never kept" not in json.dumps(payload) and payload["prompt_chars"] > 0


def test_the_person_can_withdraw_an_answer_until_it_reaches_the_asker(led, clock):
    stop(led, ASK.format(ref="pr#12"))
    (d,) = open_decisions(led)
    tasks.handle(led, "resolve", {"id": d["id"], "resolution": "declined", "channel": "ui", "by": "human"})
    tasks.handle(led, "withdraw", {"id": d["id"], "channel": "ui", "by": "human"})
    (again,) = open_decisions(led)
    assert again["id"] == d["id"] and again["resolution"] is None and again["answer"] is None
    tasks.handle(led, "resolve", {"id": d["id"], "resolution": "done", "channel": "ui", "by": "human"})
    clock.ts = "2026-11-16T06:00:00.000Z"
    prompt(led)  # it reached the builder
    with pytest.raises(tasks.Refused, match="reached builder"):
        tasks.handle(led, "withdraw", {"id": d["id"], "channel": "ui", "by": "human"})


def test_open_decisions_are_the_askers_context_so_it_does_not_ask_again(led):
    stop(led, ASK.format(ref="pr#12") + ' default="nothing merges"')
    out = hr.handle(
        led, "session-start", {"role": "builder", "payload": {"session_id": "s-b", "source": "startup"}}
    )
    ctx = out["hook_output"]["hookSpecificOutput"]
    assert ctx["hookEventName"] == "SessionStart"
    assert (
        "you have 1 open decision" in ctx["additionalContext"]
        and "nothing merges" in ctx["additionalContext"]
    )
    assert "hook_output" not in hr.handle(
        led, "session-start", {"role": "designer", "payload": {"session_id": "s-d", "source": "startup"}}
    )


def test_an_ask_already_open_is_sent_back_once_and_still_counted(led):
    assert "hook_output" not in stop(led, ASK.format(ref="pr#12"))  # the first ask is simply taken
    again = stop(led, ASK.format(ref="#12"), role="reviewer")
    assert again["hook_output"]["decision"] == "block"
    assert "already open with the person as t-" in again["hook_output"]["reason"]
    assert "hook_output" not in stop(led, ASK.format(ref="#12"), role="reviewer", active=True)  # never a loop
    (d,) = open_decisions(led)
    assert d["asks"] == 3
    new_fact = stop(led, ASK.format(ref="pr#12") + ' default="ship without it"', role="lead")
    assert "hook_output" not in new_fact  # it said something new: an amendment, not a repeat


def test_an_ask_that_says_nothing_of_what_it_changes_is_sent_back_with_what_is_missing(led):
    out = stop(led, "outcome: blocked owner=human ref=docs/plan.md")
    assert out["hook_output"]["decision"] == "block"
    assert 'effect="<do X, so Y>"' in out["hook_output"]["reason"]
    (d,) = open_decisions(led)
    assert d["incomplete"] == 1  # still logged and counted, under Rule alerts
    assert decisions.view(led, T0)["count"] == 0 and decisions.view(led, T0)["rule_asks"] == 1


def test_onloop_ask_needs_an_effect_and_refuses_an_open_subject(led):
    body = {
        "author_role": "lead",
        "ref": "pr:12",
        "ask": {"effect": "merge it, so the release goes"},
        "channel": "cli",
    }
    with pytest.raises(tasks.Refused, match="says what it changes"):
        tasks.handle(led, "ask", {**body, "ask": {}})
    out = tasks.handle(led, "ask", body)
    with pytest.raises(tasks.Refused) as e:
        tasks.handle(led, "ask", {**body, "author_role": "builder", "ref": "#12"})
    assert e.value.status == 409 and out["id"] in str(e.value)


def test_onloop_ask_exits_3_with_the_open_decision(tmp_path, capsys):
    from onloop import cli

    path = tmp_path / "ledger.db"
    args = [
        "ask",
        "--ledger",
        str(path),
        "--from",
        "lead",
        "--ref",
        "pr#12",
        "--effect",
        "merge it, so the release goes",
    ]
    assert cli.main(args) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert cli.main([*args[:4], "builder", "--ref", "#12", "--effect", "merge it"]) == 3
    assert first in capsys.readouterr().err
    assert cli.main(["ask", "--ledger", str(path), "--from", "lead", "--ref", "pr#13"]) == 2  # no effect


def test_the_format_rule_tidy_sets_asks_aside_with_their_reason_and_an_undo(led):
    stop(led, "outcome: blocked owner=human ref=docs/a.md")
    stop(led, "outcome: blocked owner=human ref=docs/b.md", role="lead")
    stop(led, ASK.format(ref="pr#12"))
    ids = [d["id"] for d in open_decisions(led) if d["incomplete"]]
    with pytest.raises(tasks.Refused, match="not an open ask without an effect"):
        tasks.handle(
            led, "tidy", {"ids": [d["id"] for d in open_decisions(led)], "channel": "ui", "by": "human"}
        )
    out = tasks.handle(led, "tidy", {"ids": ids, "channel": "ui", "by": "human"})
    assert out["logged"] == 2
    gone = led._rows("SELECT resolution, note, effect FROM human_tasks WHERE id IN (?, ?)", tuple(ids))
    assert {(g["resolution"], g["note"], g["effect"]) for g in gone} == {("dismissed", tasks.TIDY_NOTE, None)}
    tasks.handle(led, "withdraw", {"id": ids[0], "channel": "ui", "by": "human"})
    assert len(open_decisions(led)) == 2


def test_decisions_mine_lists_a_roles_open_decisions_and_the_answers_to_it(led, clock):
    stop(led, ASK.format(ref="pr#12"))
    stop(led, ASK.format(ref="pr#13"))
    d12, d13 = open_decisions(led)
    stop(led, ASK.format(ref="pr#13"), role="reviewer")  # joins d13
    tasks.handle(led, "resolve", {"id": d12["id"], "resolution": "done", "channel": "ui", "by": "human"})
    mine = decisions.mine(led, "builder", clock.ts)
    assert [d["id"] for d in mine["open"]] == [d13["id"]] and [d["id"] for d in mine["answered"]] == [
        d12["id"]
    ]
    assert [d["id"] for d in decisions.mine(led, "reviewer", clock.ts)["open"]] == [d13["id"]]


def test_the_log_replays_to_the_same_decisions(led, clock):
    stop(led, ASK.format(ref="pr#12") + ' options="A|B"')
    stop(led, ASK.format(ref="#12"), role="reviewer")
    (d,) = open_decisions(led)
    tasks.handle(
        led, "resolve", {"id": d["id"], "resolution": "done", "answer": "B", "channel": "ui", "by": "human"}
    )
    tasks.handle(led, "withdraw", {"id": d["id"], "channel": "ui", "by": "human"})
    tasks.handle(
        led, "resolve", {"id": d["id"], "resolution": "done", "answer": "A", "channel": "ui", "by": "human"}
    )
    clock.ts = "2026-11-16T06:00:00.000Z"
    prompt(led, role="reviewer")
    ok, live, rebuilt = led.rebuild_matches()
    assert ok, (live[:400], rebuilt[:400])


def test_the_hook_prints_what_onloopd_tells_the_agent(tmp_path, monkeypatch):
    """`onloop daemon hook` prints `hook_output` as the hook's JSON output, and nothing when there is none."""
    (tmp_path / "onloopd.heartbeat.json").write_text(json.dumps({"port": 1}), encoding="utf-8")
    monkeypatch.setattr(hook_client.token_mod, "read", lambda state: "secret")

    class Answer:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body

    for body, printed in (
        (
            json.dumps(
                {"logged": True, "hook_output": {"decision": "block", "reason": "already open"}}
            ).encode(),
            True,
        ),
        (json.dumps({"logged": True}).encode(), False),
    ):
        monkeypatch.setattr(hook_client.urllib.request, "urlopen", lambda req, timeout, b=body: Answer(b))
        out = io.StringIO()
        assert hook_client.post("stop", "builder", tmp_path, io.BytesIO(b"{}"), stdout=out) == 0
        assert (
            (json.loads(out.getvalue()) == {"decision": "block", "reason": "already open"})
            if printed
            else out.getvalue() == ""
        )


def test_onloop_open_and_the_carrier_give_the_session_the_three_hooks(tmp_path):
    from onloop import cli

    path = cli.write_open_hooks(tmp_path, tmp_path / "ledger.db", "builder")
    hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == {"Stop", "SessionStart", "UserPromptSubmit"}
    assert "--event prompt-submit --role builder" in hooks["UserPromptSubmit"][0]["hooks"][0]["command"]


def test_a_role_joining_a_decision_adds_what_it_says_and_erases_nothing(led):
    """The first author's weight, holds, default and options stay when a sparser ask from another role joins."""
    stop(led, ASK.format(ref="pr#12") + ' weight=critical default="nothing merges" options="Merge|Wait"')
    stop(
        led,
        'outcome: blocked owner=human ref=#12 effect="merge the export changes, so the release can go"',
        role="reviewer",
    )
    (d,) = open_decisions(led)
    assert (d["weight"], d["holds"], d["default_text"], json.loads(d["options"])) == (
        "critical",
        "the release",
        "nothing merges",
        ["Merge", "Wait"],
    )
    assert d["asks"] == 2 and d["author_role"] == "builder"
