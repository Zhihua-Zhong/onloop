"""`enforce:` turns two pre-flight warnings into gate refusals. Without it nothing about a decision changes."""

import json
import subprocess

import pytest

from onloop import audit, gate, preflight
from onloop import policy as pol
from onloop.classify import classify
from onloop.gitutil import Git
from onloop.testing import FakeGit

from .conftest import review


def strict(raw, **fields):
    return pol.from_dict(dict(raw, enforce=fields))


def decide(policy, facts, paths, gained=None):
    c = classify(paths, policy)
    c.gained_cr = gained
    return gate.decide_refusals(facts, policy, c)


# -- the block -----------------------------------------------------------------------------------------------------------------------------


def test_the_default_is_to_warn(policy):
    assert policy.enforce == {"line_endings": "warn", "uncovered_paths": "warn"}


@pytest.mark.parametrize(
    "block,fragment",
    [
        ({"line_endings": "stop"}, "enforce.line_endings must be one of"),
        ({"nothing": "refuse"}, "unknown field"),
        ("refuse", "must be a mapping"),
    ],
)
def test_a_bad_enforce_block_is_a_policy_problem(raw, block, fragment):
    raw["enforce"] = block
    assert any(fragment in p for p in pol.validate(raw))


def test_making_a_refusal_a_warning_is_a_loosening(raw):
    a, b = strict(raw, line_endings="refuse", uncovered_paths="refuse"), strict(raw, line_endings="warn")
    got = pol.loosening(a, b)
    assert any("enforce.line_endings" in g for g in got) and any("enforce.uncovered_paths" in g for g in got)
    assert not pol.loosening(b, a)  # tightening is not
    assert not pol.loosening(a, a)


# -- the refusals --------------------------------------------------------------------------------------------------------------------------


def test_with_no_enforce_block_no_p_code_appears(policy, facts):
    got = decide(policy, facts, ["src/a.py", "stray.txt"], gained=["src/a.py"])
    assert not {r.code for r in got} & {"P004", "P005"}


def test_gained_carriage_returns_refuse_only_when_asked(raw, facts):
    p = strict(raw, line_endings="refuse")
    got = decide(p, facts, ["src/a.py"], gained=["src/a.py", "src/b.py"])
    assert [r.code for r in got] == ["P004"] and "src/a.py, src/b.py" in got[0].text
    assert decide(p, facts, ["src/a.py"], gained=[]) == []


def test_an_unknown_line_ending_finding_refuses_rather_than_passes(raw, facts):
    got = decide(strict(raw, line_endings="refuse"), facts, ["src/a.py"], gained=None)
    assert [r.code for r in got] == ["P004"] and "could not be checked" in got[0].text


def test_an_uncovered_path_refuses_only_when_asked(raw, facts):
    p = strict(raw, uncovered_paths="refuse")
    got = decide(p, facts, ["src/a.py", "stray.txt", "also/stray.txt"])
    assert [r.code for r in got] == ["P005"] and "also/stray.txt, stray.txt" in got[0].text
    assert decide(p, facts, ["src/a.py"]) == []


# -- byte-identical decisions without the block ----------------------------------------------------------------------------------------

FIXTURES = [
    ({}, ["src/a.py"]),
    ({}, ["src/a.py", "stray.txt"]),
    ({"state": "CLOSED"}, ["src/a.py"]),
    ({"draft": True}, ["core/x.py"]),
    ({"title": "fix"}, ["src/a.py"]),
    ({"title": "[ghost] x"}, ["src/a.py"]),
    ({"title": "[builder] x"}, ["docs/claims/x.md"]),
    ({"reviews": []}, ["src/a.py"]),
    ({"checks": []}, ["src/a.py"]),
    ({"base": "other"}, ["docs/copy/x.md"]),
    ({}, ["onloop.yaml"]),
    ({"reviews": [review(commit="b" * 40)]}, ["src/a.py", "stray.txt"]),
]


@pytest.mark.parametrize("over,paths", FIXTURES)
def test_a_policy_with_no_enforce_block_judges_exactly_as_one_that_names_the_defaults(
    raw, facts, over, paths
):
    plain, named = pol.from_dict(raw), strict(raw, line_endings="warn", uncovered_paths="warn")
    f = dict(facts, **over)
    a = json.dumps([(r.code, r.text) for r in decide(plain, f, paths)])
    b = json.dumps([(r.code, r.text) for r in decide(named, f, paths, gained=["src/a.py"])])
    assert a == b
    assert (
        classify(paths, plain).as_dict() == classify(paths, named).as_dict()
    )  # the new field is not published


def test_the_classification_does_not_publish_the_carriage_return_finding(policy):
    c = classify(["src/a.py"], policy)
    c.gained_cr = ["src/a.py"]
    assert "gained_cr" not in c.as_dict()


# -- pre-flight --------------------------------------------------------------------------------------------------------------------------------


def test_pre_flight_reports_the_enforced_findings_as_problems_not_warnings(raw, facts):
    patches = {"src/a.py": "@@ -1 +1 @@\n-x\n+y\r"}
    plain = preflight.report(facts, pol.from_dict(raw), ["src/a.py", "stray.txt"], patches)
    assert plain["warnings"] and plain["codes"] == []
    hard = preflight.report(
        facts,
        strict(raw, line_endings="refuse", uncovered_paths="refuse"),
        ["src/a.py", "stray.txt"],
        patches,
    )
    assert sorted(hard["codes"]) == ["P004", "P005"] and not hard["warnings"] and not preflight.clean(hard)


# -- reading the carriage returns from git ----------------------------------------------------------------------------------------------------


def git(cwd, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "user.name=t",
            "-c",
            "user.email=" + "t" + "@" + "t.test",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_git_lists_a_file_that_gained_a_carriage_return_and_not_one_that_was_merely_touched(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "old.txt").write_bytes(b"a\r\nb\r\nc\r\n")
    (tmp_path / "unix.txt").write_bytes(b"a\nb\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "checkout", "-qb", "work")
    (tmp_path / "old.txt").write_bytes(b"a\r\nB\r\nc\r\n")  # touched; its new line is still CRLF
    (tmp_path / "unix.txt").write_bytes(b"a\nb\nc\r\n")  # gains one
    (tmp_path / "new.txt").write_bytes(b"one\r\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "work")
    got = Git(tmp_path).gained_cr("main", "work")
    assert got == ["new.txt", "unix.txt"]


def test_a_file_that_only_lost_its_carriage_returns_is_not_listed(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "f.txt").write_bytes(b"a\r\nb\r\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "checkout", "-qb", "work")
    (tmp_path / "f.txt").write_bytes(b"a\nb\n")
    git(tmp_path, "commit", "-qam", "lf")
    assert Git(tmp_path).gained_cr("main", "work") == []


def test_audit_and_gate_use_the_same_code_table():
    assert audit.decide_refusals is gate.decide_refusals


def test_the_fake_git_answers_the_question(tmp_path):
    f = FakeGit()
    f.gained = ["x"]
    assert f.gained_cr("a", "b") == ["x"]


# -- the gate and the audit wire it ----------------------------------------------------------------------------------------------------------------


def test_the_gate_refuses_a_merge_that_gains_carriage_returns_when_asked_and_not_otherwise(raw):
    from .test_gate import _world

    git, client = _world(dict(raw, enforce={"line_endings": "refuse"}), ["src/a.py"])
    git.gained = ["src/a.py"]
    code, notes = gate.run(7, client, git)
    assert code == 1 and client.merged == [] and any("carriage-return" in n for n in notes)
    git, client = _world(raw, ["src/a.py"])
    git.gained = ["src/a.py"]
    assert gate.run(7, client, git)[0] == 0  # the default warns, so the same change merges


def test_the_gate_refuses_a_path_nobody_owns_when_asked(raw):
    from .test_gate import _world

    git, client = _world(dict(raw, enforce={"uncovered_paths": "refuse"}), ["src/a.py", "stray.txt"])
    code, notes = gate.run(7, client, git)
    assert code == 1 and client.merged == [] and any("stray.txt" in n for n in notes)


def test_the_gate_cannot_decide_when_the_line_endings_cannot_be_read(raw):
    from .test_gate import _world

    git, client = _world(dict(raw, enforce={"line_endings": "refuse"}), ["src/a.py"])

    def broken(base, head):
        raise RuntimeError("no object")

    git.gained_cr = broken
    code, notes = gate.run(7, client, git)
    assert code == 1 and client.merged == [] and any("line endings" in n for n in notes)


def test_the_audit_judges_a_merge_by_the_same_enforcement(raw):
    from .test_audit import ROOT, alarms, world

    strict_raw = dict(raw, enforce={"line_endings": "refuse"})
    git, client = world(strict_raw)
    git.gained = ["src/a.py"]
    got = alarms(git, client)
    assert len(got) == 1 and "carriage-return" in got[0][1]
    git, client = world(raw)
    git.gained = ["src/a.py"]
    assert alarms(git, client) == []
    assert ROOT


def test_the_audit_alarms_when_the_line_endings_cannot_be_read(raw):
    from .test_audit import alarms, world

    git, client = world(dict(raw, enforce={"line_endings": "refuse"}))

    def broken(base, head):
        raise RuntimeError("no object")

    git.gained_cr = broken
    got = alarms(git, client)
    assert len(got) == 1 and "line endings" in got[0][1]


def test_removing_the_block_altogether_is_a_loosening(raw):
    assert pol.loosening(strict(raw, line_endings="refuse"), pol.from_dict(raw))


# -- reading the names from --raw -z ----------------------------------------------------------------------------------------------------------


def raw_of(*names):
    return b"".join(b":100644 100644 aaaaaaa bbbbbbb M" + b"\0" + n.encode("utf-8") + b"\0" for n in names)


def section(header, *lines):
    return (
        b"diff --git "
        + header
        + b"\nindex aaaaaaa..bbbbbbb 100644\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n"
        + b"\n".join(lines)
        + b"\n"
    )


def test_a_quoted_path_as_a_linux_git_writes_it_is_read_from_the_raw_names():
    name = 'a"b\tc.txt'
    patch = section(b'"a/a\\"b\tc.txt" "b/a\\"b\tc.txt"', b"+new\r") + section(b"a/ok.txt b/ok.txt", b"+fine")
    from onloop.gitutil import parse_gained_cr

    assert parse_gained_cr(raw_of(name, "ok.txt"), patch) == [name]


def test_a_non_ascii_name_is_read_whole():
    from onloop.gitutil import parse_gained_cr

    name = "caf\u00e9 \u65e5\u672c.txt"
    patch = section(f"a/{name} b/{name}".encode(), b"+x\r")
    assert parse_gained_cr(raw_of(name), patch) == [name]


def test_names_and_patch_that_do_not_line_up_report_every_changed_path():
    from onloop.gitutil import parse_gained_cr

    patch = section(b"a/one.txt b/one.txt", b"+x\r")
    assert parse_gained_cr(raw_of("one.txt", "two.txt"), patch) == ["one.txt", "two.txt"]
    assert parse_gained_cr(raw_of("one.txt"), patch + patch) == ["one.txt"]


@pytest.mark.parametrize("raw", [b"garbage\0one.txt\0", b":100644 100644 a b M\0"])
def test_a_file_list_that_cannot_be_read_fails_closed(raw):
    from onloop.gitutil import parse_gained_cr

    assert parse_gained_cr(raw, section(b"a/one.txt b/one.txt", b"+x")) != []


def test_a_content_line_that_looks_like_a_header_is_still_counted():
    from onloop.gitutil import parse_gained_cr

    # a removed line "-- a" shows as "--- a"; an added line "++ b" shows as "+++ b"
    same = section(b"a/s.sql b/s.sql", b"--- a\r", b"+-- a\r")
    assert parse_gained_cr(raw_of("s.sql"), same) == []
    gained = section(b"a/s.sql b/s.sql", b"+++ b\r")
    assert parse_gained_cr(raw_of("s.sql"), gained) == ["s.sql"]


def test_a_binary_file_and_a_mode_change_gain_nothing():
    from onloop.gitutil import parse_gained_cr

    patch = b"diff --git a/b.bin b/b.bin\nBinary files differ\ndiff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n"
    assert parse_gained_cr(raw_of("b.bin", "run.sh"), patch) == []


def test_the_known_limit_one_edit_that_swaps_a_crlf_line_for_a_bare_cr_line_nets_to_zero():
    from onloop.gitutil import parse_gained_cr

    patch = section(b"a/m.txt b/m.txt", b"-old\r", b"+new\r")  # documented next to P004
    assert parse_gained_cr(raw_of("m.txt"), patch) == []


CR = bytes([13])


def test_a_file_name_that_ends_in_a_carriage_return_does_not_count_as_a_gained_line():
    from onloop.gitutil import parse_gained_cr

    lines = [
        b"diff --git a/odd" + CR + b" b/odd" + CR,
        b"new file mode 100644",
        b"index 0..b",
        b"--- /dev/null",
        b"+++ b/odd" + CR,
        b"@@ -0,0 +1 @@",
        b"+new",
        b"",
    ]
    patch = bytes([10]).join(lines)
    assert parse_gained_cr(raw_of("odd" + CR.decode()), patch) == []
