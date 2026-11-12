"""The workbench: what changed between two heads, from real git. Display only: it never decides anything."""

import os
import subprocess

import pytest

from onloop import gitutil
from onloop.gitutil import Git

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t" + "@" + "example.test",
    "GIT_COMMITTER_EMAIL": "t" + "@" + "example.test",
}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "r"
    root.mkdir()

    def git(*args):
        p = subprocess.run(["git", *args], cwd=root, env=ENV, capture_output=True)
        assert p.returncode == 0, p.stderr
        return p.stdout.decode().strip()

    run_git = git
    git("init", "-q")
    git("config", "core.autocrlf", "false")

    class R:
        path = root

        def write(self, name, data):
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))

        def commit(self, msg="c"):
            git("add", "-A")
            git("commit", "-q", "--allow-empty", "-m", msg)
            return git("rev-parse", "HEAD")

        def delta(self, a, b, **kw):
            return Git(cwd=str(root)).delta_basis(a, b, **kw)

        git = staticmethod(run_git)

    return R()


def two(repo, name, before, after):
    repo.write(name, before)
    a = repo.commit("a")
    repo.write(name, after)
    b = repo.commit("b")
    return repo.delta(a, b)


def test_a_push_with_the_same_tree_is_merge_only(repo):
    repo.write("a.txt", "x\n")
    a = repo.commit("a")
    b = repo.commit("message only")
    d = repo.delta(a, b)
    assert d["tree_equal"] is True and d["kind"] == "merge_only" and d["files"] == {}


def test_crlf_only_is_counted_per_file(repo):
    d = two(repo, "a.txt", "one\ntwo\nthree\n", "one\r\ntwo\r\nthree\r\n")
    assert (
        d["kind"] == "crlf_only" and d["files"]["a.txt"]["cr_only"] == 3 and d["files"]["a.txt"]["other"] == 0
    )


def test_indentation_only_in_a_python_file_is_whitespace_only_and_flagged(repo):
    d = two(repo, "m.py", "def f():\n    return 1\n", "def f():\n  return 1\n")
    assert (
        d["kind"] == "whitespace_only"
        and d["files"]["m.py"]["indent_only"] == 1
        and d["indent_sensitive"] == ["m.py"]
    )


def test_indentation_only_in_a_file_where_it_means_nothing_is_not_flagged(repo):
    d = two(repo, "n.json", '{\n    "a": 1\n}\n', '{\n  "a": 1\n}\n')
    assert d["kind"] == "whitespace_only" and d["indent_sensitive"] == []


def test_trailing_whitespace_is_whitespace_only(repo):
    d = two(repo, "a.txt", "one\n", "one   \n")
    assert d["kind"] == "whitespace_only" and d["files"]["a.txt"]["indent_only"] == 0


def test_a_space_removed_inside_a_string_is_substantive(repo):
    d = two(repo, "a.py", "x = 'a b'\n", "x = 'ab'\n")
    assert d["kind"] == "substantive" and d["files"]["a.py"]["other"] == 1


def test_two_spaces_reduced_to_one_inside_a_string_is_substantive(repo):
    d = two(repo, "a.py", "x = 'a  b'\n", "x = 'a b'\n")
    assert d["kind"] == "substantive"


def test_a_space_between_two_tokens_is_substantive(repo):
    assert two(repo, "a.py", "return not x\n", "return notx\n")["kind"] == "substantive"


def test_a_rewritten_line_is_substantive(repo):
    d = two(repo, "a.py", "return 1\n", "return 2\n")
    assert (
        d["kind"] == "substantive" and d["files"]["a.py"]["added"] == 1 and d["files"]["a.py"]["removed"] == 1
    )


def test_a_whitespace_change_beside_a_real_change_is_substantive(repo):
    assert two(repo, "a.py", "a = 1\nb = 2\n", "a =  1\nb = 3\n")["kind"] == "substantive"


def test_a_line_added_is_substantive(repo):
    assert two(repo, "a.txt", "one\n", "one\ntwo\n")["kind"] == "substantive"


def test_a_missing_final_newline_is_never_harmless(repo):
    assert two(repo, "a.txt", "one\ntwo\n", "one\ntwo")["kind"] == "substantive"


def test_a_new_empty_file_is_substantive(repo):
    repo.write("a.txt", "x\n")
    a = repo.commit("a")
    repo.write("empty.txt", "")
    b = repo.commit("b")
    assert repo.delta(a, b)["kind"] == "substantive"


def test_a_mode_change_is_substantive(repo):
    repo.write("run.sh", "echo hi\n")
    a = repo.commit("a")
    repo.git("update-index", "--chmod=+x", "run.sh")
    repo.git("commit", "-q", "-m", "mode")
    b = repo.git("rev-parse", "HEAD")
    assert repo.delta(a, b)["kind"] == "substantive"


def test_a_binary_change_is_substantive(repo):
    assert two(repo, "b.bin", b"\x00\x01\x02", b"\x00\x01\x03")["kind"] == "substantive"


def test_a_whitespace_only_file_next_to_a_real_change_in_another_file_is_substantive(repo):
    repo.write("a.txt", "one\n")
    repo.write("b.py", "x = 1\n")
    a = repo.commit("a")
    repo.write("a.txt", "one  \n")
    repo.write("b.py", "x = 2\n")
    b = repo.commit("b")
    d = repo.delta(a, b)
    assert d["kind"] == "substantive" and d["files"]["a.txt"]["ws_only"] == 1


def test_the_indent_sensitive_list_is_a_parameter(repo):
    repo.write("m.py", "if x:\n    y\n")
    a = repo.commit("a")
    repo.write("m.py", "if x:\n  y\n")
    b = repo.commit("b")
    assert repo.delta(a, b, indent_sensitive=[".yaml"])["indent_sensitive"] == []
    assert repo.delta(a, b, indent_sensitive=["m.py"])["indent_sensitive"] == ["m.py"]


def test_a_makefile_by_name_is_indent_sensitive(repo):
    d = two(repo, "sub/Makefile", "all:\n\techo\n", "all:\n  echo\n")
    assert d["indent_sensitive"] == ["sub/Makefile"]


def test_a_commit_that_is_not_hex_is_refused_before_git_runs(repo):
    with pytest.raises(gitutil.GitError, match="not a commit id"):
        Git(cwd=str(repo.path)).delta_basis("--output=x", "abcdef1")


def test_an_injected_runner_cannot_compute_a_delta():
    with pytest.raises(gitutil.GitError):
        Git(runner=lambda argv: None).delta_basis("abcdef1", "abcdef2")


def test_a_commit_git_does_not_have_raises(repo):
    repo.write("a.txt", "x\n")
    a = repo.commit("a")
    with pytest.raises(gitutil.GitError):
        repo.delta(a, "0" * 40)


# -- parse_delta without git: the line comparison itself -------------------------------------------------------------------------


def parsed(old: bytes, new: bytes):
    raw = b":100644 100644 a b M\0f.py\0"
    diff = b"diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-" + old + b"\n+" + new + b"\n"
    return gitutil.parse_delta(raw, diff)


@pytest.mark.parametrize(
    "old,new,kind",
    [
        (b"a", b"a ", "whitespace_only"),
        (b"  a", b"a", "whitespace_only"),
        (b"a\r", b"a", "crlf_only"),
        (b"a b", b"ab", "substantive"),
        (b"a  b", b"a b", "substantive"),
        (b"a", b"a", "substantive"),
        (b"a\tb", b"a b", "substantive"),
        (b"a\rb", b"ab", "substantive"),
    ],
)
def test_the_line_comparison(old, new, kind):
    assert parsed(old, new)["kind"] == kind


def test_an_unequal_number_of_removed_and_added_lines_is_substantive():
    raw = b":100644 100644 a b M\0f.py\0"
    diff = b"diff --git a/f.py b/f.py\n@@ -1,2 +1 @@\n-a\n-b\n+a\n"
    assert gitutil.parse_delta(raw, diff)["kind"] == "substantive"


def test_a_diff_that_does_not_match_the_raw_listing_is_substantive():
    raw = b":100644 100644 a b M\0f.py\0:100644 100644 a b M\0g.py\0"
    diff = b"diff --git a/f.py b/f.py\n@@ -1 +1 @@\n-a\n+a \n"
    assert gitutil.parse_delta(raw, diff)["kind"] == "substantive"


def test_the_files_listed_are_capped_and_the_total_is_kept():
    n = gitutil.FILES_SHOWN + 5
    raw = b"".join(b":100644 100644 a b M\0f%03d.txt\0" % i for i in range(n))
    diff = b"".join(b"diff --git a/f%03d.txt b/f%03d.txt\n@@ -1 +1 @@\n-a\n+a \n" % (i, i) for i in range(n))
    d = gitutil.parse_delta(raw, diff)
    assert len(d["files"]) == gitutil.FILES_SHOWN and d["files_total"] == n and d["kind"] == "whitespace_only"


def test_a_whitespace_change_beside_a_rewritten_line_in_the_same_file_is_substantive(repo):
    d = two(repo, "a.py", "x = 1  \nb = 2\n", "x = 1\nb = 3\n")
    f = d["files"]["a.py"]
    assert d["kind"] == "substantive" and f["ws_only"] == 1 and f["other"] == 1


def test_a_whitespace_change_beside_a_new_empty_file_is_substantive(repo):
    repo.write("a.txt", "one\n")
    a = repo.commit("a")
    repo.write("a.txt", "one  \n")
    repo.write("empty.txt", "")
    b = repo.commit("b")
    assert repo.delta(a, b)["kind"] == "substantive"


def test_a_whitespace_change_beside_a_mode_change_is_substantive(repo):
    repo.write("a.txt", "one\n")
    repo.write("run.sh", "echo hi\n")
    a = repo.commit("a")
    repo.write("a.txt", "one  \n")
    repo.git("update-index", "--chmod=+x", "run.sh")
    repo.git("add", "a.txt")
    repo.git("commit", "-q", "-m", "b")
    b = repo.git("rev-parse", "HEAD")
    assert repo.delta(a, b)["kind"] == "substantive"
