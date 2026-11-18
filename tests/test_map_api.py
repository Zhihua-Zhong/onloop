"""GET /api/map (zoom 0), the map's stuck thresholds from onloopd.yaml, and the speed budget (§6: 50 ms)."""

from __future__ import annotations

import datetime as dt
import http.client
import importlib.util
import json
import statistics
import threading
import time
from pathlib import Path

import pytest

from onloop import derive
from onloop.contract import reader
from onloop.daemon import config as cfg_mod
from onloop.daemon import server as srv
from onloop.overlay.ledger import Ledger

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("contract_example_api", ROOT / "tools" / "contract_example.py")
example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(example)
END = dt.datetime(2026, 11, 17, 2, 0, 0, tzinfo=dt.UTC)


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    root = tmp_path_factory.mktemp("replay")
    example.write(root, END, days=2, n=1000, seed=3, contract="1.1")
    (root / "onloop").mkdir()
    reader.import_snapshots(root, root / "onloop" / "ledger.db")
    return root / "onloop" / "ledger.db"


@pytest.fixture
def server(replay):
    led = Ledger(replay, readonly=True)
    s = srv.make_server(led, port=0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()
    led.close()


def _get(s, path):
    port = s.server_address[1]
    c = http.client.HTTPConnection(srv.HOST, port, timeout=10)
    c.request("GET", path, headers={"Host": f"{srv.HOST}:{port}"})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, json.loads(body)


def test_the_map_api_serves_zoom_0(server):
    status, body = _get(server, "/api/map")
    assert status == 200 and body["level"] == 0 and body["path"] == []
    assert [b["kind"] for b in body["blocks"]] == ["checklist", "feedback", "changes"]
    assert set(body["signals"]) == {"waiting_on_you", "stuck", "longest_without_a_move"}
    assert body["legend"][0] == {"colour": "red", "shape": "!", "label": "jammed"}


def test_a_deeper_path_is_refused_in_plain_words_until_zoom_1_ships(server):
    status, body = _get(server, "/api/map?path=goal:release-plan")
    assert status == 400 and "zoom 1 comes next" in body["error"]


def test_onloopd_yaml_sets_the_stuck_thresholds_in_hours():
    c = cfg_mod.parse({"overview": {"stuck": {"declared": "2h", "pr": "1d"}}})
    assert dict(c.overview_stuck) == {"declared": 7200, "pr": 86400}
    with pytest.raises(cfg_mod.ConfigError, match="unknown step 'built'"):
        cfg_mod.parse({"overview": {"stuck": {"built": "2h"}}})
    with pytest.raises(cfg_mod.ConfigError, match="not a duration"):
        cfg_mod.parse({"overview": {"stuck": {"given": "soon"}}})
    with pytest.raises(cfg_mod.ConfigError, match="unknown key 'colours'"):
        cfg_mod.parse({"overview": {"colours": {}}})


def test_the_thresholds_onloopd_sets_are_the_maps_and_the_legends(replay):
    try:
        derive.set_stuck({"declared": 1800})
        with Ledger(replay, readonly=True) as led:
            z = derive.zoom0(led, "2026-11-17T03:00:00.000Z")
        assert z["thresholds_s"]["declared"] == 1800 and z["thresholds_s"]["given"] == 2 * 86400
        assert next(b for b in z["blocks"] if b["kind"] == "feedback")["tally"]["red"] == 1
    finally:
        derive.set_stuck({})
    assert derive.stuck_thresholds() == derive.DEFAULT_STUCK_S


def test_zoom_0_is_within_the_50_ms_budget_on_a_week_sized_replay(server):
    _get(server, "/api/map")  # warm
    times = []
    for _ in range(20):
        t0 = time.perf_counter()
        status, _ = _get(server, "/api/map")
        times.append((time.perf_counter() - t0) * 1000)
        assert status == 200
    assert statistics.median(times) < 50, f"median {statistics.median(times):.1f} ms over 20 calls"
