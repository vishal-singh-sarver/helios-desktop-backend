"""
Entry point for the packaged HeliosGUI backend executable.

PyInstaller bundles this script as the standalone executable.
The Electron backend-manager spawns it with: --port=<port>

It may also hand us a pipe on stdin as a liveness signal — see _start_parent_watchdog
(_watch_parent_pipe on POSIX, _watch_parent_handle on Windows).
"""

import argparse
import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn


def _say(message: str) -> None:
    """Log a line that MUST NOT be able to prevent what follows it.

    By the time the watchdog has something to report, our stdout is a socket to
    a process that just died — so print() raises, and in a thread that kills
    the thread silently. That is not hypothetical: os._exit(0) sat directly
    after a print() here and never ran once, because the print went first. The
    orphan survived, the reader thread vanished, and there was no output to say
    so. Logging is best-effort; exiting is not.
    """
    try:
        print(message, flush=True)
    except Exception:                     # noqa: BLE001 — a dead stdout is normal here
        pass


def _watch_parent_pipe() -> None:
    """Exit when the app that started us closes its end of our stdin.

    A crashed Electron app never reaches its `will-quit` handler, so the
    cleanup that would stop this process never runs — and a backend holding a
    ~1.4 GB scenario context stays resident indefinitely. One was found on a
    tester's machine from an earlier session still holding 1.33 GB, with
    nothing to notice it. A dead parent cannot clean up after itself, so the
    child has to notice and leave.

    The app spawns us with stdin as a PIPE and never writes to it. It is not a
    channel, it is a liveness signal: the app holds the write end open for as
    long as it lives, and the OS closes it the moment the app dies — for ANY
    reason, including an abort that runs no cleanup at all. The read below
    blocks until then, returns empty, and we leave.

    Chosen over getppid() polling because it is ONE mechanism for all three
    platforms and needs no native code. getppid() is POSIX-only (Windows does
    not report re-parenting), it polls, and under PyInstaller --onefile the
    bootloader sits between us and the app so the pid never matches. A pipe has
    none of those problems: nothing polled, no pid recorded, no exposure to pid
    reuse, and process topology is irrelevant.

    An OSError is NOT treated as death. EOF is the signal; anything else is
    ambiguous, and the safe reading of an ambiguous signal is to stay alive.

    os._exit skips uvicorn's shutdown deliberately. There is nothing safe to
    save here: the DB is the source of truth for the object set, `_hydrate`
    rebuilds anything absent from the snapshot, and writing a context of
    unknown freshness from a watchdog thread risks exactly the corruption
    /discard?save=false exists to prevent.
    """
    while True:
        try:
            if not os.read(0, 1):
                break                     # write end closed — the app is gone
        except ConnectionResetError:
            break                         # peer reset the socket — also gone
        except OSError as exc:
            # Ambiguous (EBADF and friends): not evidence the app died, and the
            # safe reading of an ambiguous signal is to stay alive.
            _say(f"[backend] liveness pipe unreadable ({exc}) — not watching")
            return
    _say("[backend] parent closed the liveness pipe — exiting")
    os._exit(0)


def _watch_parent_handle(pid: int) -> None:
    """Exit when the parent process object becomes signalled. Windows only.

    Same contract as _watch_parent_pipe: block until the app is gone, then
    leave. Different mechanism, because on Windows the pipe read DEADLOCKS.

    THE DEADLOCK, measured, not theorised. _watch_parent_pipe blocks in
    os.read(0, 1) on fd 0. main() armed it immediately before uvicorn.run(),
    which is what imports app.main and therefore loads libhelios.dll. A thread
    parked in a blocking CRT read on fd 0 and a main thread loading that DLL
    deadlock: the backend printed its optix line and then nothing, forever, and
    the app died on the 30s readiness timeout with the process still alive.

    Three runs, one variable, packaged build spawned from node:
        stdio[0]='ignore'          -> gate never arms   -> ready in 1.58s
        stdio[0]='pipe', fed bytes -> read never blocks -> ready in 1.57s
        stdio[0]='pipe', idle      -> read BLOCKS       -> hung past 40s
    Feeding the pipe is what proves it: the pipe is not the problem, being
    BLOCKED IN THE READ during the DLL load is.

    So on Windows we wait on the parent's process object instead. It never
    touches fd 0, so it cannot contend with the loader or the CRT's stdio
    locks, and WaitForSingleObject is exactly the primitive for "tell me when
    that process ends" — no polling, no timer.

    Pid reuse, the reason the module docstring rejected pid-based approaches,
    does not apply: OpenProcess happens ONCE, here, at startup while the app is
    demonstrably alive. The HANDLE pins that specific process object, so a
    later pid reuse cannot make us wait on a stranger. What was unsafe was
    RE-RESOLVING a recorded pid later, which this never does.

    Failure to open the handle is NOT treated as death — same rule as the
    OSError branch in _watch_parent_pipe. An ambiguous signal means stay alive.
    """
    import ctypes

    SYNCHRONIZE = 0x00100000
    INFINITE = 0xFFFFFFFF
    WAIT_OBJECT_0 = 0x00000000

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)

    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        _say(
            f"[backend] cannot open parent pid {pid} "
            f"(error {ctypes.get_last_error()}) — not watching"
        )
        return

    try:
        # Releases the GIL for the whole wait, so this costs one idle thread.
        if kernel32.WaitForSingleObject(ctypes.c_void_p(handle), INFINITE) != WAIT_OBJECT_0:
            _say("[backend] parent wait ended abnormally — not exiting")
            return
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))

    _say("[backend] parent process ended — exiting")
    os._exit(0)


def _start_parent_watchdog() -> None:
    """Arm the watchdog ONLY when stdin is really a pipe.

    THIS GATE IS NOT OPTIONAL. Spawning with stdio 'ignore' gives us /dev/null,
    where a read returns EOF immediately — an ungated reader would exit the
    backend instantly, on every startup, on every machine. The app only pipes
    stdin on the branch that added it; every other branch, a manual run, and CI
    all still hand us /dev/null or a terminal. Gating on the fd itself means
    the two sides can ship in either order, which asking for an env var would
    not have achieved.

    BOTH S_ISFIFO AND S_ISSOCK, and the socket case is the one that matters:
    Node's `stdio: 'pipe'` is a socketpair() on POSIX, so fd 0 is a SOCKET, not
    a FIFO. Checking S_ISFIFO alone looks right, passes a unit test built on
    os.pipe(), and never arms under the spawn the app actually uses — verified
    against a real node spawn, which reports S_ISSOCK=True, S_ISFIFO=False.
    Windows named pipes do report S_IFIFO (CPython maps FILE_TYPE_PIPE to
    _S_IFIFO), so both checks earn their place.

    Everything else is excluded: /dev/null and a terminal are character
    devices, and a redirected file is regular. None of them arm the reader.
    """
    try:
        mode = os.fstat(0).st_mode
        if not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
            return
    except OSError:
        # No usable stdin at all (closed fd, detached service). Nothing to watch.
        return

    # The gate above stays the authority on WHETHER to watch: a piped stdin is
    # still what says "a parent is supervising us". Windows only changes HOW we
    # wait, because reading fd 0 there deadlocks the DLL load — see
    # _watch_parent_handle. Keep the gate first so a manual run or CI, which
    # gets a character device, arms nothing on any platform.
    target, args = _watch_parent_pipe, ()
    if sys.platform == "win32":
        raw_pid = os.environ.get("HELIOS_PARENT_PID")
        try:
            parent_pid = int(raw_pid)
        except (TypeError, ValueError):
            # Piped by something that did not identify itself. Reading fd 0 here
            # is the one thing we must not do, so watch nothing and stay alive
            # rather than deadlock the whole backend at startup.
            _say(f"[backend] no usable HELIOS_PARENT_PID ({raw_pid!r}) — not watching")
            return
        target, args = _watch_parent_handle, (parent_pid,)

    threading.Thread(
        target=target, args=args, name="parent-watchdog", daemon=True,
    ).start()


def _live_cmdline(pid: int) -> str | None:
    """The command line of a running process, or None if it is not running.

    /proc on Linux, `ps` elsewhere. No psutil — it is not a dependency of the
    packaged build and this must not add one.
    """
    if sys.platform == "win32":
        # No /proc, and no `ps`. Reading another process's command line here
        # would need WMI; the app does that side with taskkill instead.
        return None
    try:
        if sys.platform.startswith("linux"):
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                raw = fh.read().decode("utf-8", "replace").rstrip("\x00")
            # An EMPTY cmdline means the entry exists but the process is gone —
            # a zombie awaiting its parent's wait(). Treat that as dead, or a
            # reaped process reads as still running and we wait 2s then SIGKILL
            # a pid that has already exited.
            return raw or None
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _own_cmdline() -> str:
    """Our command line AS THE OS REPORTS IT, not as sys.argv sees it.

    These differ: sys.argv is ["backend_wrapper.py", "--port=8008"] while
    /proc gives "python backend_wrapper.py --port=8008". Recording one and
    comparing against the other means the match never succeeds and the reap
    silently never fires — which is exactly what happened, and only an
    end-to-end run caught it. Both sides must use the same representation.
    """
    return _live_cmdline(os.getpid()) or "\x00".join(sys.argv)


def _reap_previous_backend(pid_file: Path) -> None:
    """Kill a backend left over from a previous run, then record ourselves.

    The watchdog above stops NEW orphans. It cannot help with one that already
    exists — created before the watchdog shipped, or left by a power cut, or by
    a hang the watchdog thread could not act on. A tester's machine had one from
    an earlier session still holding 1.33 GB, and nothing had noticed it: the
    app walks past a busy 8008 and quietly takes 8009, so an orphan produces no
    visible symptom at all.

    PID REUSE IS THE HAZARD HERE. A recorded pid may since have been handed to
    something entirely unrelated, and killing that would be far worse than the
    leak. So the recorded command line must match the live one exactly — a
    reused pid will not match, and anything that does match IS another instance
    of this backend, which is precisely what should go.

    THE FILE IS A CONTRACT WITH THE APP, which reaps on Windows where we cannot
    (it has taskkill /T /F; we have neither /proc nor `ps`). Layout:

        {"pid": 7413, "cmdline": "...", "platform": "linux"}

    `cmdline` is NOT portable and `platform` says how to read it: on linux it
    comes from /proc/<pid>/cmdline and is NUL-separated; on darwin from
    `ps -o command=` and is space-separated; on win32 it falls back to
    sys.argv, NUL-joined. Consumers must branch on `platform` rather than
    sniffing the separator.

    Never raises: a failure to reap must not stop the backend booting.
    """
    try:
        # The REAP is posix-only — the pid-reuse guard needs a live command
        # line to compare against, and Windows gives us no way to read one.
        # The RECORD is written EVERYWHERE, including Windows: the app is the
        # reaper there and this file is how it knows what may be killed.
        # These were one branch, and Windows wrote no file at all as a result.
        if sys.platform != "win32" and pid_file.exists():
            try:
                record = json.loads(pid_file.read_text())
                old_pid = int(record["pid"])
                old_cmd = record["cmdline"]
            except (ValueError, KeyError, OSError):
                old_pid, old_cmd = None, None

            if old_pid and old_pid != os.getpid():
                live = _live_cmdline(old_pid)
                if live is not None and live == old_cmd:
                    print(f"[backend] reaping orphan pid={old_pid}", flush=True)
                    try:
                        os.kill(old_pid, signal.SIGTERM)
                        for _ in range(20):          # up to 2s to go quietly
                            time.sleep(0.1)
                            if _live_cmdline(old_pid) is None:
                                break
                        else:
                            os.kill(old_pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass                          # already gone, or not ours to kill

        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(json.dumps({
            "pid": os.getpid(),
            "cmdline": _own_cmdline(),
            "platform": sys.platform,
        }))
    except Exception as exc:            # noqa: BLE001 — never block startup
        print(f"[backend] reap skipped: {exc}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="HeliosGUI Backend Server")
    parser.add_argument("--port", type=int, default=8008, help="Port to listen on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind to")
    args = parser.parse_args()

    from app.core.config import settings
    _reap_previous_backend(Path(settings.data_dir) / "backend.pid")
    _start_parent_watchdog()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
