"""A new backend kills an orphan left by the previous run — and nothing else.

The parent watchdog stops NEW orphans; it cannot help with one that already
exists (created before it shipped, or left by a power cut). A tester's machine
had one from an earlier session still holding 1.33 GB with nothing to notice
it — the app walks past a busy 8008 and quietly takes 8009, so an orphan is
invisible.

The hazard is PID REUSE: a recorded pid may since belong to something else
entirely, and killing that would be far worse than the leak. So the recorded
command line must match the live one exactly.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import backend_wrapper as bw


def _sleeper():
    """A stand-in process we are allowed to kill."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _alive(pid):
    return bw._live_cmdline(pid) is not None


def test_reaps_an_orphan_whose_cmdline_matches(tmp_path):
    victim = _sleeper()
    time.sleep(0.5)
    assert _alive(victim.pid)

    pid_file = tmp_path / "backend.pid"
    pid_file.write_text(json.dumps(
        {"pid": victim.pid, "cmdline": bw._live_cmdline(victim.pid)}))

    bw._reap_previous_backend(pid_file)

    for _ in range(30):
        if not _alive(victim.pid):
            break
        time.sleep(0.1)
    assert not _alive(victim.pid), "the orphan was not reaped"
    victim.wait(timeout=5)


def test_does_NOT_kill_a_reused_pid(tmp_path):
    """The dangerous case: same pid, different process. Must be left alone."""
    bystander = _sleeper()
    time.sleep(0.5)

    pid_file = tmp_path / "backend.pid"
    pid_file.write_text(json.dumps(
        {"pid": bystander.pid,
         "cmdline": "heliosgui_backend --port=8008"}))   # NOT what is running

    bw._reap_previous_backend(pid_file)

    time.sleep(0.5)
    assert _alive(bystander.pid), "killed an unrelated process on a reused pid"
    bystander.kill(); bystander.wait(timeout=5)


def test_records_itself_for_the_next_boot(tmp_path):
    pid_file = tmp_path / "backend.pid"
    bw._reap_previous_backend(pid_file)

    record = json.loads(pid_file.read_text())
    assert record["pid"] == os.getpid()
    # Compared against the READER, not against _own_cmdline() — that would be
    # the writer checked against itself, which passes no matter what it writes.
    # The bug this guards shipped once: _own_cmdline returned sys.argv while
    # _live_cmdline read /proc, so the reap's comparison never matched and it
    # silently never fired. A self-comparison could not have caught it, and the
    # mutation (_own_cmdline -> "\x00".join(sys.argv)) still passes 8/8 without
    # this line.
    assert record["cmdline"] == bw._live_cmdline(os.getpid()), \
        "what we RECORD and what we READ BACK are not the same representation"


def test_never_kills_itself(tmp_path):
    """A stale file naming our own pid must be ignored, not acted on."""
    pid_file = tmp_path / "backend.pid"
    pid_file.write_text(json.dumps(
        {"pid": os.getpid(), "cmdline": bw._own_cmdline()}))
    bw._reap_previous_backend(pid_file)          # must return, not die
    assert json.loads(pid_file.read_text())["pid"] == os.getpid()


def test_tolerates_a_missing_or_corrupt_file(tmp_path):
    """Never block startup."""
    missing = tmp_path / "nope" / "backend.pid"
    bw._reap_previous_backend(missing)
    assert missing.exists()

    corrupt = tmp_path / "backend.pid"
    corrupt.write_text("{not json at all")
    bw._reap_previous_backend(corrupt)
    assert json.loads(corrupt.read_text())["pid"] == os.getpid()


def test_a_dead_recorded_pid_is_harmless(tmp_path):
    victim = _sleeper()
    pid = victim.pid
    victim.kill(); victim.wait(timeout=5)
    time.sleep(0.3)

    pid_file = tmp_path / "backend.pid"
    pid_file.write_text(json.dumps({"pid": pid, "cmdline": "whatever"}))
    bw._reap_previous_backend(pid_file)          # must not raise
    assert json.loads(pid_file.read_text())["pid"] == os.getpid()


@pytest.mark.skipif(sys.platform == "win32", reason="posix only")
def test_windows_records_itself_but_does_not_reap(tmp_path, monkeypatch):
    """Windows must still WRITE the file, even though it cannot reap.

    The app is the reaper there — it has taskkill /T /F, we have neither /proc
    nor `ps` — and this file is the only thing telling it what may be killed.
    Both used to sit behind one `if win32: return`, so Windows wrote nothing at
    all and the app had nothing to read. That is the one platform where the
    liveness pipe is our only other protection.
    """
    killed = []
    # Kept BEFORE patching. bw.os is the one shared `os` module, so patching
    # os.kill also neuters subprocess.Popen.kill() — the cleanup below would
    # silently do nothing and leave a 60s sleeper behind on every run.
    real_kill = bw.os.kill
    monkeypatch.setattr(bw.os, "kill", lambda *a: killed.append(a))

    # The identity check is made to MATCH, so the only thing that can stop the
    # kill is the platform guard itself.
    #
    # Two earlier versions of this test passed for the wrong reason. Seeding
    # cmdline="whatever" meant no live process could ever match, so the pid-reuse
    # guard blocked the kill on every platform. Recording a real process's real
    # cmdline was no better: under the win32 patch _live_cmdline takes the `ps`
    # branch (space-separated) while the record came from /proc (NUL-separated),
    # so the REPRESENTATION mismatch blocked it instead. Both left the win32
    # branches deletable with the file still 8/8 green.
    recorded = "heliosgui_backend\x00--port=8008"
    monkeypatch.setattr(bw, "_live_cmdline", lambda pid: recorded)

    victim = _sleeper()
    time.sleep(0.4)
    pid_file = tmp_path / "backend.pid"
    pid_file.write_text(json.dumps(
        {"pid": victim.pid, "cmdline": recorded, "platform": "win32"}))

    monkeypatch.setattr(bw.sys, "platform", "win32")
    try:
        bw._reap_previous_backend(pid_file)
        assert killed == [], \
            "reaped on Windows, where there is no way to verify the cmdline"
        assert victim.poll() is None, "killed a process it could not have verified"
    finally:
        real_kill(victim.pid, signal.SIGKILL)
        victim.wait(timeout=5)
    record = json.loads(pid_file.read_text())
    assert record["pid"] == os.getpid(), "did not record itself on Windows"
    assert record["platform"] == "win32"


def test_record_says_how_to_read_its_own_cmdline(tmp_path):
    """`cmdline` is platform-specific — NUL-separated from /proc on Linux,
    space-separated from `ps` on macOS. The app parses this file on Windows
    where it cannot ask us, so the format has to be self-describing rather
    than something the reader sniffs."""
    pid_file = tmp_path / "backend.pid"
    bw._reap_previous_backend(pid_file)

    record = json.loads(pid_file.read_text())
    assert set(record) == {"pid", "cmdline", "platform"}, \
        f"pid file layout changed — the app parses this: {sorted(record)}"
    assert record["platform"] == sys.platform
