"""The Conversation API and the UI's one write (decisions 0010 and 0011), against the real server over the demo ledger."""

import http.client
import importlib.util
import json
import pathlib
import threading

import pytest

from onloop.daemon import messages_route
from onloop.daemon import server as srv
from onloop.daemon.routes import Routes
from onloop.overlay.ledger import Ledger

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("demo_ledger", ROOT / "tools" / "demo_ledger.py")
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)


@pytest.fixture
def served(tmp_path):
    path = tmp_path / "ledger.db"
    demo.build(path)
    led = Ledger(path)
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>onloop</title>", encoding="utf-8")
    routes = Routes(led, threading.Lock())
    routes.add("/messages/", messages_route.make())
    server = srv.make_server(
        led, port=0, writer=routes, token="t" * 43, ui_dir=ui, heartbeat=tmp_path / "beat.json"
    )
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield led, server.server_address[1], tmp_path
    server.shutdown()
    server.server_close()
    led.close()


def call(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection(srv.HOST, port, timeout=10)
    h = {"Host": f"localhost:{port}", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    out = json.loads(raw) if raw and r.getheader("Content-Type", "").startswith("application/json") else raw
    c.close()
    return r.status, out, r


def test_conversation_lists_the_carried_roles_and_a_thread_without_the_roles_words(served):
    _, port, _ = served
    code, body, _ = call(port, "GET", "/api/conversation?role=builder")
    assert code == 200 and body["role"] == "builder"
    assert [r["role"] for r in body["roles"]] == ["builder", "designer", "reviewer"]
    roles = {r["role"]: r for r in body["roles"]}
    assert roles["builder"]["opted_in"] == ["corrective", "message"] and roles["reviewer"]["paused"]
    assert roles["builder"]["open_command"] == "claude --resume s-builder"
    kinds = [e["type"] for e in body["thread"]]
    assert {"message", "run", "turn"} <= set(kinds)
    human = next(e for e in body["thread"] if e["type"] == "message" and e["author"] == "human")
    assert (
        human["channel"] == "ui"
        and human["delivery"]["state"] == "acked"
        and human["delivery"]["acked_by_turn"]
    )
    text = json.dumps(body)
    assert (
        "PR not opened yet" not in text and "Opened the draft" not in text
    )  # a role's words are never stored
    assert any(e["type"] == "turn" and e["line"] == "outcome: done ref=pr#102" for e in body["thread"])
    corrective = next(
        e for e in body["thread"] if e["type"] == "message" and e["delivery"]["kind"] == "corrective"
    )
    assert corrective["delivery"]["state"] == "processing" and corrective["author"] == "onloop"
    assert body["carrier"] == {
        "enabled": True,
        "paused_reason": None,
        "paused_until": None,
        "kill_switch": False,
        "configured": False,  # this server was given no carrier: block
    }


def test_an_observe_only_message_says_why_it_was_not_sent(served):
    _, port, _ = served
    _, body, _ = call(port, "GET", "/api/conversation?role=reviewer")
    m = [e for e in body["thread"] if e["type"] == "message"]
    assert len(m) == 1 and m[0]["delivery"]["state"] == "cancelled"
    assert "has not opted in to message" in m[0]["delivery"]["last_error"]
    would = [e for e in body["thread"] if e["type"] == "would"]  # the message's own is shown on the message
    assert [(w["kind"], w["why"]) for w in would] == [
        ("corrective", "observe-only: reviewer has not opted in to the corrective wake")
    ]
    assert {r["role"]: r["attention"] for r in body["roles"]} == {
        "builder": "no outcome line",
        "designer": "run refused",
        "reviewer": "paused",
    }


def test_now_and_the_conversation_agree_on_what_the_carrier_did(served):
    """A stall's wording says what onloop did about it: asked (and how far that got), or only would (observe only)."""
    from onloop.report import views

    led, _, _ = served
    stalls = {s["owner_role"]: s for s in views.now(led, led.now())["stalls"]}
    assert stalls["builder"]["would"]["label"] == "onloop asked for its outcome line (being processed)"
    assert stalls["builder"]["would"]["acting"] is True
    assert stalls["reviewer"]["would"]["label"] == "would wake (observe only)"


def test_a_refused_run_and_its_note_are_in_its_role_s_thread(served):
    _, port, _ = served
    _, body, _ = call(port, "GET", "/api/conversation?role=designer")
    assert [e["status"] for e in body["thread"] if e["type"] == "run"] == ["refused"]
    notes = [e["text"] for e in body["thread"] if e["type"] == "note"]
    assert notes == ["designer's run was refused a tool; not re-run"]
    seqs = [e["seq"] for e in body["thread"]]
    assert seqs == sorted(seqs)


def test_the_kill_switch_shows_in_the_conversation(served):
    _, port, tmp = served
    (tmp / "carrier.off").write_text("stop", encoding="utf-8")
    assert call(port, "GET", "/api/conversation")[1]["carrier"]["kill_switch"] is True


def session(port):
    code, _, r = call(port, "GET", "/")
    assert code == 200
    cookie = r.getheader("Set-Cookie")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    jar = cookie.split(";", 1)[0]
    code, body, _ = call(port, "GET", "/api/ui/csrf", headers={"Cookie": jar})
    assert code == 200
    return jar, body["csrf"]


def test_the_compose_box_posts_with_its_cookie_csrf_token_and_origin(served):
    led, port, _ = served
    jar, csrf = session(port)
    ok = {"Cookie": jar, "X-CSRF-Token": csrf, "Origin": f"http://localhost:{port}"}
    code, out, _ = call(port, "POST", "/ui/messages", {"role": "builder", "text": "rebase first"}, ok)
    assert code == 200 and out["verified"] is False and out["delivery"]
    m = led.select("SELECT author, channel FROM messages WHERE body = 'rebase first'")
    assert m == [{"author": "human", "channel": "ui"}]
    assert led.select(
        "SELECT source FROM events WHERE kind = 'delivery.created' ORDER BY seq DESC LIMIT 1"
    ) == [{"source": "ui"}]
    for drop, want in (("Cookie", 403), ("X-CSRF-Token", 403), ("Origin", 403)):
        h = {k: v for k, v in ok.items() if k != drop}
        assert call(port, "POST", "/ui/messages", {"role": "builder", "text": "x"}, h)[0] == want, drop
    assert (
        call(
            port,
            "POST",
            "/ui/messages",
            {"role": "builder", "text": "x"},
            {**ok, "Origin": "http://example.invalid"},
        )[0]
        == 403
    )
    assert (
        call(
            port, "POST", "/ui/messages", {"role": "builder", "text": "x"}, {**ok, "X-CSRF-Token": "0" * 64}
        )[0]
        == 403
    )
    forged = jar.split(".")[0] + "." + "0" * 64
    assert (
        call(port, "POST", "/ui/messages", {"role": "builder", "text": "x"}, {**ok, "Cookie": forged})[0]
        == 403
    )
    assert call(port, "POST", "/ui/messages", {"role": "nobody", "text": "x"}, ok)[0] == 404
    assert call(port, "POST", "/ui/messages", {"role": "builder", "text": ""}, ok)[0] == 422
    assert call(port, "GET", "/api/ui/csrf")[0] == 403  # no page session, no token


def test_the_compose_route_does_not_take_the_write_token_in_place_of_the_page_session(served):
    _, port, _ = served
    h = {"Authorization": "Bearer " + "t" * 43, "Origin": f"http://localhost:{port}"}
    assert call(port, "POST", "/ui/messages", {"role": "builder", "text": "x"}, h)[0] == 403


def test_without_a_writer_the_compose_route_is_405(tmp_path):
    path = tmp_path / "ledger.db"
    Ledger(path).close()
    led = Ledger(path, readonly=True)
    server = srv.make_server(led, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert (
            call(server.server_address[1], "POST", "/ui/messages", {"role": "builder", "text": "x"})[0] == 405
        )
    finally:
        server.shutdown()
        server.server_close()
        led.close()


def test_the_compose_box_is_open_only_when_a_message_can_go_somewhere():
    from onloop.carrier import config as carrier_config
    from onloop.report.views import MESSAGES_OFF, compose_state

    problems: list[str] = []
    cfg = carrier_config.parse(
        {
            "roles": {
                "builder": {
                    "session_id": "00000000-0000-4000-8000-000000000001",
                    "cwd": ".",
                    "tools": ["Read"],
                    "opt_in": ["message"],
                },
                "reviewer": {
                    "session_id": "00000000-0000-4000-8000-000000000002",
                    "cwd": ".",
                    "tools": ["Read"],
                },
            }
        },
        problems,
    )
    assert not problems
    off = compose_state("builder", ["message"], None, True, False)
    assert off == {"enabled": False, "why": MESSAGES_OFF, "command": None}
    assert compose_state("ghost", [], cfg, True, False)["why"].endswith("(no `carrier.roles.ghost`)")
    assert (
        "add `message` to `carrier.roles.reviewer.opt_in`"
        in compose_state("reviewer", [], cfg, True, False)["why"]
    )
    opted_out = compose_state("builder", [], cfg, True, False)
    assert opted_out == {
        "enabled": False,
        "why": "builder has not opted in to messages",
        "command": "onloop role opt-in builder message",
    }
    assert compose_state("builder", ["message"], cfg, False, False)["command"] == "onloop carrier start"
    assert "kill switch" in compose_state("builder", ["message"], cfg, True, True)["why"]
    assert compose_state("builder", ["message"], cfg, True, False) == {
        "enabled": True,
        "why": None,
        "command": None,
    }


def test_without_a_carrier_the_conversation_says_messages_are_off_and_a_post_is_a_plain_404(tmp_path):
    path = tmp_path / "ledger.db"
    demo.build(path)
    led = Ledger(path)
    routes = Routes(led, threading.Lock())  # onloopd with no carrier: block: no message route
    server = srv.make_server(led, port=0, writer=routes, token="t" * 43)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        _, body, _ = call(port, "GET", "/api/conversation")
        assert body["carrier"]["configured"] is False
        assert {r["compose"]["enabled"] for r in body["roles"]} == {False}
        assert body["roles"][0]["compose"]["why"].startswith(
            "Messages are off: the carrier is not configured"
        )
        code, out, _ = call(
            port, "POST", "/messages/builder", {"text": "x"}, {"Authorization": "Bearer " + "t" * 43}
        )
        assert (
            code == 404
            and out["error"] == "messages are off: no carrier configured (a carrier: block in onloopd.yaml)"
        )
    finally:
        server.shutdown()
        server.server_close()
        led.close()
