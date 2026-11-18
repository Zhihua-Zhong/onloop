"""The map's zoom 0 acceptance on made-up data: the three answers a person gets at a glance, read from zoom 0 with no
click (the overview plan's §12.9, in the generic replay's terms). The real replay's answers are checked in the
exchange folder, never here."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from onloop import derive
from onloop.contract import reader
from onloop.overlay.ledger import Ledger

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("contract_example_acc", ROOT / "tools" / "contract_example.py")
example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(example)
END = dt.datetime(2026, 11, 17, 2, 0, 0, tzinfo=dt.UTC)
NOW = "2026-11-17T03:00:00.000Z"


@pytest.fixture(scope="module")
def zoom0(tmp_path_factory):
    root = tmp_path_factory.mktemp("acceptance")
    example.write(root, END, days=2, n=400, seed=3, contract="1.1")
    (root / "onloop").mkdir()
    reader.import_snapshots(root, root / "onloop" / "ledger.db")
    with Ledger(root / "onloop" / "ledger.db", readonly=True) as led:
        return derive.zoom0(led, NOW)


def _block(z, kind):
    return next(b for b in z["blocks"] if b["kind"] == kind)


def test_line_1_the_feedback_batch_says_how_far_it_is_in_one_line(zoom0):
    b = _block(zoom0, "feedback")
    assert b["bar"]["label"] == "0 of 3 accepted" and b["reported_by_team"]
    assert (b["tally"]["amber"], b["tally"]["moving"]) == (2, 1)


def test_line_2_the_oldest_wait_says_whose_it_is_and_how_old(zoom0):
    top = zoom0["waits"][0]
    assert zoom0["signals"]["waiting_on_you"] == 1
    you = next(w for w in zoom0["waits"] if w["holder"] == "you")
    assert you["oldest_text"] == "approve the change (pr#3), so it can move" and you["age_text"] == "2 days"
    assert top["holder"] == "you" and you["note"] is None  # the opening is in the export: an exact age


def test_line_3_the_longest_pause_is_on_the_signal_row(zoom0):
    lw = zoom0["signals"]["longest_without_a_move"]
    assert lw["text"] == "1 h 38 min, the whole team" and lw["seconds"] < 2 * 3600


def test_the_most_jammed_block_and_why_are_read_without_a_click(zoom0):
    """The glance check's key (state/design/map_zoom0_glance_key.md), registered before any run."""
    red = [b for b in zoom0["blocks"] if b["colour"] == "red"]
    assert [b["id"] for b in red] == ["changes"]
    assert red[0]["reason"]["text"] == "team: change pr#5 — holding it for 1 day"
