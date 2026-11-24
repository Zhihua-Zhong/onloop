"""What a step produced, as of a moment (decision 0019, R7a's a4): its commits and the files they changed, read from git
in the repositories onloop recorded itself.

    products(reader, step, at) -> dict           GET /api/products?step=&at=
    file(reader, repo, sha, path) -> (bytes, type)   GET /api/products/file?repo=&sha=&path=

**Which commits.** A node step (`step:node:<id>`) is every commit with that `Node:` trailer, on any branch onloop recorded
a session working on. A branch step (`step:branch:<name>`) is the commits on that branch from when the step started
until it ended. Only commits at or before `at` count, so the replay's cursor shows what existed then. Other steps
(a task, a PR) have no products here yet.

**What is read.** For the list, a commit's sha, time and changed paths; a file's content only when it is asked for, and
only from a repository onloop recorded (`session.opened`, `step.started`), at a commit, a path that exists in that
commit, and at most 1 MiB. Nothing is stored. The page shows HTML in a sandboxed frame that never has onloopd's
origin (the answer carries `Content-Security-Policy: sandbox`), Markdown rendered without raw HTML, and images.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from .. import events as ev
from .. import procs
from .views import QueryError

MAX_FILE = 1024 * 1024
MAX_COMMITS = 200
_SHA = re.compile(r"^[0-9a-f]{7,64}$")
_STEP = re.compile(r"^step:(node|branch):([A-Za-z0-9][A-Za-z0-9._/-]{0,119})$")
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}
TEXT = "text/plain; charset=utf-8"
# a product's own answer: it never runs with onloopd's origin, scripts never run, and only this page may frame it
SANDBOX = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'"


def _git(repo: str, *args: str, binary: bool = False) -> subprocess.CompletedProcess:
    kw: dict[str, Any] = {"capture_output": True, "timeout": 30}
    if not binary:
        kw.update(text=True, encoding="utf-8", errors="replace")
    return procs.run(["git", "-C", repo, *args], **kw)  # noqa: S603 - a fixed argv, no shell


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    p = row.get("payload")
    return p if isinstance(p, dict) else json.loads(p or "{}")


def places(reader: Any) -> list[tuple[str, str]]:
    """Every (repository, branch) onloop recorded a session working on, in a stable order."""
    seen: set[tuple[str, str]] = set()
    for r in reader.select(
        "SELECT payload FROM events WHERE kind IN ('session.opened', 'step.started', 'run.started') ORDER BY seq"
    ):
        p = _payload(r)
        if isinstance(p.get("repo"), str) and isinstance(p.get("branch"), str) and p["repo"] and p["branch"]:
            seen.add((p["repo"], p["branch"]))
    return sorted(seen)


def repos(reader: Any) -> list[str]:
    return sorted({repo for repo, _ in places(reader)})


def _commits(repo: str, branch: str, since: str | None, until: str) -> list[dict[str, Any]]:
    args = ["log", f"--until={until}", "--format=%H%x1f%ct%x1f%(trailers:key=Node,valueonly,separator=%x1e)"]
    if since:
        args.append(f"--since={since}")
    r = _git(repo, *args, f"refs/heads/{branch}", "--")
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        sha, _, rest = line.partition("\x1f")
        ct, _, trailer = rest.partition("\x1f")
        if _SHA.match(sha) and ct.strip().isdigit():
            out.append({"sha": sha, "t_ms": int(ct) * 1000, "node": trailer.split("\x1e")[0].strip() or None})
    return out


def products(reader: Any, step: str | None, at: str | None) -> dict[str, Any]:
    m = _STEP.match(step or "")
    if not m:
        raise QueryError("`step` is a node or a branch step: step:node:<id> or step:branch:<name>")
    try:
        at = ev.norm_ts(at) if at else ev.now_ts()
    except ev.EventError as e:
        raise QueryError(f"`at`: {e}") from e
    kind, ref = m.groups()
    all_repos = repos(reader)
    found: dict[str, dict[str, Any]] = {}
    if kind == "node":
        for repo, branch in places(reader):
            for c in _commits(repo, branch, None, at):
                if c["node"] == ref:
                    found[c["sha"]] = {**c, "repo": repo}
    else:
        spans = []
        for r in reader.select(
            "SELECT kind, ts_system, payload FROM events WHERE subject_ref = ? AND kind IN ('step.started', 'step.ended') "
            "ORDER BY seq",
            (step,),
        ):
            p = _payload(r)
            if r["kind"] == "step.started" and isinstance(p.get("repo"), str):
                spans.append([p["repo"], r["ts_system"], None])
            elif r["kind"] == "step.ended" and spans and spans[-1][2] is None:
                spans[-1][2] = r["ts_system"]
        for repo, t0, t1 in spans:
            if t0 > at:
                continue
            for c in _commits(repo, ref, t0, min(t1 or at, at)):
                found[c["sha"]] = {**c, "repo": repo}
    commits = sorted(found.values(), key=lambda c: c["t_ms"])[-MAX_COMMITS:]
    files: dict[tuple[str, str], dict[str, Any]] = {}
    for c in commits:  # oldest first, so each path ends with its latest commit as of `at`
        r = _git(c["repo"], "show", "--name-only", "--no-renames", "--format=", c["sha"])
        for path in (p for p in r.stdout.splitlines() if p.strip()) if r.returncode == 0 else ():
            files[(c["repo"], path)] = {
                "repo": all_repos.index(c["repo"]),
                "path": path,
                "sha": c["sha"],
                "t_ms": c["t_ms"],
            }
    for f in files.values():
        alive = _git(all_repos[f["repo"]], "cat-file", "-e", f"{f['sha']}:{f['path']}")
        f["deleted"] = alive.returncode != 0
        f["type"] = TYPES.get(_ext(f["path"]), TEXT)
    return {
        "step": step,
        "at": at,
        "repos": [r.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] for r in all_repos],  # names only
        "commits": [
            {"sha": c["sha"], "t_ms": c["t_ms"], "repo": all_repos.index(c["repo"])} for c in commits
        ],
        "files": sorted(files.values(), key=lambda f: (f["path"], f["repo"])),
    }


def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    return name[name.rfind(".") :] if "." in name else ""


def file(reader: Any, repo: str | None, sha: str | None, path: str | None) -> tuple[bytes, str]:
    """One product file's content at a commit, from a repository onloop recorded. Raises QueryError (400) or
    LookupError (404)."""
    all_repos = repos(reader)
    try:
        i = int(repo or "")
    except ValueError as e:
        raise QueryError("`repo` is the index of a repository in the products answer") from e
    if not 0 <= i < len(all_repos):
        raise LookupError("no such repository")
    if not _SHA.match(sha or ""):
        raise QueryError("`sha` is a commit id")
    if not path or path.startswith(("/", "-")) or "\x00" in path:
        raise QueryError("`path` is a path inside the commit")
    listed = _git(all_repos[i], "ls-tree", "-r", "--name-only", "-z", sha or "", "--", path)
    if listed.returncode != 0 or path not in listed.stdout.split("\x00"):
        raise LookupError("no such file in that commit")
    size = _git(all_repos[i], "cat-file", "-s", f"{sha}:{path}")
    if size.returncode != 0 or not size.stdout.strip().isdigit() or int(size.stdout) > MAX_FILE:
        raise LookupError(f"the file is not readable here or is over {MAX_FILE} bytes")
    data = _git(all_repos[i], "show", f"{sha}:{path}", binary=True)
    if data.returncode != 0:
        raise LookupError("the file could not be read")
    return data.stdout, TYPES.get(_ext(path), TEXT)
