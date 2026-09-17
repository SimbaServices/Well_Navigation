"""Load P-5 oil/gas operators, then attach wellbore identity by operator number."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from wellnav.db import connect, init_schema, session, set_cursor
from wellnav.operators import normalize_operator_name
from wellnav.http_client import BlockedRequest
from wellnav.ingest.classify import utcnow
from wellnav.ingest.persist import update_identities
from wellnav.rrc import RESULT_CAP, RrcClient

ORG_PAGE = 100
WELL_PAGE = 100
SPECIALTIES = (("OPRS/O", "oil"), ("OPRS/G", "gas"))
PREFIX = ""


def _log(message: str) -> None:
    print(f"{PREFIX}{message}", flush=True)


def _set_prefix(prefix: str) -> None:
    global PREFIX
    PREFIX = prefix


def _shard_of(operator_number: str, shards: int) -> int:
    try:
        return int(operator_number) % shards
    except ValueError:
        return sum(ord(ch) for ch in operator_number) % shards


def fetch_operators(*, delay: float = 0.15, client: RrcClient | None = None, refresh: bool = False, state: str = "tx") -> dict:
    """Page oil and gas organization lists and upsert operators_{state}."""
    if (state or "tx").lower() == "ok":
        from wellnav.ingest.ok_operators import fetch_ok_operators

        return fetch_ok_operators(refresh=refresh)
    if (state or "tx").lower() == "la":
        from wellnav.ingest.la_operators import fetch_la_operators

        return fetch_la_operators(refresh=refresh)
    if (state or "tx").lower() == "nm":
        from wellnav.ingest.nm_wells import load_nm_operators

        return load_nm_operators()
    with session() as conn:
        init_schema(conn)
        existing = conn.execute("SELECT COUNT(*) FROM operators_tx").fetchone()[0]
    if existing and not refresh:
        _log(f"operators already stored {existing}, skipping fetch")
        return {"operators": existing, "status": "ok", "skipped": True}
    client = client or RrcClient()
    now = utcnow()
    seen: dict[str, dict] = {}
    for code, kind in SPECIALTIES:
        offset = 0
        total = None
        while True:
            if delay:
                time.sleep(delay)
            page = client.search_organizations(specialty=code, page_size=ORG_PAGE, offset=offset)
            rows = page.get("operators") or []
            total = int(page.get("total") or total or 0)
            for row in rows:
                number = (row.get("operator_number") or "").strip()
                if not number:
                    continue
                item = seen.setdefault(
                    number,
                    {
                        "operator_number": number,
                        "operator_name": normalize_operator_name(row.get("operator_name") or ""),
                        "org_status": row.get("org_status") or "",
                        "org_type": row.get("org_type") or "",
                        "oil": 0,
                        "gas": 0,
                    },
                )
                item[kind] = 1
                if row.get("operator_name"):
                    item["operator_name"] = normalize_operator_name(row["operator_name"])
            _log(f"  {kind} operators page offset={offset} got={len(rows)} total={total}")
            end = int(page.get("end") or 0)
            if not rows or (total and end >= total) or (not page.get("pager") and len(rows) < ORG_PAGE):
                break
            offset = end or (offset + len(rows))
    with session() as conn:
        init_schema(conn)
        for item in seen.values():
            conn.execute(
                """
                INSERT INTO operators_tx(
                    operator_number, operator_name, oil, gas, org_status, org_type,
                    status, wells, error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, NULL, ?)
                ON CONFLICT(operator_number) DO UPDATE SET
                    operator_name=COALESCE(NULLIF(excluded.operator_name,''), operator_name),
                    oil=MAX(operators_tx.oil, excluded.oil),
                    gas=MAX(operators_tx.gas, excluded.gas),
                    org_status=COALESCE(NULLIF(excluded.org_status,''), org_status),
                    org_type=COALESCE(NULLIF(excluded.org_type,''), org_type),
                    updated_at=excluded.updated_at
                """,
                (
                    item["operator_number"],
                    item["operator_name"],
                    item["oil"],
                    item["gas"],
                    item["org_status"],
                    item["org_type"],
                    now,
                ),
            )
        set_cursor(conn, "tx_operators_loaded_at", now, now)
    _log(f"operators stored {len(seen)}")
    return {"operators": len(seen), "status": "ok"}


def page_operator_wellbores(
    client: RrcClient,
    operator_number: str,
    operator_name: str = "",
    *,
    delay: float = 0.15,
) -> dict[str, dict]:
    identities: dict[str, dict] = {}
    queue = [{"schedule": "Y", "lease_type": ""}, {"schedule": "N", "lease_type": ""}]
    done: set[tuple[str, str]] = set()
    while queue:
        spec = queue.pop(0)
        key = (spec["schedule"], spec["lease_type"])
        if key in done:
            continue
        added, over = _page_operator_split(
            client, operator_number, operator_name, spec, identities, delay=delay
        )
        done.add(key)
        if over and not spec["lease_type"]:
            queue = [
                {"schedule": spec["schedule"], "lease_type": "O"},
                {"schedule": spec["schedule"], "lease_type": "G"},
            ] + queue
            continue
        if over:
            _log(f"  over-limit leftover {operator_number} {key} added={added}")
    return identities


def _page_operator_split(
    client: RrcClient,
    operator_number: str,
    operator_name: str,
    spec: dict,
    identities: dict[str, dict],
    *,
    delay: float,
) -> tuple[int, bool]:
    added = 0
    offset = 0
    while True:
        if delay:
            time.sleep(delay)
        page = client.search_wellbores(
            operator_numbers=[operator_number],
            operator_names=f"{operator_number} - {operator_name}" if operator_name else "",
            schedule=spec["schedule"],
            lease_type=spec["lease_type"],
            page_size=WELL_PAGE,
            offset=offset,
        )
        wells = page.get("wells") or []
        for well in wells:
            api8 = (well.get("api") or "").strip()
            if len(api8) == 8:
                if not well.get("operator_number"):
                    well["operator_number"] = operator_number
                if not well.get("operator"):
                    well["operator"] = operator_name
                identities[api8] = well
                added += 1
        if offset and offset % 1000 == 0:
            _log(f"    {operator_number} {spec['schedule'] or '-'}{spec['lease_type'] or ''} offset={offset} so_far={len(identities)}")
        if page.get("over_limit") or int(page.get("total") or 0) >= RESULT_CAP:
            return added, True
        if page.get("no_results"):
            return added, False
        total = int(page.get("total") or 0)
        end = int(page.get("end") or 0)
        if not wells or (total and end >= total) or (page.get("pager") and end and total and end >= total):
            return added, False
        if page.get("pager") and end:
            if end <= offset:
                return added, False
            offset = end
            continue
        if len(wells) < WELL_PAGE:
            return added, False
        offset += len(wells)


def _run_one(payload: dict) -> dict:
    number = payload["operator_number"]
    name = payload.get("operator_name") or ""
    delay = float(payload.get("delay") or 0.15)
    state = payload.get("state") or "tx"
    try:
        identities = page_operator_wellbores(RrcClient(), number, name, delay=delay)
        with session() as conn:
            updated = update_identities(conn, state, identities)
        return {
            "ok": True,
            "blocked": False,
            "operator_number": number,
            "identities": {},
            "wells": len(identities),
            "updated": updated,
            "error": None,
        }
    except BlockedRequest as exc:
        return {
            "ok": False,
            "blocked": True,
            "operator_number": number,
            "identities": {},
            "wells": 0,
            "updated": 0,
            "error": str(exc),
            "retry_after": exc.retry_after,
        }
    except Exception as exc:
        return {
            "ok": False,
            "blocked": False,
            "operator_number": number,
            "identities": {},
            "wells": 0,
            "updated": 0,
            "error": str(exc),
        }


def enrich_operators(
    *,
    workers: int = 4,
    delay: float = 0.15,
    limit: int | None = None,
    state: str = "tx",
    shard: int = 0,
    shards: int = 1,
) -> dict:
    """Page wellbores per operator and UPDATE existing GIS wells/permits by API."""
    shards = max(1, int(shards))
    shard = max(0, int(shard)) % shards
    _set_prefix(f"[p{shard}] " if shards > 1 else "")
    conn = connect()
    init_schema(conn)
    rows = list(
        conn.execute(
            """
            SELECT operator_number, operator_name FROM operators_tx
            WHERE status != 'ok'
            ORDER BY operator_number
            """
        ).fetchall()
    )
    conn.close()
    rows = [row for row in rows if _shard_of(row["operator_number"], shards) == shard]
    if limit:
        rows = rows[: int(limit)]
    queue = deque(
        {
            "operator_number": row["operator_number"],
            "operator_name": row["operator_name"] or "",
            "delay": delay,
            "state": state,
            "attempts": 0,
        }
        for row in rows
    )
    totals = {
        "operators": len(queue),
        "updated": 0,
        "failed": 0,
        "identities": 0,
        "shard": shard,
        "shards": shards,
        "workers": max(1, int(workers)),
    }
    _log(
        f"enrich-operators shard {shard}/{shards} workers={totals['workers']} "
        f"remaining={len(queue)}"
    )
    if totals["workers"] <= 1:
        _run_sequential(queue, state, totals)
    else:
        _run_pool(queue, state, totals)
    now = utcnow()
    with session() as conn:
        set_cursor(conn, f"tx_operators_enriched_shard_{shard}_at", now, now)
        remaining = conn.execute(
            "SELECT COUNT(*) FROM operators_tx WHERE status != 'ok'"
        ).fetchone()[0]
        if remaining == 0:
            set_cursor(conn, "tx_operators_enriched_at", now, now)
    totals["status"] = "failed" if totals["failed"] else "ok"
    _log(f"enrich-operators finished {totals}")
    return totals


def _run_sequential(queue: deque, state: str, totals: dict) -> None:
    done = 0
    while queue:
        payload = queue.popleft()
        done += 1
        _log(
            f"  start {payload['operator_number']} {payload['operator_name'][:40]} "
            f"({done}/{totals['operators']})"
        )
        result = _finish_one(payload)
        _commit_operator(state, result, totals)
        if done % 25 == 0 or not queue:
            _log(
                f"  progress {done}/{totals['operators']} patched_rows={totals['updated']} "
                f"wellbores={totals['identities']} failed={totals['failed']}"
            )


def _run_pool(queue: deque, state: str, totals: dict) -> None:
    workers = totals["workers"]
    inflight: dict = {}
    done = 0
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_set_prefix,
        initargs=(PREFIX,),
    ) as pool:
        while queue or inflight:
            while queue and len(inflight) < workers:
                payload = queue.popleft()
                _log(
                    f"  start {payload['operator_number']} {payload['operator_name'][:40]} "
                    f"inflight={len(inflight) + 1}/{workers} queued={len(queue)}"
                )
                fut = pool.submit(_run_one, payload)
                inflight[fut] = payload
            finished, _ = wait(inflight, return_when=FIRST_COMPLETED)
            for fut in finished:
                payload = inflight.pop(fut)
                result = fut.result()
                if result.get("blocked") and int(payload.get("attempts") or 0) < 3:
                    payload["attempts"] = int(payload.get("attempts") or 0) + 1
                    wait_s = float(result.get("retry_after") or 5)
                    _log(
                        f"  requeue {payload['operator_number']} in {wait_s:.1f}s "
                        f"({payload['attempts']}/3)"
                    )
                    queue.append(payload)
                    continue
                done += 1
                _commit_operator(state, result, totals)
                if done % 10 == 0 or (not queue and not inflight):
                    _log(
                        f"  progress {done}/{totals['operators']} "
                        f"patched_rows={totals['updated']} wellbores={totals['identities']} "
                        f"failed={totals['failed']} inflight={len(inflight)} queued={len(queue)}"
                    )


def _finish_one(payload: dict) -> dict:
    result = _run_one(payload)
    attempts = int(payload.get("attempts") or 0)
    while result.get("blocked") and attempts < 3:
        wait_s = float(result.get("retry_after") or 5)
        attempts += 1
        _log(f"  retry {payload['operator_number']} in {wait_s:.1f}s ({attempts}/3)")
        time.sleep(wait_s)
        result = _run_one(payload)
    return result


def spawn_shards(*, shards: int, workers: int, delay: float, limit: int | None = None) -> dict:
    """Launch one OS process per shard. Each shard load-balances its own workers."""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    procs = []
    _log(f"spawning {shards} shard processes x {workers} workers")
    for shard in range(shards):
        cmd = [
            sys.executable,
            "-m",
            "wellnav.ingest",
            "load-operators",
            "--shard",
            str(shard),
            "--shards",
            str(shards),
            "--workers",
            str(workers),
            "--delay",
            str(delay),
        ]
        if limit:
            cmd += ["--limit", str(limit)]
        procs.append(subprocess.Popen(cmd, env=env))
    codes = [proc.wait() for proc in procs]
    failed = sum(1 for code in codes if code)
    return {
        "status": "failed" if failed else "ok",
        "shards": shards,
        "workers": workers,
        "exit_codes": codes,
    }


def _commit_operator(state: str, result: dict, totals: dict) -> None:
    number = result["operator_number"]
    now = utcnow()
    with session() as conn:
        updated = 0
        if result.get("ok"):
            updated = int(result.get("updated") or 0)
            if result.get("identities"):
                updated = update_identities(conn, state, result.get("identities") or {})
            conn.execute(
                """
                UPDATE operators_tx
                SET status='ok', wells=?, error=NULL, updated_at=?
                WHERE operator_number=?
                """,
                (int(result.get("wells") or 0), now, number),
            )
            totals["updated"] += updated
            totals["identities"] += int(result.get("wells") or 0)
            _log(f"  ok {number}: {result.get('wells') or 0} wellbores, {updated} rows patched")
            return
        totals["failed"] += 1
        conn.execute(
            """
            UPDATE operators_tx
            SET status='failed', error=?, updated_at=?
            WHERE operator_number=?
            """,
            ((result.get("error") or "")[:400], now, number),
        )
        _log(f"  failed {number}: {result.get('error')}")


def load_operators(
    *,
    workers: int = 4,
    delay: float = 0.15,
    limit: int | None = None,
    shard: int | None = None,
    shards: int = 1,
    state: str = "tx",
) -> dict:
    if (state or "tx").lower() == "ok":
        from wellnav.ingest.ok_operators import load_ok_operators

        return load_ok_operators(refresh=True)
    if (state or "tx").lower() == "la":
        from wellnav.ingest.la_operators import load_la_operators

        return load_la_operators(refresh=True)
    if (state or "tx").lower() == "nm":
        from wellnav.ingest.nm_wells import load_nm_operators

        return load_nm_operators()
    loaded = fetch_operators(delay=delay)
    if shard is None and shards > 1:
        return {**loaded, **spawn_shards(shards=shards, workers=workers, delay=delay, limit=limit)}
    enriched = enrich_operators(
        workers=workers,
        delay=delay,
        limit=limit,
        shard=shard or 0,
        shards=max(1, shards),
    )
    return {**loaded, **enriched}
