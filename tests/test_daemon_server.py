"""onloopd's HTTP surface: loopback only, its own Host only, read-only JSON, and an SSE stream of new events."""

import http.client
import json
import threading

import pytest

from onloop.daemon import server as srv


class FakeReader:
    def __init__(self):
        self.events = [
            {"seq": 1, "kind": "item.opened", "subject_ref": "pr#1", "payload": {}},
            {"seq": 2, "kind": "wait.opened", "subject_ref": "pr#1", "payload": {}},
        ]

    def health(self):
        return {"schema_version": 1, "last_seq": len(self.events)}

    def items(self):
        return [{"id": "pr#1", "state": "open"}]

    def waits(self, *, include_cleared=False):
        rows = [{"id": "w1", "on_ref": "gate", "cleared_at": None}]
        return rows + ([{"id": "w0", "on_ref": "gate", "cleared_at": "x"}] if include_cleared else [])

    def leases(self, *, include_released=False):
        return [{"id": "l1", "resource_id": "gate"}]

    def constraint(self, now):
        return {"now": now, "constraint": {"on_ref": "gate"}, "bottlenecks": []}

    def events_after(self, seq, limit):
        return [e for e in self.events if e["seq"] > seq][:limit]


@pytest.fixture
def server():
    s = srv.make_server(FakeReader(), port=0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def request(s, path, method="GET", host=None, headers=None):
    port = s.server_address[1]
    c = http.client.HTTPConnection(srv.HOST, port, timeout=5)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", host or f"{srv.HOST}:{port}")
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    c.putheader("Content-Length", "0")
    c.endheaders()
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, dict(r.getheaders()), body


def test_any_other_address_is_refused_before_binding():
    for host in ("0.0.0.0", "::", "192.168.1.10", "localhost", "::1", ""):  # scan:allow
        with pytest.raises(srv.NotLoopback):
            srv.make_server(FakeReader(), port=0, host=host)


def test_it_binds_the_loopback_address(server):
    assert server.server_address[0] == srv.HOST


def test_health_names_the_version_the_pid_and_the_ledger(server):
    code, headers, body = request(server, "/health")
    got = json.loads(body)
    assert code == 200 and headers["Content-Type"].startswith("application/json")
    assert got["status"] == "ok" and got["pid"] > 0 and got["ledger"] == {"schema_version": 1, "last_seq": 2}


def test_the_read_endpoints_answer_from_the_reader(server):
    assert json.loads(request(server, "/api/items")[2]) == {"items": [{"id": "pr#1", "state": "open"}]}
    assert len(json.loads(request(server, "/api/waits")[2])["waits"]) == 1
    assert len(json.loads(request(server, "/api/waits?all=1")[2])["waits"]) == 2
    assert json.loads(request(server, "/api/leases")[2])["leases"][0]["id"] == "l1"
    assert json.loads(request(server, "/api/constraint")[2])["constraint"]["on_ref"] == "gate"
    assert [e["seq"] for e in json.loads(request(server, "/api/events?after=1")[2])["events"]] == [2]
    assert request(server, "/api/nope")[0] == 404


def test_writes_are_refused_and_another_host_is_refused(server):
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert request(server, "/api/items", method=method)[0] == 405
    port = server.server_address[1]
    assert request(server, "/health", host=f"evil.example:{port}")[0] == 403  # no DNS rebinding
    assert request(server, "/health", host=f"localhost:{port}")[0] == 200  # the name WebAuthn needs
    for host in (
        f"localhost:{port + 1}",
        srv.HOST,
        "localhost",
        f"LOCALHOST.:{port}",
        f"127.0.0.2:{port}",  # scan:allow
        f"[::1]:{port}",
    ):
        assert request(server, "/health", host=host)[0] == 403, host


def test_the_stream_sends_new_events_with_their_sequence_numbers(server):
    port = server.server_address[1]
    c = http.client.HTTPConnection(srv.HOST, port, timeout=5)
    c.putrequest("GET", "/api/stream", skip_host=True)
    c.putheader("Host", f"{srv.HOST}:{port}")
    c.putheader("Last-Event-ID", "1")
    c.endheaders()
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
    lines = []
    while len([x for x in lines if x.startswith("id:")]) < 1:
        lines.append(r.fp.readline().decode("utf-8").rstrip("\n"))
    lines += [r.fp.readline().decode("utf-8").rstrip("\n") for _ in range(2)]
    c.close()
    assert "id: 2" in lines and "event: wait.opened" in lines
    assert json.loads(next(x for x in lines if x.startswith("data: "))[6:])["seq"] == 2


def test_the_plain_stream_leaves_out_the_event_name_for_a_browser(server):
    port = server.server_address[1]
    c = http.client.HTTPConnection(srv.HOST, port, timeout=5)
    c.putrequest("GET", "/api/stream?after=1&plain=1", skip_host=True)
    c.putheader("Host", f"localhost:{port}")
    c.endheaders()
    r = c.getresponse()
    lines = []
    while not any(x.startswith("data: ") for x in lines):
        lines.append(r.fp.readline().decode("utf-8").rstrip("\n"))
    c.close()
    assert "id: 2" in lines and not any(x.startswith("event:") for x in lines)
