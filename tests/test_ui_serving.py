"""onloopd serves the built UI read-only, from its own origin, under a strict CSP (decision 0009)."""

import http.client
import importlib.util
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from onloop.daemon import server as srv
from onloop.daemon import ui

from .test_daemon_server import FakeReader

TOOL = Path(__file__).resolve().parent.parent / "tools" / "check_wheel.py"
_spec = importlib.util.spec_from_file_location("check_wheel", TOOL)
check_wheel = importlib.util.module_from_spec(_spec)
sys.modules["check_wheel"] = check_wheel
_spec.loader.exec_module(check_wheel)


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "ui"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>onloop</title>", encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (root / "assets" / "index-abc123.css").write_text("body{}", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (root / ".secret.txt").write_text("no", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    return root


def serve(ui_dir):
    s = srv.make_server(FakeReader(), port=0, ui_dir=ui_dir)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


@pytest.fixture
def server(bundle):
    s = serve(bundle)
    yield s
    s.shutdown()
    s.server_close()


def request(s, path, method="GET", host=None):
    port = s.server_address[1]
    c = http.client.HTTPConnection(srv.HOST, port, timeout=5)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    c.putheader("Host", host or f"localhost:{port}")
    c.putheader("Content-Length", "0")
    c.endheaders()
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, body


def test_the_index_and_the_assets_are_served_with_their_types_and_the_csp(server):
    code, h, body = request(server, "/")
    assert code == 200 and body.startswith(b"<!doctype html>") and h["content-type"].startswith("text/html")
    assert h["cache-control"] == "no-store"
    csp = h["content-security-policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    code, h, body = request(server, "/assets/index-abc123.js")
    assert code == 200 and body == b"console.log(1)" and h["content-type"].startswith("text/javascript")
    assert "immutable" in h["cache-control"]
    assert request(server, "/assets/index-abc123.css")[1]["content-type"].startswith("text/css")
    assert request(server, "/favicon.svg")[1]["cache-control"] == "no-store"


def test_a_path_with_no_extension_answers_the_index_so_a_reload_works(server):
    for path in ("/now", "/flow/zone", "/?x=1"):
        code, h, body = request(server, path)
        assert code == 200 and body.startswith(b"<!doctype html>"), path


@pytest.mark.parametrize(
    "path",
    [
        "/../outside.txt",
        "/assets/../../outside.txt",
        "/assets/%2e%2e/%2e%2e/outside.txt",
        "/assets/..%5c..%5coutside.txt",
        "/%2e%2e/outside.txt",
        "/.secret.txt",
        "/assets/missing.js",
        "/index.py",
        "/assets/%00.js",
    ],
)
def test_nothing_outside_the_bundle_or_hidden_or_unknown_is_served(server, path):
    code, _, body = request(server, path)
    assert code == 404 and b"outside" not in body and b"no" != body


def test_the_ui_is_read_only_and_answers_only_its_own_host(server):
    assert request(server, "/", method="POST")[0] == 405
    assert request(server, "/", method="PUT")[0] == 405
    assert request(server, "/", method="DELETE")[0] == 405
    assert request(server, "/", host="evil.example:80")[0] == 403
    code, h, body = request(server, "/", method="HEAD")
    assert code == 200 and body == b"" and int(h["content-length"]) > 0
    assert request(server, "/api/nothing")[0] == 404  # an unknown API path is not the index


def test_without_a_build_the_page_says_how_to_build_it(tmp_path):
    s = serve(tmp_path / "no-ui")
    try:
        code, h, body = request(s, "/")
        assert code == 503 and b"npm run build" in body and "content-security-policy" in h
        assert request(s, "/api/items")[0] == 200  # the API answers whether or not the UI is built
    finally:
        s.shutdown()
        s.server_close()


def test_resolve_never_leaves_the_root(bundle):
    assert ui.resolve(bundle, "/").path == bundle / "index.html"
    assert ui.resolve(bundle, "/assets/index-abc123.js").immutable is True
    for bad in ("/../outside.txt", "relative", "/a\\b.js", "/.git/config"):
        assert ui.resolve(bundle, bad).status == 404
    link = bundle / "assets" / "elsewhere"  # a link inside the bundle that leads out of it
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(bundle.parent), str(link))  # a junction needs no privilege
    else:
        link.symlink_to(bundle.parent, target_is_directory=True)
    assert (link / "outside.txt").is_file()
    assert ui.resolve(bundle, "/assets/elsewhere/outside.txt").status == 404


# -- the wheel must carry the bundle -----------------------------------------------------------------------------------


def wheel(tmp_path, names):
    path = tmp_path / "onloop-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return path


def test_the_wheel_check_passes_only_with_the_ui_inside(tmp_path, capsys):
    good = wheel(tmp_path, ["onloop/__init__.py", "onloop/ui/index.html", "onloop/ui/assets/index-a.js"])
    assert check_wheel.main([str(good)]) == 0
    (tmp_path / "bad").mkdir()
    bad = wheel(tmp_path / "bad", ["onloop/__init__.py", "onloop/templates/onloop.yaml"])
    assert check_wheel.main([str(bad)]) == 1
    assert "onloop/ui/index.html" in capsys.readouterr().err
    (tmp_path / "noassets").mkdir()
    half = wheel(tmp_path / "noassets", ["onloop/ui/index.html"])
    assert check_wheel.main([str(half)]) == 1
