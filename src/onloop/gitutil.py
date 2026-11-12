"""Thin, fakeable wrappers around the `git` command."""

from __future__ import annotations

import contextlib
import re
import subprocess
import tempfile
from collections.abc import Callable

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def run(argv: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


class GitError(RuntimeError):
    pass


BASE_REF = "refs/onloop/base"
PR_REF = "refs/onloop/pr/{n}"
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$")
INDENT_SENSITIVE = (".py", ".yaml", ".yml", ".md", ".mk", "Makefile")
_SHA = re.compile(r"^[0-9a-f]{7,64}$")
FILES_SHOWN = 100


def branch_name(base: str) -> str:
    """A branch name safe to put in a refspec: no leading dash, no `..`, no `:`, `~`, `^`, `*`, `?`, `[`, space."""
    if (
        not isinstance(base, str)
        or not _BRANCH.match(base)
        or ".." in base
        or base.endswith((".lock", "/", "."))
    ):
        raise GitError(f"not a branch name onloop will fetch: {base!r}")
    return base


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

    def fetch_pr(self, number: int, base: str) -> None:
        """Fetch a pull request's head and the base branch into onloop's own namespace, and nowhere else.

        The refspecs are fixed here: `refs/onloop/pr/<n>` and `refs/onloop/base`. Nothing the caller passes becomes a
        destination, no tag is fetched and nothing is pruned, so no branch, tag or working tree changes. A fetch writes
        nothing remote, which is why this one is allowed in observe mode.
        """
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise GitError(f"not a pull request number: {number!r}")
        branch = branch_name(base)
        self._run(
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            "origin",
            f"+refs/heads/{branch}:{BASE_REF}",
            f"+refs/pull/{number}/head:{PR_REF.format(n=number)}",
        )

    def fetch_base(self, base: str) -> None:
        """Fetch the base branch into `refs/onloop/base` (after a merge, before a check or a revert)."""
        self._run(
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            "origin",
            f"+refs/heads/{branch_name(base)}:{BASE_REF}",
        )

    def has_revert(self, number: int, merge_sha: str) -> bool:
        """Has the base branch already got the revert of this merge? Looks for the trailer `revert_merge` writes."""
        if (
            isinstance(number, bool)
            or not isinstance(number, int)
            or not re.fullmatch(r"[0-9a-f]{7,64}", merge_sha or "")
        ):
            raise GitError("has_revert needs a pull request number and a hex commit")
        out = self._run(
            "log", BASE_REF, "-F", f"--grep=Gate-Revert: #{number} {merge_sha}", "--format=%H", "-n", "1"
        )
        return bool(out.strip())

    def is_bare(self) -> bool:
        return self._run("rev-parse", "--is-bare-repository").strip() == "true"

    def changed_files(self, base: str, head: str) -> list[str]:
        """Every path the head changes relative to the merge base, from `git diff` itself (not the host's list).

        `--no-renames` reports a rename as a delete plus an add, so moving a file out of a reserved path is seen as
        touching the reserved path.
        """
        out = self._run("diff", "--name-only", "--no-renames", "-z", f"{base}...{head}")
        return sorted(p for p in out.split("\0") if p)

    def head_commit(self) -> str:
        """The current commit, full length."""
        return self._run("rev-parse", "HEAD").strip()

    def file_commit_time(self, path: str) -> str | None:
        """When `path` was last committed, as the one stored timestamp format, or None when git has no commit for it."""
        out = self._run("log", "-1", "--format=%cI", "--", path).strip()
        if not out:
            return None
        import datetime as dt

        d = dt.datetime.fromisoformat(out).astimezone(dt.UTC)
        return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"

    def file_is_clean(self, path: str) -> bool:
        """True when `path` is tracked and its working copy equals the committed one."""
        if not self._run("ls-files", "--", path).strip():
            return False
        return not self._run("status", "--porcelain", "--", path).strip()

    def merge_base(self, a: str, b: str) -> str:
        return self._run("merge-base", a, b).strip()

    def supports_verbatim(self) -> bool:
        """`git patch-id --verbatim` arrived in git 2.39."""
        m = re.match(r"git version (\d+)\.(\d+)", run(["git", "--version"], self.cwd).stdout)
        return bool(m) and (int(m.group(1)), int(m.group(2))) >= (2, 39)

    def patch_id(self, base: str, head: str) -> str | None:
        """`git patch-id --verbatim` of the change `head` makes relative to its own merge-base with `base`.

        The id ignores line numbers, so a push that only merges the base into the branch has the id it had before.
        It does not ignore whitespace (`--stable` does, and a dedent changes what Python does): indentation and line
        endings both change the id. `--verbatim` needs git 2.39; on an older git there is no safe id, so None is
        returned and the caller counts the push as substantive. Needs a real git: the diff is piped into `patch-id`.
        """
        if self._runner:
            raise GitError("patch_id needs a real git, not an injected runner")
        if not self.supports_verbatim():
            return None
        mb = self.merge_base(base, head)
        diff = subprocess.run(
            ["git", "diff", "--no-color", "--no-ext-diff", "--no-textconv", mb, head],
            cwd=self.cwd,
            capture_output=True,
        )
        if diff.returncode != 0:
            raise GitError(
                f"`git diff {mb[:7]} {head[:7]}` failed: {diff.stderr.decode('utf-8', 'replace')[:300]}"
            )
        if not diff.stdout.strip():
            return "empty"
        pid = subprocess.run(
            ["git", "patch-id", "--verbatim"], cwd=self.cwd, input=diff.stdout, capture_output=True
        )
        if pid.returncode != 0 or not pid.stdout.strip():
            raise GitError(f"`git patch-id` failed: {pid.stderr.decode('utf-8', 'replace')[:300]}")
        return pid.stdout.decode("ascii", "replace").split()[0]

    def delta_basis(
        self, old: str, new: str, *, indent_sensitive: tuple[str, ...] | list[str] = INDENT_SENSITIVE
    ) -> dict:
        """What changed between two heads, for display. Never an input to a decision: `substantive` still comes from the
        patch id. Needs a real git with both commits present locally."""
        if self._runner:
            raise GitError("delta_basis needs a real git, not an injected runner")
        for rev in (old, new):
            if not isinstance(rev, str) or not _SHA.match(rev):
                raise GitError(f"not a commit id: {rev!r}")
        if (
            self._run("rev-parse", f"{old}^{{tree}}").strip()
            == self._run("rev-parse", f"{new}^{{tree}}").strip()
        ):
            return {
                "tree_equal": True,
                "kind": "merge_only",
                "files": {},
                "files_total": 0,
                "indent_sensitive": [],
            }
        base = ["git", "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames"]
        out = []
        for extra in (["--raw", "-z"], ["-U0"]):
            p = subprocess.run([*base, *extra, old, new], cwd=self.cwd, capture_output=True)
            if p.returncode != 0:
                raise GitError(
                    f"`git diff {old[:7]} {new[:7]}` failed: {p.stderr.decode('utf-8', 'replace')[:300]}"
                )
            out.append(p.stdout)
        return parse_delta(out[0], out[1], indent_sensitive=indent_sensitive)

    def show(self, rev: str, path: str) -> str | None:
        """The text of `path` at `rev`, or None when it does not exist there."""
        argv = ["git", "show", f"{rev}:{path}"]
        p = self._runner(argv) if self._runner else run(argv, self.cwd)
        return p.stdout if p.returncode == 0 else None

    def gained_cr(self, base: str, head: str) -> list[str]:
        """The paths in which the change adds more lines ending in a carriage return than it removes, from `git diff`
        itself (bytes: text mode would fold the carriage return away). The names come from `--raw -z`, which never quotes
        or escapes them; the lines come from the patch, whose sections are matched to the names by position.

        A line rewritten and still CRLF is one added and one removed, so it nets to nothing; a file that is only touched is
        not listed; a file that lost its carriage returns is not listed. Lines added to a file that already used CRLF do
        count: they are new carriage returns. **Known limit:** the count is net per file, so one edit that removes a CRLF
        line and, elsewhere in the same file, adds a bare CR line nets to zero and is not found. If the names and the
        patch do not line up, every changed path is reported: the check fails closed."""
        if self._runner:
            raise GitError("gained_cr needs a real git, not an injected runner")

        def diff(*opts: str) -> bytes:
            p = subprocess.run(
                [
                    "git",
                    "-c",
                    "core.quotepath=off",
                    "diff",
                    "--no-color",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-renames",
                    *opts,
                    f"{base}...{head}",
                ],
                cwd=self.cwd,
                capture_output=True,
            )
            if p.returncode != 0:
                raise GitError(f"`git diff` failed: {p.stderr.decode('utf-8', 'replace')[:300]}")
            return p.stdout

        return parse_gained_cr(diff("--raw", "-z"), diff("-U0"))

    def show_bytes(self, rev: str, path: str) -> bytes | None:
        """The exact bytes of `path` at `rev` (a path relative to the working directory, written `./name`), or None when
        the commit or the path is not there. No text decoding and no end-of-line conversion."""
        if self._runner:
            raise GitError("show_bytes needs a real git, not an injected runner")
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}|HEAD", rev or ""):
            raise GitError(f"not a commit id: {rev!r}")
        p = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=self.cwd, capture_output=True)
        return p.stdout if p.returncode == 0 else None

    def toplevel(self) -> str:
        """The root of the working tree this checkout is in."""
        return self._run("rev-parse", "--show-toplevel").strip()

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


# -- the delta between two heads -----------------------------------------------------------------------------------------------

RANGES_SHOWN = 40
_HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? ")


def parse_gained_cr(raw: bytes, patch: bytes) -> list[str]:
    """`gained_cr` on the output of `git diff --raw -z` and `git diff -U0` for the same revisions. Pure."""
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    names: list[str] = []
    for meta, path in zip(fields[0::2], fields[1::2], strict=False):
        if not meta.startswith(b":"):
            return ["(unparseable file list)"]
        names.append(path.decode("utf-8", "replace"))
    if len(fields) % 2:
        return sorted(names) or ["(unparseable file list)"]
    sections = re.split(rb"(?m)^diff --git ", patch)[1:]
    if len(sections) != len(names):
        return sorted(names)
    gained = []
    for name, section in zip(names, sections, strict=True):
        net, inside = 0, False
        for line in section.split(b"\n"):
            if line.startswith(b"@@"):
                inside = True
            elif inside and line[:1] in (b"+", b"-") and line.endswith(b"\r"):
                net += 1 if line[:1] == b"+" else -1
        if net > 0:
            gained.append(name)
    return sorted(gained)


def _blank_file() -> dict:
    return {"added": 0, "removed": 0, "cr_only": 0, "ws_only": 0, "indent_only": 0, "other": 0, "ranges": []}


def _old_range(header: bytes) -> list[int] | None:
    """The old-side lines a hunk covers, inclusive. A pure insertion after line n touches n and n + 1: a comment
    anchored beside an insertion is called touched, never unchanged."""
    m = _HUNK.match(header)
    if not m:
        return None
    start, count = int(m.group(1)), int(m.group(2)) if m.group(2) is not None else 1
    return [start, start + count - 1] if count else [start, start + 1]


def _classify(removed: bytes, added: bytes) -> str:
    """How one old line differs from the line that replaced it. Only leading and trailing whitespace and a carriage
    return are looked at; whitespace *inside* a line is a real change (a space in a string, between two tokens)."""
    if removed == added:
        return "other"  # same bytes: a change we cannot see here (a final newline), never harmless
    if removed.rstrip(b"\r") == added.rstrip(b"\r"):
        return "cr_only"
    if removed.strip() == added.strip():
        return "ws_only"
    return "other"


def parse_delta(
    raw: bytes, diff: bytes, *, indent_sensitive: tuple[str, ...] | list[str] = INDENT_SENSITIVE
) -> dict:
    """The delta from `git diff --raw --no-renames -z` and the zero-context unified diff of the same two commits.

    Pure, so it is tested without git. Lines are compared one pair at a time, never with a `git diff` whitespace flag:
    `-w` would call a space removed inside a string whitespace-only, and `-b` collapses runs of spaces inside a line.
    """
    parts = raw.split(b"\0")
    entries, i = [], 0
    while i + 1 < len(parts) and parts[i].startswith(b":"):
        meta = parts[i][1:].split()
        entries.append((meta, parts[i + 1].decode("utf-8", "replace")))
        i += 2
    plain = [p for meta, p in entries if len(meta) >= 5 and meta[0] == meta[1] and meta[4].startswith(b"M")]
    structural = len(plain) != len(entries)  # an added, deleted or mode-changed file is a real change
    chunks = re.split(rb"(?m)^diff --git ", diff)[1:]
    files: dict[str, dict[str, int]] = {}
    binary = False
    for (_meta, path), chunk in zip(entries, chunks, strict=False):
        f = _blank_file()
        files[path] = f
        if re.search(rb"(?m)^(Binary files |GIT binary patch)", chunk):
            binary = True
            f["other"] += 1
            f["ranges"] = None  # a binary file has no lines: any anchor in it is touched
            continue
        headers = re.findall(rb"(?m)^@@[^\n]*", chunk)
        hunks = re.split(rb"(?m)^@@[^\n]*\n", chunk)[1:]
        ranges = [_old_range(x) for x in headers]
        f["ranges"] = [r for r in ranges if r][:RANGES_SHOWN]
        if len(headers) > RANGES_SHOWN or any(r is None for r in ranges):
            f["ranges_cut"] = True
        for h in hunks:
            old, new = [], []
            for line in h.split(b"\n"):
                if line.startswith(b"-"):
                    old.append(line[1:])
                elif line.startswith(b"+"):
                    new.append(line[1:])
            f["removed"] += len(old)
            f["added"] += len(new)
            if len(old) != len(new):
                f["other"] += max(len(old), len(new))
                continue
            for a, b in zip(old, new, strict=True):
                kind = _classify(a, b)
                f[kind] += 1
                if kind == "ws_only" and a[: len(a) - len(a.lstrip())] != b[: len(b) - len(b.lstrip())]:
                    f["indent_only"] += 1
    total = {
        k: sum(f[k] for f in files.values())
        for k in ("added", "removed", "cr_only", "ws_only", "indent_only", "other")
    }
    if structural or binary or total["other"] or not entries or len(chunks) != len(entries):
        kind = "substantive"
    elif total["ws_only"]:
        kind = "whitespace_only"
    elif total["cr_only"]:
        kind = "crlf_only"
    else:
        kind = "substantive"

    def sensitive(path: str) -> bool:
        base = path.rsplit("/", 1)[-1]
        return any(base == s or (s.startswith(".") and base.endswith(s)) for s in indent_sensitive)

    shown = dict(sorted(files.items())[:FILES_SHOWN])
    return {
        "tree_equal": False,
        "kind": kind,
        "files": shown,
        "files_total": len(files),
        "indent_sensitive": sorted(p for p, f in files.items() if f["indent_only"] and sensitive(p)),
    }
