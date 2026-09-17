"""Partition Texas counties across subprocesses and save into SQLite.

Workers pull from a queue (one subprocess per in-flight partition). GIS
requests are load-balanced through a shared limiter. A blocked request
does not fail the job: that partition is re-queued after the next waiting
county so work continues and the blocked pull is re-initiated later.
"""

from __future__ import annotations

import multiprocessing as mp
import shutil
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from wellnav.db import ROOT, connect, get_meta, init_schema, session, set_cursor
from wellnav.ingest.classify import utcnow
from wellnav.ingest.persist import update_identities, upsert_permits, upsert_wells
from wellnav.ingest.worker import run_partition
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS, TX_COUNTIES

DEFAULT_MAX_RETRIES = 8
SCRATCH_ROOT = ROOT / "data" / "ingest_scratch"


def _partitions(counties: list[str] | None) -> list[tuple[str, str]]:
    wanted = {c.zfill(3) for c in counties} if counties else None
    rows = [(code, name) for code, name in TX_COUNTIES if wanted is None or code in wanted]
    if wanted and not rows:
        raise ValueError(f"No Texas county partitions matched {sorted(wanted)}")
    return rows


def _log(message: str) -> None:
    print(message, flush=True)


def _init_worker(lock, last, delay: float) -> None:
    from wellnav.ingest.limiter import init_limiter

    init_limiter(lock, last, delay)


def _take_ready(queue: deque) -> dict | None:
    n = len(queue)
    for _ in range(n):
        item = queue.popleft()
        if float(item.get("not_before") or 0) <= time.monotonic():
            return item
        queue.append(item)
    return None


def _soonest_wait(queue: deque) -> float:
    now = time.monotonic()
    waits = [float(item.get("not_before") or 0) - now for item in queue]
    return max(0.0, min(waits)) if waits else 0.0


def _requeue_after_next(queue: deque, payload: dict) -> None:
    """Run the next queued partition first, then re-initiate the blocked one."""
    if not queue:
        queue.append(payload)
        return
    next_item = queue.popleft()
    queue.appendleft(payload)
    queue.appendleft(next_item)


def load_texas(
    *,
    workers: int = 6,
    counties: list[str] | None = None,
    permit_only: bool = False,
    identity_only: bool = False,
    delay: float = 0.15,
    state: str = "tx",
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict:
    parts = _partitions(counties)
    if identity_only:
        kind = "identity_load"
    elif permit_only:
        kind = "permit_refresh"
    else:
        kind = "full_load"
    now = utcnow()
    conn = connect()
    init_schema(conn)
    lifetime = int(get_meta(conn, "permit_lifetime_days", str(DEFAULT_PERMIT_LIFETIME_DAYS)))
    cur = conn.execute(
        """
        INSERT INTO sync_jobs(kind, state, status, workers, started_at)
        VALUES (?, ?, 'running', ?, ?)
        """,
        (kind, state, workers, now),
    )
    job_id = cur.lastrowid
    for code, name in parts:
        conn.execute(
            """
            INSERT INTO sync_partitions(job_id, partition_key, partition_name, status)
            VALUES (?, ?, ?, 'queued')
            """,
            (job_id, code, name),
        )
    conn.commit()
    conn.close()

    scratch_dir = SCRATCH_ROOT / str(job_id)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    queue: deque[dict] = deque(
        {
            "county_code": code,
            "county_name": name,
            "lifetime_days": lifetime,
            "delay": delay,
            "permit_only": permit_only,
            "identity_only": identity_only,
            "job_id": job_id,
            "attempt": 1,
            "not_before": 0.0,
            "scratch_path": str(scratch_dir / f"{code}.json"),
        }
        for code, name in parts
    )

    totals = {
        "wells": 0,
        "permits": 0,
        "failed": 0,
        "blocked_retries": 0,
        "partitions": len(parts),
    }
    ctx = mp.get_context("spawn")
    lock = ctx.Lock()
    last = ctx.Value("d", 0.0)
    worker_count = max(1, workers)
    inflight: dict = {}

    _log(
        f"load-texas job {job_id}: {len(parts)} county partitions, "
        f"{worker_count} subprocesses, {delay:.2f}s spacing, max_retries={max_retries}"
        + (", identity-only" if identity_only else "")
        + (", permit-only" if permit_only else "")
    )

    with ProcessPoolExecutor(
        max_workers=worker_count,
        mp_context=ctx,
        initializer=_init_worker,
        initargs=(lock, last, delay),
    ) as pool:
        while queue or inflight:
            while len(inflight) < worker_count:
                payload = _take_ready(queue)
                if payload is None:
                    break
                _mark_status(job_id, payload["county_code"], "running")
                future = pool.submit(run_partition, payload)
                inflight[future] = payload
                _log(
                    f"  start {payload['county_code']} {payload['county_name']} "
                    f"(attempt {payload['attempt']}, inflight {len(inflight)}, queued {len(queue)})"
                )

            if not inflight:
                wait_for = _soonest_wait(queue)
                if wait_for > 0:
                    _log(f"  waiting {wait_for:.1f}s for blocked partitions to become ready")
                    time.sleep(min(wait_for, 5.0))
                continue

            done, _ = wait(inflight.keys(), return_when=FIRST_COMPLETED)
            for future in done:
                payload = inflight.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "county_code": payload["county_code"],
                        "county_name": payload["county_name"],
                        "ok": False,
                        "blocked": False,
                        "error": str(exc),
                    }
                _handle_result(
                    job_id,
                    state,
                    payload,
                    result,
                    queue,
                    totals,
                    max_retries=max_retries,
                )

    finished = utcnow()
    with session() as conn:
        status = "failed" if totals["failed"] else "ok"
        conn.execute(
            """
            UPDATE sync_jobs SET status = ?, finished_at = ?, message = ?
            WHERE id = ?
            """,
            (
                status,
                finished,
                (
                    f"{totals['wells']} wells, {totals['permits']} permits, "
                    f"{totals['failed']} failed, {totals['blocked_retries']} blocked retries"
                ),
                job_id,
            ),
        )
        if identity_only:
            set_cursor(conn, "tx_identity_refreshed_at", finished, finished)
        elif permit_only:
            set_cursor(conn, "tx_permits_refreshed_at", finished, finished)
        else:
            set_cursor(conn, "tx_full_load_finished_at", finished, finished)
    shutil.rmtree(scratch_dir, ignore_errors=True)
    totals["job_id"] = job_id
    totals["status"] = "failed" if totals["failed"] else "ok"
    _log(f"load-texas job {job_id} finished: {totals}")
    return totals


def _handle_result(
    job_id: int,
    state: str,
    payload: dict,
    result: dict,
    queue: deque,
    totals: dict,
    *,
    max_retries: int,
) -> None:
    code = result.get("county_code") or payload["county_code"]
    name = result.get("county_name") or payload["county_name"]
    if result.get("ok"):
        _commit_partition(job_id, state, result, totals)
        _log(
            f"  ok {code} {name}: {result.get('default_features', 0)} wells-layer, "
            f"{result.get('surface_features', 0)} surface-layer, "
            f"{result.get('identities', 0)} identities"
        )
        return

    if result.get("blocked"):
        attempt = int(payload.get("attempt") or 1)
        if attempt < max_retries:
            totals["blocked_retries"] += 1
            retry_after = float(result.get("retry_after") or min(30, 3 * attempt))
            payload["attempt"] = attempt + 1
            payload["not_before"] = time.monotonic() + retry_after
            _mark_status(job_id, code, "blocked", error=result.get("error"))
            _requeue_after_next(queue, payload)
            _log(
                f"  blocked {code} {name}: {result.get('error')}; "
                f"re-queue after next partition (retry {payload['attempt']} in {retry_after:.1f}s)"
            )
            return
        totals["failed"] += 1
        _mark_status(job_id, code, "failed", error=result.get("error"), finished=True)
        _log(f"  failed {code} {name} after {attempt} blocked attempts: {result.get('error')}")
        return

    totals["failed"] += 1
    _mark_status(job_id, code, "failed", error=result.get("error"), finished=True)
    _log(f"  failed {code} {name}: {result.get('error')}")


def _mark_status(job_id: int, county_code: str, status: str, error: str | None = None, finished: bool = False) -> None:
    now = utcnow()
    with session() as conn:
        if status == "running":
            conn.execute(
                """
                UPDATE sync_partitions
                SET status = 'running', started_at = COALESCE(started_at, ?), error = NULL
                WHERE job_id = ? AND partition_key = ?
                """,
                (now, job_id, county_code),
            )
            return
        if finished:
            conn.execute(
                """
                UPDATE sync_partitions
                SET status = ?, finished_at = ?, error = ?
                WHERE job_id = ? AND partition_key = ?
                """,
                (status, now, error, job_id, county_code),
            )
            return
        conn.execute(
            """
            UPDATE sync_partitions
            SET status = ?, error = ?
            WHERE job_id = ? AND partition_key = ?
            """,
            (status, error, job_id, county_code),
        )


def _commit_partition(job_id: int, state: str, result: dict, totals: dict) -> None:
    now = utcnow()
    with session() as conn:
        if result.get("identity_only"):
            wells = update_identities(conn, state, result.get("identity_map") or {})
            permits = 0
        else:
            wells = upsert_wells(conn, state, result.get("wells") or [])
            permits = upsert_permits(conn, state, result.get("permits") or [])
        totals["wells"] += wells
        totals["permits"] += permits
        conn.execute(
            """
            UPDATE sync_partitions
            SET status = 'ok', wells = ?, permits = ?, started_at = COALESCE(started_at, ?),
                finished_at = ?, error = NULL
            WHERE job_id = ? AND partition_key = ?
            """,
            (wells, permits, now, now, job_id, result["county_code"]),
        )


def job_status(limit: int = 8) -> list[dict]:
    conn = connect()
    init_schema(conn)
    jobs = conn.execute(
        """
        SELECT id, kind, state, status, workers, started_at, finished_at, message
        FROM sync_jobs ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for job in jobs:
        parts = conn.execute(
            """
            SELECT status, COUNT(*) AS n FROM sync_partitions
            WHERE job_id = ? GROUP BY status
            """,
            (job["id"],),
        ).fetchall()
        item = dict(job)
        item["partitions"] = {row["status"]: row["n"] for row in parts}
        out.append(item)
    conn.close()
    return out
