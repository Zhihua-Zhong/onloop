"""onloopd's HTTP surface: health, read-only JSON over the ledger's projections, and a Server-Sent Events stream.

It binds the loopback address only: `make_server` refuses any other host, and every request must come from the loopback address
and name this server by its own Host: the loopback address or `localhost`, with its port. Any other Host is refused, so a
web page elsewhere cannot reach it by DNS rebinding. `localhost` is accepted because WebAuthn refuses an IP literal as the
relying-party id, so a page that uses passkeys is served as http://localhost:<port>.
GET and HEAD read. POST is taken only on the write routes (`/hooks/...` and `/sources/...`) and only when the server
was given a writer and a token: the request must carry `Authorization: Bearer <token>`, `Content-Type:
application/json` and a body of at most 1 MiB that decodes strictly as UTF-8 to a JSON object. Everything else is 405.
The token keeps out other programs and web pages; it is not a boundary against agents running as the same user, which
can read it (decision 0008), so what arrives here is evidence, never authority.

    GET /health                 {"status": "ok", "version", "pid", "started_at", "ledger": {...}}
    GET /api/items              the items projection
    GET /api/waits              open waits (?all=1 for cleared ones too)
    GET /api/leases             live leases (?all=1 for released ones too)
    GET /api/constraint         the constraint over the open waits, now, with typed reasons
    GET /api/turns              the latest role turns, newest first (?role=R, ?limit=N up to 500)
    GET /api/alerts             open alerts (?all=1 for closed ones too)
    GET /api/schedules          the declared schedules and their last results
    GET /api/roles              the roles and their states
    GET /api/report/observe     what onloop would have done and what happened then (?since=TIME, default a day ago; ?until=TIME, default now)
    GET /api/events?after=N     up to 500 events after sequence number N, oldest first; filters `kind`, `subject`,
                                `source`, `actor`, `since`, `until` (the ledger's own time); `latest=1&before=N` for
                                the newest first, paged by `next_before`; a filter that is not valid is a 400
    GET /api/now                the Now view: the constraint and its next move, what waits on the human, the stalls,
                                and health (schedules, onloopd's heartbeat, the dead-man switch)
    GET /api/flow               the Flow view: cards by zone, resources, waits, edges, and the constraint's downstream
    GET /api/stream             SSE: each new event as `id: <seq>`, `event: <kind>`, `data: <json>`; resumes after
                                Last-Event-ID (or ?after=N); a comment line every 15 s keeps the connection alive;
                                ?plain=1 leaves out the `event:` line, so a browser's EventSource sees every event
    GET /api/conversation       the Conversation view: the carried roles and one role's thread (?role=R)
    GET /api/ui/csrf            the page's CSRF token, for a request that carries the page's session cookie
    POST /ui/messages           the UI's one write (decision 0011): {"role", "text"}, a person's message to a role. It
                                needs the page's session cookie (HttpOnly, SameSite=Strict, set when `/` is served),
                                its CSRF token in `X-CSRF-Token`, an `Origin` of this onloopd, and a writer; anything
                                else is 403 (405 without a writer). It is evidence, not authority: a request from a
                                local user, not verified
    GET /, GET /assets/...      the UI's built files (`ui.py`), with a strict Content-Security-Policy
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import http.server
import ipaddress
import json
import os
import pathlib
import secrets
import sys
import threading
import time
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from .. import __version__
from .. import events as ev
from ..report import views
from . import ui

HOST = "127.0.0.1"  # scan:allow
DEFAULT_PORT = 8790
EVENTS_PAGE = 500
STREAM_POLL_S = 1.0
STREAM_KEEPALIVE_S = 15.0
MAX_BODY = 1024 * 1024
WRITE_PREFIXES = ("/hooks/", "/sources/", "/messages/")
UI_WRITE = "/ui/messages"
UI_COOKIE = "onloop_ui"
UI_MAX_BODY = 64 * 1024


class Reader(Protocol):
    """What the server reads; the ledger provides it."""

    def health(self) -> dict[str, Any]: ...
    def items(self) -> list[dict[str, Any]]: ...
    def waits(self, *, include_cleared: bool = False) -> list[dict[str, Any]]: ...
    def leases(self, *, include_released: bool = False) -> list[dict[str, Any]]: ...
    def constraint(self, now: str) -> dict[str, Any]: ...
    def events_after(self, seq: int, limit: int) -> list[dict[str, Any]]: ...


class WriteRefused(Exception):
    """A write the writer will not take: its HTTP status and a reason."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status


class Writer(Protocol):
    """What the write routes call; onloopd's main loop provides it, under its one writer lock."""

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


def allowed_hosts(port: int) -> tuple[str, str]:
    """The Host header values onloopd answers to: exactly these two, port included."""
    return (f"{HOST}:{port}", f"localhost:{port}")


class NotLoopback(ValueError):
    """onloopd binds the loopback address only."""


def check_host(host: str) -> str:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as e:
        raise NotLoopback(f"onloopd binds {HOST} only, not {host!r}") from e
    if str(ip) != HOST:
        raise NotLoopback(f"onloopd binds {HOST} only, not {host!r}")
    return HOST


# Errors that mean "another program holds this port": EADDRINUSE on POSIX, WSAEADDRINUSE on Windows, and WSAEACCES,
# which Windows gives when the port is held exclusively.
_IN_USE = {errno.EADDRINUSE, 10048, 10013}


class PortInUse(OSError):
    """The port is taken. onloopd never falls back to another port: the hooks and the jobs post to this one."""

    def __init__(self, host: str, port: int, cause: OSError):
        self.host, self.port = host, port
        super().__init__(
            f"port {port} on {host} is already in use by another program ({cause}). onloopd does not pick another "
            f"port. Free it, or choose one with `onloop daemon run --port N` or `port: N` in onloopd.yaml"
        )


class DaemonServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # On Windows, SO_REUSEADDR lets a socket share a port another program holds; never set it there.
    allow_reuse_address = sys.platform != "win32"

    def __init__(
        self,
        address: tuple[str, int],
        reader: Reader,
        writer: Writer | None = None,
        token: str | None = None,
        heartbeat: pathlib.Path | None = None,
        ui_dir: pathlib.Path | None = None,
        carrier: Any = None,
    ):
        super().__init__(address, _Handler)
        self._carrier = carrier  # the carrier's configuration, None when onloopd.yaml has no carrier: block
        self.reader = reader
        self.writer = writer
        self.token = token
        self.heartbeat = heartbeat  # onloopd's heartbeat file, for /api/now's health
        self.ui_dir = ui_dir or ui.UI_DIR  # the built UI, package data
        self.started_at = ev.now_ts()
        self.stopping = threading.Event()
        self.ui_key = secrets.token_bytes(
            32
        )  # this process's own: a restart makes the page fetch a new token

    # -- the page's session (decision 0011) -------------------------------------------------------------------------

    def _sign(self, what: str) -> str:
        return hmac.new(self.ui_key, what.encode("utf-8"), hashlib.sha256).hexdigest()

    def new_session(self) -> str:
        nonce = secrets.token_hex(16)
        return f"{nonce}.{self._sign('session:' + nonce)}"

    def session_nonce(self, cookie_header: str | None) -> str | None:
        """The nonce of a valid page session cookie in the request, or None."""
        for part in (cookie_header or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name != UI_COOKIE or "." not in value:
                continue
            nonce, sig = value.split(".", 1)
            if hmac.compare_digest(sig.encode(), self._sign("session:" + nonce).encode()):
                return nonce
        return None

    def csrf_for(self, nonce: str) -> str:
        return self._sign("csrf:" + nonce)

    @property
    def carrier(self) -> Any:
        return self._carrier() if callable(self._carrier) else self._carrier

    def shutdown(self) -> None:
        self.stopping.set()  # ends open SSE streams at their next poll
        super().shutdown()


def make_server(
    reader: Reader,
    port: int = DEFAULT_PORT,
    host: str = HOST,
    *,
    writer: Writer | None = None,
    token: str | None = None,
    heartbeat: pathlib.Path | None = None,
    ui_dir: pathlib.Path | None = None,
    carrier: Any = None,
) -> DaemonServer:
    """A server on the loopback address; `port=0` picks a free one. Any other host is refused before binding.
    Without both a writer and a token, every POST is 405."""
    if (writer is None) != (token is None):
        raise ValueError("a writer needs a token, and a token a writer")
    host = check_host(host)
    try:
        return DaemonServer((host, port), reader, writer, token, heartbeat, ui_dir, carrier)
    except OSError as e:
        if e.errno in _IN_USE or getattr(e, "winerror", None) in _IN_USE:
            raise PortInUse(host, port, e) from e
        raise


class _Handler(http.server.BaseHTTPRequestHandler):
    server: DaemonServer
    protocol_version = "HTTP/1.1"
    server_version = f"onloopd/{__version__}"

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default; onloopd logs its own events
        pass

    # -- checks ------------------------------------------------------------------------------------------------------

    def _refusal(self) -> str | None:
        if self.client_address[0] != HOST:
            return "onloopd answers this machine's loopback address only"
        port = self.server.server_address[1]
        if self.headers.get("Host") not in allowed_hosts(port):
            return f"onloopd answers only as {HOST}:{port} or localhost:{port}"
        return None

    def _send_json(self, code: int, body: Any, head_only: bool = False) -> None:
        data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    # -- methods -----------------------------------------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - the http.server naming
        self._answer(head_only=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._answer(head_only=True)

    def _not_allowed(self) -> None:
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD, POST" if self.server.writer is not None else "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_PUT = do_PATCH = do_DELETE = _not_allowed  # noqa: N815

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == UI_WRITE:
            self._ui_write()
            return
        if self.server.writer is None or not path.startswith(WRITE_PREFIXES):
            self._drain()
            self._not_allowed()
            return
        refusal = self._refusal()
        if refusal:
            self._drain()
            self._send_json(403, {"error": refusal})
            return
        given = self.headers.get("Authorization") or ""
        expected = f"Bearer {self.server.token}"
        if not hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
            self._drain()
            self._send_json(401, {"error": "a write needs onloopd's token: Authorization: Bearer <token>"})
            return
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            self._drain()
            self._send_json(415, {"error": "a write is a JSON body (Content-Type: application/json)"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "-1")
        except ValueError:
            length = -1
        if length < 0:
            self._send_json(411, {"error": "a write needs a Content-Length"})
            return
        if length > MAX_BODY:
            self.close_connection = True
            self._send_json(413, {"error": f"a write is at most {MAX_BODY} bytes"})
            return
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8", errors="strict"))
        except UnicodeDecodeError:
            self._send_json(400, {"error": "the body is not valid UTF-8"})
            return
        except ValueError:
            self._send_json(400, {"error": "the body is not JSON"})
            return
        if not isinstance(body, dict):
            self._send_json(400, {"error": "the body is not a JSON object"})
            return
        try:
            out = self.server.writer.post(path, body)
        except WriteRefused as e:
            self._send_json(e.status, {"error": str(e)})
            return
        except Exception as e:  # noqa: BLE001 - a failed write is a 500 with its reason, and nothing half-written
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"[:300]})
            return
        self._send_json(200, out)

    def _ui_write(self) -> None:
        """The compose box's POST: the page's cookie, its CSRF token and this onloopd's origin, or 403."""
        if self.server.writer is None:
            self._drain()
            self._not_allowed()
            return
        port = self.server.server_address[1]
        refusal = self._refusal()
        origin = self.headers.get("Origin")
        nonce = self.server.session_nonce(self.headers.get("Cookie"))
        token = self.headers.get("X-CSRF-Token") or ""
        if refusal is None and origin not in (f"http://{h}" for h in allowed_hosts(port)):
            refusal = "a message from the page must come from this onloopd's own origin"
        if refusal is None and nonce is None:
            refusal = "a message from the page needs the page's session; reload it"
        if refusal is None and not hmac.compare_digest(
            token.encode(), self.server.csrf_for(nonce or "").encode()
        ):
            refusal = "a message from the page needs its CSRF token; reload it"
        if refusal:
            self._drain()
            self._send_json(403, {"error": refusal})
            return
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            self._drain()
            self._send_json(415, {"error": "a message is a JSON body (Content-Type: application/json)"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "-1")
        except ValueError:
            length = -1
        if length < 0 or length > UI_MAX_BODY:
            self.close_connection = True
            self._send_json(
                413 if length > UI_MAX_BODY else 411, {"error": f"a message is at most {UI_MAX_BODY} bytes"}
            )
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError):
            self._send_json(400, {"error": "the body is not a JSON object in UTF-8"})
            return
        role = body.get("role") if isinstance(body, dict) else None
        if not isinstance(role, str):
            self._send_json(400, {"error": 'a message is {"role": ..., "text": ...}'})
            return
        try:
            out = self.server.writer.post(f"/messages/{role}", {"text": body.get("text"), "channel": "ui"})
        except WriteRefused as e:
            self._send_json(e.status, {"error": str(e)})
            return
        except Exception as e:  # noqa: BLE001 - a failed write is a 500 with its reason
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"[:300]})
            return
        self._send_json(200, out)

    def _drain(self) -> None:
        """Read a refused request's body (up to MAX_BODY), so the client sees the answer rather than a reset."""
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if 0 < length <= MAX_BODY:
            self.rfile.read(length)
        elif length > MAX_BODY:
            self.close_connection = True

    def _answer(self, head_only: bool) -> None:
        refusal = self._refusal()
        if refusal:
            self._send_json(403, {"error": refusal}, head_only)
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query, keep_blank_values=True)
        flag = query.get("all", ["0"])[0] in ("1", "true")
        reader = self.server.reader
        try:
            if url.path == "/health":
                body = {
                    "status": "ok",
                    "version": __version__,
                    "pid": os.getpid(),
                    "started_at": self.server.started_at,
                    "ledger": reader.health(),
                }
            elif url.path == "/api/items":
                body = {"items": reader.items()}
            elif url.path == "/api/waits":
                body = {"waits": reader.waits(include_cleared=flag)}
            elif url.path == "/api/leases":
                body = {"leases": reader.leases(include_released=flag)}
            elif url.path == "/api/constraint":
                body = reader.constraint(ev.now_ts())
            elif url.path == "/api/turns":
                role = query.get("role", [None])[0]
                limit = min(max(_int(query.get("limit", ["200"])[0]), 1), EVENTS_PAGE)
                body = {"turns": reader.turns(role=role, limit=limit)}
            elif url.path == "/api/alerts":
                body = {"alerts": reader.alerts(include_closed=flag)}
            elif url.path == "/api/schedules":
                body = {"schedules": reader.schedules()}
            elif url.path == "/api/roles":
                body = {"roles": reader.roles()}
            elif url.path == "/api/report/observe":
                now = ev.now_ts()
                given = query.get("since", [None])[0]
                until = query.get("until", [None])[0]
                try:
                    since = ev.norm_ts(given) if given else ev.shift_ts(now, -86400)
                    now = ev.norm_ts(until) if until else now
                except ev.EventError as e:
                    self._send_json(400, {"error": str(e)}, head_only)
                    return
                from ..report import observe

                body = observe.build(reader, since, now)
            elif url.path == "/api/events":
                try:
                    body = views.events_query(reader, query)
                except views.QueryError as e:
                    self._send_json(400, {"error": str(e)}, head_only)
                    return
            elif url.path == "/api/now":
                body = views.now(reader, ev.now_ts(), self._heartbeat())
            elif url.path == "/api/flow":
                body = views.flow(reader, ev.now_ts())
            elif url.path == "/api/conversation":
                kill = bool(self.server.heartbeat) and (self.server.heartbeat.parent / "carrier.off").exists()
                body = views.conversation(
                    reader,
                    ev.now_ts(),
                    query.get("role", [None])[0],
                    kill_switch=kill,
                    carrier_cfg=self.server.carrier,
                )
            elif url.path == "/api/ui/csrf":
                nonce = self.server.session_nonce(self.headers.get("Cookie"))
                if nonce is None:
                    self._send_json(403, {"error": "no page session; reload the page"}, head_only)
                    return
                body = {"csrf": self.server.csrf_for(nonce)}
            elif url.path == "/api/stream" and not head_only:
                plain = query.get("plain", ["0"])[0] in ("1", "true")
                self._stream(_int(self.headers.get("Last-Event-ID") or query.get("after", ["0"])[0]), plain)
                return
            elif url.path.startswith("/api/") or url.path == "/api":
                self._send_json(404, {"error": "not found"}, head_only)
                return
            else:
                self._static(url.path, head_only)
                return
        except Exception as e:  # noqa: BLE001 - a read failure is a 500 with its reason, never a dropped connection
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"[:300]}, head_only)
            return
        self._send_json(200, body, head_only)

    def _static(self, path: str, head_only: bool) -> None:
        found = ui.resolve(self.server.ui_dir, path)
        if found.status == 200 and found.path is not None:
            data = found.path.read_bytes()
            ctype = found.content_type
        elif found.status == 503:
            data, ctype = ui.NOT_BUILT.encode("utf-8"), ui.CONTENT_TYPES[".html"]
        else:
            data, ctype = b"not found\n", "text/plain; charset=utf-8"
        self.send_response(found.status)
        if found.status == 200 and ctype.startswith("text/html"):
            self.send_header(
                "Set-Cookie", f"{UI_COOKIE}={self.server.new_session()}; HttpOnly; SameSite=Strict; Path=/"
            )
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Cache-Control", "public, max-age=31536000, immutable" if found.immutable else "no-store"
        )
        for k, v in ui.HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def _heartbeat(self) -> dict[str, Any] | None:
        path = self.server.heartbeat
        if path is None:
            return None
        try:
            beat = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None  # no heartbeat yet, or one being rewritten: shown as missing, never as fine
        return beat if isinstance(beat, dict) else None

    def _stream(self, after: int, plain: bool = False) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        last_beat = time.monotonic()
        try:
            self.wfile.write(b": onloopd stream\n\n")
            self.wfile.flush()
            while not self.server.stopping.is_set():
                for e in self.server.reader.events_after(after, EVENTS_PAGE):
                    after = int(e["seq"])
                    data = json.dumps(e, ensure_ascii=False, sort_keys=True)
                    named = "" if plain else f"event: {e['kind']}\n"
                    self.wfile.write(f"id: {after}\n{named}data: {data}\n\n".encode())
                    self.wfile.flush()
                if time.monotonic() - last_beat >= STREAM_KEEPALIVE_S:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    last_beat = time.monotonic()
                self.server.stopping.wait(STREAM_POLL_S)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return


def _int(text: str) -> int:
    try:
        return max(0, int(text))
    except (TypeError, ValueError):
        return 0
