import copy
import datetime as dt

import yaml

from onloop import ledger, watchdog

GOOD = {"focus": "ship", "workstreams": [{"id": "w1", "title": "t", "owner": "builder", "state": "active",
                                          "since": "2026-01-01"}],
        "decisions": [{"id": "d1", "title": "t", "size": "small", "status": "pending", "recommendation": "yes"}],
        "queues": {"builder": [{"id": "q1", "title": "t", "state": "todo"}, {"id": "q2", "title": "u", "state": "doing"}],
                   "lead": []},
        "changelog": [{"date": "2026-01-01", "who": "lead", "summary": "start"}]}
ROLES = ["builder", "lead"]


def mutated(**kw):
    d = copy.deepcopy(GOOD)
    d.update(kw)
    return d


def test_a_good_ledger_passes_and_each_defect_is_named():
    assert ledger.check(GOOD, ROLES, GOOD) == []
    bad = [
        mutated(focus=""), mutated(workstreams=[dict(GOOD["workstreams"][0], state="x")]),
        mutated(workstreams=[dict(GOOD["workstreams"][0], owner="ghost")]),
        mutated(workstreams=[dict(GOOD["workstreams"][0], since="soon")]),
        mutated(queues={"builder": [{"id": "w1", "title": "t", "state": "todo"}]}),
        mutated(queues={"ghost": []}), mutated(queues={"builder": [{"id": "z", "title": "t", "state": "x"}]}),
        mutated(decisions=[dict(GOOD["decisions"][0], recommendation="")]),
        mutated(decisions=[dict(GOOD["decisions"][0], size="huge")]),
        mutated(changelog=[{"date": "bad", "who": "x", "summary": "y"}]),
    ]
    for ledger_ in bad:
        assert ledger.check(ledger_, ROLES), ledger_
    assert ledger.check("nope") == ["the ledger is not a mapping"]


def test_the_changelog_is_append_only():
    appended = mutated(changelog=GOOD["changelog"] + [{"date": "2026-01-02", "who": "lead", "summary": "more"}])
    assert ledger.check(appended, ROLES, GOOD) == []
    assert ledger.check(mutated(changelog=[]), ROLES, GOOD)
    assert ledger.check(mutated(changelog=[dict(GOOD["changelog"][0], summary="rewritten")]), ROLES, GOOD)
    assert ledger.check(mutated(changelog=[{"date": "2026-01-02", "who": "lead", "summary": "x"}] + GOOD["changelog"]),
                        ROLES, GOOD)


def test_a_role_pulls_the_item_it_is_doing_then_the_first_todo_and_an_empty_queue_says_so():
    assert ledger.next_item(GOOD, "builder")["id"] == "q2"
    assert ledger.next_item(mutated(queues={"builder": [{"id": "a", "state": "todo"}, {"id": "b", "state": "todo"}]}),
                            "builder")["id"] == "a"
    assert ledger.next_item(GOOD, "lead") is None
    assert ledger.next_item(GOOD, "stranger") is None


def test_render_lists_what_the_human_must_decide_and_escapes_html():
    text = ledger.render_markdown(GOOD)
    assert "Waiting for the human" in text and "d1" in text and "Recommendation: yes" in text
    nasty = mutated(focus="<script>x</script>")
    assert "<script>" not in ledger.render_html(nasty)


def test_the_ledger_template_written_by_init_is_valid(tmp_path):
    from onloop.init import init
    init(tmp_path, human="owner-a", reviewer="rev-bot", agents="agent-bot")
    data = ledger.load(tmp_path / "ledger.yaml")
    roles = list(yaml.safe_load((tmp_path / "onloop.yaml").read_text(encoding="utf-8"))["roles"])
    assert ledger.check(data, roles) == []


NOW = dt.datetime(2026, 1, 20, tzinfo=dt.UTC)


def test_the_watchdog_flags_stuck_items_idle_roles_and_unreviewed_pull_requests():
    stuck = {"workstreams": [{"id": "w1", "state": "blocked", "since": "2026-01-10"},
                             {"id": "w2", "state": "active", "since": "2025-01-01"}],
             "queues": {"builder": [{"id": "q1", "state": "doing", "since": "2026-01-19"}], "lead": [],
                        "ops": [{"id": "q9", "state": "doing", "since": "2026-01-01"}]}}
    prs = [{"number": 5, "state": "OPEN", "head": "a" * 40, "head_pushed_at": "2026-01-10T00:00:00Z", "reviews": []},
           {"number": 6, "state": "OPEN", "head": "b" * 40, "head_pushed_at": "2026-01-10T00:00:00Z",
            "reviews": [{"author": {"login": "rev-bot"}, "commit": "b" * 40, "state": "APPROVED"}]},
           {"number": 8, "state": "OPEN", "draft": True, "head": "c" * 40, "head_pushed_at": "2026-01-01T00:00:00Z",
            "reviews": []}]
    got = watchdog.scan(stuck, prs, NOW, reviewer="rev-bot")
    text = "\n".join(got)
    assert "workstream w1 has been blocked" in text and "lead is idle" in text and "#5 has waited" in text
    assert "ops: item q9" in text
    assert "w2" not in text and "#6" not in text and "#8" not in text and "builder: item" not in text


def test_a_verdict_on_an_older_head_does_not_stop_the_clock():
    prs = [{"number": 5, "state": "OPEN", "head": "a" * 40, "head_pushed_at": "2026-01-10T00:00:00Z",
            "reviews": [{"author": {"login": "rev-bot"}, "commit": "z" * 40, "state": "APPROVED"}]}]
    assert any("#5" in f for f in watchdog.scan({"queues": {}}, prs, NOW, reviewer="rev-bot"))


def test_the_ledger_sla_overrides_the_default():
    data = {"sla": {"doing": 30}, "queues": {"builder": [{"id": "q", "state": "doing", "since": "2026-01-01"}]}}
    assert watchdog.scan(data, [], NOW) == []


def test_the_self_tests_bite():
    assert ledger.self_test()[0] and watchdog.self_test()[0]
