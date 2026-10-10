"""Tests for tools/confidentiality_scan.py. Samples are built at runtime so this file stays clean."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "confidentiality_scan.py"
_spec = importlib.util.spec_from_file_location("confidentiality_scan", TOOL)
scan = importlib.util.module_from_spec(_spec)
sys.modules["confidentiality_scan"] = scan
_spec.loader.exec_module(scan)

DASHES = "-" * 5
IP = ".".join(["198", "51", "100", "23"])
EMAIL = "who" + "@" + "mail" + ".example"
WIN_PATH = "C:" + "\\" + "Users" + "\\" + "someone" + "\\" + "doc.txt"
JP = "\u65e5\u672c\u8a9e"
KANA = "\u30ab\u30bf\u30ab\u30ca"
FULLWIDTH = "\uff21\uff22"

GENERIC = scan.generic_patterns()


def kinds(text: str) -> set[str]:
    return {h.kind for h in scan.scan_text(text, GENERIC)}


@pytest.mark.parametrize(
    ("kind", "sample"),
    [
        ("ipv4", f"host {IP} up"),
        ("ipv4", ".".join(["0"] * 4)),
        ("ipv4", ".".join(["255"] * 4)),
        ("email", f"mail {EMAIL}"),
        ("windows-user-path", WIN_PATH),
        ("windows-user-path", WIN_PATH.replace("\\", "/")),
        ("windows-user-path", WIN_PATH.lower()),
        ("key-block", DASHES + "BEGIN RSA PRIVATE KEY" + DASHES),
        ("key-block", DASHES + "BEGIN PRIVATE KEY" + DASHES),
        ("key-block", DASHES + "BEGIN CERTIFICATE" + DASHES),
        ("ssh-key", "ssh-rsa " + "AAAA" + "B3NzaC1yc2E" + "A" * 20),
        ("ssh-key", "ssh-ed25519 " + "AAAA" + "C3NzaC1lZDI1NTE5" + "A" * 12),
        ("token", "ghp" + "_" + "x" * 36),
        ("token", "github" + "_pat_" + "A1b2C3d4E5f6G7h8I9j0K1"),
        ("token", "xox" + "b-" + "1234567890-abcdef"),
        ("token", "AKIA" + "ABCDEFGHIJKLMNOP"),
        ("cjk", JP),
        ("cjk", KANA),
        ("cjk", FULLWIDTH),
        ("cjk", "\u3001"),
    ],
)
def test_generic_kind_is_caught(kind: str, sample: str) -> None:
    assert kind in kinds(sample)


@pytest.mark.parametrize(
    "sample",
    [
        "version 1.2.3",
        "10.0.0",
        "1.2.3.4.5",
        "256.1.1.1",
        "a" + "@" + "b",
        "decorator @pytest.fixture",
        "plain ASCII text, nothing to see",
        "caf\u00e9 na\u00efve \u00fcber \u00e5ngstr\u00f6m",
        "relative/path/to/file.txt",
        "C:" + "\\" + "Windows" + "\\" + "System32",
        DASHES + "BEGIN" + DASHES,
        "ghp" + "_" + "short",
    ],
)
def test_clean_sample_is_not_flagged(sample: str) -> None:
    assert kinds(sample) == set()


def test_line_numbers_are_reported() -> None:
    hits = scan.scan_text("fine\nstill fine\n" + IP, GENERIC)
    assert [(h.line, h.kind) for h in hits] == [(3, "ipv4")]


def test_secret_kinds_do_not_print_an_excerpt() -> None:
    hits = scan.scan_text("ghp" + "_" + "x" * 36, GENERIC)
    assert hits[0].excerpt is None
    hits = scan.scan_text("host " + IP, GENERIC)
    assert hits[0].excerpt and IP in hits[0].excerpt


def test_allow_marker_opts_out_and_is_counted(tmp_path: Path) -> None:
    text = f"{IP}  # {scan.ALLOW_MARKER}\n{IP}\n"
    hits = scan.scan_text(text, GENERIC)
    assert [h.allowed for h in hits] == [True, False]
    (tmp_path / "f.txt").write_text(text, encoding="utf-8")
    found, allowed, scanned = scan.scan_root(tmp_path, GENERIC)
    assert (len(found), allowed, scanned) == (1, 1, 1)


def test_denylist_hit_is_case_insensitive_and_hides_the_pattern(tmp_path: Path) -> None:
    deny = tmp_path / "deny.txt"
    deny.write_text("# a comment\n\nzebra\\d+\n", encoding="utf-8")
    patterns = scan.load_denylist(deny)
    assert len(patterns) == 1
    hits = scan.scan_text("one\nthe ZEBRA42 line", patterns)
    assert [(h.line, h.kind, h.excerpt) for h in hits] == [(2, "denylist pattern #3", None)]


def test_denylist_comments_and_blanks_are_ignored(tmp_path: Path) -> None:
    deny = tmp_path / "deny.txt"
    deny.write_text("# zebra\n\n   \n# more\n", encoding="utf-8")
    assert scan.load_denylist(deny) == []


def test_unreadable_denylist_raises_without_quoting_patterns(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot read"):
        scan.load_denylist(tmp_path / "missing.txt")
    bad = tmp_path / "bad.txt"
    bad.write_text("zebra(\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1") as info:
        scan.load_denylist(bad)
    assert "zebra" not in str(info.value)


def test_large_and_binary_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "bin.dat").write_bytes(b"\0" + IP.encode())
    (tmp_path / "big.txt").write_text(IP + "\n" + "x" * scan.MAX_BYTES, encoding="utf-8")
    found, _, scanned = scan.scan_root(tmp_path, GENERIC)
    assert found == [] and scanned == 0


def test_scanner_and_test_sources_are_clean() -> None:
    for path in (TOOL, Path(__file__)):
        assert scan.scan_text(path.read_text(encoding="utf-8"), GENERIC) == [], path.name


# --- CLI, via main() -------------------------------------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    return path


@needs_git
def test_cli_clean_repo_exits_zero(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    assert scan.main(["--root", str(repo)]) == 0
    assert "0 hits" in capsys.readouterr().out


@needs_git
def test_cli_finds_untracked_file_and_prints_path_line_kind(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "a.txt").write_text("ok\n" + IP + "\n", encoding="utf-8")
    assert scan.main(["--root", str(repo)]) == 1
    assert "a.txt:2: ipv4" in capsys.readouterr().out


@needs_git
def test_cli_ignores_gitignored_files(repo: Path) -> None:
    (repo / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    (repo / "secret.txt").write_text(IP, encoding="utf-8")
    assert scan.main(["--root", str(repo), "--quiet"]) == 0


@needs_git
def test_cli_scans_tracked_files(repo: Path) -> None:
    (repo / "a.txt").write_text(EMAIL, encoding="utf-8")
    _git(repo, "add", "a.txt")
    assert scan.main(["--root", str(repo), "--quiet"]) == 1


@needs_git
def test_cli_denylist_output_leaks_nothing(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "a.txt").write_text("fine\nan Obscure-Term here\n", encoding="utf-8")
    deny = tmp_path / "outside.txt"
    deny.write_text("# header\nobscure-term\n", encoding="utf-8")
    assert scan.main(["--root", str(repo), "--denylist", str(deny)]) == 1
    out = capsys.readouterr().out
    assert "a.txt:2: denylist pattern #2" in out
    assert "bscure" not in out


@needs_git
def test_cli_refuses_denylist_inside_root(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    deny = repo / "deny.txt"
    deny.write_text("anything\n", encoding="utf-8")
    assert scan.main(["--root", str(repo), "--denylist", str(deny)]) == 2
    assert "outside" in capsys.readouterr().err


@needs_git
def test_cli_unreadable_denylist_is_a_usage_error(repo: Path, tmp_path: Path) -> None:
    assert scan.main(["--root", str(repo), "--denylist", str(tmp_path / "nope.txt")]) == 2


def test_cli_missing_root_is_a_usage_error(tmp_path: Path) -> None:
    assert scan.main(["--root", str(tmp_path / "does-not-exist")]) == 2


def test_self_test_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert scan.main(["--self-test"]) == 0
    out = capsys.readouterr().out
    for kind in ("ipv4", "email", "windows-user-path", "key-block", "ssh-key", "token", "cjk"):
        assert f"self-test {kind}: ok" in out
    assert "FAIL" not in out


def test_self_test_bites_when_a_pattern_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    original = scan.generic_patterns
    monkeypatch.setattr(scan, "generic_patterns", lambda: [p for p in original() if p.kind != "ipv4"])
    assert scan.self_test() >= 1


def test_a_git_ref_after_an_at_sign_is_not_an_email():
    assert "email" not in kinds("pip install git+https://host.example/owner/repo@v0.1.0")


def test_a_diff_line_prefix_before_an_at_sign_is_not_an_email():
    assert "email" not in kinds("+@pytest.fixture")
    assert "email" not in kinds("-@pytest.mark.parametrize")
