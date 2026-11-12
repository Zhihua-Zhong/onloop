"""The outcome line (decision 0007): one grammar, read only from the last line, failing closed."""

import pathlib
import re

import pytest

from onloop import disposition as d

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples" / "roles" / "outcome-line.md"


@pytest.mark.parametrize(
    ("text", "value", "ref", "owner", "waker"),
    [
        ("outcome: done", "done", None, None, None),
        ("outcome: done ref=#12", "done", "#12", None, None),
        ("outcome: idle", "idle", None, None, None),
        ("outcome: in_review", "in_review", None, None, None),
        ("outcome: in_review ref=pr#4 owner=reviewer", "in_review", "pr#4", "reviewer", None),
        (
            "outcome: blocked owner=operator ref=docs/runbook.md",
            "blocked",
            "docs/runbook.md",
            "operator",
            None,
        ),
        ("outcome: handoff ref=issue#7 owner=builder", "handoff", "issue#7", "builder", None),
        ("outcome: continue ref=pr#4 waker=pid:4242", "continue", "pr#4", None, "pid:4242"),
        (
            "outcome: continue ref=pr#4 waker=cron:nightly-build",
            "continue",
            "pr#4",
            None,
            "cron:nightly-build",
        ),
        (
            'outcome: continue ref="a b" waker=monitor:build-queue',
            "continue",
            "a b",
            None,
            "monitor:build-queue",
        ),
        (
            "Done with the parser.\n\nI ran the tests.\r\n\r\n  outcome: done ref=#1  \n\n",
            "done",
            "#1",
            None,
            None,
        ),
        ("Fertig. Prüfungen grün.\noutcome: done ref=docs/ü.md", "done", "docs/ü.md", None, None),
        (b"outcome: idle", "idle", None, None, None),
    ],
)
def test_valid_lines_parse(text, value, ref, owner, waker):
    got = d.parse(text)
    assert isinstance(got, d.Outcome), got
    assert (got.value, got.ref, got.owner, got.waker) == (value, ref, owner, waker)


@pytest.mark.parametrize(
    ("text", "because"),
    [
        (None, "no final message"),
        ("", "empty"),
        ("  \n \n", "empty"),
        (42, "not text"),
        (b"\xff\xfe", "not valid UTF-8"),
        ("outcome: done\ufffd", "undecodable"),
        ("I will set X before opening the PR.", "not an outcome line"),
        ("outcome: done\nI'll keep going.", "not an outcome line"),
        ("```\noutcome: done\n```", "not an outcome line"),
        ("Outcome: done", "not an outcome line"),
        ("outcome:", "names no value"),
        ("outcome: finished", "unknown outcome"),
        ("outcome: blocked ref=#1", "needs owner"),
        ("outcome: blocked owner=ops", "needs ref"),
        ("outcome: handoff owner=builder", "needs ref"),
        ("outcome: continue ref=#1", "needs waker"),
        ("outcome: continue waker=pid:1", "needs ref"),
        ("outcome: idle ref=#1", "takes no ref"),
        ("outcome: done waker=pid:1", "takes no waker"),
        ("outcome: done note=hi", "takes no note"),
        ("outcome: done ref=#1 ref=#2", "given twice"),
        ('outcome: done ref=""', "empty"),
        ("outcome: blocked owner=Ops ref=#1", "not a role name"),
        ("outcome: continue ref=#1 waker=pid:abc", "waker"),
        ("outcome: continue ref=#1 waker=hope:soon", "waker"),
        ("outcome: continue ref=#1 waker=pid:", "waker"),
        ('outcome: done ref="unclosed', "cannot read"),
        ("outcome: done ref=a=b=c extra", "cannot read"),
        ("outcome: done " + "x" * 3000, "longer than"),
    ],
)
def test_invalid_lines_fail_closed_and_say_why(text, because):
    got = d.parse(text)
    assert isinstance(got, d.ParseError), got
    assert because in got.reason


def test_a_formatted_outcome_parses_back_to_itself():
    for o in (
        d.Outcome("done"),
        d.Outcome("in_review", ref="pr#9", owner="reviewer"),
        d.Outcome("blocked", ref="needs a reviewer", owner="lead"),
        d.Outcome("continue", ref="#3", waker="monitor:queue.depth"),
    ):
        back = d.parse("some text\n" + d.format_line(o))
        assert isinstance(back, d.Outcome) and (back.value, back.ref, back.owner, back.waker) == (
            o.value,
            o.ref,
            o.owner,
            o.waker,
        )


def test_the_waker_names_its_kind_and_id():
    o = d.parse("outcome: continue ref=#1 waker=cron:nightly:0300")
    assert (o.waker_kind, o.waker_id) == ("cron", "nightly:0300")


def test_every_example_in_the_role_template_paragraph_parses():
    text = EXAMPLES.read_text(encoding="utf-8")
    examples = re.findall(r"^outcome: .+$", text.split("Examples that onloop accepts")[1], re.M)
    assert len(examples) == 8
    for line in examples:
        assert isinstance(d.parse(line), d.Outcome), line
    template = re.findall(r"^outcome: (\w+)", text.split("Examples that onloop accepts")[0], re.M)
    assert template == list(d.OUTCOMES)  # the paragraph names every value, in order


def test_the_parser_bites_in_the_selftest():
    ok, lines = d.self_test()
    assert ok, lines
