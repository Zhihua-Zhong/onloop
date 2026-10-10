"""Thin, fakeable wrappers around the `git` command."""
from __future__ import annotations

import contextlib
import subprocess
import tempfile
from collections.abc import Callable

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def run(argv: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


class GitError(RuntimeError):
    pass


class Git:
    """The few git operations onloop needs. `runner` is injectable so tests never need a real repository."""

    def __init__(self, cwd: str | None = None, runner: Runner | None = None):
        self.cwd = cwd
        self._runner = runner

    def _run(self, *args: str) -> str:
        argv = ["git", *args]
        p = self._runner(argv) if self._runner else run(argv, self.cwd)
        if p.returncode != 0:
            raise GitError(f"`git {' '.join(args)}` failed: {(p.stderr or p.stdout).strip()[:300]}")
        return p.stdout

    def changed_files(self, base: str, head: str) -> list[str]:
        """Every path the head changes relative to the merge base, from `git diff` itself (not the host's list).

        `--no-renames` reports a rename as a delete plus an add, so moving a file out of a reserved path is seen as
        touching the reserved path.
        """
        out = self._run("diff", "--name-only", "--no-renames", "-z", f"{base}...{head}")
        return sorted(p for p in out.split("\0") if p)

    def show(self, rev: str, path: str) -> str | None:
        """The text of `path` at `rev`, or None when it does not exist there."""
        argv = ["git", "show", f"{rev}:{path}"]
        p = self._runner(argv) if self._runner else run(argv, self.cwd)
        return p.stdout if p.returncode == 0 else None

    def rev_parse(self, rev: str) -> str:
        return self._run("rev-parse", rev).strip()

    def first_parent_commits(self, since: str | None, until: str = "HEAD") -> list[str]:
        rng = f"{since}..{until}" if since else until
        return [s for s in self._run("rev-list", "--first-parent", "--reverse", rng).split() if s]

    def parents(self, sha: str) -> list[str]:
        return self._run("rev-list", "--parents", "-n", "1", sha).split()[1:]

    def message(self, sha: str) -> str:
        return self._run("log", "-1", "--format=%B", sha)

    def tree(self, sha: str) -> str:
        return self._run("rev-parse", f"{sha}^{{tree}}").strip()

    def has_commit(self, sha: str) -> bool:
        argv = ["git", "cat-file", "-e", f"{sha}^{{commit}}"]
        p = self._runner(argv) if self._runner else run(argv, self.cwd)
        return p.returncode == 0

    @contextlib.contextmanager
    def in_worktree(self, rev: str):
        """A clean, detached worktree of `rev` in a temporary directory, as a `Git` bound to it; removed afterwards."""
        with tempfile.TemporaryDirectory(prefix="onloop-wt-") as tmp:
            self._run("worktree", "add", "--detach", tmp, rev)
            try:
                yield Git(cwd=tmp, runner=self._runner)
            finally:
                self._run("worktree", "remove", "--force", tmp)

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        argv = ["git", "merge-base", "--is-ancestor", ancestor, descendant]
        p = self._runner(argv) if self._runner else run(argv, self.cwd)
        return p.returncode == 0
