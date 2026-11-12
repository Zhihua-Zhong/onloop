"""D4: a post-merge step without evidence. Story 10 replayed: a merge naming a registry step leaves the pull request
not done until the step's evidence is attached."""

import copy

import pytest

from onloop import cli
from onloop import policy as pol
from onloop.ingest import github as ingest
from onloop.overlay.ledger import Ledger
from onloop.testing import FakeGit

from .conftest import BASE
from .test_ingest_github import scripted
from .test_intake import Clock, pr, step

BODY = "Adds the parser.\n\nPost-merge: operator: publish the package to the registry\n"


def merged(body):
    return pr(
        1,
        state="MERGED",
        body=body,
        merge_sha="c" * 40,
        merged_at="2026-10-12T00:09:00Z",
        merged_by={"login": "agents-app", "type": "Bot"},
    )


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db", clock=Clock())
    yield led
    led.close()


def merge(led, body, policy=None):
    gh = scripted([step(pr(1)), step(merged(body))])
    git = FakeGit()
    git.patch_ids = {}
    ingest.tick(led, "o/r", gh, git, policy=policy)
    gh.advance()
    ingest.tick(led, "o/r", gh, git, policy=policy)


def item(led, iid):
    rows = led.select("SELECT * FROM items WHERE id = ?", (iid,))
    return rows[0] if rows else None


def test_story_10_the_merge_is_not_done_until_the_step_has_evidence(ledger, tmp_path, capsys):
    merge(ledger, BODY)
    parent, follow = item(ledger, "pr#1"), item(ledger, "pr#1/post-merge/1")
    assert parent["state"] == "blocked"
    assert (follow["kind"], follow["owner_role"], follow["state"], follow["evidence_required"]) == (
        "followup",
        "operator",
        "open",
        1,
    )
    assert follow["title"] == "publish the package to the registry" and follow["parent_id"] == "pr#1"
    [w] = ledger.waits()
    assert (w["waiter"], w["on_kind"], w["on_ref"], w["reason"]) == (
        "pr#1",
        "role",
        "operator",
        "missing_evidence",
    )
    top = ledger.constraint("2026-10-13T00:00:00.000Z")["constraint"]
    assert top["on_ref"] == "operator" and top["reasons"] == {"missing_evidence": 1}

    path = str(tmp_path / "ledger.db")
    ledger.close()
    assert (
        cli.main(
            ["item", "evidence", "pr#1/post-merge/1", "https://registry.example/pkg/1.2.0", "--ledger", path]
        )
        == 0
    )
    assert "pr#1 is done" in capsys.readouterr().out
    with Ledger(path) as led:
        assert item(led, "pr#1")["state"] == "done" and item(led, "pr#1/post-merge/1")["state"] == "done"
        assert item(led, "pr#1/post-merge/1")["evidence_ref"] == "https://registry.example/pkg/1.2.0"
        assert led.waits() == []
        assert led.rebuild_matches()[0]
    assert (
        cli.main(["item", "evidence", "pr#1/post-merge/1", "again", "--ledger", path]) == 1
    )  # already has evidence


def test_two_steps_need_both(ledger):
    merge(ledger, "Post-merge: operator: publish\nPost-merge: writer: announce the release\n")
    assert [w["on_ref"] for w in ledger.waits()] == ["operator", "writer"]
    ledger.append(
        {
            "id": "ev1",
            "source": "ui",
            "kind": "item.evidence",
            "subject_ref": "pr#1/post-merge/1",
            "payload": {"id": "pr#1/post-merge/1", "evidence_ref": "log#1"},
        }
    )
    assert item(ledger, "pr#1")["state"] == "blocked" and [w["on_ref"] for w in ledger.waits()] == ["writer"]


def test_a_merge_without_steps_is_done(ledger):
    merge(ledger, "Adds the parser.")
    assert item(ledger, "pr#1")["state"] == "done" and ledger.waits() == []


def test_the_pattern_is_configurable_in_the_policy(ledger):
    raw = copy.deepcopy(BASE)
    raw["post_merge"] = {"pattern": r"^- \[ \] (?P<role>[a-z]+) to (?P<step>.+)$"}
    merge(ledger, "## After merge\n- [ ] operator to tag the release\n", policy=pol.from_dict(raw))
    assert item(ledger, "pr#1/post-merge/1")["title"] == "tag the release"


@pytest.mark.parametrize(
    ("block", "problem"),
    [
        ({"pattern": "(unclosed"}, "not a regular expression"),
        ({"pattern": r"^Post-merge: (?P<role>\w+)$"}, "named groups `role` and `step`"),
        ({"regex": "x"}, "unknown field(s): regex"),
        ("Post-merge", "must be a mapping"),
    ],
)
def test_a_pattern_the_policy_cannot_use_is_refused(block, problem):
    assert any(problem in p for p in pol.validate_post_merge(block)), pol.validate_post_merge(block)


def test_the_default_convention():
    body = (
        "Post-merge: operator: publish the package\n"
        "post-merge: operator: lowercase prefix is not the convention\n"
        "Post-merge: Not A Role: skipped\n"
        "  Post-merge: operator: indented is not the convention\n"
        "Post-merge: writer:   announce it   \n"
    )
    assert pol.post_merge_steps(body) == [
        {"role": "operator", "step": "publish the package"},
        {"role": "writer", "step": "announce it"},
    ]
    many = "".join(f"Post-merge: operator: step {n}\n" for n in range(30))
    assert len(pol.post_merge_steps(many)) == pol.POST_MERGE_MAX_STEPS
    assert pol.post_merge_steps(None) == []
