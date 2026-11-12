"""The carrier inside onloopd: its config block, the hook spool across a respawn, and `onloop say` through the message
route (decisions 0010 and 0011)."""

import io
import json
import threading

import pytest

from onloop import cli
from onloop.daemon import config as daemon_config
from onloop.daemon import hook_client, hook_receiver, messages_route
from onloop.daemon import server as srv
from onloop.daemon import token as token_mod
from onloop.daemon.routes import Routes
from onloop.overlay.ledger import Ledger

from . import carrier_helpers as h


def test_onloopd_yaml_takes_a_carrier_block_and_checks_it_whole(tmp_path):
    good = {
        "carrier": {
            "coordinator": "lead",
            "roles": {
                "builder": {
                    "session_id": h.SID,
                    "cwd": str(tmp_path),
                    "tools": ["Read"],
                    "opt_in": ["message"],
                }
            },
        }
    }
    cfg = daemon_config.parse(good)
    assert cfg.carrier.role("builder").opt_in == ("message",) and cfg.carrier.coordinator == "lead"
    assert daemon_config.parse({}).carrier is None  # not configured: no carrier at all
    with pytest.raises(daemon_config.ConfigError) as e:
        daemon_config.parse(
            {
                "carrier": {
                    "roles": {
                        "builder": {"session_id": "nope", "cwd": ".", "tools": ["Bash"], "opt_in": ["all"]}
                    }
                }
            }
        )
    text = str(e.value)
    assert "session_id" in text and "names a shell" in text and "opt_in" in text


def test_a_hook_spooled_while_onloopd_was_away_is_logged_once(tmp_path):
    payload = json.dumps(
        {"session_id": h.SID, "prompt_id": "p1", "last_assistant_message": "done\noutcome: done"}
    )
    assert (
        hook_client.post("stop", "builder", tmp_path, io.BytesIO(payload.encode())) == 0
    )  # no onloopd: spooled
    assert (
        hook_client.post("stop", "builder", tmp_path, io.BytesIO(payload.encode())) == 0
    )  # the same hook twice
    assert len((tmp_path / hook_client.SPOOL).read_text(encoding="utf-8").splitlines()) == 2
    with Ledger(tmp_path / "ledger.db") as led:
        assert hook_receiver.ingest_spool(led, tmp_path) == 1
        assert not (tmp_path / hook_client.SPOOL).exists()
        assert hook_receiver.ingest_spool(led, tmp_path) == 0
        t = led.select("SELECT role, session_id, outcome FROM turns")
        assert t == [{"role": "builder", "session_id": h.SID, "outcome": "done"}]
        ev = led.select("SELECT payload FROM events WHERE kind = 'hook.stop'")[0]["payload"]
        assert ev["spooled_at"] and "last_assistant_message" not in json.dumps(ev)  # no message text
    assert "spooled for onloopd" in (tmp_path / hook_client.ERROR_LOG).read_text(encoding="utf-8")


def test_a_bad_spool_line_is_kept_aside_not_dropped(tmp_path):
    (tmp_path / hook_client.SPOOL).write_text("not json\n", encoding="utf-8")
    with Ledger(tmp_path / "ledger.db") as led:
        assert hook_receiver.ingest_spool(led, tmp_path) == 0
    assert (tmp_path / "hook-spool.bad.jsonl").read_text(encoding="utf-8").strip() == "not json"


@pytest.fixture
def onloopd(tmp_path):
    """onloopd's server and message route over a ledger with a carried role, and its heartbeat and token files."""
    led = Ledger(tmp_path / "ledger.db")
    led.append(
        {
            "id": "b",
            "source": "config",
            "kind": "session.bound",
            "subject_ref": "role:builder",
            "payload": {"role": "builder", "session_id": h.SID},
        }
    )
    routes = Routes(led, threading.Lock())
    routes.add("/messages/", messages_route.make())
    secret, _ = token_mod.issue(tmp_path)
    server = srv.make_server(led, port=0, writer=routes, token=secret)
    port = server.server_address[1]
    (tmp_path / "onloopd.heartbeat.json").write_text(
        json.dumps({"port": port, "pid": 1, "at": "x"}), encoding="utf-8"
    )
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield led, port
    server.shutdown()
    server.server_close()
    led.close()


def test_onloop_say_posts_through_onloopd_and_marks_it_unverified(tmp_path, onloopd, capsys):
    led, _ = onloopd
    code = cli.main(["say", "builder", "please look at the parser", "--ledger", str(tmp_path / "ledger.db")])
    out = capsys.readouterr().out
    assert code == 0 and "not verified" in out and "has not opted in" in out
    m = led.select("SELECT author, channel, body FROM messages")[0]
    assert m == {"author": "human", "channel": "cli", "body": "please look at the parser"}
    d = led.select("SELECT kind, state, message_id FROM deliveries")[0]
    assert d["kind"] == "message" and d["state"] == "created"
    assert led.select("SELECT source FROM events WHERE kind = 'message.posted'") == [{"source": "cli"}]


def test_onloop_say_to_a_role_that_is_not_carried_is_refused(tmp_path, onloopd, capsys):
    code = cli.main(["say", "nobody", "hi", "--ledger", str(tmp_path / "ledger.db")])
    assert code == 2 and "not a carried role" in capsys.readouterr().err


def test_onloop_say_without_onloopd_says_so(tmp_path, capsys):
    code = cli.main(["say", "builder", "hi", "--ledger", str(tmp_path / "ledger.db")])
    assert code == 2 and "not reachable" in capsys.readouterr().err


def test_the_carrier_commands_start_stop_and_show(tmp_path, capsys):
    path = str(tmp_path / "ledger.db")
    assert cli.main(["carrier", "stop", "--ledger", path]) == 0
    assert (tmp_path / "carrier.off").exists()
    assert cli.main(["carrier", "status", "--ledger", path]) == 0
    out = capsys.readouterr().out
    assert '"kill_switch": true' in out and '"paused_reason": "kill_switch"' in out
    assert cli.main(["carrier", "start", "--ledger", path]) == 0
    assert not (tmp_path / "carrier.off").exists()
    with Ledger(tmp_path / "ledger.db") as led:
        assert led.select("SELECT enabled, paused_reason FROM carrier_state") == [
            {"enabled": 1, "paused_reason": None}
        ]


def test_role_opt_out_takes_effect_and_opt_in_cannot_pass_the_config(tmp_path, capsys):
    cfg = h.config(tmp_path, roles={"builder": h.role(cwd=tmp_path, opt_in=("message",))})
    c = h.carrier(tmp_path, cfg)
    h.start(c)
    c.led.close()
    path = str(tmp_path / "ledger.db")
    assert cli.main(["role", "opt-out", "builder", "message", "--ledger", path]) == 0
    assert cli.main(["role", "opt-in", "builder", "corrective", "--ledger", path]) == 0
    c = h.carrier(tmp_path, cfg)
    assert c.may_act(cfg.role("builder"), "message").startswith("observe-only")
    assert c.may_act(cfg.role("builder"), "corrective").startswith(
        "observe-only"
    )  # not in the config's opt_in
