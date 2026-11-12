"""onloopd as a process: it starts, serves the ledger on the loopback address, ingests the legacy store on its tick,
refuses another interface, and stops cleanly when asked through its pid file."""

import json
import pathlib
import sqlite3
import subprocess
import sys
import time
import urllib.request

import pytest

from onloop import procs
from onloop.daemon import main as daemon
from onloop.daemon import server as srv

from .legacy_poll_store import Store

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "store_schema_1.sql"


def legacy_store(tmp_path):
    path = tmp_path / "onloop.db"
    con = sqlite3.connect(path)
    con.executescript(FIXTURE.read_text(encoding="utf-8"))
    con.execute("PRAGMA user_version=1")
    con.commit()
    con.close()
    Store(path).close()
    return path


def start(tmp_path, *extra):
    ledger = tmp_path / "state" / "ledger.db"
    argv = [
        sys.executable,
        "-m",
        "onloop.cli",
        "daemon",
        "run",
        "--ledger",
        str(ledger),
        "--port",
        "0",
        "--tick",
        "0.5",
    ]
    proc = procs.start(
        [*argv, *extra], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
    )
    return proc, ledger.parent


def wait_for(predicate, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = predicate()
        if got:
            return got
        time.sleep(0.2)
    raise AssertionError("timed out")


def heartbeat(state):
    path = state / daemon.HEARTBEAT_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def get(port, path):
    with urllib.request.urlopen(f"http://{srv.HOST}:{port}{path}", timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def test_onloopd_serves_an_imported_ledger_and_stops_cleanly_by_pid(tmp_path):
    store = legacy_store(tmp_path)
    ledger = tmp_path / "state" / "ledger.db"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    from onloop import cli

    assert (
        cli.main(["overlay", "import", "--store", str(store), "--ledger", str(ledger)]) == 0
    )  # the upgrade path
    proc, state = start(tmp_path)
    try:
        beat = wait_for(lambda: (b := heartbeat(state)) and b["last_seq"] > 0 and b)
        # The pid is the serving process's own (on Windows a venv's python.exe may be a launcher with a child).
        pid = int((state / daemon.PID_FILE).read_text(encoding="utf-8").strip())
        assert beat["pid"] == pid and procs.pid_alive(pid)
        port = beat["port"]
        health = get(port, "/health")
        assert health["status"] == "ok" and health["pid"] == pid
        assert health["ledger"]["cursors"] == {"legacy:onloop.db": beat["last_seq"]}
        assert {"pr#1", "pr#2"} <= {i["id"] for i in get(port, "/api/items")["items"]}
        assert len(get(port, "/api/waits")["waits"]) == 2 and len(get(port, "/api/leases")["leases"]) == 3
        assert get(port, "/api/constraint")["constraint"]["on_kind"] == "resource"
        code, message = daemon.request_stop(state, timeout_s=30)
        assert code == 0 and "stopped" in message
        assert proc.wait(timeout=10) == 0 and not procs.pid_alive(pid)
        assert not (state / daemon.PID_FILE).exists() and not (state / daemon.STOP_FILE).exists()
        out = proc.stdout.read().decode("utf-8", errors="replace")
        assert f"listening on http://{srv.HOST}:{port}" in out and "onloopd: stopped" in out
    finally:
        if proc.poll() is None:
            procs.kill_tree(proc)


def test_a_second_onloopd_on_the_same_ledger_refuses_to_start(tmp_path):
    proc, state = start(tmp_path)
    try:
        wait_for(lambda: heartbeat(state))
        second, _ = start(tmp_path)
        assert second.wait(timeout=30) == 2
        assert b"already running" in second.stdout.read()
    finally:
        daemon.request_stop(state, timeout_s=30)
        proc.wait(timeout=10)


@pytest.mark.parametrize("host", ["0.0.0.0", "::"])  # scan:allow
def test_another_interface_is_refused_before_anything_starts(tmp_path, host):
    proc, state = start(tmp_path, "--host", host)
    assert proc.wait(timeout=30) == 2
    assert b"binds 127.0.0.1 only" in proc.stdout.read()  # scan:allow
    assert not (state / daemon.PID_FILE).exists()


def test_stop_says_so_when_nothing_runs(tmp_path):
    assert daemon.request_stop(tmp_path)[0] == 1


def test_a_hook_posted_by_the_hook_command_lands_in_the_ledger(tmp_path):
    from onloop.overlay.ledger import Ledger

    proc, state = start(tmp_path)
    try:
        wait_for(lambda: heartbeat(state))
        stop = (pathlib.Path(__file__).parent / "fixtures" / "hooks" / "stop.json").read_bytes()
        hook = subprocess.run(
            [
                sys.executable,
                "-m",
                "onloop.cli",
                "daemon",
                "hook",
                "--event",
                "stop",
                "--role",
                "builder",
                "--ledger",
                str(state / "ledger.db"),
            ],
            input=stop,
            capture_output=True,
            timeout=30,
        )
        assert hook.returncode == 0 and not (state / "hook-errors.log").exists(), hook.stderr
        with Ledger(state / "ledger.db", readonly=True) as led:
            turns = led._rows("SELECT role, outcome, parse_error FROM turns")
        assert turns == [{"role": "builder", "outcome": None, "parse_error": turns[0]["parse_error"]}]
        assert "not an outcome line" in turns[0]["parse_error"]
    finally:
        daemon.request_stop(state, timeout_s=30)
        proc.wait(timeout=10)


def test_a_job_pings_onloopd_for_a_schedule_its_config_declares(tmp_path):
    from onloop.daemon import token
    from onloop.overlay.ledger import Ledger

    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "onloopd.yaml").write_text(
        "schedules:\n  - id: nightly-backup\n    period: 1d\n    owner: ops\n", encoding="utf-8"
    )
    proc, state = start(tmp_path)
    try:
        beat = wait_for(lambda: heartbeat(state))
        url = f"http://{srv.HOST}:{beat['port']}/sources/schedules/nightly-backup/fail"
        req = urllib.request.Request(
            url,
            data=b'{"log": "disk full"}',
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token.read(state)}"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200 and json.loads(r.read())["logged"] == 1
        with Ledger(state / "ledger.db", readonly=True) as led:
            s = led.schedules()
        assert [(x["id"], x["last_status"], x["log_tail"]) for x in s] == [
            ("nightly-backup", "fail", "disk full")
        ]
    finally:
        daemon.request_stop(state, timeout_s=30)
        proc.wait(timeout=10)


def test_onloopd_does_not_start_with_a_config_it_cannot_take(tmp_path):
    bad = tmp_path / "onloopd.yaml"
    bad.write_text("schedules:\n  - id: nightly-backup\n    period: daily\n", encoding="utf-8")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "onloop.cli",
            "daemon",
            "run",
            "--ledger",
            str(tmp_path / "ledger.db"),
            "--config",
            str(bad),
            "--port",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 2 and "not a duration: 'daily'" in r.stderr and "schedules[0].owner" in r.stderr
    assert not (tmp_path / "onloopd.pid").exists()


def test_a_port_in_use_is_a_clear_error_and_no_other_port(tmp_path):
    import socket

    holder = socket.socket()
    holder.bind((srv.HOST, 0))
    holder.listen()
    port = holder.getsockname()[1]
    try:
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "onloop.cli",
                "daemon",
                "run",
                "--ledger",
                str(tmp_path / "ledger.db"),
                "--port",
                str(port),
                "--tick",
                "0.5",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        holder.close()
    assert r.returncode == 2, r.stdout + r.stderr
    assert f"port {port} on {srv.HOST} is already in use" in r.stdout and "--port N" in r.stdout
    assert "listening" not in r.stdout and not (tmp_path / "onloopd.pid").exists()


def test_the_port_comes_from_the_flag_then_the_config_then_the_default(tmp_path):
    from onloop.daemon import config as cfg_mod

    assert srv.DEFAULT_PORT == 8790
    assert cfg_mod.parse({"port": 8799}).port == 8799 and cfg_mod.parse({}).port is None
    for bad in (80, 70000, "8790", True):
        with pytest.raises(cfg_mod.ConfigError, match="port must be"):
            cfg_mod.parse({"port": bad})
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "onloopd.yaml").write_text("port: 0\n", encoding="utf-8")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "onloop.cli",
            "daemon",
            "run",
            "--ledger",
            str(tmp_path / "state" / "ledger.db"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 2 and "port must be a whole number from 1024 to 65535" in r.stderr
