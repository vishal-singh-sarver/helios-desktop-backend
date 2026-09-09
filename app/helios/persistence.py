"""
Context persistence — scenario-level save + load.

Storage layout (per scenario):
  data/projects/{project_id}/scenarios/{scenario_id}/
      context_file/
          context.xml             ← PyHelios state for this scenario
          archives/
              autosave_<ts>.xml.gz  ← rotated history, capped at MAX_AUTOSAVE_ARCHIVES
      weather/
          *.csv                   ← uploaded weather CSVs persist here
      metadata/                   ← reserved for future use
      export_files/               ← reserved for future use

  SQLite project_versions table (defined in migrations/001_initial.sql):
      scene_xml BLOB              ← lzma-compressed XML (archived versions)
      registry_json TEXT

Compression tiers:
  gzip  (stdlib) — autosave archives.  Fast, ~70% reduction.
  lzma  (stdlib) — versioned snapshots in SQLite. Slower, ~85-90% reduction.

Phase 1 transitional rule:
    The project's in-memory PyHelios scene (ProjectContext) is NOT persisted
    to disk. Only scenario contexts (ScenarioContext) are persisted — they
    capture weather state today, and will absorb the scene state once
    Phase 2 collapses ProjectContext into ScenarioContext.
"""
import gzip
import json
import logging
import lzma
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from app.core.config import settings


logger = logging.getLogger(__name__)

# How many rotated context.xml snapshots to keep per scenario. ONE: the
# previous save is a rollback point, older ones were never read by anything and
# a scene of this size makes them expensive — a 613 MB context.xml gzips to
# tens of MB, ten deep, per scenario, across 1,300+ scenarios.
#
# The rotation prunes to below this number BEFORE writing the new archive, so a
# value of N leaves exactly N on disk.
MAX_AUTOSAVE_ARCHIVES = 1


# ── Path helpers ─────────────────────────────────────────────────────────────


def _ensure_scenario_structure(project_id: str, scenario_id: str) -> Path:
    """Create the canonical per-scenario folder shape. Idempotent.

    After this call the following subfolders are guaranteed to exist:
        context_file/
        context_file/archives/
        weather/
        metadata/
        export_files/

    Returns the scenario's root folder.
    """
    base = settings.scenario_dir(project_id, scenario_id)
    base.mkdir(parents=True, exist_ok=True)
    (base / "context_file").mkdir(exist_ok=True)
    (base / "context_file" / "archives").mkdir(exist_ok=True)
    (base / "weather").mkdir(exist_ok=True)
    (base / "metadata").mkdir(exist_ok=True)
    (base / "export_files").mkdir(exist_ok=True)
    return base


def _scenario_context_xml(project_id: str, scenario_id: str) -> Path:
    return settings.scenario_context_file_dir(project_id, scenario_id) / "context.xml"


def _scenario_archives_dir(project_id: str, scenario_id: str) -> Path:
    return settings.scenario_context_file_dir(project_id, scenario_id) / "archives"


# ── Autosave ──────────────────────────────────────────────────────────────────


def _rotate_scenario_current(project_id: str, scenario_id: str) -> None:
    """
    Compress existing context.xml → archives/autosave_<TIMESTAMP>.xml.gz
    Delete oldest archive if over MAX_AUTOSAVE_ARCHIVES.
    """
    current_xml = _scenario_context_xml(project_id, scenario_id)
    if not current_xml.exists():
        return

    archives_dir = _scenario_archives_dir(project_id, scenario_id)
    archives_dir.mkdir(parents=True, exist_ok=True)

    # Enforce cap (sort oldest → newest by mtime)
    existing = sorted(archives_dir.glob("autosave_*.xml.gz"), key=lambda p: p.stat().st_mtime)
    while len(existing) >= MAX_AUTOSAVE_ARCHIVES:
        existing[0].unlink(missing_ok=True)
        existing = existing[1:]

    # Compress current.xml into the timestamped archive
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archive_path = archives_dir / f"autosave_{ts}.xml.gz"

    # STREAMED, a megabyte at a time. This was read_bytes() + gzip.compress(),
    # which held the entire previous snapshot in RAM *and* its compressed copy —
    # ~400 MB of transient allocation on a 200 MB scene, measured. It runs
    # inside /discard, which is exactly when the next project starts loading, so
    # it was a large part of the peak that aborts the process on Linux.
    # Byte-for-byte identical output; only the memory profile changes.
    with current_xml.open("rb") as src, \
            gzip.open(archive_path, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    current_xml.unlink(missing_ok=True)


_STALE_TEMP_SECONDS = 60 * 60      # an hour; a 1000x1000 writeXML takes ~16s


def _sweep_stale_temps(context_dir: Path) -> None:
    """Delete half-written context.xml temps left by a killed backend.

    The temp used to be a NamedTemporaryFile in /tmp, which the OS cleared on
    reboot. It now lives BESIDE context.xml so os.replace cannot fail EXDEV —
    which also means nothing ever clears it. A SIGKILL during writeXML (the
    app's reaper, the OOM killer, a power cut) strands the partial file in the
    project folder for good, and `_project_disk_stats` sums everything under
    that tree, so a 240 MB corpse also inflates the size shown in the UI.

    AGE-BASED, not "delete every temp found". Two saves for one scenario can
    overlap — a queued autosave and a synchronous discard save both take
    .read(), and readers run concurrently — so a young temp may be one that
    another thread is writing right now. An hour is far beyond any real write.

    Best-effort: a scenario that cannot be tidied must still be saveable.
    """
    cutoff = time.time() - _STALE_TEMP_SECONDS
    try:
        for stale in context_dir.glob("context.xml.tmp-*"):
            try:
                if stale.stat().st_mtime < cutoff:
                    stale.unlink(missing_ok=True)
                    logger.info("[scenario-autosave] removed stale temp %s", stale.name)
            except OSError:
                continue
    except OSError:
        pass


def trigger_scenario_autosave(sctx) -> None:
    """
    Persist a scenario's PyHelios context to disk.

    Path:
        data/projects/<pid>/scenarios/<sid>/context_file/context.xml

    Rotates the previous context.xml into archives/ as a gzipped backup.
    No-op when PyHelios isn't available or the context lacks writeXML.
    """
    if not sctx.context or not hasattr(sctx.context, "writeXML"):
        return

    # GUARDED, because this is now the first write to the USER'S data directory.
    # The temp used to be a NamedTemporaryFile in /tmp — a different filesystem,
    # essentially never full or read-only — so every data-dir failure surfaced
    # later, inside one of the logging handlers below. Moving it beside the
    # target (required, so os.replace cannot fail EXDEV) put an unguarded
    # OSError on the path: on EACCES, EROFS, EDQUOT or a full disk it escaped
    # into the save worker, where concurrent.futures stores the exception on a
    # Future nobody reads. The save died silently — nothing on stderr, nothing
    # in backend.log — and wait_for_scenario_saves() still reported success.
    try:
        _ensure_scenario_structure(sctx.project_id, sctx.scenario_id)
        final_path = _scenario_context_xml(sctx.project_id, sctx.scenario_id)
        _sweep_stale_temps(final_path.parent)
        # suffix=".xml" is load-bearing: PyHelios validates the output extension.
        fd, tmp_name = tempfile.mkstemp(
            dir=final_path.parent, prefix="context.xml.tmp-", suffix=".xml")
        os.close(fd)
        tmp_path = Path(tmp_name)
    except OSError:
        logger.exception(
            "[scenario-autosave] cannot open a temp file for scenario %s — "
            "is the data directory writable?", sctx.scenario_id)
        return
    _started = time.monotonic()

    # CAPTURED BEFORE the write, not after. writeXML takes seconds on a large
    # scene, and a mutation landing inside that window is not in the bytes we
    # are about to lay down. Recording the sequence we actually serialised
    # leaves such a scene dirty, so the next save — or /discard — still writes.
    _writing_seq = sctx.mutation_seq

    try:
        sctx.context.writeXML(str(tmp_path))
    except Exception:
        logger.exception("[scenario-autosave] writeXML failed for scenario %s", sctx.scenario_id)
        tmp_path.unlink(missing_ok=True)
        return

    try:
        _rotate_scenario_current(sctx.project_id, sctx.scenario_id)
        # MOVED, not copied through RAM. This was read_bytes() + write_bytes(),
        # which held the whole scene in memory for no reason — the engine had
        # already written the file. 200 MB -> 0 MB, measured.
        #
        # os.replace is also atomic, so a failure now leaves the previous
        # context.xml intact instead of a truncated one.
        os.replace(tmp_path, final_path)
        # Only now is the file on disk. Set AFTER os.replace, never before: a
        # failure between here and there must leave the scene dirty.
        sctx.saved_seq = _writing_seq
        # Size comes from stat(), not len(raw_xml): there is no raw_xml any
        # more, and reading the file back just to measure it would reintroduce
        # exactly the buffering this removed.
        logger.info("[save]    written   scenario=%s %.1f MB in %.1fs",
                    sctx.scenario_id[:8], final_path.stat().st_size / 1048576,
                    time.monotonic() - _started)
    except Exception:
        logger.exception(
            "[scenario-autosave] rotation/write failed for scenario %s",
            sctx.scenario_id,
        )
        tmp_path.unlink(missing_ok=True)


# ── Deferred save ─────────────────────────────────────────────────────────────
#
# ONE worker PER SCENARIO. The single worker is what keeps a scenario's saves
# ordered — a slower earlier write must never land after, and overwrite, a
# newer one — and geometry and weather share a context.xml, so they share a
# queue. But that ordering only has to hold WITHIN a scenario.
#
# It used to be one worker for the whole process, so a 16s writeXML on one
# scene delayed every other scene's save: closing a 700x700 project made the
# next project's first save wait 7.58s. Nothing required that — two Contexts
# write in parallel perfectly well.

_SAVE_POOLS: dict[str, ThreadPoolExecutor] = {}
_POOLS_LOCK = threading.Lock()

# ── Coalescing ───────────────────────────────────────────────────────────────
#
# A save used to be submitted the instant a mutation happened, and it holds
# sctx.lock.read() for the whole write — so the NEXT mutation waited for it.
# Creating a second 1000x1000 ground therefore sat behind the first ground's
# ~215 MB write before it could even start building, and the scene was then
# serialised twice: 215 MB, then 430 MB.
#
# The submit is delayed a moment instead. A mutation arriving inside that window
# cancels the pending save before it ever runs, so a burst of edits costs ONE
# write of the final scene rather than one per edit. What makes this sound is
# that trigger_scenario_autosave serialises the LIVE context when it runs, not a
# snapshot taken at submit time — the surviving save contains every coalesced
# mutation by construction.
#
# A save already RUNNING is never cancelled (Future.cancel() returns False and
# we leave it alone): it may predate the newest mutation, so it has to finish
# and the newer state gets its own save. mutation_seq/saved_seq still decide
# what is dirty, exactly as before.
_DEBOUNCE_SECONDS = 1.5

# scenario_id -> the timer that has not submitted yet (cancelling it is free)
_PENDING_TIMERS: dict[str, threading.Timer] = {}
# scenario_id -> the context that timer will save. Held alongside so a
# whole-process flush can reach it without unpacking the timer's arguments.
_PENDING_SCTX: dict[str, object] = {}
# scenario_id -> the submitted save (cancellable only until the worker picks it up)
_PENDING_FUTURES: dict[str, Future] = {}


def _pool_for(scenario_id: str) -> ThreadPoolExecutor:
    """This scenario's save queue, created on first use.

    Never evicted. A pool is one idle thread (~8 KB of stack); reclaiming them
    would mean proving no save is in flight, which is a race for no gain.
    """
    pool = _SAVE_POOLS.get(scenario_id)
    if pool is not None:
        return pool
    with _POOLS_LOCK:
        # Re-checked inside the lock: two mutations on a new scenario can race
        # here, and the loser would otherwise get a second pool — two workers
        # for one scenario, which is exactly the ordering guarantee we rely on.
        pool = _SAVE_POOLS.get(scenario_id)
        if pool is None:
            pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=f"autosave-{scenario_id[:8]}")
            _SAVE_POOLS[scenario_id] = pool
        return pool


def _submit_save(sctx, timer: threading.Timer | None = None) -> None:
    """Hand this scenario's save to its worker.

    Called by the debounce timer when it fires, and directly by `_flush_pending`
    when a caller cannot wait out the window.

    `timer` is the one whose firing led here, and it is the claim ticket: the
    entry in `_PENDING_TIMERS` is removed by whoever acts first, under the lock.
    A timer that fires while a flush is already holding the lock therefore finds
    its own entry gone and returns, instead of submitting a second save for the
    same state. `None` means the caller already claimed it.
    """
    def _run() -> None:
        # This scenario's own lock, not a process-wide one, and taken INSIDE
        # the queued work rather than around the submit: held at submit time it
        # would be released before the write ran, letting writeXML serialise a
        # context mid-mutation.
        with sctx.lock.read():
            trigger_scenario_autosave(sctx)

    sid = sctx.scenario_id
    # Resolved BEFORE taking _POOLS_LOCK: _pool_for takes that same lock to
    # create a scenario's pool on first use, and threading.Lock is not
    # reentrant — calling it from inside the block deadlocks the first save of
    # every scenario.
    pool = _pool_for(sid)
    with _POOLS_LOCK:
        if timer is not None:
            if _PENDING_TIMERS.get(sid) is not timer:
                # Superseded by a newer mutation, or already claimed by a
                # flush. Either way this save is not ours to make.
                return
            _PENDING_TIMERS.pop(sid, None)
            _PENDING_SCTX.pop(sid, None)
        _PENDING_FUTURES[sid] = pool.submit(_run)
    logger.debug("[save]    queued    scenario=%s", sid[:8])


def queue_scenario_autosave(sctx) -> None:
    """QUEUE a context.xml save — the caller does NOT wait for the write.

    No response is built from context.xml (geometry serializes from the DB +
    session state, weather from the live context), so making a mutation wait on
    writeXML was pure latency — and on a 1000x1000 ground that is a million
    primitives serialized while the user watches a spinner.

    DEBOUNCED (see the Coalescing note above): the submit is delayed briefly and
    a mutation arriving inside that window replaces the pending save, so a burst
    of edits costs one write of the final scene instead of one write each. The
    delay is invisible to callers — nothing reads context.xml, and every path
    that needs the file on disk goes through `wait_for_scenario_saves`, which
    flushes first.

    The lock is taken INSIDE the queued work, not around the submit: held at
    submit time it would be released before the write ran, letting writeXML
    serialize a context mid-mutation.

    Best-effort — `trigger_scenario_autosave` no-ops when headless and
    swallows/logs any writeXML failure, so a queued save never surfaces.
    """
    # Every mutation site calls this immediately after mutating, so it is the
    # one place that reliably means "the context no longer matches disk".
    # /discard reads the counters to decide whether it can skip its writeXML.
    # Bumped BEFORE anything is cancelled below: a coalesced-away save must
    # still leave the scenario dirty, or the write would be skipped entirely.
    sctx.mutation_seq += 1

    sid = sctx.scenario_id
    with _POOLS_LOCK:
        timer = _PENDING_TIMERS.pop(sid, None)
        if timer is not None:
            timer.cancel()      # never submitted; nothing was spent on it
        pending = _PENDING_FUTURES.get(sid)
        if pending is not None and pending.cancel():
            # Only reached when the worker had not started it. A RUNNING save
            # returns False here and is deliberately left alone: it may predate
            # this mutation, so it has to finish and this state gets its own.
            _PENDING_FUTURES.pop(sid, None)
        # The timer is passed its own handle so that when it fires it can check
        # whether it is still the pending one (see _submit_save). Registered
        # BEFORE start() so a very short window cannot fire against an empty
        # table and decline its own save.
        holder: list[threading.Timer] = []
        timer = threading.Timer(
            _DEBOUNCE_SECONDS, lambda: _submit_save(sctx, holder[0]))
        holder.append(timer)
        timer.daemon = True     # must never hold the process open at shutdown
        _PENDING_TIMERS[sid] = timer
        _PENDING_SCTX[sid] = sctx
        timer.start()


def _flush_pending(sctx) -> None:
    """Submit this scenario's debounced save NOW instead of waiting out the
    window. No-op when nothing is pending.

    Load-bearing for `wait_for_scenario_saves`: a debounced save has not reached
    the pool yet, so draining the pool without this would return before the
    write happened — and /discard would release the context believing it was on
    disk. Every caller that needs the file present goes through here.
    """
    with _POOLS_LOCK:
        # Claiming the entry is what makes this safe: a timer that fires now
        # finds its own entry gone and declines to submit, so the save happens
        # exactly once whichever of us gets here first.
        timer = _PENDING_TIMERS.pop(sctx.scenario_id, None)
        _PENDING_SCTX.pop(sctx.scenario_id, None)
    if timer is None:
        return              # nothing debounced; anything queued is on the pool
    # Returns None whether or not it was still waiting — the claim above is the
    # thing that decides, not this call.
    timer.cancel()
    _submit_save(sctx)


def wait_for_scenario_saves(sctx=None) -> None:
    """Block until queued saves have been written.

    With `sctx`, waits for THAT scenario only — each pool has one worker, so a
    task submitted now cannot run until the saves ahead of it are done. Without
    it, waits for every scenario, which is what the tests and shutdown want.

    Passing the scenario matters on the paths that block a user: /discard
    waiting on every scenario in the process would reintroduce exactly the
    cross-scenario stall this change removes.

    Flushes the debounce first — a pending save is not on the pool yet, so
    draining alone would report success before it had been written.
    """
    if sctx is not None:
        _flush_pending(sctx)
        _pool_for(sctx.scenario_id).submit(lambda: None).result()
        return
    # Snapshotted: _flush_pending mutates these dicts as it goes.
    for pending in list(_PENDING_SCTX.values()):
        _flush_pending(pending)
    for pool in list(_SAVE_POOLS.values()):
        pool.submit(lambda: None).result()


# ── Load ──────────────────────────────────────────────────────────────────────


def load_scenario_snapshot(sctx) -> bool:
    """
    Restore a scenario's PyHelios context from disk.

    Reads:
        data/projects/<pid>/scenarios/<sid>/context_file/context.xml

    Returns True when the context is trustworthy — loaded, or nothing to load.
    Returns False when loadXML RAISED, because it does not unwind: a failed load
    leaves everything it read so far in the context (3,000,000 primitives
    measured on a 613 MB file). Hydration then rebuilds every DB row on top of
    those orphans, so the scene is held twice and the doubled context is saved
    back — making the next open worse again. Callers must discard the context
    on False rather than build on it.
    """
    new_xml = _scenario_context_xml(sctx.project_id, sctx.scenario_id)
    if not new_xml.exists():
        logger.info("[context] no snapshot scenario=%s — building from the DB",
                    sctx.scenario_id[:8])
        return True

    size_mb = new_xml.stat().st_size / 1048576
    started = time.monotonic()
    try:
        sctx.context.loadXML(str(new_xml))
        logger.info("[context] loaded    scenario=%s %.1f MB in %.1fs",
                    sctx.scenario_id[:8], size_mb, time.monotonic() - started)
        return True
    except Exception as exc:
        # One line, not a traceback. This failure is EXPECTED and handled: a
        # tiled ground fails to reload because texture_repeat is not written to
        # context.xml, so the repeat returns as 1 and the engine's
        # subdiv < repeat x texture_pixels check rejects it. It fires on every
        # open of such a scenario, and a 12-line stack for a known, recovered
        # condition trains the reader to skip the log — which is how a real
        # error gets missed. Anything OTHER than that keeps its traceback.
        detail = str(exc)
        if "resolution of the texture image" in detail:
            logger.warning(
                "[context] load-failed scenario=%s after %.1fs — texture repeat "
                "not persisted (known engine limit); rebuilding from the DB",
                sctx.scenario_id[:8], time.monotonic() - started)
        else:
            logger.exception("[context] load-failed scenario=%s — rebuilding "
                             "from the DB", sctx.scenario_id[:8])
        return False


# ── Versioning (SQLite-based, unchanged) ─────────────────────────────────────


def save_version(project_id: str, label: str, ctx, registry: dict,
                 metadata: dict, db) -> int:
    """
    Compress current XML with lzma and insert a new project_versions row.
    Returns the new version id.
    """
    from app.db.models import ProjectVersion, Project
    from sqlalchemy import func

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        ctx.writeXML(tmp_path)
        raw_xml = Path(tmp_path).read_bytes()
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    compressed = lzma.compress(raw_xml, preset=6)

    last = (
        db.query(func.max(ProjectVersion.version_num))
        .filter(ProjectVersion.project_id == project_id)
        .scalar()
    )
    next_num = (last or 0) + 1

    row = ProjectVersion(
        project_id=project_id,
        version_num=next_num,
        label=label or f"Version {next_num}",
        scene_xml=compressed,
        registry_json=json.dumps({"metadata": metadata, "objects": registry}),
        bytes_original=len(raw_xml),
        bytes_compressed=len(compressed),
    )
    db.add(row)

    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        from datetime import datetime as _dt, timezone as _tz
        project.updated_at = _dt.now(_tz.utc).isoformat()
        project.current_version_id = row.id

    db.commit()
    db.refresh(row)
    return row.id


def restore_version(project_id: str, version_id: int, ctx, db) -> dict:
    """
    Decompress an archived version from SQLite and load it into ctx.
    Returns the registry dict.
    """
    from app.db.models import ProjectVersion

    row = db.query(ProjectVersion).filter(
        ProjectVersion.id == version_id,
        ProjectVersion.project_id == project_id,
    ).first()

    if not row:
        raise ValueError(f"Version {version_id} not found for project {project_id}")

    raw_xml = lzma.decompress(row.scene_xml)

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp.write(raw_xml)
        tmp_path = tmp.name

    try:
        ctx.loadXML(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return json.loads(row.registry_json)


def list_versions(project_id: str, db) -> list:
    """Return all version rows for a project (without the blob)."""
    from app.db.models import ProjectVersion

    rows = (
        db.query(
            ProjectVersion.id,
            ProjectVersion.version_num,
            ProjectVersion.label,
            ProjectVersion.created_at,
            ProjectVersion.bytes_original,
            ProjectVersion.bytes_compressed,
        )
        .filter(ProjectVersion.project_id == project_id)
        .order_by(ProjectVersion.version_num.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "version_num": r.version_num,
            "label": r.label,
            "created_at": r.created_at,
            "bytes_original": r.bytes_original,
            "bytes_compressed": r.bytes_compressed,
        }
        for r in rows
    ]


