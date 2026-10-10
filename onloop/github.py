"""The one thin client every GitHub call goes through, so tests can replace it.

`GitHub` shells out to the `gh` CLI. A pull request is read into a plain dict (`PullRequest`) so that the gate and the
audit are pure functions of facts, and a test builds those facts by hand.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

Facts = dict[str, Any]
GhRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


class GitHubError(RuntimeError):
    pass


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")


def json_stream(text: str) -> list[Any]:
    """Every JSON document in `text`, in order. `gh api --paginate` prints one document per page, back to back."""
    dec, i, out = json.JSONDecoder(), 0, []
    text = text.strip()
    while i < len(text):
        obj, j = dec.raw_decode(text, i)
        out.append(obj)
        i = j
        while i < len(text) and text[i].isspace():
            i += 1
    return out


def actor(user: dict[str, Any] | None) -> dict[str, str | None]:
    """{login, type} for a REST user. The REST API reports an App as `slug[bot]`; the policy names the bare slug (as
    GraphQL does), so a Bot's login is returned without the suffix. The type is kept, so a `User` that registered the
    same bare name is still not the App."""
    user = user or {}
    login = user.get("login")
    if user.get("type") == "Bot" and isinstance(login, str):
        login = login.removesuffix("[bot]")
    return {"login": login, "type": user.get("type")}


class GitHub:
    """Reads and writes one repository through `gh`. Every method is a single, named call."""

    def __init__(self, repo: str, runner: GhRunner | None = None):
        self.repo = repo
        self._runner = runner or _run

    # -- plumbing ------------------------------------------------------------------------------------------

    def _gh(self, *args: str) -> str:
        p = self._runner(["gh", *args])
        if p.returncode != 0:
            raise GitHubError(f"`gh {' '.join(args[:3])} ...` failed: {(p.stderr or p.stdout).strip()[:300]}")
        return p.stdout

    def _api(self, path: str, *extra: str, paginate: bool = False) -> list[Any]:
        args = ["api", *extra, f"repos/{self.repo}/{path}"]
        if paginate:
            args.insert(1, "--paginate")
        return json_stream(self._gh(*args))

    def _api_list(self, path: str, key: str | None = None) -> list[Any]:
        items: list[Any] = []
        for page in self._api(path, paginate=True):
            items.extend(page.get(key, []) if key and isinstance(page, dict) else page)
        return items

    # -- reading -------------------------------------------------------------------------------------------

    def pr(self, number: int) -> Facts:
        """Everything the gate and the audit need to know about one pull request, as plain data."""
        raw = self._api(f"pulls/{number}")[0]
        head = raw["head"]["sha"]
        reviews = self._api_list(f"pulls/{number}/reviews")
        commits = self._api_list(f"pulls/{number}/commits")
        checks = self._api_list(f"commits/{head}/check-runs", "check_runs")
        timeline = self._api_list(f"issues/{number}/timeline")
        return {
            "number": number,
            "state": "MERGED" if raw.get("merged") else raw["state"].upper(),
            "draft": bool(raw.get("draft")),
            "base": raw["base"]["ref"],
            "head": head,
            "title": raw.get("title") or "",
            "body": raw.get("body") or "",
            "commit_messages": [c["commit"]["message"] for c in commits],
            "head_pushed_at": ((commits[-1]["commit"].get("committer") or {}).get("date") if commits else None),
            "reviews": [{"id": r["id"], "author": actor(r.get("user")), "state": r["state"],
                         "commit": r.get("commit_id"), "submitted_at": r.get("submitted_at")} for r in reviews],
            "dismissals": [{"review_id": (e.get("dismissed_review") or {}).get("review_id"),
                            "previous_state": (e.get("dismissed_review") or {}).get("state"),
                            "actor": actor(e.get("actor")), "at": e.get("created_at")}
                           for e in timeline if e.get("event") == "review_dismissed"],
            "checks": [{"name": c["name"], "conclusion": (c.get("conclusion") or "").upper() or None}
                       for c in checks],
            "merged_by": actor(raw.get("merged_by")) if raw.get("merged") else None,
            "merged_at": raw.get("merged_at"),
            "merge_sha": raw.get("merge_commit_sha"),
        }

    def pr_for_commit(self, sha: str) -> int | None:
        """The pull request a merge commit on the default branch belongs to, or None."""
        pulls = self._api_list(f"commits/{sha}/pulls")
        for p in pulls:
            if p.get("merge_commit_sha") == sha:
                return int(p["number"])
        return None

    def open_prs(self) -> list[int]:
        return [int(p["number"]) for p in self._api_list("pulls?state=open&per_page=100")]

    # -- writing -------------------------------------------------------------------------------------------

    def merge(self, number: int, head_sha: str) -> None:
        """Merge only if the head is still exactly `head_sha`: a push after the decision makes GitHub refuse."""
        self._gh("pr", "merge", str(number), "-R", self.repo, "--merge", "--match-head-commit", head_sha)

    def comment(self, number: int, body: str) -> None:
        self._gh("pr", "comment", str(number), "-R", self.repo, "--body", body)
