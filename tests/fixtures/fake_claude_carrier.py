"""A stand-in for `claude` in the carrier's tests, written by hand: generic values only, no captured output.

  auth status        logged in on a subscription, unless FAKE_LOGGED_IN=0
  agents --json      the JSON list in the file FAKE_AGENTS names (default: an empty list)
  -p --resume ID ... read one stream-json user message from stdin and answer as CLI 2.1.321 does: `system/init`,
                     the replay of that message (with --replay-user-messages), one `rate_limit_event`, one
                     `result`; then wait for stdin to close and exit 0

Its behaviour, from the environment:
  FAKE_MODE          ok (default) | hang (replay, then sleep without output) | slow (FAKE_SECONDS before the result)
                     | noresult (exit 1 after the replay) | malformed (a line that is not JSON)
  FAKE_SECONDS       how long `slow` takes (default 1)
  FAKE_DENIALS       how many permission denials the result carries (default 0)
  FAKE_RATE          the rate-limit status: allowed (default) | allowed_warning | rejected
  FAKE_OVERAGE       1: the rate-limit event says isUsingOverage
  FAKE_API_STATUS    the result's api_error_status (default null); the result is then is_error
  FAKE_HOOK_LEDGER   a ledger file: log the Stop hook there, as onloopd's hook receiver would
  FAKE_HOOK_ROLE     the role that hook names, when the --settings file names none
  FAKE_OUTCOME       the turn's last line (default "outcome: done"); "none" for a turn without one
  FAKE_ENV_DUMP      a file: write the names of the variables in the environment there
  FAKE_LOCKDIR       a folder: hold `<session>.lock` while running, and append to `overlaps.log` when it is
                     already held (two runs on one session at once); also appends `runs.log`
"""

import json
import os
import pathlib
import sys
import time


def arg(name):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else None


def say(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def hold(lockdir, sid):
    if not lockdir:
        return None
    d = pathlib.Path(lockdir)
    path = d / f"{sid}.lock"
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        with open(d / "overlaps.log", "a", encoding="utf-8") as f:
            f.write(f"{sid} {os.getpid()} {time.time()}\n")
        return None
    return path


def hook_role():
    """The role the carrier's hook settings name (`--settings`), as the real CLI's Stop hook would post it."""
    import re

    path = arg("--settings")
    if path and os.path.exists(path):
        found = re.search(r"--role ([a-z][a-z0-9_-]*)", pathlib.Path(path).read_text(encoding="utf-8"))
        if found:
            return found.group(1)
    return os.environ.get("FAKE_HOOK_ROLE", "builder")


def log_stop(sid, prompt_id, outcome):
    led = os.environ.get("FAKE_HOOK_LEDGER")
    if not led:
        return
    sys.path.insert(0, os.environ.get("FAKE_SRC", ""))
    from onloop.daemon import hook_receiver
    from onloop.overlay.ledger import Ledger

    message = "Done with the step." + ("" if outcome == "none" else f"\n{outcome}")
    body = {
        "role": hook_role(),
        "payload": {"session_id": sid, "prompt_id": prompt_id, "last_assistant_message": message},
    }
    with Ledger(led) as ledger:
        ledger.append(hook_receiver.event_for("stop", body))


def main():
    if sys.argv[1:3] == ["auth", "status"]:
        ok = os.environ.get("FAKE_LOGGED_IN", "1") == "1"
        print(json.dumps({"loggedIn": ok, "authMethod": "claude.ai" if ok else "none"}))
        sys.exit(0 if ok else 1)
    if sys.argv[1:3] == ["agents", "--json"]:
        path = os.environ.get("FAKE_AGENTS")
        print(pathlib.Path(path).read_text(encoding="utf-8") if path and os.path.exists(path) else "[]")
        sys.exit(0)
    sid = arg("--resume") or arg("--session-id") or "none"
    if os.environ.get("FAKE_ENV_DUMP"):
        pathlib.Path(os.environ["FAKE_ENV_DUMP"]).write_text(json.dumps(sorted(os.environ)), encoding="utf-8")
    mode = os.environ.get("FAKE_MODE", "ok")
    lock = hold(os.environ.get("FAKE_LOCKDIR"), sid)
    if os.environ.get("FAKE_LOCKDIR"):
        with open(pathlib.Path(os.environ["FAKE_LOCKDIR"]) / "runs.log", "a", encoding="utf-8") as f:
            f.write(f"{sid} {os.getpid()} start {time.time()}\n")
    try:
        say({"type": "system", "subtype": "init", "session_id": sid})
        line = sys.stdin.buffer.readline()
        msg = json.loads(line.decode("utf-8"))
        content = msg["message"]["content"]
        if "--replay-user-messages" in sys.argv:
            say(
                {
                    "type": "user",
                    "isReplay": True,
                    "session_id": sid,
                    "message": {"role": "user", "content": content},
                }
            )
        if mode == "malformed":
            sys.stdout.write("this is not json\n")
            sys.stdout.flush()
            time.sleep(30)
        if mode == "hang":
            time.sleep(300)
        if mode == "noresult":
            sys.exit(1)
        if mode == "slow":
            time.sleep(float(os.environ.get("FAKE_SECONDS", "1")))
        status = os.environ.get("FAKE_RATE", "allowed")
        say(
            {
                "type": "rate_limit_event",
                "session_id": sid,
                "rate_limit_info": {
                    "status": status,
                    "resetsAt": 1900000000,
                    "rateLimitType": "five_hour",
                    "overageStatus": "rejected",
                    "isUsingOverage": os.environ.get("FAKE_OVERAGE") == "1",
                    "unifiedWindows": {
                        "five_hour": {"utilization": 0.5, "resetsAt": 1900000000, "status": status}
                    },
                },
            }
        )
        api = os.environ.get("FAKE_API_STATUS")
        denials = int(os.environ.get("FAKE_DENIALS", "0"))
        prompt_id = f"p-{os.getpid()}-{time.time_ns()}"
        log_stop(sid, prompt_id, os.environ.get("FAKE_OUTCOME", "outcome: done"))
        say(
            {
                "type": "result",
                "subtype": "success",
                "is_error": bool(api),
                "api_error_status": int(api) if api else None,
                "terminal_reason": "api_error" if api else "completed",
                "num_turns": 1,
                "session_id": sid,
                "result": "Done with the step.",
                "permission_denials": [{"tool_name": "Write", "tool_input": {"file_path": "x.txt"}}]
                * denials,
            }
        )
        sys.stdin.buffer.read()  # until the carrier closes stdin
    finally:
        if lock is not None:
            lock.unlink()


if __name__ == "__main__":
    main()
