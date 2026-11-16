"""The outcome line (decisions 0007 and 0012): one grammar, read only from the last line, failing closed."""

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
    assert len(examples) == 12
    for line in examples:
        assert isinstance(d.parse(line), d.Outcome), line
    template = re.findall(r"^outcome: (\w+)", text.split("**Asking the person.**")[0], re.M)
    assert template == list(d.OUTCOMES)  # the paragraph names every value, in order
    # the ask example in the paragraph itself parses too
    ask = re.findall(r"^outcome: .+$", text.split("**Asking the person.**")[1].split("Examples")[0], re.M)
    assert ask and all(isinstance(d.parse(a.replace("<the id onloop ask printed>", "t-1")), d.Outcome) for a in ask)


def test_the_parser_bites_in_the_selftest():
    ok, lines = d.self_test()
    assert ok, lines


# -- version 2 (decision 0012) ------------------------------------------------------------------------------------------

V2_VALID = [
    "outcome: blocked owner=human ref=#1",
    "outcome: blocked owner=human ref=pr#45 goal=demo/14 weight=critical days=2 effect=\"a, so b\" holds=\"x waits\"",
    "outcome: blocked owner=human ref=#1 cost_from=2026-11-20 holds=x effect=\"a, so b\"",
    "outcome: handoff owner=human ref=#1 days=? holds=x",
    "outcome: in_review ref=pr#2 owner=human weight=supporting goal=none",
    "outcome: done ref=pr#3 goal=demo/14",
    "outcome: done resolves=t-1a2b",
    "outcome: continue ref=pr#4 waker=event:gh-run:9876543210",
    "outcome: continue ref=pr#4 waker=event:gh-checks:owner/name/3f2a9c1 goal=demo/2",
    "outcome: continue ref=pr#4 waker=event:gh-review:pr48",
    "outcome: blocked owner=human ref=#1 days=0 holds=x",
    "outcome: blocked owner=human ref=#1 days=365 holds=x",
]


@pytest.mark.parametrize("text", V2_VALID)
def test_version_2_lines_parse(text):
    assert isinstance(d.parse(text), d.Outcome), d.parse(text)


@pytest.mark.parametrize(
    ("text", "because"),
    [
        ("outcome: continue ref=#1 waker=pid:0", "not 0"),
        ("outcome: blocked owner=human ref=#1 weight=urgent", "weight"),
        ("outcome: blocked owner=human ref=#1 goal=14", "goal"),
        ("outcome: blocked owner=human ref=#1 cost_from=2026-11-20 days=3", "not both"),
        ("outcome: blocked owner=human ref=#1 cost_from=20-11-2026", "YYYY-MM-DD"),
        ("outcome: blocked owner=human ref=#1 cost_from=2026-02-30", "calendar date"),
        ("outcome: blocked owner=human ref=#1 days=366", "days"),
        ("outcome: blocked owner=human ref=#1 days=-1", "days"),
        ("outcome: blocked owner=human ref=#1 days=soon", "days"),
        ("outcome: blocked owner=human ref=#1 effect=\"" + "x" * 201 + "\"", "longer than 200"),
        ("outcome: idle goal=demo/1", "takes no goal"),
        ("outcome: done weight=critical", "takes no weight"),
        ("outcome: continue ref=#1 waker=event:gh-run", "waker"),
        ("outcome: continue ref=#1 waker=event:GH:1", "waker"),
        ("outcome: continue ref=pr#49 follow-up waker=event:gh-checks:a1b2", "double quotes"),
    ],
)
def test_version_2_refusals(text, because):
    got = d.parse(text)
    assert isinstance(got, d.ParseError), got
    assert because in got.reason


def test_the_ask_fields_are_kept_as_written():
    o = d.parse(
        'outcome: blocked owner=human ref=pr#45 goal=demo/14 weight=critical days=2 effect="approve it, so it merges" '
        'holds="the demo waits" default="nothing merges"'
    )
    assert o.ask == {
        "goal": "demo/14",
        "weight": "critical",
        "days": "2",
        "effect": "approve it, so it merges",
        "holds": "the demo waits",
        "default": "nothing merges",
    }
    assert o.cost_from is None  # a count is turned into a date at ingest, not by the parser


def test_a_version_2_outcome_formats_and_parses_back():
    o = d.Outcome(
        "blocked",
        ref="pr#45",
        owner="human",
        goal="demo/14",
        weight="critical",
        cost_from="2026-11-20",
        effect="approve it, so it merges",
        holds="the demo waits",
    )
    line = d.format_line(o)
    assert line == (
        'outcome: blocked owner=human ref=pr#45 goal=demo/14 weight=critical cost_from=2026-11-20 '
        'effect="approve it, so it merges" holds="the demo waits"'
    )
    assert d.parse(line) == d.Outcome(**{**o.__dict__, "line": line})


def _v1_corpus():
    """The frozen version-1 parser, and every line the version-1 tests, its self-test and the examples use."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "disposition_v1", pathlib.Path(__file__).resolve().parent / "fixtures" / "disposition_v1.py"
    )
    v1 = importlib.util.module_from_spec(spec)
    sys.modules["disposition_v1"] = v1  # a dataclass looks its module up while it is built
    spec.loader.exec_module(v1)
    lines = [case[0] for case in test_valid_lines_parse.pytestmark[0].args[1]]
    lines += [case[0] for case in test_invalid_lines_fail_closed_and_say_why.pytestmark[0].args[1]]
    lines += [
        "",
        "outcome: done\nI'll continue later.",
        "outcome: blocked ref=#1",
        "outcome: continue ref=#1",
        "outcome: idle ref=#1",
        "work done\n\noutcome: continue ref=pr#4 waker=pid:42",
        "outcome: continue ref=#1 waker=hope:soon",
        "outcome: continue ref=#1 waker=pid:0",
        "outcome: blocked owner=human ref=#1 goal=demo/1",
        "outcome: continue ref=#1 waker=event:gh-run:1",
    ]
    text = EXAMPLES.read_text(encoding="utf-8")
    lines += re.findall(r"^outcome: .+$", text, re.M)
    return v1, lines


def test_every_version_1_line_parses_the_same_under_version_2_except_pid_0():
    """Backward compatibility (decision 0012): a line version 1 accepted gives the same value, ref, owner and waker;
    the only line version 1 accepted and version 2 refuses is `waker=pid:0`. Lines version 1 refused that version 2
    accepts carry a new key or an `event:` waker."""
    v1, corpus = _v1_corpus()
    narrowed, widened = [], []
    for text in corpus:
        a, b = v1.parse(text), d.parse(text)
        if isinstance(a, v1.Outcome):
            if isinstance(b, d.ParseError):
                narrowed.append(text)
            else:
                assert (a.value, a.ref, a.owner, a.waker) == (b.value, b.ref, b.owner, b.waker), text
                assert b.ask == {} and b.resolves is None, text
        elif isinstance(b, d.Outcome):
            widened.append(text)
    assert narrowed == ["outcome: continue ref=#1 waker=pid:0"]
    assert widened and all(("=" in w.split("outcome:")[1]) for w in widened)
    for w in widened:
        o = d.parse(w)
        assert o.ask or o.resolves or (o.waker or "").startswith("event:"), w


def test_an_older_onloop_refuses_a_version_2_line():
    """The rollout rule: a role writes the new keys only once its onloop is 0.5.0 or later."""
    v1, _ = _v1_corpus()
    got = v1.parse("outcome: blocked owner=human ref=#1 goal=demo/14")
    assert isinstance(got, v1.ParseError) and "takes no goal" in got.reason
