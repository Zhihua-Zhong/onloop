import io
import json
import os
import subprocess
import sys
import time

from onloop import cli, liveness

ENV = {"CLAUDE_CODE_SESSION_ID": "cli-1", "CLAUDE_CODE_HOST_SESSION_ID": "local_abc"}


def rec(tmp_path, event, payload, env=ENV):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path = liveness.record(event, str(tmp_path), text, env=env)
    return json.loads(open(path, encoding="utf-8").read())


def test_state_follows_the_events_and_since_moves_only_on_change(tmp_path):
    r = rec(tmp_path, "SessionStart", {"session_id": "cli-1"})
    assert r["state"] == "idle"
    r = rec(tmp_path, "UserPromptSubmit", {"session_id": "cli-1", "prompt": "hello"})
    assert r["state"] == "running"
    since = r["since"]
    r = rec(tmp_path, "PreToolUse", {"session_id": "cli-1"})
    assert r["state"] == "running" and r["since"] == since
    r = rec(tmp_path, "Stop", {"session_id": "cli-1"})
    assert r["state"] == "idle"
    r = rec(tmp_path, "SessionEnd", {"session_id": "cli-1"})
    assert r["state"] == "ended"


def test_a_blocking_notification_blocks_and_the_next_tool_event_clears(tmp_path):
    rec(tmp_path, "UserPromptSubmit", {"session_id": "cli-1", "prompt": "x"})
    r = rec(
        tmp_path,
        "Notification",
        {
            "session_id": "cli-1",
            "notification_type": "permission_prompt",
            "message": "Claude needs your permission to use Bash",
        },
    )
    assert r["state"] == "blocked_on_prompt" and "blocked" in r
    assert r["last_notification"]["type"] == "permission_prompt"
    r = rec(tmp_path, "Stop", {"session_id": "cli-1"})
    assert r["state"] == "idle" and "blocked" not in r


def test_a_non_blocking_notification_changes_no_state(tmp_path):
    rec(tmp_path, "Stop", {"session_id": "cli-1"})
    r = rec(
        tmp_path,
        "Notification",
        {"session_id": "cli-1", "notification_type": "idle_prompt", "message": "waiting"},
    )
    assert r["state"] == "idle" and r["last_notification"]["type"] == "idle_prompt"


def test_both_ids_come_from_the_environment(tmp_path):
    r = rec(tmp_path, "SessionStart", {}, env=ENV)
    assert r["session_id"] == "cli-1" and r["host_session_id"] == "local_abc"


def test_the_prompt_text_is_never_written_only_hash_and_length(tmp_path):
    secret = "my password is hunter2-SECRET"
    rec(tmp_path, "UserPromptSubmit", {"session_id": "cli-1", "prompt": secret, "prompt_id": "p1"})
    files = [f for f in os.listdir(tmp_path)]
    assert files == ["cli-1.json"]  # no temp file left behind
    raw = open(tmp_path / "cli-1.json", encoding="utf-8").read()
    assert "hunter2" not in raw and "SECRET" not in raw
    p = json.loads(raw)["last_prompt"]
    assert p["length"] == len(secret) and len(p["sha256"]) == 64 and p["prompt_id"] == "p1"


def test_a_session_id_cannot_leave_the_directory(tmp_path):
    rec(tmp_path, "Stop", {"session_id": "../../evil"}, env={})
    assert os.listdir(tmp_path) == ["unknown.json"]


def test_a_malformed_payload_still_exits_zero(tmp_path, monkeypatch):
    for bad in ("not json at all", "[1, 2, 3]", "null", "\x00\xff", ""):
        monkeypatch.setattr(sys, "stdin", io.StringIO(bad))
        assert cli.main(["liveness", "record", "--event", "Stop", "--dir", str(tmp_path)]) == 0


def test_unreadable_arguments_and_an_unwritable_directory_still_exit_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert cli.main(["liveness", "record"]) == 0  # --event missing
    assert cli.main(["liveness", "nonsense", "--event", "Stop"]) == 0
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert cli.main(["liveness", "record", "--event", "Stop", "--dir", str(blocker / "sub")]) == 0


def test_a_failing_recorder_leaves_a_trace_a_reader_can_see(tmp_path, monkeypatch):
    monkeypatch.setattr(liveness, "record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert liveness.main_record("Stop", str(tmp_path)) == 0
    assert "boom" in (tmp_path / liveness.ERROR_LOG).read_text()


def test_a_stdin_that_never_closes_is_cut_off_within_the_timeout(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = subprocess.Popen(
        [sys.executable, "-m", "onloop.cli", "liveness", "record", "--event", "Stop", "--dir", str(tmp_path)],
        stdin=subprocess.PIPE,
        cwd=root,
    )
    t0 = time.time()
    try:
        assert p.wait(timeout=liveness.TIMEOUT_SECONDS + 3) == 0
    finally:
        if p.poll() is None:
            p.kill()
    assert time.time() - t0 < liveness.TIMEOUT_SECONDS + 3


def test_a_pasted_line_hashes_the_same_whatever_surrounds_it(tmp_path):
    import hashlib

    line = "Go: apply plan abc123 once."
    r = rec(tmp_path, "UserPromptSubmit", {"session_id": "cli-1", "prompt": f"  {line}\n\n"})
    assert r["last_prompt"]["sha256"] == hashlib.sha256(line.encode()).hexdigest()
    assert r["last_prompt"]["length"] == len(line)


def test_a_session_outside_the_roots_leaves_no_trace(tmp_path, monkeypatch):
    d = tmp_path / "rec"
    root = tmp_path / "work"
    for cwd in (
        str(tmp_path / "personal"),
        str(tmp_path / "workshop"),
        "",
        None,
    ):  # 'workshop' shares a prefix with 'work'
        payload = {"session_id": "cli-1", "hook_event_name": "Stop"}
        if cwd is not None:
            payload["cwd"] = cwd
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        assert (
            cli.main(["liveness", "record", "--event", "Stop", "--dir", str(d), "--only-under", str(root)])
            == 0
        )
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert (
        cli.main(["liveness", "record", "--event", "Stop", "--dir", str(d), "--only-under", str(root)]) == 0
    )
    assert not d.exists()  # no file, no directory, no _errors.log, no path


def test_a_session_inside_a_root_is_recorded_in_a_subfolder(tmp_path):
    root = tmp_path / "Work"
    sub = root / "a" / "b"
    path = liveness.record(
        "Stop",
        str(tmp_path / "rec"),
        json.dumps({"session_id": "cli-1", "cwd": str(sub)}),
        env={},
        only_under=[str(root)],
    )
    assert path is not None and os.path.exists(path)
    assert (
        liveness.record(
            "Stop",
            str(tmp_path / "rec"),
            json.dumps({"session_id": "cli-1", "cwd": str(root)}),
            env={},
            only_under=[str(root)],
        )
        is not None
    )


def test_another_case_of_the_root_is_the_same_folder_only_where_the_platform_says_so(tmp_path):
    """Windows paths ignore case, so the root written in upper case is still the root. On POSIX another case is
    another folder, and a session there is outside the root. The rule is the platform's own (os.path.normcase)."""
    root = tmp_path / "Work"
    path = liveness.record(
        "Stop",
        str(tmp_path / "rec"),
        json.dumps({"session_id": "cli-1", "cwd": str(root / "a").upper()}),
        env={},
        only_under=[str(root)],
    )
    if os.path.normcase("A") == "a":
        assert path is not None and os.path.exists(path)
    else:
        assert path is None
