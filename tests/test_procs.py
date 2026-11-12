"""procs.pid_alive: a running process is alive; one that has exited is not, also before its parent reaps it."""

import os
import subprocess
import sys
import time

import pytest

from onloop import procs


def test_a_running_child_is_alive_and_a_reaped_one_is_not():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert procs.pid_alive(p.pid)
    finally:
        p.kill()
        p.wait()
    assert not procs.pid_alive(p.pid)
    assert not procs.pid_alive(0) and not procs.pid_alive(-1)


@pytest.mark.skipif(
    not os.path.isdir("/proc/self"), reason="a zombie's state is read from /proc; Windows has no zombies"
)
def test_an_exited_child_not_yet_reaped_is_not_alive():
    """A zombie: kill(pid, 0) still finds it, but it runs nothing. onloopd's stop, its start check and the waker probe
    all ask pid_alive, so a zombie read as alive makes `onloop daemon stop` wait out its timeout (CI on Linux)."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        end = time.monotonic() + 10
        state = ""
        while time.monotonic() < end:
            with open(f"/proc/{p.pid}/stat", encoding="ascii") as f:
                state = f.read().rsplit(")", 1)[1].split()[0]
            if state == "Z":
                break
            time.sleep(0.02)
        assert state == "Z"  # exited, and not reaped: nothing here has called wait()
        assert not procs.pid_alive(p.pid)
    finally:
        p.wait()
