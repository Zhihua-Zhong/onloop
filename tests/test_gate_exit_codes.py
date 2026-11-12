"""The gate's exit code for each refusal code, pinned: where its decisions are recorded changed (decision 0009, the
ledger in place of onloop.db); what it decides, and how it exits, did not."""

import copy

import pytest

from onloop import gate

from .conftest import review
from .test_gate import _world


def _strict(raw, **enforce):
    out = copy.deepcopy(raw)
    out["enforce"] = enforce
    return out


# code -> (policy change, changed files, facts overrides, extra refusals, lines gained a carriage return)
CASES = {
    "G010": (None, ["src/a.py"], {"state": "CLOSED"}, None, None),
    "G011": (None, ["src/a.py"], {"draft": True}, None, None),
    "G012": (None, ["src/a.py"], {"base": "other"}, None, None),
    "G020": (
        None,
        ["src/a.py"],
        {"reviews": [review(), review(state="CHANGES_REQUESTED", id_=2, at="2026-01-02T00:00:00Z")]},
        None,
        None,
    ),
    "G021": (None, ["src/a.py"], {"reviews": []}, None, None),
    "G022": (None, ["docs/claims/x.md"], {"title": "[lead] x"}, None, None),
    "G030": (None, ["src/a.py"], {"checks": []}, None, None),
    "G031": (None, ["src/a.py"], {"checks": [{"name": "ci", "conclusion": "FAILURE"}]}, None, None),
    "G090": (None, ["src/a.py"], {}, ["plugin 'x' failed"], None),
    "G091": (None, ["src/a.py"], {}, [gate.Refusal("G091", "project check 'x' failed")], None),
    "P001": (None, ["src/a.py"], {"title": "fix"}, None, None),
    "P002": (None, ["src/a.py"], {"title": "[ghost] fix"}, None, None),
    "P003": (None, ["docs/copy/x.md"], {"title": "[builder] x"}, None, None),
    "P004": ({"line_endings": "refuse"}, ["src/a.py"], {}, None, ["src/a.py"]),
    "P005": ({"uncovered_paths": "refuse"}, ["src/a.py", "stray.txt"], {}, None, None),
}


def test_every_refusal_code_has_a_case():
    assert set(CASES) == set(gate.CODES)


@pytest.mark.parametrize("code", sorted(CASES))
def test_each_refusal_code_exits_1_and_is_recorded_with_its_code(raw, code):
    enforce, files, facts, extra, gained = CASES[code]
    git, client = _world(_strict(raw, **enforce) if enforce else raw, files, **facts)
    if gained is not None:
        git.gained = gained
    got = []
    exit_code, notes = gate.run(
        7,
        client,
        git,
        extra=(lambda *_: extra) if extra else None,
        record=lambda kind, number, payload: got.append((kind, payload)),
    )
    assert exit_code == 1 and notes
    assert [k for k, _ in got] == ["gate.refused"] and code in got[0][1]["codes"]


def test_a_clean_pull_request_still_exits_0_and_records_a_merge(raw):
    git, client = _world(raw, ["src/a.py"])
    got = []
    assert gate.run(7, client, git, record=lambda kind, number, payload: got.append(kind))[0] == 0
    assert got == ["gate.merged"]
