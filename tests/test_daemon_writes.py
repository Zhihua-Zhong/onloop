"""onloopd's write path: POST only on the write routes, only with the token, only a small JSON object in UTF-8."""

import http.client
import json
import subprocess
import sys
import threading

import pytest

from onloop.daemon import routes as routes_mod
from onloop.daemon import server as srv
from onloop.daemon import token

from .test_daemon_server import FakeReader

TOKEN = "t0ken-for-tests"


class FakeWriter:
    def __init__(self):
        self.got = []

    def post(self, path, body):
        if path == "/hooks/refuse":
            raise srv.WriteRefused(422, "not this one")
        self.got.append((path, body))
        return {"ok": True}


@pytest.fixture
def server():
    w = FakeWriter()
    s = srv.make_server(FakeReader(), port=0, writer=w, token=TOKEN)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    s.fake = w
    yield s
    s.shutdown()
    s.server_close()


def post(s, path, body=b"{}", *, auth=f"Bearer {TOKEN}", ctype="application/json", host=None, length=None):
    port = s.server_address[1]
    c = http.client.HTTPConnection(srv.HOST, port, timeout=5)
    c.putrequest("POST", path, skip_host=True)
    c.putheader("Host", host or f"{srv.HOST}:{port}")
    if auth:
        c.putheader("Authorization", auth)
    if ctype:
        c.putheader("Content-Type", ctype)
    c.putheader("Content-Length", str(len(body) if length is None else length))
    c.endheaders()
    try:
        c.send(body)
    except OSError:
        pass
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data) if data else None


def test_a_good_write_reaches_the_writer(server):
    code, out = post(server, "/hooks/stop", json.dumps({"role": "builder"}).encode())
    assert code == 200 and out == {"ok": True} and server.fake.got == [("/hooks/stop", {"role": "builder"})]


def test_localhost_is_a_good_host_for_a_write_too(server):
    assert post(server, "/hooks/stop", host=f"localhost:{server.server_address[1]}")[0] == 200


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"auth": None}, 401),
        ({"auth": "Bearer wrong"}, 401),
        ({"auth": TOKEN}, 401),  # no scheme
        ({"host": "evil.example:1"}, 403),
        ({"ctype": "text/plain"}, 415),
        ({"ctype": None}, 415),
        ({"body": b"\xff\xfe{}"}, 400),
        ({"body": b"not json"}, 400),
        ({"body": b"[1, 2]"}, 400),
        ({"length": srv.MAX_BODY + 1}, 413),  # declared, not sent: refused before reading
    ],
)
def test_a_bad_write_is_refused_and_reaches_nothing(server, kw, code):
    body = kw.pop("body", b"{}")
    assert post(server, "/hooks/stop", body, **kw)[0] == code
    assert server.fake.got == []


def test_post_elsewhere_is_405_and_a_writer_refusal_keeps_its_status(server):
    assert post(server, "/api/items")[0] == 405
    assert post(server, "/health")[0] == 405
    assert post(server, "/hooks/refuse")[0] == 422


def test_without_a_writer_every_post_is_405():
    s = srv.make_server(FakeReader(), port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        assert post(s, "/hooks/stop")[0] == 405
    finally:
        s.shutdown()
        s.server_close()
    with pytest.raises(ValueError):
        srv.make_server(FakeReader(), port=0, token="t")


def test_routes_dispatch_by_prefix_under_the_lock():
    calls = []
    lock = threading.Lock()

    def handler(ledger, rest, body):
        calls.append((rest, body, lock.locked()))
        return {"ok": rest}

    r = routes_mod.Routes(ledger=None, lock=lock)
    r.add("/hooks/", handler)
    assert r.post("/hooks/stop", {"a": 1}) == {"ok": "stop"} and calls == [("stop", {"a": 1}, True)]
    with pytest.raises(srv.WriteRefused) as e:
        r.post("/sources/x/ping", {})
    assert e.value.status == 404
    with pytest.raises(ValueError):
        r.add("/api/", handler)


def test_the_token_is_fresh_each_time_and_readable_back(tmp_path):
    first, _ = token.issue(tmp_path)
    second, _ = token.issue(tmp_path)
    assert first != second and len(second) >= 40 and token.read(tmp_path) == second
    assert token.read(tmp_path / "none") is None


def test_a_token_file_readable_by_others_is_a_warning_not_a_failure(tmp_path, monkeypatch):
    path = tmp_path / token.TOKEN_FILE
    path.write_text("x\n", encoding="utf-8")
    if sys.platform == "win32":
        broad = subprocess.CompletedProcess(
            ["icacls"],
            0,
            f"{path} NT AUTHORITY\\SYSTEM:(I)(F)\n    BUILTIN\\Users:(I)(RX)\n    Everyone:(R)\n",
            "",
        )
        warning = token.readable_by_others(path, run=lambda argv: broad)
        assert "BUILTIN\\Users" in warning and "Everyone" in warning
        assert "could not check" in token.readable_by_others(
            path, run=lambda argv: subprocess.CompletedProcess(argv, 5, "", "")
        )
        assert token.readable_by_others(path) is None  # the real ACL of a file in the user's temp folder
    else:
        path.chmod(0o644)
        assert "readable by group or others" in token.readable_by_others(path)
        path.chmod(0o600)
        assert token.readable_by_others(path) is None
