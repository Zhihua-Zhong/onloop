"""Fakes for tests and self-tests: a git history and a GitHub that exist only in memory. No network, no repository."""

from __future__ import annotations

import contextlib
from typing import Any


class FakeGit:
    """A history as data. `commits` maps sha -> {"parents": [...], "message": str, "tree": str, "files": {...}}.

    `files` is what each commit's tree holds for the paths a test cares about (path -> text); `changed` maps
    (base, head) -> the paths that diff changes.
    """

    def __init__(
        self,
        commits: dict[str, dict[str, Any]] | None = None,
        changed: dict[tuple[str, str], list[str]] | None = None,
        order: list[str] | None = None,
    ):
        self.commits = commits or {}
        self.changed = changed or {}
        self.order = order or list(self.commits)
        self.calls: list[tuple[str, ...]] = []
        self.unfetched = False  # True: the pull request head cannot be fetched
        self.pr_heads: dict[int, str] = {}  # what refs/onloop/pr/N resolves to after the fetch
        self.worktrees: list[str] = []

    def fetch_pr(self, number: int, base: str) -> None:
        self._run("fetch", "origin", base, f"+refs/pull/{number}/head:refs/onloop/pr/{number}")

    def fetch_base(self, base: str) -> None:
        self._run("fetch", "origin", base)

    def is_bare(self) -> bool:
        return getattr(self, "bare", True)

    reverted: set[tuple[int, str]] = set()

    def has_revert(self, number: int, merge_sha: str) -> bool:
        return (number, merge_sha) in self.reverted

    def changed_files(self, base: str, head: str) -> list[str]:
        return sorted(self.changed.get((base, head), []))

    def show(self, rev: str, path: str) -> str | None:
        return (self.commits.get(rev) or {}).get("files", {}).get(path)

    def first_parent_commits(self, since: str | None, until: str = "HEAD") -> list[str]:
        seq = self.order
        if since and since in seq:
            seq = seq[seq.index(since) + 1 :]
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
        yield self  # the fake has no directories; `cwd` is None

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return True

    cwd = None
    patch_ids: dict[str, str] = {}
    gained: list[str] = []
    deltas: dict[tuple[str, str], dict] = {}
    delta_asked: list[tuple[str, str, list[str]]] = []

    def gained_cr(self, base: str, head: str) -> list[str]:
        return list(self.gained)

    def delta_basis(self, old: str, new: str, *, indent_sensitive=()) -> dict:
        self.delta_asked.append((old, new, list(indent_sensitive)))
        if (old, new) not in self.deltas:
            raise RuntimeError("commit not present")
        return self.deltas[(old, new)]

    def patch_id(self, base: str, head: str) -> str | None:
        return self.patch_ids.get(head)

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
        self.posted: list[tuple[int, str]] = []
        self.rows: list[dict[str, Any]] = []
        self.edited: list[tuple[int, str]] = []
        self.readied: list[int] = []

    def pr(self, number: int) -> dict[str, Any]:
        return self.prs[number]

    def pr_files(self, number: int) -> list[str]:
        return list(self.prs[number].get("files") or [])

    def pr_patches(self, number: int) -> dict[str, str]:
        return dict(self.prs[number].get("patches") or {})

    def pr_for_commit(self, sha: str) -> int | None:
        return self.commit_pr.get(sha)

    def open_prs(self) -> list[int]:
        return [n for n, p in self.prs.items() if p.get("state") == "OPEN"]

    trailers: list[str | None]

    def merge(self, number: int, head_sha: str, trailer: str | None = None) -> None:
        self.__dict__.setdefault("trailers", []).append(trailer)
        self.merged.append((number, head_sha))
        p = self.prs[number]
        if p["head"] != head_sha:
            raise RuntimeError("head moved: GitHub refuses the merge")
        p["state"] = "MERGED"

    def comment(self, number: int, body: str) -> None:
        self.posted.append((number, body))
        self.rows.append({"id": len(self.rows) + 1, "number": number, "body": body, "author": self.author})

    author = "agent-bot"

    def find_comment(
        self, number: int, marker: str, authors: list[str] | None = None
    ) -> dict[str, Any] | None:
        from . import identity

        for r in self.rows:
            if r["number"] != number or marker not in r["body"]:
                continue
            if authors is not None and not any(
                identity.same_identity(r["author"], f"{a}/Bot") for a in authors
            ):
                continue
            return {"id": r["id"], "body": r["body"]}
        return None

    def update_comment(self, comment_id: int, body: str) -> None:
        for r in self.rows:
            if r["id"] == comment_id:
                r["body"] = body
                self.edited.append((comment_id, body))
                return
        raise KeyError(comment_id)

    def comments(self, number: int) -> list[str]:
        return [r["body"] for r in self.rows if r["number"] == number]

    def ready(self, number: int) -> None:
        self.readied.append(number)

    installation_ok = True

    def installation_repositories(self) -> None:
        if not self.installation_ok:
            raise RuntimeError("not an installation token")


class ScriptedGitHub(FakeGitHub):
    """A GitHub whose answers change over time: `steps[i]` is `{"prs": {number: facts}, "main": sha, "rate": {...}}`.

    A test calls `advance()` between ticks. `open_pr_list` answers 304 when asked with the ETag it last gave for an
    identical list, as GitHub does; `calls` records every method call so a test can assert what a tick cost.
    """

    def __init__(self, steps: list[dict[str, Any]]):
        super().__init__()
        self.steps = steps
        self.i = 0
        self.calls: list[tuple] = []

    def advance(self) -> None:
        self.i += 1

    @property
    def now(self) -> dict[str, Any]:
        return self.steps[min(self.i, len(self.steps) - 1)]

    def _open(self) -> list[dict[str, Any]]:
        return [
            {"number": n, "updated_at": f.get("updated_at"), "head": f["head"], "draft": bool(f.get("draft"))}
            for n, f in sorted(self.now["prs"].items())
            if f.get("state") == "OPEN"
        ]

    def open_pr_list(self, etag: str | None = None) -> dict[str, Any]:
        import hashlib
        import json

        items = self._open()
        tag = 'W/"' + hashlib.sha1(json.dumps(items, sort_keys=True).encode()).hexdigest()[:12] + '"'
        rate = dict(self.now.get("rate") or {"remaining": 5000, "limit": 5000, "reset": None})
        self.calls.append(("open_pr_list", etag, etag == tag))
        if etag == tag:
            return {"not_modified": True, "etag": etag, "items": [], "rate": rate}
        return {"not_modified": False, "etag": tag, "items": items, "rate": rate}

    def pr(self, number: int) -> dict[str, Any]:
        import copy

        self.calls.append(("pr", number))
        return copy.deepcopy(self.now["prs"][number])

    def pr_files(self, number: int) -> list[str]:
        return list(self.now["prs"][number].get("files") or [])

    def pr_patches(self, number: int) -> dict[str, str]:
        return dict(self.now["prs"][number].get("patches") or {})

    def checks(self, head: str) -> list[dict[str, Any]]:
        import copy

        self.calls.append(("checks", head))
        for f in self.now["prs"].values():
            if f["head"] == head:
                return copy.deepcopy(f.get("checks") or [])
        return []

    def branch_head(self, branch: str) -> str:
        self.calls.append(("branch_head", branch))
        return self.now.get("main", "m" * 40)
