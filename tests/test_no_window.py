"""No console window pops up from onloop on Windows (0.6.1).

A console program (git, claude, wevtutil, taskkill) started by a process with no console of its own (onloopd under
pythonw, a scheduled task) gets a new console window, which with Windows Terminal as the default terminal pops up and
takes the keyboard. Every child goes through `procs` (run, popen, run_text, start), which adds CREATE_NO_WINDOW; these
tests fail on a spawn that bypasses it, and on a helper that stops adding the flag."""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys

import pytest

from onloop import procs

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "onloop"

# what starts a process; only procs.py may name these
SPAWNS = {
    "subprocess": {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"},
    "os": {
        "system",
        "popen",
        "startfile",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "posix_spawn",
        "posix_spawnp",
    }
    | {f"spawn{s}" for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
}
# the only places a child keeps the person's console: a command the person runs through onloop and works in
IN_CONSOLE = {"cli.py", "asapp.py"}


def _annotations(tree: ast.AST) -> set[int]:
    """ids of the nodes inside type annotations, where naming subprocess.Popen starts nothing"""
    out: set[int] = set()
    for node in ast.walk(tree):
        anns = []
        if isinstance(node, ast.arg | ast.AnnAssign):
            anns.append(node.annotation)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            anns.append(node.returns)
        for a in anns:
            if a is not None:
                out.update(id(n) for n in ast.walk(a))
    return out


def _bypasses(path: pathlib.Path, root: pathlib.Path = SRC) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _annotations(tree)
    found = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.attr in SPAWNS.get(node.value.id, ())
        ):
            found.append(f"{path.relative_to(root)}:{node.lineno} {node.value.id}.{node.attr}")
        if isinstance(node, ast.ImportFrom) and node.module in SPAWNS:
            for alias in node.names:
                if alias.name in SPAWNS[node.module]:
                    found.append(
                        f"{path.relative_to(root)}:{node.lineno} from {node.module} import {alias.name}"
                    )
    return found


def test_every_spawn_in_onloop_goes_through_procs():
    bad = [b for f in sorted(SRC.rglob("*.py")) if f.name != "procs.py" for b in _bypasses(f)]
    assert bad == [], "start children through onloop.procs (run, popen, run_text, start), never directly"


def test_the_scan_sees_a_bypass(tmp_path):
    """The scan itself bites: a direct spawn in a module is found, an annotation is not."""
    f = tmp_path / "mod.py"
    f.write_text(
        "import subprocess\nfrom subprocess import Popen\n"
        "def f(p: subprocess.Popen[str]) -> subprocess.Popen[str]:\n    return subprocess.run(['git'])\n",
        encoding="utf-8",
    )
    assert [b.split(" ", 1)[1] for b in _bypasses(f, tmp_path)] == [
        "from subprocess import Popen",
        "subprocess.run",
    ]


def test_only_a_command_the_person_works_in_keeps_their_console():
    users = sorted(
        f.name
        for f in SRC.rglob("*.py")
        if f.name != "procs.py" and "run_in_console" in f.read_text(encoding="utf-8")
    )
    assert set(users) <= IN_CONSOLE, users


@pytest.mark.parametrize("helper", ["run", "popen"])
def test_on_windows_every_child_gets_no_console_window(monkeypatch, helper):
    seen = {}

    def fake(argv, **kw):
        seen.update(kw)
        return "started"

    monkeypatch.setattr(procs, "on_windows", lambda: True)
    monkeypatch.setattr(procs.subprocess, "run" if helper == "run" else "Popen", fake)
    getattr(procs, helper)(["git", "log"], creationflags=0x200, cwd="x")
    assert seen["creationflags"] == 0x200 | procs.CREATE_NO_WINDOW and seen["cwd"] == "x"


def test_run_text_and_start_add_it_too(monkeypatch):
    seen = []
    monkeypatch.setattr(procs, "on_windows", lambda: True)
    monkeypatch.setattr(
        procs.subprocess,
        "run",
        lambda argv, **kw: seen.append(kw) or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    procs.run_text(["git", "status"])
    assert seen[-1]["creationflags"] & procs.CREATE_NO_WINDOW
    assert procs.spawn_kwargs()["creationflags"] & procs.CREATE_NO_WINDOW
    flags = []
    procs.start(["claude"], popen=lambda argv, **kw: flags.append(kw["creationflags"]))
    assert flags[0] & procs.CREATE_NO_WINDOW


def test_elsewhere_nothing_changes(monkeypatch):
    seen = {}
    monkeypatch.setattr(procs, "on_windows", lambda: False)
    monkeypatch.setattr(procs.subprocess, "run", lambda argv, **kw: seen.update(kw))
    procs.run(["git"], cwd="x")
    assert seen == {"cwd": "x"}


def test_the_persons_own_command_keeps_their_console(monkeypatch):
    seen = {}
    monkeypatch.setattr(procs, "on_windows", lambda: True)
    monkeypatch.setattr(procs.subprocess, "run", lambda argv, **kw: seen.update(kw))
    procs.run_in_console(["claude"], env={})
    assert "creationflags" not in seen


# `onloop daemon start`: onloopd in the background, with no console window


class _Child:
    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code


def test_daemon_start_runs_onloopd_with_no_console_and_returns_its_pid(tmp_path, monkeypatch):
    from onloop.daemon import main as daemon

    seen = {}

    def fake_popen(argv, **kw):
        seen.update(kw, argv=argv)
        (tmp_path / daemon.PID_FILE).write_text(
            str(os.getpid()), encoding="utf-8"
        )  # a live pid, as run writes it
        return _Child()

    monkeypatch.setattr(procs, "on_windows", lambda: True)
    monkeypatch.setattr(procs.subprocess, "Popen", fake_popen)
    code, message = daemon.start_detached(tmp_path / "ledger.db", ["--port", "8790"], python="py")
    assert code == 0 and f"pid {os.getpid()}" in message
    assert seen["argv"] == [
        "py",
        "-m",
        "onloop",
        "daemon",
        "run",
        "--ledger",
        str(tmp_path / "ledger.db"),
        "--port",
        "8790",
    ]
    assert (
        seen["creationflags"] & procs.CREATE_NO_WINDOW
        and seen["creationflags"] & procs.CREATE_NEW_PROCESS_GROUP
    )
    assert seen["stdin"] == subprocess.DEVNULL and seen["stderr"] == subprocess.STDOUT
    assert pathlib.Path(seen["stdout"].name) == tmp_path / daemon.LOG_FILE


def test_daemon_start_elsewhere_runs_it_in_its_own_session(tmp_path, monkeypatch):
    from onloop.daemon import main as daemon

    seen = {}

    def fake_popen(argv, **kw):
        seen.update(kw)
        (tmp_path / daemon.PID_FILE).write_text(str(os.getpid()), encoding="utf-8")
        return _Child()

    monkeypatch.setattr(procs, "on_windows", lambda: False)
    assert daemon.start_detached(tmp_path / "ledger.db", popen=fake_popen)[0] == 0
    assert seen["start_new_session"] is True and "creationflags" not in seen


def test_daemon_start_leaves_a_running_onloopd_alone(tmp_path):
    from onloop.daemon import main as daemon

    (tmp_path / daemon.PID_FILE).write_text(str(os.getpid()), encoding="utf-8")
    code, message = daemon.start_detached(
        tmp_path / "ledger.db", popen=lambda *a, **k: pytest.fail("started twice")
    )
    assert code == 0 and "already running" in message


def test_daemon_start_says_so_when_onloopd_stops_at_once(tmp_path):
    from onloop.daemon import main as daemon

    code, message = daemon.start_detached(
        tmp_path / "ledger.db", popen=lambda *a, **k: _Child(2), timeout_s=5
    )
    assert code == 2 and "exit 2" in message and daemon.LOG_FILE in message


@pytest.mark.skipif(sys.platform != "win32", reason="consoles are a Windows matter")
def test_a_child_and_its_own_children_have_no_console_window():
    """Two levels, as the carrier's claude runs git and its hooks: the child onloop starts runs its own child the plain
    way, with no flags. Neither has a console window (GetConsoleWindow is 0): the grandchild shares the child's hidden
    console instead of getting a new, visible one."""
    level2 = "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"
    level1 = (
        "import ctypes, subprocess, sys\n"
        "print(ctypes.windll.kernel32.GetConsoleWindow())\n"
        f"print(subprocess.run([sys.executable, '-c', {level2!r}], capture_output=True, text=True).stdout.strip())\n"
    )
    out = procs.run(
        [sys.executable, "-c", level1], capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["0", "0"]


# Both branches, on every machine: the Windows branch's flags are procs' own numbers, and the POSIX branch passes no
# creationflags. 0.6.1's CI found what a Windows-only run hid: a branch read a constant only Windows' subprocess has.


def test_the_platform_check_is_os_name():
    assert procs.on_windows() is (os.name == "nt")


def test_the_windows_flags_need_nothing_from_windows():
    for name in ("CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP"):
        assert isinstance(getattr(procs, name), int)
        if hasattr(subprocess, name):  # on Windows, the same numbers subprocess has
            assert getattr(procs, name) == getattr(subprocess, name)


@pytest.mark.parametrize("windows", [True, False])
def test_each_branch_runs_on_this_machine(monkeypatch, windows):
    """The POSIX branch on Windows and the Windows branch on POSIX, through every helper that branches."""
    seen = []
    monkeypatch.setattr(procs, "on_windows", lambda: windows)
    monkeypatch.setattr(
        procs.subprocess,
        "run",
        lambda argv, **kw: seen.append(("run", kw)) or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    monkeypatch.setattr(procs.subprocess, "Popen", lambda argv, **kw: seen.append(("popen", kw)) or _Child())
    procs.run(["git", "status"], cwd="x")
    procs.run_text(["git", "log"])
    procs.popen(["claude"], creationflags=procs.CREATE_NEW_PROCESS_GROUP if windows else 0)
    procs.start(["claude"])
    for _, kw in seen:
        if windows:
            assert kw["creationflags"] & procs.CREATE_NO_WINDOW
        else:
            assert not kw.get("creationflags")
    assert procs.spawn_kwargs() == (
        {"creationflags": procs.CREATE_NEW_PROCESS_GROUP | procs.CREATE_NO_WINDOW}
        if windows
        else {"start_new_session": True}
    )


@pytest.mark.parametrize("windows", [True, False])
def test_daemon_start_takes_either_branch_on_this_machine(tmp_path, monkeypatch, windows):
    from onloop.daemon import main as daemon

    seen = {}

    def fake_popen(argv, **kw):
        seen.update(procs.no_window(kw))
        (tmp_path / daemon.PID_FILE).write_text(str(os.getpid()), encoding="utf-8")
        return _Child()

    monkeypatch.setattr(procs, "on_windows", lambda: windows)
    code, _ = daemon.start_detached(tmp_path / "ledger.db", popen=fake_popen)
    assert code == 0
    if windows:
        assert (
            seen["creationflags"] == procs.CREATE_NEW_PROCESS_GROUP | procs.CREATE_NO_WINDOW
            and "start_new_session" not in seen
        )
    else:
        assert seen["start_new_session"] is True and "creationflags" not in seen


def test_no_source_reads_a_windows_only_constant_from_subprocess():
    """subprocess defines these only on Windows, so reading one fails on Linux and macOS the moment a branch reaches it,
    and a test run on Windows never sees it (0.6.1). Use procs' own numbers instead. Fails on this machine too."""
    windows_only = {
        "CREATE_NO_WINDOW",
        "CREATE_NEW_PROCESS_GROUP",
        "CREATE_NEW_CONSOLE",
        "DETACHED_PROCESS",
        "STARTUPINFO",
        "STARTF_USESHOWWINDOW",
        "SW_HIDE",
        "CREATE_BREAKAWAY_FROM_JOB",
        "CREATE_DEFAULT_ERROR_MODE",
    }
    bad = []
    for f in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "subprocess"
                and node.attr in windows_only
            ):
                bad.append(f"{f.relative_to(SRC)}:{node.lineno} subprocess.{node.attr}")
    assert bad == []
