"""The one thin client every GitHub call goes through, so tests can replace it.

`GitHub` shells out to the `gh` CLI. A pull request is read into a plain dict (`PullRequest`) so that the gate and the
audit are pure functions of facts, and a test builds those facts by hand.

The readers that need no repository object use the module functions: `get_list` and `get_object` (GETs only, for the
work graph's issue reader and the supervisor's source) and `current_repo` (the repository `gh` is pointed at). Each
takes a runner, the same `GhRunner` the class takes; without one it calls `gh` through `_run`. Commands a person or
an agent asked for (`onloop as-app`, a role's tools) run `gh` themselves and are not GitHub calls of onloop's.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from typing import Any

from . import identity

TRAILER_KEY = "Onloop-Merge"
Facts = dict[str, Any]
GhRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


class GitHubError(RuntimeError):
    pass


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _runner(runner: GhRunner | None) -> GhRunner:
    return runner or _run  # looked up at call time, so a test can replace _run


def get_list(path: str, runner: GhRunner | None = None) -> list[Any]:
    """Every item of a paginated GET of `path` (`gh api --method GET --paginate`). Raises GitHubError on a failed read
    or a page that is not a list, so a partial picture is never taken for a complete one."""
    p = _runner(runner)(["gh", "api", "--method", "GET", "--paginate", path])
    if p.returncode != 0:
        raise GitHubError(f"gh api {path} failed: {(p.stderr or p.stdout).strip()[:300]}")
    items: list[Any] = []
    for page in json_stream(p.stdout):
        if not isinstance(page, list):
            raise GitHubError(f"gh api {path} did not return a list")
        items.extend(page)
    return items


def get_object(path: str, runner: GhRunner | None = None) -> dict[str, Any]:
    """One GET of `path` that answers a JSON object. Raises GitHubError otherwise."""
    p = _runner(runner)(["gh", "api", "--method", "GET", path])
    if p.returncode != 0:
        raise GitHubError(f"gh api {path} failed: {(p.stderr or p.stdout).strip()[:300]}")
    try:
        obj = json.loads(p.stdout)
    except ValueError as e:
        raise GitHubError(f"gh api {path} did not return JSON") from e
    if not isinstance(obj, dict):
        raise GitHubError(f"gh api {path} did not return an object")
    return obj


def current_repo(runner: GhRunner | None = None) -> str | None:
    """OWNER/NAME of the repository `gh` resolves from the working directory, or None when it cannot say."""
    p = _runner(runner)(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


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


def parse_http(text: str) -> tuple[int | None, dict[str, str], str]:
    """(status, lowercase headers, body) from `gh api -i` output. gh exits non-zero on a 304; the text still says 304."""
    head, _, body = text.replace("\r\n", "\n").partition("\n\n")
    lines = head.split("\n")
    status = None
    if lines and lines[0].startswith("HTTP/"):
        parts = lines[0].split()
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    headers = {}
    for ln in lines[1:]:
        k, sep, v = ln.partition(":")
        if sep:
            headers[k.strip().lower()] = v.strip()
    return status, headers, body


def rate_of(headers: dict[str, str]) -> dict[str, int | None]:
    def num(k: str) -> int | None:
        v = headers.get(k)
        return int(v) if v and v.isdigit() else None

    return {
        "remaining": num("x-ratelimit-remaining"),
        "limit": num("x-ratelimit-limit"),
        "reset": num("x-ratelimit-reset"),
    }


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

    THREADS_QUERY = (
        "query($owner:String!,$name:String!,$n:Int!){repository(owner:$owner,name:$name){pullRequest(number:$n){"
        "reviewThreads(first:100){pageInfo{hasNextPage} nodes{id isResolved resolvedBy{login __typename} path originalLine "
        "comments(first:1){nodes{createdAt originalCommit{oid} author{login __typename}}}}}}}}"
    )

    def threads(self, number: int) -> dict[str, Any] | None:
        """The pull request's review threads (first 100), or None when GitHub would not say. Display only: a failure
        here never fails the read of the pull request."""
        owner, _, name = self.repo.partition("/")
        try:
            out = json_stream(
                self._gh(
                    "api",
                    "graphql",
                    "-f",
                    f"query={self.THREADS_QUERY}",
                    "-f",
                    f"owner={owner}",
                    "-f",
                    f"name={name}",
                    "-F",
                    f"n={number}",
                )
            )
            block = out[0]["data"]["repository"]["pullRequest"]["reviewThreads"]
            nodes = block["nodes"]
        except (GitHubError, KeyError, IndexError, TypeError, ValueError):
            return None
        rows = []
        for t in nodes:
            first = ((t.get("comments") or {}).get("nodes") or [{}])[0]
            rows.append(
                {
                    "id": t["id"],
                    "path": t.get("path"),
                    "original_line": t.get("originalLine"),
                    "original_commit": (first.get("originalCommit") or {}).get("oid"),
                    "author": actor(
                        {**(first.get("author") or {}), "type": (first.get("author") or {}).get("__typename")}
                    ),
                    "created_at": first.get("createdAt"),
                    "resolved": bool(t.get("isResolved")),
                    "resolved_by": (
                        actor({**t["resolvedBy"], "type": t["resolvedBy"].get("__typename")})
                        if t.get("resolvedBy")
                        else None
                    ),
                }
            )
        return {"rows": rows, "truncated": bool((block.get("pageInfo") or {}).get("hasNextPage"))}

    def issue_comment(self, comment_id: int) -> dict[str, Any]:
        """One comment on an issue or pull request, as GitHub reports it (its own `issue_url`, `user`, `body`, times)."""
        return self._api(f"issues/comments/{int(comment_id)}")[0]

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
            "author": actor(raw.get("user")),
            "body": raw.get("body") or "",
            "commit_messages": [c["commit"]["message"] for c in commits],
            "head_pushed_at": (
                (commits[-1]["commit"].get("committer") or {}).get("date") if commits else None
            ),
            "reviews": [
                {
                    "id": r["id"],
                    "author": actor(r.get("user")),
                    "state": r["state"],
                    "commit": r.get("commit_id"),
                    "submitted_at": r.get("submitted_at"),
                    "body": r.get("body") or "",
                }
                for r in reviews
            ],
            "dismissals": [
                {
                    "review_id": (e.get("dismissed_review") or {}).get("review_id"),
                    "previous_state": (e.get("dismissed_review") or {}).get("state"),
                    "actor": actor(e.get("actor")),
                    "at": e.get("created_at"),
                }
                for e in timeline
                if e.get("event") == "review_dismissed"
            ],
            "checks": [
                {
                    "name": c["name"],
                    "conclusion": (c.get("conclusion") or "").upper() or None,
                    "id": c.get("id"),
                }
                for c in checks
            ],
            "merged_by": actor(raw.get("merged_by")) if raw.get("merged") else None,
            "merged_at": raw.get("merged_at"),
            "merge_sha": raw.get("merge_commit_sha"),
            **self._threads_facts(number),
        }

    def _threads_facts(self, number: int) -> dict[str, Any]:
        """`threads` and how the read went: ok, truncated (more than 100) or failed. A failed read is `None`, never `[]`."""
        got = self.threads(number)
        if got is None:
            return {"threads": None, "threads_read": "failed"}
        return {"threads": got["rows"], "threads_read": "truncated" if got["truncated"] else "ok"}

    def open_pr_list(self, etag: str | None = None) -> dict[str, Any]:
        """The open pull requests (first 100) as `{number, updated_at, head, draft}`, by a conditional request.

        Passing the ETag of the last answer makes GitHub reply 304 when nothing changed, which costs no rate limit.
        Returns `{not_modified, etag, items, rate}`; `rate` is `{remaining, limit, reset}` from the response headers.
        """
        status, headers, body = self._api_conditional(
            "pulls?state=open&per_page=100&sort=updated&direction=desc", etag
        )
        rate = rate_of(headers)
        if status == 304:
            return {"not_modified": True, "etag": etag, "items": [], "rate": rate}
        items = [
            {
                "number": int(p["number"]),
                "updated_at": p.get("updated_at"),
                "head": p["head"]["sha"],
                "draft": bool(p.get("draft")),
            }
            for p in json.loads(body or "[]")
        ]
        return {"not_modified": False, "etag": headers.get("etag"), "items": items, "rate": rate}

    def checks(self, head: str) -> list[dict[str, Any]]:
        """The check runs on a commit: one call. Used while a pull request's checks are still settling."""
        runs = self._api_list(f"commits/{head}/check-runs", "check_runs")
        return [
            {"name": c["name"], "conclusion": (c.get("conclusion") or "").upper() or None, "id": c.get("id")}
            for c in runs
        ]

    def branch_head(self, branch: str) -> str:
        return str(self._api(f"commits/{branch}")[0]["sha"])

    def _api_conditional(self, path: str, etag: str | None) -> tuple[int, dict[str, str], str]:
        args = ["api", "-i"]
        if etag:
            args += ["-H", f"If-None-Match: {etag}"]
        p = self._runner(["gh", *args, f"repos/{self.repo}/{path}"])
        status, headers, body = parse_http(p.stdout or "")
        if status is None or (p.returncode != 0 and status != 304):
            raise GitHubError(f"`gh api {path[:40]}` failed: {(p.stderr or p.stdout).strip()[:300]}")
        return status, headers, body

    def pr_files(self, number: int) -> list[str]:
        """The paths a pull request changes, as the host lists them. For display only: the gate reads `git diff`."""
        out: list[str] = []
        for f in self._api_list(f"pulls/{number}/files"):
            out.append(f["filename"])
            if f.get("previous_filename"):
                out.append(f["previous_filename"])
        return sorted(set(out))

    def pr_patches(self, number: int) -> dict[str, str]:
        """The unified diff GitHub shows for each changed file, by path (absent for binary or very large files)."""
        return {f["filename"]: f.get("patch") or "" for f in self._api_list(f"pulls/{number}/files")}

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

    def merge(self, number: int, head_sha: str, trailer: str | None = None) -> None:
        """Merge only if the head is still exactly `head_sha`: a push after the decision makes GitHub refuse.

        `trailer` is the id of the event the merge was decided on; it goes into the merge commit as `Onloop-Merge: <id>`,
        which is how a later run knows the merge is onloop's own and not another tool's under the same account."""
        argv = ["pr", "merge", str(number), "-R", self.repo, "--merge", "--match-head-commit", head_sha]
        if trailer is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", trailer):
                raise ValueError("the Onloop-Merge trailer is an event id: 64 hex digits")
            argv += ["--body", f"{TRAILER_KEY}: {trailer}"]
        self._gh(*argv)

    def comments(self, number: int) -> list[str]:
        """The bodies of the comments on a pull request, oldest first (for `verify` to search for a marker)."""
        return [c.get("body") or "" for c in self._api_list(f"issues/{number}/comments")]

    def find_comment(
        self, number: int, marker: str, authors: list[str] | None = None
    ) -> dict[str, Any] | None:
        """The first comment on a pull request whose body contains `marker`, as {id, body}; None if there is none.

        With `authors`, only a comment written by one of those Bot accounts counts, so that anyone else who writes the
        marker into a comment cannot make onloop edit theirs or stop it posting its own."""
        for c in self._api_list(f"issues/{number}/comments"):
            if marker not in (c.get("body") or ""):
                continue
            if authors is not None:
                who = actor(c.get("user"))
                if not any(
                    identity.same_identity(f"{who['login']}/{who['type']}", f"{a}/Bot")
                    for a in authors
                    if who.get("login")
                ):
                    continue
            return {"id": c["id"], "body": c.get("body") or ""}
        return None

    def update_comment(self, comment_id: int, body: str) -> None:
        self._gh(
            "api", "-X", "PATCH", f"repos/{self.repo}/issues/comments/{comment_id}", "-f", f"body={body}"
        )

    def installation_repositories(self) -> None:
        """Succeeds only for an installation token: `GET /installation/repositories` is refused to every other credential,
        a person's login included. Raises GitHubError otherwise."""
        self._gh("api", "installation/repositories", "--jq", ".total_count")

    def ready(self, number: int) -> None:
        self._gh("pr", "ready", str(number), "-R", self.repo)

    def comment(self, number: int, body: str) -> None:
        self._gh("pr", "comment", str(number), "-R", self.repo, "--body", body)
