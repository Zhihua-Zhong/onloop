"""The GitHub ingest into the overlay ledger: intake's events, under intake's ids, with its state in the ledger."""

import copy

import pytest

from onloop import policy as pol
from onloop.ingest import github as ingest
from onloop.overlay import importer
from onloop.overlay.ledger import Ledger
from onloop.testing import FakeGit, ScriptedGitHub

from . import store_intake
from .conftest import BASE
from .legacy_poll_store import Store
from .test_intake import H1, H2, Clock, pr, review, step

BODY = "Adds the parser.\n\nPost-merge: operator: publish the package to the registry\n"


def story():
    """A pull request opened, reviewed, pushed again, checked green, closed by a merge; a second one closed."""
    ci = [{"name": "ci", "conclusion": "SUCCESS", "id": 9}]
    return [
        step(pr(1, draft=True), pr(2, head="a" * 40)),
        step(pr(1, updated_at="2026-10-12T00:05:00Z"), pr(2, head="a" * 40)),
        step(
            pr(1, reviews=[review()], updated_at="2026-10-12T00:06:00Z"),
            pr(2, head="a" * 40),
        ),
        step(pr(1, head=H2, checks=ci, updated_at="2026-10-12T00:07:00Z"), pr(2, head="a" * 40)),
        step(
            pr(1, head=H2, checks=ci, updated_at="2026-10-12T00:07:00Z"), pr(2, head="a" * 40), main="n" * 40
        ),
        step(
            pr(
                1,
                head=H2,
                checks=ci,
                state="MERGED",
                body=BODY,
                merge_sha="c" * 40,
                merged_at="2026-10-12T00:09:00Z",
                merged_by={"login": "agents-app", "type": "Bot"},
            ),
            pr(2, head="a" * 40, state="CLOSED"),
            main="c" * 40,
        ),
    ]


def scripted(steps):
    gh = ScriptedGitHub(copy.deepcopy(steps))
    for f in [f for s in gh.steps for f in s["prs"].values()]:
        f.setdefault("state", "OPEN")
    return gh


@pytest.fixture
def both(tmp_path):
    clock = Clock()
    st = Store(tmp_path / "onloop.db", clock=clock)
    led = Ledger(tmp_path / "ledger.db", clock=clock)
    yield st, led, clock
    st.close()
    led.close()


def run_both(st, led, clock, steps, policy):
    a, b = scripted(steps), scripted(steps)
    git = FakeGit()
    git.patch_ids = {}
    for _ in steps:
        r1 = store_intake.tick(st, "o/r", a, git, policy=policy)
        r2 = ingest.tick(led, "o/r", b, git, policy=policy)
        assert r1.appended == r2.appended and r1.not_modified == r2.not_modified
        a.advance()
        b.advance()
        clock.minutes(1)
    return a, b


def test_the_ledger_ingest_gives_intakes_events_under_intakes_ids(both):
    st, led, clock = both
    run_both(st, led, clock, story(), pol.from_dict(BASE))
    legacy = [(e["id"], e["kind"], e["subject"]) for e in st.events()]
    direct = [(e["id"], e["kind"], e["subject_ref"]) for e in led.events_after(0, 1000)]
    assert direct == legacy
    kinds = [k for _, k, _ in direct]
    for k in ("pr.opened", "pr.ready", "review.submitted", "pr.head_moved", "check.completed", "pr.merged"):
        assert k in kinds, kinds
    assert {"pr.closed", "main.advanced"} <= set(kinds)
    assert all(e["source"] == "github" for e in led.events_after(0, 1000))


def test_a_merge_keeps_the_body_for_the_post_merge_steps(both):
    st, led, clock = both
    run_both(st, led, clock, story(), None)
    merged = [e for e in led.events_after(0, 1000) if e["kind"] == "pr.merged"]
    assert len(merged) == 1 and merged[0]["payload"]["body"] == BODY
    assert merged[0]["payload"]["repo"] == "o/r" and merged[0]["at"] == "2026-10-12T00:09:00.000Z"


def test_the_legacy_import_and_the_direct_ingest_deduplicate(both, tmp_path):
    st, led, clock = both
    run_both(st, led, clock, story(), None)
    before = led.last_seq()
    got = importer.import_poll_store(led, tmp_path / "onloop.db")
    assert led.last_seq() == before, got  # every event the poll loop wrote is already in the ledger


def test_the_state_lives_in_the_ledger_and_a_304_costs_no_event(tmp_path):
    clock = Clock()
    led = Ledger(tmp_path / "ledger.db", clock=clock)
    steps = [step(pr(1)), step(pr(1))]
    gh = scripted(steps)
    git = FakeGit()
    git.patch_ids = {}
    ingest.tick(led, "o/r", gh, git)
    gh.advance()
    state = led.ingest_state("github:o/r")
    assert set(state) == {"snap:1", "settle:1", "main_head", "etag"} and state["snap:1"]["head"] == H1
    seq = led.last_seq()
    gh.calls.clear()
    res = ingest.tick(led, "o/r", gh, git)
    assert res.not_modified and led.last_seq() == seq
    assert [c[0] for c in gh.calls][0] == "open_pr_list"
    led.close()


def test_a_failed_read_appends_nothing(tmp_path):
    class Down(ScriptedGitHub):
        def pr(self, number):
            raise RuntimeError("gh api failed: HTTP 502")

    led = Ledger(tmp_path / "ledger.db", clock=Clock())
    git = FakeGit()
    git.patch_ids = {}
    with pytest.raises(RuntimeError):
        ingest.tick(led, "o/r", Down([step(pr(1))]), git)
    assert led.last_seq() == 0 and led.ingest_state("github:o/r") == {}
    led.close()


def test_replay_does_not_touch_the_ingest_state(both):
    st, led, clock = both
    run_both(st, led, clock, story(), None)
    state = led.ingest_state("github:o/r")
    assert led.rebuild_matches()[0]
    assert led.ingest_state("github:o/r") == state


def test_onloopd_runs_the_ingest_on_its_tick_when_it_is_due(tmp_path):
    from onloop.daemon import config as cfg_mod
    from onloop.daemon import main as daemon

    watch = cfg_mod.parse({"github": {"repo": "o/r", "every": "1m"}}).github
    assert (watch.repo, watch.base, watch.every_s, watch.workdir) == ("o/r", "main", 60, None)
    led = Ledger(tmp_path / "ledger.db", clock=Clock())
    src = daemon.GitHubSource(watch, client=scripted([step(pr(1))]))
    cfg = daemon.DaemonConfig(ledger=tmp_path / "ledger.db")
    first = daemon.tick(led, cfg, (src,))
    assert first.errors == [] and first.imported == [
        {"source": "github:o/r", "appended": 1, "not_modified": False}
    ]
    assert daemon.tick(led, cfg, (src,)).imported == []  # not due again for a minute
    assert [e["kind"] for e in led.events_after(0, 10)] == ["pr.opened"]
    opened = led.events_after(0, 10)[0]["payload"]
    assert opened["head"] == H1
    led.close()


def test_a_failing_ingest_is_reported_on_the_tick_not_fatal(tmp_path):
    from onloop.daemon import config as cfg_mod
    from onloop.daemon import main as daemon

    class Down(ScriptedGitHub):
        def open_pr_list(self, etag=None):
            raise RuntimeError("gh: not logged in")

    led = Ledger(tmp_path / "ledger.db", clock=Clock())
    src = daemon.GitHubSource(cfg_mod.parse({"github": {"repo": "o/r"}}).github, client=Down([]))
    res = daemon.tick(led, daemon.DaemonConfig(ledger=tmp_path / "ledger.db"), (src,))
    assert res.errors == ["github:o/r: RuntimeError: gh: not logged in"] and led.last_seq() == 0
    led.close()


@pytest.mark.parametrize(
    ("raw", "problem"),
    [
        ({"repo": "just-a-name"}, "github.repo must be owner/name"),
        ({"repo": "o/r", "every": "10s"}, "at least 30s"),
        ({"repo": "o/r", "branch": "main"}, "unknown key 'branch'"),
        ({"repo": "o/r", "workdir": ""}, "github.workdir must be a path"),
        ("o/r", "github must be a mapping"),
    ],
)
def test_a_github_block_with_a_problem_is_refused(raw, problem):
    from onloop.daemon import config as cfg_mod

    with pytest.raises(cfg_mod.ConfigError) as e:
        cfg_mod.parse({"github": raw})
    assert any(problem in p for p in e.value.problems), e.value.problems


def test_a_failure_while_writing_leaves_neither_events_nor_state(tmp_path, monkeypatch):
    led = Ledger(tmp_path / "ledger.db", clock=Clock())
    git = FakeGit()
    git.patch_ids = {}

    def broken(*_a, **_kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(led, "put_ingest_state", broken)
    with pytest.raises(RuntimeError):
        ingest.tick(led, "o/r", scripted([step(pr(1))]), git)
    assert led.last_seq() == 0 and led.conn.execute("SELECT COUNT(*) FROM ingest_state").fetchone()[0] == 0
    led.close()
