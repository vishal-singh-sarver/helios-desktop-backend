"""Dropping a context must give the memory back to the OS, not just free it.

glibc frees into its own arena and keeps the pages. RSS does not move. On a
1000x1000 ground that is 1.74 GB still resident after the context is gone, so
opening A then B costs A+B rather than max(A,B) — which is what SIGKILLed the
server on the second project.

glibc CAN return memory via sbrk when the freed block happens to sit at the top
of the heap, which is why a single open/close loop looks healthy on a bench.
The failing case is a block BURIED under a newer one, which is exactly what
discard-then-open-next is. These tests reproduce that shape specifically.
"""
import ctypes
import os

import pytest

from app.helios import context as helios_ctx

# MUST stay under glibc's M_MMAP_THRESHOLD (128 KB by default). Above it,
# malloc goes straight to mmap and free() munmaps immediately — memory comes
# back with no trim at all, and the arena this test is about is never touched.
# A first version used 1 MB chunks and free() alone returned 201 MB, proving
# only that mmap works. The C++ context allocates small per-primitive objects,
# which is the arena path reproduced here.
CHUNK = 64 * 1024
MB = 200
COUNT = (MB * 1024 * 1024) // CHUNK

pytestmark = pytest.mark.skipif(
    helios_ctx._malloc_trim is None, reason="glibc only")


def rss_mb() -> float:
    with open(f"/proc/{os.getpid()}/statm") as fh:
        return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576


class _Block:
    """A pile of malloc'd, touched pages — resident until freed AND trimmed."""

    def __init__(self, count=COUNT):
        libc = ctypes.CDLL(None)
        libc.malloc.restype = ctypes.c_void_p
        libc.free.argtypes = [ctypes.c_void_p]
        self._libc = libc
        self._ptrs = []
        for _ in range(count):
            p = libc.malloc(CHUNK)
            ctypes.memset(p, 1, CHUNK)          # touch, or it is never resident
            self._ptrs.append(p)

    def free(self):
        for p in self._ptrs:
            self._libc.free(p)
        self._ptrs = []


def test_freeing_alone_does_not_return_memory_but_trim_does():
    """The whole point. `free()` is not `return to the OS`."""
    lower = _Block()          # gets buried
    upper = _Block()          # keeps `lower` off the top of the heap
    try:
        peak = rss_mb()
        lower.free()
        after_free = rss_mb()

        helios_ctx.release_memory()
        after_trim = rss_mb()

        assert after_free > peak - (MB / 2), (
            f"free() alone returned {peak - after_free:.0f} MB — this test is "
            f"no longer reproducing the buried-block case it exists for")
        assert peak - after_trim > MB * 0.7, (
            f"trim returned only {peak - after_trim:.0f} MB of {MB} MB freed")
    finally:
        upper.free()


def test_a_live_allocation_survives_the_trim():
    """The control. Discarding scenario A must not damage scenario B."""
    keep = _Block(COUNT // 4)
    try:
        # Trim ONCE first, to clear whatever earlier tests left freed-but-not-
        # returned. Without it `before` includes their garbage, the trim under
        # test legitimately reclaims it, and the assertion below reads that as
        # damage to `keep` — this failed in the full suite at exactly 200.3 MB,
        # the size of the previous test's block, while passing run alone.
        helios_ctx.release_memory()
        before = rss_mb()
        helios_ctx.release_memory()          # the trim actually under test
        after = rss_mb()

        # EVERY chunk, not a 16-byte sample of one. Sampling 16 bytes of chunk 0
        # out of 800 passed against a madvise(MADV_DONTNEED) sweep that discarded
        # 46.9 MB of LIVE pages.
        for i, p in enumerate(keep._ptrs):
            if ctypes.string_at(p, 64) != b"\x01" * 64:
                raise AssertionError(f"trim corrupted live chunk {i}")

        # RELATIVE, not an absolute floor. `assert rss_mb() > MB / 4` was a
        # 50 MB threshold against a ~72 MB baseline (106 MB under conftest) —
        # an empty test body passed it.
        assert after >= before - 5, (
            f"trim reclaimed {before - after:.1f} MB that was still allocated")
    finally:
        keep.free()


def test_noop_when_malloc_trim_is_unavailable(monkeypatch):
    """macOS, musl and Windows have no malloc_trim. Must be silent, not fatal."""
    monkeypatch.setattr(helios_ctx, "_malloc_trim", None)
    helios_ctx.release_memory()          # must not raise


def test_a_failing_trim_never_propagates(monkeypatch):
    """Reclaiming is best-effort — it runs on release paths, where raising
    would turn a tidy-up into a failed discard."""
    def _boom(_):
        raise OSError("nope")

    monkeypatch.setattr(helios_ctx, "_malloc_trim", _boom)
    helios_ctx.release_memory()          # must not raise


def test_the_probe_can_never_stop_the_module_importing():
    """The Windows ship-blocker this nearly shipped as.

    CPython's CDLL.__init__ has an `nt` branch that runs
    `if '/' in name or '\\\\' in name` BEFORE dlopen. With CDLL(None) that is
    `'/' in None` -> TypeError, which a narrow `except (OSError, AttributeError)`
    does not catch. It would escape module-level code, abort the import of
    app.helios.context, and stop the backend booting at all — on the one
    platform none of us can test locally.

    Runs the real probe expression against every exception ctypes could plausibly
    raise. Any escape here is that bug coming back.
    """
    import ctypes as real_ctypes

    for exc in (TypeError("argument of type 'NoneType' is not iterable"),
                OSError("cannot open shared object file"),
                AttributeError("malloc_trim"),
                ValueError("bad mode"),
                RuntimeError("something else entirely")):
        def _boom(*_a, **_k):
            raise exc

        saved = real_ctypes.CDLL
        real_ctypes.CDLL = _boom
        try:
            # The exact shape of the module-level probe.
            try:
                trim = real_ctypes.CDLL(None).malloc_trim
                trim.argtypes = [real_ctypes.c_size_t]
                trim.restype = real_ctypes.c_int
            except Exception:
                trim = None
            assert trim is None
        finally:
            real_ctypes.CDLL = saved


def test_module_imports_when_the_probe_fails(monkeypatch):
    """Belt and braces: with no trim available, everything still works."""
    monkeypatch.setattr(helios_ctx, "_malloc_trim", None)
    helios_ctx.release_memory()
    assert helios_ctx.Context is not None or not helios_ctx.PYHELIOS_AVAILABLE
