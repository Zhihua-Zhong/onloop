"""The cross-check of every token figure (the onloop lead's rule after the dogfood's round 2, 2026-11-22): a carried
run's CLI reports its own tokens at the end of the run, and the replay shows how much of that its telemetry brought,
flagging any run below 95%."""

from onloop import timeline as tl
from onloop.carrier.stream import Stream
from onloop.overlay.ledger import Ledger

from .test_timeline import Clock, at, otel


def run(led, clock, rid, session, started, ended, usage):
    clock.t = started
    led.append(
        {
            "id": f"start-{rid}",
            "source": "carrier",
            "kind": "run.started",
            "subject_ref": "role:builder",
            "actor": "builder",
            "payload": {
                "id": rid,
                "role": "builder",
                "session_id": session,
                "lease_id": f"lease-{rid}",
                "deliveries": [],
            },
        }
    )
    clock.t = ended
    led.append(
        {
            "id": f"end-{rid}",
            "source": "carrier",
            "kind": "run.ended",
            "subject_ref": "role:builder",
            "actor": "builder",
            "payload": {"id": rid, "status": "ok", "usage": usage},
        }
    )


def usage(total):
    return {"input": total, "output": 0, "cache_creation": 0, "cache_read": 0, "total": total}


def test_a_run_whose_telemetry_brought_what_it_reported_is_covered_and_a_short_one_is_flagged(tmp_path):
    c = Clock()
    with Ledger(tmp_path / "l.db", clock=c) as led:
        otel(led, c, at("09:01"), "q1", "s1", input_tokens=600, output_tokens=400)
        otel(led, c, at("09:01"), "qx", "s-other", input_tokens=5000)  # another session: never this run's
        run(led, c, "r1", "s1", at("09:00"), at("09:02"), usage(1000))
        # the second run on the same session: its sequence restarted and most of its records never came
        otel(led, c, at("09:11"), "q2", "s1", input_tokens=100)
        run(led, c, "r2", "s1", at("09:10"), at("09:12"), usage(1000))
        cov = tl.coverage(led, at("08:00"), at("10:00"))
    assert (cov["runs"], cov["reported"], cov["received"], cov["share"]) == (2, 2000, 1100, 0.55)
    assert [(x["run"], x["received"], x["share"]) for x in cov["low"]] == [("r2", 100, 0.1)]


def test_the_last_export_after_run_ended_counts_but_never_the_sessions_next_run(tmp_path):
    c = Clock()
    with Ledger(tmp_path / "l.db", clock=c) as led:
        run(led, c, "r1", "s1", at("09:00"), at("09:02"), usage(300))
        otel(led, c, "2026-11-02T09:02:10.000Z", "late", "s1", input_tokens=300)  # inside the grace
        run(led, c, "r2", "s1", "2026-11-02T09:02:15.000Z", at("09:05"), usage(50))
        # the next run's, though inside the first run's grace
        otel(led, c, "2026-11-02T09:02:25.000Z", "next", "s1", input_tokens=50)
        cov = tl.coverage(led, at("08:00"), at("10:00"))
    assert cov["low"] == [] and cov["received"] == 350


def test_a_run_from_before_the_cli_count_was_recorded_is_not_counted(tmp_path):
    c = Clock()
    with Ledger(tmp_path / "l.db", clock=c) as led:
        run(led, c, "r0", "s1", at("09:00"), at("09:02"), None)
        cov = tl.coverage(led, at("08:00"), at("10:00"))
    assert cov == {"runs": 0, "reported": 0, "received": 0, "share": None, "low": [], "low_below": 0.95}


def result(usage=None, since_last=None, model_usage=None):
    o = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1}
    if usage is not None:
        o["usage"] = usage
    if since_last is not None:
        o["model_usage_since_last_result"] = since_last
    if model_usage is not None:
        o["modelUsage"] = model_usage
    return o


def feed(*objs):
    import json

    s = Stream()
    for o in objs:
        s.feed(json.dumps(o))
    return s


def camel(i, o, cr, cc):
    return {"inputTokens": i, "outputTokens": o, "cacheReadInputTokens": cr, "cacheCreationInputTokens": cc}


def snake(i, o, cr, cc):
    return {
        "input_tokens": i,
        "output_tokens": o,
        "cache_read_input_tokens": cr,
        "cache_creation_input_tokens": cc,
    }


def test_the_cli_count_is_each_turns_usage_over_every_model_never_the_sessions_total():
    """Measured on CLI 2.1.321: on a resumed session `modelUsage` carries what earlier processes used (here 6, 10, 3310,
    3007 from before this run); `model_usage_since_last_result` is the turn's own, per model."""
    resumed = result(
        snake(4, 5, 3167, 18),
        since_last={"model-a": camel(4, 5, 3167, 18), "model-b": camel(1, 1, 0, 0)},
        model_usage={"model-a": camel(10, 15, 6477, 3025), "model-b": camel(1, 1, 0, 0)},
    )
    second = result(snake(2, 3, 100, 7), since_last={"model-a": camel(2, 3, 100, 7)})
    assert feed(resumed, second).reported_usage() == {
        "input": 7,
        "output": 9,
        "cache_creation": 25,
        "cache_read": 3267,
        "total": 41,
    }


def test_without_the_per_turn_model_usage_each_turns_usage_is_summed_and_without_either_there_is_no_count():
    a = result(snake(2, 5, 1, 10), model_usage={"m": camel(99, 99, 99, 99)})
    b = result(snake(4, 5, 2, 0))
    assert feed(a, b).reported_usage()["total"] == 26
    assert feed(result()).reported_usage() is None and Stream().reported_usage() is None
