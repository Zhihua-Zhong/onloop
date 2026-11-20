"""A step's products as of a moment (decision 0019, a4): the files its commits changed, read from git in a repository
onloop recorded, never anything else; a product's HTML is served in its own sandbox, never with onloopd's origin."""

import json
import os
import subprocess
import threading
import urllib.error
import urllib.request

import pytest

from onloop.daemon import server as srv
from onloop.overlay.ledger import Ledger
from onloop.report import products
from onloop.report.views import QueryError

from .test_timeline import Clock, at

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000")


def repo_with(path, commits):
    """commits: (hh:mm, node, {path: bytes or None to delete})"""
    path.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t",
    }

    def git(*a, when=None):
        e = dict(env)
        if when:
            e["GIT_AUTHOR_DATE"] = e["GIT_COMMITTER_DATE"] = when
        return subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True, env=e).stdout

    git("init", "-q", "-b", "work")
    shas = []
    for hm, node, files in commits:
        for name, data in files.items():
            if data is None:
                git("rm", "-q", name)
            else:
                (path / name).parent.mkdir(parents=True, exist_ok=True)
                (path / name).write_bytes(data)
                git("add", name)
        git(
            "commit",
            "-q",
            "-m",
            f"change\n\nNode: {node}" if node else "change",
            when=at(hm).replace(".000Z", "+00:00"),
        )
        shas.append(git("rev-parse", "HEAD").decode().strip())
    return path, shas


@pytest.fixture
def setup(tmp_path):
    repo, shas = repo_with(
        tmp_path / "repo",
        [
            (
                "10:10",
                "a1",
                {"page.html": b"<h1>v1</h1><script>alert(1)</script>", "notes.md": b"# Notes\n\nfirst"},
            ),
            ("10:40", "a1", {"notes.md": b"# Notes\n\nsecond", "img/pic.png": PNG}),
            ("10:50", "a2", {"page.html": None, "other.txt": b"x"}),
        ],
    )
    clock = Clock()
    led = Ledger(tmp_path / "l.db", clock=clock)
    clock.t = at("10:00")
    for kind, subject in (("session.opened", "session:s1"), ("step.started", "step:branch:work")):
        led.append(
            {
                "id": kind,
                "source": "cli",
                "kind": kind,
                "subject_ref": subject,
                "actor": "builder",
                "payload": {"session_id": "s1", "role": "builder", "repo": str(repo), "branch": "work"},
            }
        )
    clock.t = at("10:45")
    led.append(
        {
            "id": "end",
            "source": "cli",
            "kind": "step.ended",
            "subject_ref": "step:branch:work",
            "actor": "builder",
            "payload": {"session_id": "s1", "role": "builder"},
        }
    )
    yield led, repo, shas
    led.close()


def test_a_node_steps_products_are_what_its_commits_changed_as_of_the_cursor(setup):
    led, _repo, shas = setup
    early = products.products(led, "step:node:a1", at("10:20"))
    assert [(f["path"], f["sha"]) for f in early["files"]] == [("notes.md", shas[0]), ("page.html", shas[0])]
    later = products.products(led, "step:node:a1", at("10:55"))
    got = {f["path"]: (f["sha"], f["type"], f["deleted"]) for f in later["files"]}
    assert got == {
        "img/pic.png": (shas[1], "image/png", False),
        "notes.md": (shas[1], "text/markdown; charset=utf-8", False),
        "page.html": (shas[0], "text/html; charset=utf-8", False),
    }
    assert [c["sha"] for c in later["commits"]] == shas[:2] and later["repos"] == [
        "repo"
    ]  # a name, never a path
    assert (
        products.products(led, "step:node:a2", at("10:45"))["files"] == []
    )  # its commit is after the cursor


def test_a_branch_steps_products_are_its_branchs_commits_while_it_was_open(setup):
    led, _repo, shas = setup
    p = products.products(led, "step:branch:work", at("11:00"))
    assert [c["sha"] for c in p["commits"]] == shas[:2]  # the 10:50 commit came after the step ended at 10:45


def test_a_file_is_read_at_its_commit_only_from_a_recorded_repository(setup):
    led, _repo, shas = setup
    data, ctype = products.file(led, "0", shas[0], "notes.md")
    assert data == b"# Notes\n\nfirst" and ctype.startswith("text/markdown")
    with pytest.raises(LookupError):
        products.file(led, "1", shas[0], "notes.md")  # no second repository was recorded
    with pytest.raises(LookupError):
        products.file(led, "0", shas[0], "img/pic.png")  # not in that commit yet
    with pytest.raises(LookupError):
        products.file(led, "0", shas[0], "../l.db")
    for bad in (
        ("0", "HEAD", "notes.md"),
        ("0", shas[0], "/etc/hosts"),
        ("0", shas[0], "-p"),
        ("x", shas[0], "notes.md"),
    ):
        with pytest.raises(QueryError):
            products.file(led, *bad)
    with pytest.raises(QueryError):
        products.products(led, "step:task:t-1", at("10:30"))


def test_onloopd_serves_a_products_html_in_its_own_sandbox(setup):
    led, _repo, shas = setup
    s = srv.make_server(led, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        base = f"http://{srv.HOST}:{s.server_address[1]}"
        with urllib.request.urlopen(
            f"{base}/api/products?step=step:node:a1&at=2026-11-02T10:55:00Z", timeout=10
        ) as r:
            assert len(json.loads(r.read())["files"]) == 3
        with urllib.request.urlopen(
            f"{base}/api/products/file?repo=0&sha={shas[0]}&path=page.html", timeout=10
        ) as r:
            csp = r.headers["Content-Security-Policy"]
            assert r.headers["Content-Type"].startswith("text/html") and r.read().startswith(b"<h1>v1</h1>")
        assert (
            csp.split(";")[0].strip() == "sandbox"
            and "allow-same-origin" not in csp
            and "allow-scripts" not in csp
        )
        assert "frame-ancestors 'self'" in csp
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{base}/api/products/file?repo=0&sha={shas[0]}&path=nope.txt", timeout=10)
        assert e.value.code == 404
    finally:
        s.shutdown()
        s.server_close()
