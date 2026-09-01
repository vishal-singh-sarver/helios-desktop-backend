"""The backend exits when the app that started it dies.

A crashed Electron app never reaches `will-quit`, so the cleanup that would
stop this process never runs. A backend holding a ~1.4 GB scenario context
then stays resident indefinitely — one was found on a tester's machine from an
earlier session still holding 1.33 GB, with nothing to notice it. A dead parent
cannot clean up after itself, so the child has to notice and leave.

The signal is a PIPE on stdin that the app holds open and never writes to. The
OS closes it when the app dies for any reason, we read EOF, and we exit.

The gate is the whole risk here, so most of this file is about the gate. The
app only pipes stdin on the branch that added it; every other branch spawns
with stdio 'ignore', which is /dev/null, where a read returns EOF immediately.
An ungated reader would exit the backend on every startup on every machine.
"""
import os
import socket
import subprocess
import sys
import textwrap
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

import backend_wrapper as bw

BACKEND_ROOT = str(Path(bw.__file__).resolve().parent)


@contextmanager
def _stdin_is(fd):
    """Run the block with `fd` installed as file descriptor 0."""
    saved = os.dup(0)
    try:
        os.dup2(fd, 0)
        yield
    finally:
        os.dup2(saved, 0)
        os.close(saved)


def _armed(monkeypatch):
    """Whether the watchdog thread started, without ever running it."""
    started = {}

    class _Spy(threading.Thread):
        def start(self):
            started["yes"] = True

    monkeypatch.setattr(bw.threading, "Thread", _Spy)
    bw._start_parent_watchdog()
    return started.get("yes", False)


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------

def test_arms_when_stdin_is_a_pipe(monkeypatch):
    """The one case that should arm: the app handed us a live pipe."""
    r, w = os.pipe()
    try:
        with _stdin_is(r):
            assert _armed(monkeypatch) is True
    finally:
        os.close(r)
        os.close(w)


def test_arms_when_stdin_is_a_socket(monkeypatch):
    """The case that actually ships, and the one a FIFO-only gate misses.

    Node's `stdio: 'pipe'` is socketpair() on POSIX, so the app hands us a
    SOCKET. Probed against a real node spawn: S_ISSOCK=True, S_ISFIFO=False.
    A gate written against os.pipe() alone passes its unit test and then never
    arms in production — silently, with no symptom but the orphan itself.
    """
    a, b = socket.socketpair()
    try:
        with _stdin_is(a.fileno()):
            assert _armed(monkeypatch) is True
    finally:
        a.close()
        b.close()


def test_does_not_arm_on_devnull(monkeypatch):
    """THE regression test. stdio 'ignore' gives /dev/null, where a read
    returns EOF at once — arming there kills the backend on every startup."""
    fd = os.open(os.devnull, os.O_RDONLY)
    try:
        with _stdin_is(fd):
            assert _armed(monkeypatch) is False
    finally:
        os.close(fd)


def test_does_not_arm_on_a_regular_file(monkeypatch, tmp_path):
    """`backend_wrapper.py < some-file` must not be read as a dead parent."""
    target = tmp_path / "in.txt"
    target.write_bytes(b"")
    fd = os.open(target, os.O_RDONLY)
    try:
        with _stdin_is(fd):
            assert _armed(monkeypatch) is False
    finally:
        os.close(fd)


def test_does_not_arm_when_stdin_is_closed(monkeypatch):
    """A detached service may have no fd 0 at all. os.fstat raises; we return."""
    saved = os.dup(0)
    try:
        os.close(0)
        assert _armed(monkeypatch) is False
    finally:
        os.dup2(saved, 0)
        os.close(saved)


# --------------------------------------------------------------------------
# The reader, end to end, in a real process
# --------------------------------------------------------------------------

def _child(extra: str = "") -> str:
    return textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {BACKEND_ROOT!r})
        import backend_wrapper as bw
        bw._start_parent_watchdog()
        sys.stderr.write("armed\\n"); sys.stderr.flush()
        {extra}
        time.sleep(30)
        sys.exit(7)
    """)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe semantics")
def test_closing_the_pipe_exits_the_backend():
    """The actual fix: parent lets go of the write end, child leaves."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _child()],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stderr.readline() == b"armed\n"

    proc.stdin.close()                      # the app dying, in effect
    assert proc.wait(timeout=5) == 0, "did not exit on EOF"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe semantics")
def test_exits_even_though_stdout_died_with_the_parent():
    """The failure this file exists to prevent from coming back.

    A real parent death takes stdout with it, so the log line announcing the
    exit raises — and in a thread, that kills the thread before os._exit(0)
    runs. The watchdog then disappears silently and the orphan lives. It has
    to exit with nowhere to write, so both pipes close here.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", _child()],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stderr.readline() == b"armed\n"

    proc.stdout.close()                     # stdout is gone, as after a crash
    proc.stdin.close()
    assert proc.wait(timeout=5) == 0, \
        "did not exit — a failed log line swallowed the exit again"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe semantics")
def test_stays_alive_while_the_pipe_is_held_open():
    """The control. Without this, a test that kills the process for the wrong
    reason still passes."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _child()],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stderr.readline() == b"armed\n"
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=2)            # still running, as it must be
    finally:
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe semantics")
def test_a_stray_byte_does_not_kill_it():
    """Nothing is meant to be written, but a byte must not read as death."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _child()],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stderr.readline() == b"armed\n"
    proc.stdin.write(b"x")
    proc.stdin.flush()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=2)
    finally:
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe semantics")
def test_devnull_child_survives_startup():
    """The landmine, in a real process: spawned exactly as every unmerged
    branch still spawns it, the backend must boot and stay up."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _child()],
        stdin=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    assert proc.stderr.readline() == b"armed\n"
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=2)
    finally:
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX socketpair")
def test_the_reader_actually_runs_on_a_socket_not_just_a_fifo():
    """Covers the path PRODUCTION uses, which nothing else here executed.

    subprocess.PIPE gives a FIFO; node's stdio:'pipe' gives a SOCKETPAIR. Every
    end-to-end test above used PIPE, and the one socketpair test monkeypatches
    Thread so the reader body never runs — so the socket path had no coverage at
    all, in the shape that actually ships.
    """
    parent, child = socket.socketpair()
    src = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {BACKEND_ROOT!r})
        import backend_wrapper as bw
        bw._start_parent_watchdog()
        import threading
        assert any(t.name == "parent-watchdog" for t in threading.enumerate())
        sys.stderr.write("armed\\n"); sys.stderr.flush()
        import time; time.sleep(30)
        sys.exit(7)
    """)
    proc = subprocess.Popen([sys.executable, "-c", src],
                            stdin=child, stderr=subprocess.PIPE)
    child.close()
    try:
        assert proc.stderr.readline() == b"armed\n"
        parent.close()                       # the app dying
        assert proc.wait(timeout=5) == 0, "did not exit on EOF from a SOCKET"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        parent.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_a_reset_connection_is_treated_as_death(monkeypatch):
    """ConnectionResetError must exit; other OSErrors must not.

    Neither branch was executed by any test — deleting the
    `except ConnectionResetError` clause left the file 10/10 green.
    """
    calls = {"exit": []}
    monkeypatch.setattr(bw.os, "_exit", lambda code: calls["exit"].append(code))

    monkeypatch.setattr(bw.os, "read", lambda *a: (_ for _ in ()).throw(
        ConnectionResetError(104, "reset")))
    bw._watch_parent_pipe()
    assert calls["exit"] == [0], "a reset socket did not count as the app dying"

    calls["exit"].clear()
    monkeypatch.setattr(bw.os, "read", lambda *a: (_ for _ in ()).throw(
        OSError(9, "EBADF")))
    bw._watch_parent_pipe()
    assert calls["exit"] == [], "an ambiguous OSError killed a healthy backend"
