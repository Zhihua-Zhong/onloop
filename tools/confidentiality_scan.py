#!/usr/bin/env python3
"""Confidentiality scan for a git repository.

Scans tracked files (plus untracked files that are not ignored) for material that must not ship:
IPv4 addresses, e-mail addresses, Windows user paths, key material and CJK text. An optional
denylist, read from a file outside the repository, adds project-specific patterns. A denylist hit
is reported by pattern number only, so the denylist never leaks into logs.

The generic patterns are written so this file does not trigger them: key markers are assembled
from fragments and CJK ranges are written as escapes. Samples used by --self-test are built at
runtime for the same reason.

Exit codes: 0 clean, 1 hits (or a failed self-test), 2 usage error.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
ALLOW_MARKER = "scan:" + "allow"
EXCERPT_WIDTH = 100

_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_DASHES = "-" * 5
_PEM_BEGIN = _DASHES + "BEGIN "
_CJK = "[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]"
_QUOTES = "\"'"


@dataclass(frozen=True)
class Pattern:
    kind: str
    regex: re.Pattern[str]
    show_excerpt: bool = True


@dataclass(frozen=True)
class Hit:
    line: int
    kind: str
    excerpt: str | None
    allowed: bool


def generic_patterns() -> list[Pattern]:
    """The always-on patterns."""
    return [
        Pattern("ipv4", re.compile(rf"(?<![\d.])(?:{_OCTET}\.){{3}}{_OCTET}(?!\.?\d)")),
        Pattern("email", re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
        Pattern(
            "windows-user-path",
            re.compile(rf"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s{_QUOTES}]+[\\/]", re.IGNORECASE),
        ),
        Pattern(
            "key-block",
            re.compile(
                re.escape(_PEM_BEGIN) + r"(?:[A-Z0-9]+ )*(?:PRIVATE KEY|CERTIFICATE)" + re.escape(_DASHES)
            ),
            show_excerpt=False,
        ),
        Pattern(
            "ssh-key",
            re.compile(r"\bssh-(?:rsa|ed25519) AAAA[0-9A-Za-z+/=]{20,}"),
            show_excerpt=False,
        ),
        Pattern(
            "token",
            re.compile(
                r"\bgh[pousr]_[A-Za-z0-9]{36}\b"
                r"|\bgithub_pat_[A-Za-z0-9_]{22,}"
                r"|\bxox[bap]-[A-Za-z0-9-]{10,}"
                r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
            ),
            show_excerpt=False,
        ),
        Pattern("cjk", re.compile(_CJK + "+")),
    ]


def load_denylist(path: Path, index: int = 1) -> list[Pattern]:
    """Read one regular expression per line. Raises ValueError without quoting any pattern."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read denylist {path}: {type(exc).__name__}") from None
    patterns: list[Pattern] = []
    for number, line in enumerate(raw.splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            compiled = re.compile(text, re.IGNORECASE)
        except re.error:
            raise ValueError(f"denylist {path}: line {number} is not a valid regular expression") from None
        label = f"denylist pattern #{number}" if index == 1 else f"denylist {index} pattern #{number}"
        patterns.append(Pattern(label, compiled, show_excerpt=False))
    return patterns


def scan_text(text: str, patterns: Iterable[Pattern]) -> list[Hit]:
    """Return one hit per (line, pattern). Lines carrying the opt-out marker are flagged allowed."""
    patterns = list(patterns)
    hits: list[Hit] = []
    for number, line in enumerate(text.splitlines(), start=1):
        allowed = ALLOW_MARKER in line
        for pattern in patterns:
            if not pattern.regex.search(line):
                continue
            excerpt = None
            if pattern.show_excerpt:
                excerpt = line.strip()
                if len(excerpt) > EXCERPT_WIDTH:
                    excerpt = excerpt[: EXCERPT_WIDTH - 3] + "..."
            hits.append(Hit(number, pattern.kind, excerpt, allowed))
    return hits


def read_scannable(path: Path) -> str | None:
    """Text of a file, or None if it is missing, too large or binary."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def _git(root: Path, *args: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True
    ).stdout.decode("utf-8", errors="surrogateescape")
    return [p for p in out.split("\0") if p]


def list_files(root: Path) -> list[Path]:
    """Tracked plus untracked-not-ignored files; falls back to a directory walk outside git."""
    try:
        names = set(_git(root, "ls-files", "-z"))
        names |= set(_git(root, "ls-files", "-z", "--others", "--exclude-standard"))
        return [root / n for n in sorted(names)]
    except (subprocess.CalledProcessError, FileNotFoundError):
        skip = {".git", ".venv", "node_modules", "__pycache__"}
        return sorted(
            p for p in root.rglob("*") if p.is_file() and not skip.intersection(p.relative_to(root).parts)
        )


def scan_root(root: Path, patterns: list[Pattern]) -> tuple[list[tuple[str, Hit]], int, int]:
    """Return (hits, allowed_count, files_scanned). Allowed hits are not in the hit list."""
    found: list[tuple[str, Hit]] = []
    allowed = 0
    scanned = 0
    for path in list_files(root):
        text = read_scannable(path)
        if text is None:
            continue
        scanned += 1
        rel = path.relative_to(root).as_posix()
        for hit in scan_text(text, patterns):
            if hit.allowed:
                allowed += 1
            else:
                found.append((rel, hit))
    return found, allowed, scanned


def denylist_inside_root(denylist: Path, root: Path) -> bool:
    return denylist.resolve().is_relative_to(root.resolve())


def git_toplevel() -> Path | None:
    try:
        out = (
            subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, check=True)
            .stdout.decode("utf-8")
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return Path(out) if out else None


def self_test() -> int:
    """Prove each generic kind is caught, and the denylist and opt-out behave. Returns failures."""
    generic = generic_patterns()
    dotted = ".".join(["203", "0", "113", "7"])
    samples = {
        "ipv4": f"server = {dotted}",
        "email": "contact: " + "someone" + "@" + "example" + ".org",
        "windows-user-path": "path = " + "C:" + "\\" + "Users" + "\\" + "someone" + "\\" + "file",
        "key-block": _PEM_BEGIN + "RSA PRIVATE KEY" + _DASHES,
        "ssh-key": "ssh-" + "ed25519 " + "AAAA" + "C3NzaC1lZDI1NTE5" + "A" * 12,
        "token": "ghp" + "_" + "a1B2" * 9,
        "cjk": "note \u65e5\u672c\u8a9e",
    }
    failures = 0

    def check(label: str, ok: bool) -> None:
        nonlocal failures
        print(f"self-test {label}: {'ok' if ok else 'FAIL'}")
        if not ok:
            failures += 1

    for kind, sample in samples.items():
        check(kind, kind in {h.kind for h in scan_text(sample, generic) if not h.allowed})
    check("clean-text", not scan_text("version 1.2.3 and 10.0.0 and a@b, caf\u00e9", generic))

    marked = scan_text(samples["ipv4"] + "  # " + ALLOW_MARKER, generic)
    check("allow-marker", bool(marked) and all(h.allowed for h in marked))

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"
        root.mkdir()
        outside = base / "deny.txt"
        outside.write_text("# comment\n\nsecretword\\d+\n", encoding="utf-8")
        inside = root / "deny.txt"
        inside.write_text("secretword\n", encoding="utf-8")
        hits = scan_text("a line\nhas SecretWord42 here", load_denylist(outside))
        check(
            "denylist-hit",
            len(hits) == 1
            and hits[0].line == 2
            and hits[0].kind == "denylist pattern #3"
            and hits[0].excerpt is None,
        )
        check("denylist-outside-root", not denylist_inside_root(outside, root))
        check("denylist-inside-root-refused", denylist_inside_root(inside, root))
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="confidentiality_scan",
        description="Scan a repository for material that must not ship.",
    )
    parser.add_argument("--root", type=Path, help="directory to scan (default: git top-level of cwd)")
    parser.add_argument(
        "--denylist",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help="file of regular expressions, one per line, kept outside the repository (repeatable)",
    )
    parser.add_argument("--self-test", action="store_true", help="prove the scanner catches each kind")
    parser.add_argument("--quiet", action="store_true", help="print hits only, no summary")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    if args.self_test:
        return 1 if self_test() else 0

    root = args.root or git_toplevel()
    if root is None or not root.is_dir():
        print("error: no repository found; pass --root", file=sys.stderr)
        return 2
    root = root.resolve()

    patterns = generic_patterns()
    for index, denylist in enumerate(args.denylist, start=1):
        if denylist_inside_root(denylist, root):
            print("error: a denylist must live outside the scanned root", file=sys.stderr)
            return 2
        try:
            patterns += load_denylist(denylist, index)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    found, allowed, scanned = scan_root(root, patterns)
    for rel, hit in found:
        suffix = f" | {hit.excerpt}" if hit.excerpt else ""
        print(f"{rel}:{hit.line}: {hit.kind}{suffix}")
    if not args.quiet:
        print(f"scanned {scanned} files: {len(found)} hits, {allowed} lines opted out with {ALLOW_MARKER}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
