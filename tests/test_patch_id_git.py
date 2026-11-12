"""A head's patch id against its own merge-base, with real git: merging the base is not a substantive change."""

import subprocess

import pytest

from onloop.github import GitHub, GitHubError, parse_http, rate_of
from onloop.gitutil import Git


def sh(cwd, *args):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return p.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    sh(tmp_path, "init", "-q", "-b", "main", ".")
    sh(tmp_path, "config", "user.email", "t")
    sh(tmp_path, "config", "user.name", "t")
    sh(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "a.txt").write_text("one\ntwo\n", encoding="utf-8", newline="\n")
    (tmp_path / "other.txt").write_text("x\n", encoding="utf-8", newline="\n")
    sh(tmp_path, "add", "-A")
    sh(tmp_path, "commit", "-qm", "base")
    return tmp_path


def commit(repo, name, text, msg, newline="\n"):
    (repo / name).write_text(text, encoding="utf-8", newline=newline)
    sh(repo, "add", "-A")
    sh(repo, "commit", "-qm", msg)
    return sh(repo, "rev-parse", "HEAD")


def test_merging_the_base_into_the_branch_keeps_the_patch_id(repo):
    sh(repo, "checkout", "-qb", "feature")
    h1 = commit(repo, "a.txt", "one\ntwo\nthree\n", "feature")
    git = Git(cwd=str(repo))
    first = git.patch_id("main", h1)  # base at its old tip
    sh(repo, "checkout", "-q", "main")
    commit(repo, "other.txt", "x\ny\nz\n", "base moves")  # the base moves between two polls
    sh(repo, "checkout", "-q", "feature")
    sh(repo, "merge", "-q", "--no-edit", "main")
    h2 = sh(repo, "rev-parse", "HEAD")
    assert h2 != h1
    assert git.patch_id("main", h2) == first  # each head against its own merge-base
    assert git.patch_id("main", h1) == first  # and an unmerged head, though the base moved on


def test_a_shared_old_merge_base_would_have_said_substantive(repo):
    """The wrong way, to show the test above can fail: diff the new head against the old merge-base."""
    sh(repo, "checkout", "-qb", "feature")
    h1 = commit(repo, "a.txt", "one\ntwo\nthree\n", "feature")
    old_base = sh(repo, "merge-base", "main", h1)
    sh(repo, "checkout", "-q", "main")
    commit(repo, "other.txt", "x\ny\n", "base moves")
    sh(repo, "checkout", "-q", "feature")
    sh(repo, "merge", "-q", "--no-edit", "main")
    h2 = sh(repo, "rev-parse", "HEAD")
    git = Git(cwd=str(repo))

    def naive(base, head):
        d = subprocess.run(["git", "diff", base, head], cwd=repo, capture_output=True).stdout
        return subprocess.run(
            ["git", "patch-id", "--stable"], cwd=repo, input=d, capture_output=True, text=False
        ).stdout.split()[0]

    assert naive(old_base, h1) != naive(old_base, h2)
    assert git.patch_id("main", h1) != "" and git.patch_id("main", h2) == git.patch_id("main", h1)


def test_a_substantive_push_changes_the_patch_id(repo):
    sh(repo, "checkout", "-qb", "feature")
    h1 = commit(repo, "a.txt", "one\ntwo\nthree\n", "feature")
    h2 = commit(repo, "a.txt", "one\ntwo\nthree\nfour\n", "more")
    git = Git(cwd=str(repo))
    assert git.patch_id("main", h1) != git.patch_id("main", h2)


def test_a_line_ending_rewrite_is_a_different_patch_and_is_left_to_the_reviewer(repo):
    """Line endings change the id (`--verbatim` keeps whitespace). Rewriting every line's ending rewrites every line. Intake records it as substantive;
    telling the reviewer it was only line endings is the delta pane's job."""
    sh(repo, "checkout", "-qb", "feature")
    h1 = commit(repo, "a.txt", "one\ntwo\nthree\n", "feature")
    h2 = commit(repo, "a.txt", "one\ntwo\nthree\n", "crlf", newline="\r\n")
    git = Git(cwd=str(repo))
    assert git.patch_id("main", h2) != git.patch_id("main", h1)


def test_a_dedent_that_changes_what_python_does_is_a_different_patch(repo):
    """`--stable` ignores whitespace, so moving `b = 3` out of an `if` block kept the id. `--verbatim` does not."""
    sh(repo, "checkout", "-qb", "feature")
    inside = "if a:\n    b = 3\n"
    outside = "if a:\nb = 3\n"
    h1 = commit(repo, "a.py", inside, "inside")
    h2 = commit(repo, "a.py", outside, "dedent")
    git = Git(cwd=str(repo))
    base_id = git.patch_id("main", h1)
    assert git.patch_id("main", h2) != base_id
    stable = [
        subprocess.run(
            ["git", "patch-id", "--stable"],
            input=subprocess.run(["git", "diff", "main", h], cwd=repo, capture_output=True).stdout,
            cwd=repo,
            capture_output=True,
        ).stdout.split()[0]
        for h in (h1, h2)
    ]
    assert stable[0] == stable[1]  # --stable was blind to it (the Reviewer's case)


def test_an_old_git_has_no_safe_id_so_the_push_counts_as_substantive(repo, monkeypatch):
    sh(repo, "checkout", "-qb", "feature")
    h1 = commit(repo, "a.txt", "one\ntwo\nthree\n", "feature")
    git = Git(cwd=str(repo))
    monkeypatch.setattr(Git, "supports_verbatim", lambda self: False)
    assert git.patch_id("main", h1) is None


def test_the_version_check_reads_git_version(repo):
    assert Git(cwd=str(repo)).supports_verbatim() is True  # this suite needs a git of 2.39 or newer


def test_a_head_with_no_change_has_the_empty_id(repo):
    git = Git(cwd=str(repo))
    assert git.patch_id("main", "main") == "empty"


# -- the conditional request -----------------------------------------------------------------------------------------

OK = (
    'HTTP/2.0 200 OK\r\nEtag: W/"abc"\r\nX-Ratelimit-Remaining: 4990\r\nX-Ratelimit-Limit: 5000\r\n'
    "X-Ratelimit-Reset: 1790000000\r\n\r\n"
    '[{"number": 7, "updated_at": "2026-10-12T00:00:00Z", "draft": false, "head": {"sha": "'
    + "a" * 40
    + '"}}]'
)
NOT_MODIFIED = 'HTTP/2.0 304 Not Modified\r\nEtag: W/"abc"\r\nX-Ratelimit-Remaining: 4990\r\n\r\n'


class Done:
    def __init__(self, out, code=0, err=""):
        self.stdout, self.returncode, self.stderr = out, code, err


def test_parse_http_reads_status_headers_and_body():
    status, headers, body = parse_http(OK)
    assert status == 200 and headers["etag"] == 'W/"abc"' and body.startswith("[{")
    assert rate_of(headers) == {"remaining": 4990, "limit": 5000, "reset": 1790000000}


def test_the_list_is_read_and_its_etag_kept():
    seen = []

    def runner(argv):
        seen.append(argv)
        return Done(OK)

    res = GitHub("o/r", runner).open_pr_list(None)
    assert res["items"] == [
        {"number": 7, "updated_at": "2026-10-12T00:00:00Z", "head": "a" * 40, "draft": False}
    ]
    assert res["etag"] == 'W/"abc"' and not res["not_modified"] and res["rate"]["remaining"] == 4990
    assert "If-None-Match" not in " ".join(seen[0])


def test_a_304_is_not_modified_even_though_gh_exits_non_zero():
    seen = []

    def runner(argv):
        seen.append(argv)
        return Done(NOT_MODIFIED, code=1, err="gh: HTTP 304")

    res = GitHub("o/r", runner).open_pr_list('W/"abc"')
    assert res["not_modified"] and res["items"] == [] and res["etag"] == 'W/"abc"'
    assert 'If-None-Match: W/"abc"' in seen[0]


def test_a_real_error_is_an_error():
    with pytest.raises(GitHubError):
        GitHub(
            "o/r", lambda argv: Done("HTTP/2.0 500 Server Error\r\n\r\n", code=1, err="boom")
        ).open_pr_list(None)
    with pytest.raises(GitHubError):
        GitHub("o/r", lambda argv: Done("", code=1, err="not logged in")).open_pr_list(None)
