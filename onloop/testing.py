"""Fakes for tests and self-tests: a git history and a GitHub that exist only in memory. No network, no repository."""
from __future__ import annotations

import contextlib
from typing import Any


class FakeGit:
    """A history as data. `commits` maps sha -> {"parents": [...], "message": str, "tree": str, "files": {...}}.

    `files` is what each commit's tree holds for the paths a test cares about (path -> text); `changed` maps
    (base, head) -> the paths that diff changes.
    """

    def __init__(self, commits: dict[str, dict[str, Any]] | None = None, changed: dict[tuple[str, str],
                 list[str]] | None = None, order: list[str] | None = None):
        self.commits = commits or {}
        self.changed = changed or {}
        self.order = order or list(self.commits)
        self.calls: list[tuple[str, ...]] = []
        self.unfetched = False                 # True: the pull request head cannot be fetched
        self.pr_heads: dict[int, str] = {}     # what refs/onloop/pr/N resolves to after the fetch
        self.worktrees: list[str] = []

    def changed_files(self, base: str, head: str) -> list[str]:
        return sorted(self.changed.get((base, head), []))

    def show(self, rev: str, path: str) -> str | None:
        return (self.commits.get(rev) or {}).get("files", {}).get(path)

    def first_parent_commits(self, since: str | None, until: str = "HEAD") -> list[str]:
        seq = self.order
        if since and since in seq:
            seq = seq[seq.index(since) + 1:]
        return list(seq)

    def parents(self, sha: str) -> list[str]:
        return list(self.commits[sha]["parents"])

    def message(self, sha: str) -> str:
        return self.commits.get(sha, {}).get("message", "")

    def tree(self, sha: str) -> str:
        return self.commits[sha]["tree"]

    def rev_parse(self, rev: str) -> str:
        if rev.startswith("refs/onloop/pr/"):
            return self.pr_heads.get(int(rev.rsplit("/", 1)[1]), rev)
        return rev

    def has_commit(self, sha: str) -> bool:
        return not self.unfetched

    @contextlib.contextmanager
    def in_worktree(self, rev: str):
        self.worktrees.append(rev)
        yield self                              # the fake has no directories; `cwd` is None

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return True

    cwd = None

    def _run(self, *args: str) -> str:
        if self.unfetched and args[:1] == ("fetch",):
            raise RuntimeError("fetch failed")
        self.calls.append(args)
        return ""


class FakeGitHub:
    """Pull requests as data. `prs` maps number -> facts dict (see GitHub.pr); `commit_pr` maps merge sha -> number."""

    def __init__(self, prs: dict[int, dict[str, Any]] | None = None, commit_pr: dict[str, int] | None = None):
        self.prs = prs or {}
        self.commit_pr = commit_pr or {}
        self.merged: list[tuple[int, str]] = []
        self.comments: list[tuple[int, str]] = []

    def pr(self, number: int) -> dict[str, Any]:
        return self.prs[number]

    def pr_for_commit(self, sha: str) -> int | None:
        return self.commit_pr.get(sha)

    def open_prs(self) -> list[int]:
        return [n for n, p in self.prs.items() if p.get("state") == "OPEN"]

    def merge(self, number: int, head_sha: str) -> None:
        self.merged.append((number, head_sha))
        p = self.prs[number]
        if p["head"] != head_sha:
            raise RuntimeError("head moved: GitHub refuses the merge")
        p["state"] = "MERGED"

    def comment(self, number: int, body: str) -> None:
        self.comments.append((number, body))
