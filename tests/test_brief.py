"""The smart layer (0.7.0 plan, section 3; src/onloop/brief.py): text written over computed facts, checked, cached in
the ledger by the facts' hash, capped, and the facts alone whenever it fails. No test calls the model: a fake runner
answers with the recorded answers (tests/fixtures/brief, real `claude -p` output over the generic demo's facts)."""

import importlib.util
import json
import pathlib
import sys

import pytest

from onloop import brief, decisions, tasks
from onloop import events as ev
from onloop.overlay.ledger import Ledger

NOW = "2026-11-09T14:52:00.000Z"
HERE = pathlib.Path(__file__).resolve().parent
REC = HERE / "fixtures" / "brief"
_spec = importlib.util.spec_from_file_location("demo_ledger", HERE.parent / "tools" / "demo_ledger.py")
demo = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("demo_ledger", demo)
_spec.loader.exec_module(demo)


def recorded(kind):
    return json.loads((REC / f"{kind}.json").read_text(encoding="utf-8"))["output"]


@pytest.fixture(scope="module")
def demo_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("brief") / "ledger.db"
    demo.build(path, NOW)
    return path


@pytest.fixture
def led(demo_path, tmp_path):
    path = tmp_path / "ledger.db"
    path.write_bytes(demo_path.read_bytes())
    clock = [NOW]
    with Ledger(path, clock=lambda: clock[0]) as led:
        led.clock = clock
        yield led


class Fake:
    """A `claude -p` that answers each kind with `answers[kind]` (a dict is the JSON text; an exception is raised)."""

    def __init__(self, answers=None, code=0):
        self.answers = answers or {k: recorded(k) for k in brief.KINDS}
        self.code = code
        self.calls = []

    def __call__(self, argv, stdin, env, cwd, timeout):
        self.calls.append(
            {
                "argv": argv,
                "stdin": stdin,
                "env": env,
                "cwd": cwd,
                "timeout": timeout,
                "cwd_empty": not list(pathlib.Path(cwd).iterdir()),
            }
        )
        kind = next(k for k in brief.KINDS if brief._ASK[k] in stdin)
        a = self.answers[kind]
        if isinstance(a, Exception):
            raise a
        wrapper = {
            "type": "result",
            "result": json.dumps(a),
            "usage": {"input_tokens": 2, "output_tokens": 9},
        }
        return self.code, json.dumps(wrapper), ""


def test_the_recorded_answers_pass_the_checks_on_the_demo(led):
    f = brief.facts(led, NOW)
    for kind in brief.KINDS:
        assert brief.check(kind, recorded(kind), f) == [], kind


def test_one_pass_writes_each_kind_once_then_reads_it_from_the_ledger(led):
    fake = Fake()
    assert brief.refresh(led, NOW, run=fake) == {k: "generated" for k in brief.KINDS}
    assert len(fake.calls) == 3
    assert brief.refresh(led, NOW, run=fake) == {k: "cached" for k in brief.KINDS}
    assert len(fake.calls) == 3  # the cache is the ledger, keyed by the facts' hash
    pages = brief.for_pages(led, NOW)
    assert pages["brief"]["text"] == recorded("brief") and not pages["brief"]["stale"]
    assert pages["merges"]["text"]["merges"][0]["merge"] == "t-demo-dcolours"


def test_the_call_is_headless_on_the_subscription_with_the_prompt_on_stdin(led, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    fake = Fake()
    brief.refresh(led, NOW, kinds=("brief",), run=fake, claude=("claude.cmd",))
    call = fake.calls[0]
    assert call["argv"][1:] == ["-p", "--output-format", "json", "--tools", "", "--model", "sonnet"]
    assert "ANTHROPIC_API_KEY" not in call["env"]
    assert (
        "Facts:" in call["stdin"] and "t-demo0001" in call["stdin"]
    )  # cmd.exe would cut a multi-line argument
    assert call["timeout"] == brief.TIMEOUT_S and call["cwd_empty"]  # an empty folder, removed after


def test_a_number_not_in_the_facts_is_rejected_and_the_page_keeps_the_facts(led):
    bad = {"sentences": [{"text": "12 decisions wait on you, up 40% on last week.", "about": None}]}
    fake = Fake({**{k: recorded(k) for k in brief.KINDS}, "brief": bad})
    assert brief.refresh(led, NOW, kinds=("brief",), run=fake) == {"brief": "rejected"}
    why = led.select("SELECT payload FROM events WHERE kind = 'text.rejected'")[0]["payload"]
    assert "not in the facts" in json.dumps(why)
    assert brief.for_pages(led, NOW)["brief"]["text"] is None


def test_a_suggestion_must_be_one_of_the_recorded_options(led):
    out = recorded("decisions")
    out = {
        "decisions": [
            {**d, "suggested": "Ship it next month"} if d["suggested"] else d for d in out["decisions"]
        ]
    }
    problems = brief.check("decisions", out, brief.facts(led, NOW))
    assert problems and all("is not one of its options" in p for p in problems)


def test_a_merge_names_two_open_decisions(led):
    f = brief.facts(led, NOW)
    assert brief.check("merges", {"merges": [{"keep": "t-demo0004", "merge": "t-nope", "why": "same"}]}, f)
    assert brief.check(
        "merges", {"merges": [{"keep": "t-demo0004", "merge": "t-demo0004", "why": "same"}]}, f
    )


@pytest.mark.parametrize(
    "answer, code",
    [
        (FileNotFoundError("claude"), 0),  # no CLI
        (TimeoutError("120 s"), 0),  # subprocess.TimeoutExpired, or any spawn that failed
        ({"sentences": []}, 1),  # the CLI exited non-zero (not signed in, over the limit)
    ],
)
def test_a_failed_call_is_logged_debounced_and_leaves_the_facts(led, answer, code):
    fake = Fake({**{k: recorded(k) for k in brief.KINDS}, "brief": answer}, code=code)
    assert brief.refresh(led, NOW, kinds=("brief",), run=fake) == {"brief": "failed"}
    assert brief.refresh(led, NOW, kinds=("brief",), run=fake) == {"brief": "waiting"}
    assert len(fake.calls) == 1
    later = ev.shift_ts(NOW, brief.DEBOUNCE_S + 1)
    led.clock[0] = later
    assert brief.refresh(led, later, kinds=("brief",), run=fake) == {"brief": "failed"}
    assert brief.for_pages(led, later)["brief"]["text"] is None


def test_calls_are_capped_per_kind_per_day(led, monkeypatch):
    monkeypatch.setattr(brief, "CAPS", {**brief.CAPS, "brief": 2})
    monkeypatch.setattr(brief, "DEBOUNCE_S", 0)
    fake = Fake({**{k: recorded(k) for k in brief.KINDS}, "brief": TimeoutError("slow")})
    t = NOW
    for _ in range(2):
        t = ev.shift_ts(t, 1)
        led.clock[0] = t
        assert brief.refresh(led, t, kinds=("brief",), run=fake) == {"brief": "failed"}
    assert brief.refresh(led, ev.shift_ts(t, 1), kinds=("brief",), run=fake) == {"brief": "capped"}
    assert len(fake.calls) == 2


def test_new_facts_make_the_text_stale_and_the_page_shows_the_facts(led):
    brief.refresh(led, NOW, run=Fake())
    first = decisions.open_decisions(led, NOW)[0][0]
    tasks.handle(
        led,
        "resolve",
        {"id": first["id"], "resolution": "done", "answer": "Approve", "channel": "ui", "by": "human"},
    )
    pages = brief.for_pages(led, NOW)
    assert pages["brief"]["text"] is None and pages["brief"]["stale"] is True
    assert pages["decisions"]["stale"] is True


def test_an_hour_passing_keeps_the_decisions_texts(led):
    brief.refresh(led, NOW, run=Fake())
    later = ev.shift_ts(NOW, 3600)
    assert brief.for_pages(led, later)["decisions"]["text"] is not None  # no ages, no order in its facts


def test_smart_off(monkeypatch):
    assert brief.enabled() is False  # tests/conftest.py: no test calls the model
    monkeypatch.setenv("ONLOOP_SMART", "on")
    assert brief.enabled() is True and brief.enabled(False) is False and brief.enabled("off") is False


def test_smart_is_a_key_of_onloopd_yaml(tmp_path):
    from onloop.daemon import config

    p = tmp_path / "onloopd.yaml"
    p.write_text("smart: off\n", encoding="utf-8")
    assert config.load(p).smart is False
    p.write_text("smart: maybe\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="smart is on or off"):
        config.load(p)


def test_the_merge_a_person_confirms_closes_one_into_the_other(led):
    keep, gone = "t-demo0004", "t-demo-dcolours"
    before = tasks.get(led, keep)["asks"]
    out = tasks.handle(led, "merge", {"keep": keep, "merge": gone, "channel": "ui", "by": "human"})
    assert out["logged"]
    g, k = tasks.get(led, gone), tasks.get(led, keep)
    assert (g["stage"], g["resolution"], g["superseded_by"]) == ("resolved", "superseded", f"task:{keep}")
    assert k["stage"] != "resolved" and k["asks"] == before + 1
    assert decisions.answer_text(g) == "Merged"
    with pytest.raises(tasks.Refused, match="both decisions must be open"):
        tasks.handle(led, "merge", {"keep": keep, "merge": gone, "channel": "ui", "by": "human"})
    with pytest.raises(tasks.Refused, match="two decisions"):
        tasks.handle(led, "merge", {"keep": keep, "merge": keep, "channel": "ui", "by": "human"})


def test_the_demo_replays_its_recorded_texts(tmp_path):
    path = tmp_path / "ledger.db"
    demo.build(path, NOW)
    assert demo.texts(path, NOW) == {k: "logged" for k in brief.KINDS}
    with Ledger(path) as led:
        assert all(v["text"] for v in brief.for_pages(led, NOW).values())


def test_the_decisions_texts_cover_every_open_decision_once(led):
    f = brief.facts(led, NOW)
    out = recorded("decisions")
    assert brief.check("decisions", {"decisions": out["decisions"][:-1]}, f)  # one left out
    assert brief.check("decisions", {"decisions": out["decisions"] + out["decisions"][:1]}, f)  # one twice


def test_a_merge_is_undone_until_the_decision_it_went_into_is_answered(led):
    keep, gone = "t-demo0004", "t-demo-dcolours"
    before = tasks.get(led, keep)["asks"]
    tasks.handle(led, "merge", {"keep": keep, "merge": gone, "channel": "ui", "by": "human"})
    done = {d["id"]: d for d in decisions.answered(led, NOW)}
    assert done[gone]["can_unmerge"] and done[gone]["merged_into"] == keep
    tasks.handle(led, "unmerge", {"id": gone, "channel": "ui", "by": "human"})
    g, k = tasks.get(led, gone), tasks.get(led, keep)
    assert g["stage"] != "resolved" and g["superseded_by"] is None and k["asks"] == before
    # merged again, then the kept decision answered: the merge stands
    tasks.handle(led, "merge", {"keep": keep, "merge": gone, "channel": "ui", "by": "human"})
    tasks.handle(led, "resolve", {"id": keep, "resolution": "done", "channel": "ui", "by": "human"})
    assert not {d["id"]: d for d in decisions.answered(led, NOW)}[gone]["can_unmerge"]
    with pytest.raises(tasks.Refused, match="no merge to undo"):
        tasks.handle(led, "unmerge", {"id": gone, "channel": "ui", "by": "human"})


def test_keep_apart_is_logged_stops_the_proposal_and_is_undone(led):
    brief.refresh(led, NOW, run=Fake())
    assert brief.for_pages(led, NOW)["merges"]["text"]["merges"]
    tasks.handle(
        led, "keep_apart", {"a": "t-demo-dcolours", "b": "t-demo0004", "channel": "ui", "by": "human"}
    )
    assert tasks.kept_apart(led) == {("t-demo-dcolours", "t-demo0004")} or tasks.kept_apart(led) == {
        tuple(sorted(("t-demo-dcolours", "t-demo0004")))
    }
    pages = brief.for_pages(led, NOW)
    assert pages["merges"]["text"] is None and pages["merges"]["stale"]  # gone at once, in every browser
    assert brief.check("merges", recorded("merges"), brief.facts(led, NOW))  # and never proposed again
    tasks.handle(
        led,
        "keep_apart",
        {"a": "t-demo0004", "b": "t-demo-dcolours", "apart": False, "channel": "ui", "by": "human"},
    )
    assert tasks.kept_apart(led) == set()


@pytest.mark.parametrize(
    "text, ok",
    [
        ("Seven decisions wait on you.", True),  # 7 is in the demo's facts
        ("Nineteen decisions wait on you.", False),
        ("It was asked twice.", True),
        ("better than the one before", True),  # "one" is a pronoun here, never checked
    ],
)
def test_number_words_are_checked_like_digits(led, text, ok):
    f = brief.facts(led, NOW)
    out = {"sentences": [{"text": text, "about": None}]}
    assert (brief.check("brief", out, f) == []) is ok


def test_the_facts_carry_ages_in_buckets_so_time_passing_does_not_make_the_brief_stale(led):
    import datetime as dt

    from onloop import localday

    was = localday.zone()
    localday.set_zone(dt.UTC)  # 14:52 to 15:22 UTC: one local day, and no age leaves its bucket
    try:
        a = brief.facts(led, NOW)
        b = brief.facts(led, ev.shift_ts(NOW, 1800))
    finally:
        localday.set_zone(was)
    assert a["decisions"][0]["waiting"] == "hours" and a["waited_hours"]["this_week"].startswith("over ")
    assert brief.facts_hash(a) == brief.facts_hash(b)


@pytest.fixture
def denylist(tmp_path, monkeypatch):
    """A denylist of placeholder patterns: one the generic demo's facts match ("import walk"), and one they do not."""
    path = tmp_path / "denylist.txt"
    path.write_text("# placeholder patterns\nplaceholder-never-[0-9]+\nIMPORT +WALK\n", encoding="utf-8")
    monkeypatch.setattr(brief, "_DENYLIST", [str(path)])
    return path


def test_facts_that_match_the_denylist_never_reach_the_model(led, denylist):
    fake = Fake()
    assert brief.refresh(led, NOW, run=fake) == {k: "rejected" for k in brief.KINDS}
    assert fake.calls == []  # no call at all
    rows = led.select("SELECT payload FROM events WHERE kind = 'text.rejected'")
    payloads = [r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"]) for r in rows]
    assert {tuple(p["reasons"]) for p in payloads} == {("denylist",)} and all(
        p["hits"] == 1 for p in payloads
    )
    assert "import walk" not in json.dumps(payloads).lower()  # the match is never logged
    assert all(v["text"] is None for v in brief.for_pages(led, NOW).values())  # the pages show the facts


def test_a_question_whose_facts_match_the_denylist_is_not_sent(led, denylist, monkeypatch):
    monkeypatch.setenv("ONLOOP_SMART", "on")
    fake = Fake()
    with pytest.raises(brief.Off, match="denylist"):
        brief.ask(led, NOW, "What is waiting on me?", run=fake)
    assert fake.calls == []


def test_a_denylist_that_cannot_be_read_stops_every_call(led, tmp_path, monkeypatch):
    monkeypatch.setattr(brief, "_DENYLIST", [str(tmp_path / "missing.txt")])
    fake = Fake()
    assert brief.refresh(led, NOW, kinds=("brief",), run=fake) == {"brief": "rejected"}
    assert fake.calls == []
    p = led.select("SELECT payload FROM events WHERE kind = 'text.rejected'")[0]["payload"]
    assert "denylist unreadable" in json.dumps(p)


def test_facts_that_pass_the_denylist_are_sent(led, tmp_path, monkeypatch):
    path = tmp_path / "denylist.txt"
    path.write_text("placeholder-never-[0-9]+\n", encoding="utf-8")
    monkeypatch.setattr(brief, "_DENYLIST", [str(path)])
    fake = Fake()
    assert brief.refresh(led, NOW, kinds=("brief",), run=fake) == {"brief": "generated"}
    assert len(fake.calls) == 1


def test_smart_denylist_is_a_key_of_onloopd_yaml(tmp_path):
    from onloop.daemon import config

    p = tmp_path / "onloopd.yaml"
    p.write_text("smart_denylist: C:/somewhere/denylist.txt\n", encoding="utf-8")
    assert config.load(p).smart_denylist == ("C:/somewhere/denylist.txt",)
