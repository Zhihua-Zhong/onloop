"""The GitHub Issues adapter over recorded JSON: the mapping, the parent rules, read-only calls, and an idempotent sync."""

import copy
import json
import pathlib
import subprocess

import pytest

from onloop.supervisor import github_issues as gi

FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "workgraph_issues.json").read_text(encoding="utf-8")
)

CONFIG = {
    "type": "github-issues",
    "repo": "owner-a/example",
    "labels": {
        "role_prefix": "role/",
        "kind": {"kind/topic": "topic", "kind/decision": "decision"},
        "state": {"state/waiting-human": "waiting-human", "state/blocked": "blocked"},
        "class": {"class/expedite": "expedite", "class/background": "background"},
        "weight": {"prio/now": 3, "prio/next": 2, "prio/later": 1},
    },
    "parent": {
        "sub_issues": True,
        "body_patterns": [r"(?im)^\s*Parent:\s*#(\d+)", r"(?i)\bparent topic #(\d+)"],
    },
    "depends_on": {"dependencies": True, "body_patterns": [r"(?im)^\s*Blocked by:[^\n]*"]},
}


class Recorded:
    """Answers `gh api` from the fixture, records every argv, and refuses anything that is not a GET."""

    def __init__(self, fixture):
        self.fx, self.calls = fixture, []

    def __call__(self, argv):
        self.calls.append(argv)
        assert argv[:4] == ["gh", "api", "--method", "GET"], argv
        path = argv[-1]
        if "/sub_issues" in path:
            out = json.dumps(self.fx["sub_issues"][path.split("/issues/")[1].split("/")[0]])
        elif "/dependencies/blocked_by" in path:
            out = json.dumps(self.fx["blocked_by"][path.split("/issues/")[1].split("/")[0]])
        else:
            out = "".join(json.dumps(p) for p in self.fx["pages"])  # --paginate prints pages back to back
        return subprocess.CompletedProcess(argv, 0, out, "")


@pytest.fixture
def cfg():
    return gi.SourceConfig.from_dict(copy.deepcopy(CONFIG))


def by_id(nodes):
    return {n["id"]: n for n in nodes}


def test_the_mapping_reads_kind_role_state_class_and_weight_from_the_labels(cfg):
    n = by_id(gi.observe(cfg, Recorded(FIXTURE)))
    assert n["#1"]["kind"] == "topic" and n["#1"]["class"] == "expedite" and n["#1"]["weight"] == 3
    assert n["#1"]["owner"] == "lead" and n["#1"]["url"] == "https://github.com/owner-a/example/issues/1"
    assert n["#2"]["kind"] == "task" and n["#2"]["class"] == "standard" and n["#2"]["weight"] == 2
    assert n["#4"]["kind"] == "decision" and n["#4"]["state"] == "waiting-human"
    assert n["#7"]["state"] == "blocked"
    assert n["#6"]["state"] == "cancelled"  # closed as not planned
    assert (
        n["#2"]["created"] == "2026-03-01T09:00:00.000Z" and n["#2"]["updated"] == "2026-03-02T09:00:00.000Z"
    )


def test_pull_requests_are_not_nodes(cfg):
    assert "#9" not in by_id(gi.observe(cfg, Recorded(FIXTURE)))


def test_parents_come_from_sub_issues_first_then_from_the_body(cfg):
    n = by_id(gi.observe(cfg, Recorded(FIXTURE)))
    assert n["#2"]["spawned_by"] == "#1"  # "Parent: #1" line
    assert n["#3"]["spawned_by"] == "#1"  # "parent topic #1" phrase
    assert n["#7"]["spawned_by"] == "#5"
    assert n["#8"]["spawned_by"] == "#1"  # a sub-issue of #1, although its body says #5
    assert n["#1"]["spawned_by"] is None


def test_dependencies_come_from_github_and_from_the_body(cfg):
    n = by_id(gi.observe(cfg, Recorded(FIXTURE)))
    assert n["#3"]["depends_on"] == ["#2", "#9"]


def test_default_and_expiry_are_read_from_the_body(cfg):
    n = by_id(gi.observe(cfg, Recorded(FIXTURE)))
    assert n["#4"]["default"] == "no purchase; builds stay slow"
    assert n["#4"]["expires"] == "2026-03-20 (end of the quarter)"
    assert n["#2"]["default"] is None


def test_only_issues_that_have_sub_issues_or_dependencies_cost_a_call_and_every_call_is_a_get(cfg):
    rec = Recorded(FIXTURE)
    gi.observe(cfg, rec)
    paths = [c[-1] for c in rec.calls]
    assert paths == [
        "repos/owner-a/example/issues?state=all&per_page=100",
        "repos/owner-a/example/issues/1/sub_issues?per_page=100",
        "repos/owner-a/example/issues/3/dependencies/blocked_by?per_page=100",
    ]


def test_a_failed_read_raises_so_a_partial_picture_is_never_synced(cfg, tmp_path):
    def broken(argv):
        return subprocess.CompletedProcess(argv, 1, "", "HTTP 502")

    with pytest.raises(gi.AdapterError, match="502"):
        gi.observe(cfg, broken)


def test_the_configuration_refuses_words_outside_the_vocabulary():
    with pytest.raises(gi.AdapterError, match="kind"):
        gi.SourceConfig.from_dict({**CONFIG, "labels": {"kind": {"type/epic": "epic"}}})
    with pytest.raises(gi.AdapterError, match="OWNER/NAME"):
        gi.SourceConfig.from_dict({**CONFIG, "repo": "not a repo"})
    with pytest.raises(gi.AdapterError, match="regular expression"):
        gi.SourceConfig.from_dict({**CONFIG, "parent": {"body_patterns": ["(unclosed"]}})
    with pytest.raises(gi.AdapterError, match="unknown source keys"):
        gi.SourceConfig.from_dict({**CONFIG, "labelz": {}})


def test_a_url_that_is_not_https_is_not_kept(cfg):
    issue = copy.deepcopy(FIXTURE["pages"][0][1])
    issue["html_url"] = "javascript:alert(1)"
    assert gi.node_of(cfg, issue)["url"] is None
