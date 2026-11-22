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
    assert cov == {
        "runs": 0,
        "reported": 0,
        "received": 0,
        "share": None,
        "low": [],
        "high": [],
        "low_below": 0.95,
        "high_above": 1.02,
    }


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


def tagged(led, clock, when, eid, session, run_id, total):
    clock.t = when
    led.append(
        {
            "id": eid,
            "source": "otel",
            "kind": "cost.request",
            "subject_ref": f"session:{session}",
            "actor": "builder",
            "payload": {"session_id": session, "role": "builder", "run_id": run_id, "input_tokens": total},
        }
    )


def test_a_runs_own_telemetry_counts_for_it_though_it_lands_after_the_next_run_started(tmp_path):
    """The carried review's case (round 4): the carrier starts the next run at once, and the first run's last batch
    lands after it. Tagged with its run id (onloop.run), it is the first run's; by time alone it was the next run's,
    flagging the wrong one and hiding the right one."""
    c = Clock()
    with Ledger(tmp_path / "l.db", clock=c) as led:
        tagged(led, c, at("09:01"), "a", "s1", "r1", 600)
        run(led, c, "r1", "s1", at("09:00"), at("09:02"), usage(1000))
        run(led, c, "r2", "s1", "2026-11-02T09:02:01.000Z", at("09:05"), usage(500))
        tagged(led, c, "2026-11-02T09:02:04.000Z", "late", "s1", "r1", 400)  # r1's last batch
        tagged(led, c, at("09:03"), "b", "s1", "r2", 100)  # r2 lost 400 of its 500
        cov = tl.coverage(led, at("08:00"), at("10:00"))
    assert [(x["run"], x["received"]) for x in cov["low"]] == [("r2", 100)] and cov["high"] == []


def test_more_than_was_reported_or_telemetry_against_nothing_is_flagged_high_and_a_share_just_under_is_low(
    tmp_path,
):
    c = Clock()
    with Ledger(tmp_path / "l.db", clock=c) as led:
        tagged(led, c, at("09:01"), "a", "s1", "r1", 1100)  # counted twice somewhere: 110%
        run(led, c, "r1", "s1", at("09:00"), at("09:02"), usage(1000))
        tagged(led, c, at("09:11"), "b", "s2", "r2", 50)  # telemetry against a count of nothing
        run(led, c, "r2", "s2", at("09:10"), at("09:12"), usage(0))
        tagged(led, c, at("09:21"), "c", "s3", "r3", 94996)  # 94.996%: rounds to 95%, still below
        run(led, c, "r3", "s3", at("09:20"), at("09:22"), usage(100000))
        cov = tl.coverage(led, at("08:00"), at("10:00"))
    assert [(x["run"], x["share"]) for x in cov["high"]] == [("r1", 1.1), ("r2", None)]
    assert [(x["run"], x["share"]) for x in cov["low"]] == [("r3", 0.95)]


def test_a_tagged_record_from_another_session_is_the_runs_nested_cost_not_its_coverage(tmp_path):
    """A carried recheck (node r2-coverage-recheck, its A): a claude the run starts inherits the run id with a session
    of its own; its tokens are not in the run's CLI count, so they are shown apart, never as over-coverage."""
    c = Clock()
    with Ledger(tmp_path / "l.db", clock=c) as led:
        tagged(led, c, at("09:01"), "own", "s1", "r1", 1000)
        tagged(led, c, at("09:01"), "child", "s-child", "r1", 300)
        run(led, c, "r1", "s1", at("09:00"), at("09:02"), usage(1000))
        cov = tl.coverage(led, at("08:00"), at("10:00"))
    assert cov["high"] == [] and cov["low"] == [] and cov["received"] == 1000
