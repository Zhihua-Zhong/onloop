"""`onloop ingest github --once`: one read of any repository into the ledger, by GETs through `gh` only."""

import json
import subprocess

import pytest

from onloop import cli
from onloop import github as gh_mod
from onloop.overlay.ledger import Ledger

WRITES = ("-X", "--method", "-f", "-F", "--field", "--raw-field", "--input")


class FakeGh:
    """Answers the open-pull-request list and the base branch, and records every call. Any write flag fails."""

    def __init__(self, open_prs=None):
        self.calls = []
        self.open_prs = open_prs or []

    def __call__(self, argv):
        self.calls.append(argv)
        assert argv[0] == "gh" and argv[1] == "api", argv
        bad = [a for a in argv if a in WRITES]
        assert not bad, f"a write flag in {argv}"
        path = argv[-1]
        if "pulls?state=open" in path:
            body = json.dumps(self.open_prs)
            out = f'HTTP/2.0 200 OK\r\nEtag: W/"abc"\r\nX-Ratelimit-Remaining: 4999\r\n\r\n{body}'
        elif path.endswith("commits/main"):
            out = json.dumps({"sha": "m" * 40})
        else:
            raise AssertionError(f"unexpected call {argv}")
        return subprocess.CompletedProcess(argv, 0, out, "")


def test_one_read_of_any_repository_by_gets_only(tmp_path, monkeypatch, capsys):
    fake = FakeGh()
    monkeypatch.setattr(gh_mod, "_run", fake)
    path = tmp_path / "ledger.db"
    assert (
        cli.main(["ingest", "github", "--once", "--repo", "some-org/some-repo", "--ledger", str(path)]) == 0
    )
    assert (
        "github:some-org/some-repo: 0 new event(s); list read; 0 pull request(s) known"
        in capsys.readouterr().out
    )
    assert [c[-1] for c in fake.calls] == [
        "repos/some-org/some-repo/pulls?state=open&per_page=100&sort=updated&direction=desc",
        "repos/some-org/some-repo/commits/main",
    ]
    with Ledger(path, readonly=True) as led:
        state = led.ingest_state("github:some-org/some-repo")
    assert state["main_head"] == "m" * 40 and state["etag"] == 'W/"abc"'


def test_a_failed_read_writes_nothing(tmp_path, monkeypatch, capsys):
    def down(argv):
        return subprocess.CompletedProcess(argv, 1, "", "gh: not logged in")

    monkeypatch.setattr(gh_mod, "_run", down)
    path = tmp_path / "ledger.db"
    assert cli.main(["ingest", "github", "--once", "--repo", "o/r", "--ledger", str(path)]) == 1
    assert "nothing was written" in capsys.readouterr().err
    with Ledger(path, readonly=True) as led:
        assert led.last_seq() == 0 and led.ingest_state("github:o/r") == {}


@pytest.mark.parametrize("argv", [["--repo", "not-a-repo"], ["--repo", "o/r", "--base", "a b"]])
def test_a_bad_repository_or_branch_is_refused(tmp_path, argv, capsys):
    assert cli.main(["ingest", "github", "--once", *argv, "--ledger", str(tmp_path / "l.db")]) == 2
