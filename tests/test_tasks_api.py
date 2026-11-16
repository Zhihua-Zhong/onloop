"""The task list's writes over HTTP (decision 0013): the token route `/tasks/` for the CLI, and the page's
`/ui/tasks/resolve`, `/ui/tasks/keep` and `/ui/visits` under decision 0011's rules."""

import http.client
import json
import threading

import pytest

from onloop.daemon import server as srv
from onloop.daemon import sources
from onloop.daemon.routes import Routes
from onloop.overlay.ledger import Ledger

TOKEN = "t" * 43


@pytest.fixture
def served(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>onloop</title>", encoding="utf-8")
    routes = Routes(led, threading.Lock())
    routes.add("/tasks/", sources.tasks_route)
    server = srv.make_server(
        led, port=0, writer=routes, token=TOKEN, ui_dir=ui, heartbeat=tmp_path / "b.json"
    )
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield led, server.server_address[1]
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


def session(port):
    _, _, r = call(port, "GET", "/")
    jar = r.getheader("Set-Cookie").split(";", 1)[0]
    _, body, _ = call(port, "GET", "/api/ui/csrf", headers={"Cookie": jar})
    return {"Cookie": jar, "X-CSRF-Token": body["csrf"], "Origin": f"http://localhost:{port}"}


def test_the_cli_route_needs_the_token(served):
    led, port = served
    body = {"author_role": "builder", "ref": "pr#1", "ask": {"effect": "a, so b"}}
    assert call(port, "POST", "/tasks/ask", body)[0] == 401
    code, out, _ = call(port, "POST", "/tasks/ask", body, {"Authorization": f"Bearer {TOKEN}"})
    assert code == 200 and out["id"].startswith("t-")
    assert call(port, "POST", "/tasks/nope", {}, {"Authorization": f"Bearer {TOKEN}"})[0] == 404


def test_the_page_marks_keeps_and_visits_under_the_ui_write_rules(served):
    led, port = served
    _, out, _ = call(
        port,
        "POST",
        "/tasks/ask",
        {"author_role": "builder", "ref": "pr#1", "ask": {"effect": "a, so b"}},
        {"Authorization": f"Bearer {TOKEN}"},
    )
    tid = out["id"]
    ok = session(port)
    for drop in ("Cookie", "X-CSRF-Token", "Origin"):
        h = {k: v for k, v in ok.items() if k != drop}
        assert call(port, "POST", "/ui/tasks/resolve", {"id": tid, "resolution": "done"}, h)[0] == 403, drop
    assert call(port, "POST", "/ui/visits", {}, {**ok, "Origin": "http://example.invalid"})[0] == 403
    assert call(port, "POST", "/ui/tasks/resolve", {"id": tid, "resolution": "expired"}, ok)[0] == 422
    assert call(port, "POST", "/ui/tasks/keep", {"id": tid, "reason": "later"}, ok)[0] == 200
    assert call(port, "POST", "/ui/visits", {}, ok)[0] == 200
    code, out, _ = call(
        port, "POST", "/ui/tasks/resolve", {"id": tid, "resolution": "done", "by": "lead"}, ok
    )
    assert code == 200 and out["verified"] is False
    (t,) = led.select("SELECT * FROM human_tasks")
    assert (t["resolution"], t["resolved_by"], t["evidence"]) == (
        "done",
        "human",
        "human:ui",
    )  # `by` is not taken
    assert [v["channel"] for v in led.select("SELECT * FROM visits")] == ["ui"]
    assert call(port, "POST", "/ui/tasks/resolve", {"id": "t-nothere", "resolution": "done"}, ok)[0] == 404
