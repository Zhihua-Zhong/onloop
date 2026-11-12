"""A stand-in for `claude -p` in the supervisor's tests. It ignores its flags and acts as FAKE_CLAUDE_MODE says.

  ok     read the prompt from stdin, print a result object as the last line, as `--output-format stream-json` does
         (with FAKE_CLAUDE_STRUCTURED, a JSON value, the result also carries it as structured_output, and one
         permission denial)
  slow   the same after printing one message line and sleeping FAKE_CLAUDE_SECONDS (default 2)
  fail   print an error result, exit 1
  hang   start a grandchild that sleeps, write both pids to FAKE_CLAUDE_PIDS, then sleep without output

`auth status` answers as `claude auth status` does: logged in, unless FAKE_CLAUDE_LOGGED_IN=0.
"""

import json
import os
import subprocess
import sys
import time


def main() -> None:
    if sys.argv[1:3] == ["auth", "status"]:
        logged_in = os.environ.get("FAKE_CLAUDE_LOGGED_IN", "1") == "1"
        print(json.dumps({"loggedIn": logged_in, "authMethod": "claude.ai" if logged_in else "none"}))
        sys.exit(0 if logged_in else 1)
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    prompt = sys.stdin.read() if mode != "hang" else ""
    if mode == "slow":
        print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
        time.sleep(float(os.environ.get("FAKE_CLAUDE_SECONDS", "2")))
        mode = "ok"
    if mode == "ok":
        res = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 3,
            "result": f"done ({len(prompt)} chars)",
            "total_cost_usd": 0.0,
        }
        if os.environ.get("FAKE_CLAUDE_STRUCTURED"):
            res["structured_output"] = json.loads(os.environ["FAKE_CLAUDE_STRUCTURED"])
            res["permission_denials"] = [{"tool_name": "Bash", "tool_input": {"command": "gh pr comment 1"}}]
        print(json.dumps(res))
        sys.exit(0)
    if mode == "fail":
        print(
            json.dumps(
                {"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 1}
            )
        )
        print("something broke", file=sys.stderr)
        sys.exit(1)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    with open(os.environ["FAKE_CLAUDE_PIDS"], "w", encoding="utf-8") as f:
        f.write(f"{os.getpid()} {child.pid}")
    time.sleep(120)


if __name__ == "__main__":
    main()
