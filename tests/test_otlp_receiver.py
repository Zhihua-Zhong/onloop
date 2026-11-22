"""onloopd's OTLP receiver (decision 0019): Claude Code's telemetry becomes `otel` events, identity dropped on arrival,
only the named fields kept, a redelivered batch logged once, and anything not OTLP/JSON stored nowhere."""

import gzip
import http.client
import json
import threading

import pytest

from onloop.daemon import otlp
from onloop.daemon import server as srv
from onloop.daemon.routes import Routes
from onloop.overlay.ledger import Ledger

from .test_daemon_server import FakeReader
from .test_overlay_ledger import T0

TOKEN = "t0ken-for-tests"
IDENTITY = {
    "user.email": "email-0000",
    "organization.id": "org-0000",
    "user.account_uuid": "acct-uuid-0000",
    "user.account_id": "acct-0000",
    "user.id": "anon-0000",
}


def attrs(d):
    out = []
    for k, v in d.items():
        if isinstance(v, bool):
            out.append({"key": k, "value": {"boolValue": v}})
        elif isinstance(v, int):
            out.append({"key": k, "value": {"intValue": str(v)}})  # OTLP/JSON writes int64 as a string
        elif isinstance(v, float):
            out.append({"key": k, "value": {"doubleValue": v}})
        else:
            out.append({"key": k, "value": {"stringValue": v}})
    return out


def record(name, seq, session="s-1", **fields):
    a = {
        **IDENTITY,
        "session.id": session,
        "event.name": name,
        "event.timestamp": "2026-11-20T10:00:00.000Z",
        "event.sequence": seq,
        **fields,
    }
    return {
        "timeUnixNano": "1795135954417000000",
        "body": {"stringValue": f"claude_code.{name}"},
        "attributes": attrs(a),
    }


def logs(*records, role="builder"):
    res = {"service.name": "claude-code", **IDENTITY}
    if role is not None:
        res["onloop.role"] = role
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": attrs(res)},
                "scopeLogs": [
                    {"scope": {"name": "com.anthropic.claude_code.events"}, "logRecords": list(records)}
                ],
            }
        ]
    }


API = record(
    "api_request",
    3,
    **{
        "prompt.id": "p-1",
        "model": "claude-test",
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_tokens": 300,
        "cache_creation_tokens": 40,
        "duration_ms": 1500,
        "request_id": "req-1",
        "query_source": "repl_main_thread",
        "cost_usd": 0.0125,
        "client_request_id": "c-1",
        "effort": "high",
    },
)
PROMPT = record(
    "user_prompt",
    2,
    **{"prompt.id": "p-1", "prompt_length": "42", "prompt": "SECRET PROMPT", "prompt_text": "SECRET"},
)
TOOL = record(
    "tool_result",
    4,
    **{
        "prompt.id": "p-1",
        "tool_name": "Bash",
        "success": "true",
        "duration_ms": 80,
        "tool_parameters": "rm -rf x",
    },
)


def test_the_three_kinds_keep_only_their_fields_and_no_identity():
    got = otlp.events_for("logs", logs(PROMPT, API, TOOL, record("plugin_loaded", 1)))
    assert [e["kind"] for e in got] == ["turn.prompted", "cost.request", "tool.ended"]
    assert all(
        e["source"] == "otel" and e["subject_ref"] == "session:s-1" and e["actor"] == "builder" for e in got
    )
    prompted, cost, tool = (e["payload"] for e in got)
    assert cost == {
        "session_id": "s-1",
        "role": "builder",
        "prompt_id": "p-1",
        "model": "claude-test",
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_tokens": 300,
        "cache_creation_tokens": 40,
        "duration_ms": 1500,
        "request_id": "req-1",
        "query_source": "repl_main_thread",
        "cost_usd": 0.0125,
        "claimed_at": "2026-11-20T10:00:00.000Z",
        "sequence": 3,
    }
    assert prompted["length"] == 42 and "prompt" not in prompted and "prompt_text" not in prompted
    assert tool["success"] is True and tool["tool_name"] == "Bash" and "tool_parameters" not in tool
    text = json.dumps(got)
    for k, v in IDENTITY.items():
        assert k not in text and v not in text
    assert "SECRET" not in text and "rm -rf" not in text


def test_identity_is_scrubbed_from_the_body_itself_before_anything_reads_it():
    body = logs(API)
    otlp.events_for("logs", body)
    text = json.dumps(body)
    assert not any(k in text for k in IDENTITY) and "session.id" in text


def test_a_record_without_a_session_or_with_another_name_is_skipped():
    no_session = record("api_request", 5)
    no_session["attributes"] = [a for a in no_session["attributes"] if a["key"] != "session.id"]
    assert otlp.events_for("logs", logs(no_session, record("assistant_response", 6))) == []


@pytest.mark.parametrize("role", [None, "Not A Role", "x" * 41])
def test_a_missing_or_malformed_role_leaves_the_actor_empty(role):
    (e,) = otlp.events_for("logs", logs(API, role=role))
    assert e["actor"] is None and e["payload"]["role"] is None


@pytest.mark.parametrize(
    ("signal", "body", "code"),
    [
        ("logs", {"something": []}, 400),
        ("logs", {"resourceLogs": {}}, 400),
        ("metrics", {"resourceLogs": []}, 400),
        ("traces", {"resourceSpans": []}, 404),
    ],
)
def test_a_body_that_is_not_an_otlp_export_request_is_refused(signal, body, code):
    with pytest.raises(srv.WriteRefused) as e:
        otlp.events_for(signal, body)
    assert e.value.status == code


@pytest.fixture
def live(tmp_path):
    with Ledger(tmp_path / "l.db", clock=lambda: T0) as led:
        s = srv.make_server(FakeReader(), port=0, writer=Routes(led, threading.Lock()), token=TOKEN)
        s_routes = s.writer
        s_routes.add("/v1/", otlp.handle)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        yield s, led
        s.shutdown()
        s.server_close()


def post(s, path, raw, *, auth=f"Bearer {TOKEN}", headers=()):
    port = s.server_address[1]
    c = http.client.HTTPConnection(srv.HOST, port, timeout=5)
    h = {"Host": f"{srv.HOST}:{port}", "Content-Type": "application/json", "Content-Length": str(len(raw))}
    if auth:
        h["Authorization"] = auth
    h.update(dict(headers))
    c.request("POST", path, body=raw, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data) if data else None


def otel_rows(led):
    return [dict(r) for r in led.conn.execute("SELECT * FROM events WHERE source = 'otel' ORDER BY seq")]


def test_a_logs_export_is_logged_once_even_when_redelivered(live):
    s, led = live
    raw = json.dumps(logs(PROMPT, API, TOOL)).encode()
    assert post(s, "/v1/logs", raw) == (200, {"partialSuccess": {}})
    assert post(s, "/v1/logs", raw) == (200, {"partialSuccess": {}})
    rows = otel_rows(led)
    assert [r["kind"] for r in rows] == ["turn.prompted", "cost.request", "tool.ended"]
    assert all(
        r["at"] is None and r["ts_system"] == T0 for r in rows
    )  # the ledger's own time, never the claim
    stored = json.dumps(rows)
    assert not any(v in stored for v in IDENTITY.values()) and "SECRET" not in stored


def test_a_metrics_export_is_answered_and_stored_nowhere(live):
    s, led = live
    raw = json.dumps({"resourceMetrics": [{"resource": {"attributes": attrs(IDENTITY)}, "scopeMetrics": []}]})
    before = led.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert post(s, "/v1/metrics", raw.encode()) == (200, {"partialSuccess": {}})
    assert led.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"auth": None}, 401),
        ({"headers": {"Content-Type": "application/x-protobuf"}}, 415),
        ({"headers": {"Content-Encoding": "gzip"}}, 415),
    ],
)
def test_an_export_without_the_token_as_protobuf_or_compressed_is_refused_and_stored_nowhere(live, kw, code):
    s, led = live
    raw = json.dumps(logs(API)).encode()
    if kw.get("headers", {}).get("Content-Encoding") == "gzip":
        raw = gzip.compress(raw)
    assert post(s, "/v1/logs", raw, **kw)[0] == code
    assert otel_rows(led) == []


def test_a_body_that_is_not_otlp_is_answered_400_and_stored_nowhere(live):
    s, led = live
    assert post(s, "/v1/logs", b'{"hello": "world"}')[0] == 400
    assert post(s, "/v1/traces", b'{"resourceSpans": []}')[0] == 404
    assert otel_rows(led) == []


def _api(seq, prompt, request, session="s-1", tokens=10):
    fields = {"model": "claude-test", "input_tokens": tokens, "output_tokens": 1, "request_id": request}
    if prompt is not None:
        fields["prompt.id"] = prompt
    return record("api_request", seq, session=session, **fields)


def test_a_resumed_runs_records_are_logged_though_its_sequence_starts_again(live):
    """The dogfood's round 2 (2026-11-22): a carried turn is a new process resuming the same session, so Claude Code's
    event.sequence starts again; keyed on session and sequence alone, the second run's records were dropped as
    redelivered. Each run has its own prompt id, and each request its own id."""
    s, led = live
    run1 = logs(
        record("user_prompt", 3, **{"prompt.id": "p-run1", "prompt_length": 5}), _api(13, "p-run1", "req-a")
    )
    run2 = logs(
        record("user_prompt", 3, **{"prompt.id": "p-run2", "prompt_length": 7}), _api(13, "p-run2", "req-b")
    )
    for raw in (run1, run2, run2):  # run 2's batch delivered twice
        assert post(s, "/v1/logs", json.dumps(raw).encode())[0] == 200
    rows = otel_rows(led)
    assert [(r["kind"], json.loads(r["payload"])["prompt_id"]) for r in rows] == [
        ("turn.prompted", "p-run1"),
        ("cost.request", "p-run1"),
        ("turn.prompted", "p-run2"),
        ("cost.request", "p-run2"),
    ]


def test_a_request_without_a_prompt_id_is_keyed_on_its_request_id(live):
    s, led = live
    for raw in (logs(_api(13, None, "req-a")), logs(_api(13, None, "req-b")), logs(_api(13, None, "req-b"))):
        assert post(s, "/v1/logs", json.dumps(raw).encode())[0] == 200
    assert [json.loads(r["payload"])["request_id"] for r in otel_rows(led)] == ["req-a", "req-b"]


def _health(s):
    port = s.server_address[1]
    c = http.client.HTTPConnection(srv.HOST, port, timeout=5)
    c.request("GET", "/health", headers={"Host": f"{srv.HOST}:{port}"})
    r = c.getresponse()
    body = json.loads(r.read())
    c.close()
    return body


def test_health_counts_every_write_by_answer_and_every_record_kept_stored_or_already_logged(live):
    """The onloop lead's rule after round 2: a silent drop is never invisible again. Counts only, never a body."""
    s, led = live
    raw = json.dumps(logs(PROMPT, API, TOOL, record("some_other_event", 9))).encode()
    assert post(s, "/v1/logs", raw)[0] == 200
    assert post(s, "/v1/logs", raw)[0] == 200  # redelivered: its records are already logged
    assert post(s, "/v1/logs", gzip.compress(raw), headers={"Content-Encoding": "gzip"})[0] == 415
    assert post(s, "/v1/logs", raw, auth="Bearer wrong")[0] == 401
    assert post(s, "/v1/logs", b'{"hello": "world"}')[0] == 400
    r = _health(s)["receipts"]
    assert r["since"] and r["requests"] == {"v1": {"200": 2, "400": 1, "401": 1, "415": 1}}
    assert r["records"] == {"v1": {"received": 8, "kept": 6, "stored": 3, "duplicate": 3}}
    assert "SECRET" not in json.dumps(r) and "claude-test" not in json.dumps(r)
