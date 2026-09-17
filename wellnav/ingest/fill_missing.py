"""Fill wells that still lack an operator via county wellbore CSV dumps."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from wellnav.db import connect, session, set_cursor
from wellnav.http_client import BlockedRequest
from wellnav.ingest.classify import utcnow
from wellnav.ingest.persist import update_identities
from wellnav.rrc import RrcClient

PREFIX = ""


def _log(message: str) -> None:
    print(f"{PREFIX}{message}", flush=True)


def _set_prefix(prefix: str) -> None:
    global PREFIX
    PREFIX = prefix


def _shard_of(county_code: str, shards: int) -> int:
    # Texas county FIPS values are odd; divide first so shards 0/2 are not empty.
    try:
        return (int(county_code) // 2) % shards
    except ValueError:
        return sum(ord(ch) for ch in county_code) % shards


def _operator_lookup() -> dict[str, str]:
    conn = connect()
    rows = conn.execute("SELECT operator_number, operator_name FROM operators_tx").fetchall()
    conn.close()
    out: dict[str, str] = {}
    for row in rows:
        name = (row["operator_name"] or "").strip().upper()
        if name and name not in out:
            out[name] = row["operator_number"]
    return out


def _run_county(payload: dict) -> dict:
    code = payload["county_code"]
    name = payload.get("county_name") or code
    delay = float(payload.get("delay") or 0.15)
    state = payload.get("state") or "tx"
    lookup = _operator_lookup()
    identities: dict[str, dict] = {}
    try:
        client = RrcClient()
        for schedule in ("Y", "N"):
            if delay:
                time.sleep(delay)
            page = client.download_wellbore_csv(county_code=code, schedule=schedule)
            if page.get("over_limit"):
                for lease_type in ("O", "G"):
                    if delay:
                        time.sleep(delay)
                    split = client.download_wellbore_csv(
                        county_code=code, schedule=schedule, lease_type=lease_type
                    )
                    for well in split.get("wells") or []:
                        _store(identities, well, lookup)
                continue
            for well in page.get("wells") or []:
                _store(identities, well, lookup)
        with session() as conn:
            updated = update_identities(conn, state, identities)
        return {
            "ok": True,
            "blocked": False,
            "county_code": code,
            "county_name": name,
            "wellbores": len(identities),
            "updated": updated,
            "error": None,
        }
    except BlockedRequest as exc:
        return {
            "ok": False,
            "blocked": True,
            "county_code": code,
            "county_name": name,
            "wellbores": 0,
            "updated": 0,
            "error": str(exc),
            "retry_after": exc.retry_after,
        }
    except Exception as exc:
        return {
            "ok": False,
            "blocked": False,
            "county_code": code,
            "county_name": name,
            "wellbores": 0,
            "updated": 0,
            "error": str(exc),
        }


def _identity_score(well: dict) -> tuple[int, int, int, int]:
    return (
        int(bool((well.get("operator") or "").strip())),
        int(bool((well.get("lease_name") or "").strip())),
        int(bool((well.get("lease_no") or "").strip())),
        int(bool((well.get("district") or "").strip())),
    )


def _store(identities: dict[str, dict], well: dict, lookup: dict[str, str]) -> None:
    api8 = (well.get("api") or "").strip()
    if len(api8) != 8:
        return
    operator = (well.get("operator") or "").strip()
    if operator and not well.get("operator_number"):
        well["operator_number"] = lookup.get(operator.upper(), "")
    current = identities.get(api8)
    if current is None or _identity_score(well) > _identity_score(current):
        identities[api8] = well


def missing_counties(state: str = "tx") -> list[dict]:
    conn = connect()
    rows = list(
        conn.execute(
            """
            SELECT county_code, MAX(county) AS county_name, COUNT(*) AS missing
            FROM wells_tx
            WHERE operator IS NULL OR operator = ''
            GROUP BY county_code
            ORDER BY missing DESC
            """
        ).fetchall()
    )
    conn.close()
    return [
        {
            "county_code": (row["county_code"] or "").zfill(3),
            "county_name": row["county_name"] or "",
            "missing": int(row["missing"]),
        }
        for row in rows
        if row["county_code"]
    ]


def fill_missing(
    *,
    workers: int = 4,
    delay: float = 0.15,
    shard: int = 0,
    shards: int = 1,
    state: str = "tx",
) -> dict:
    shards = max(1, int(shards))
    shard = max(0, int(shard)) % shards
    _set_prefix(f"[p{shard}] " if shards > 1 else "")
    rows = [row for row in missing_counties(state) if _shard_of(row["county_code"], shards) == shard]
    queue = deque(
        {
            "county_code": row["county_code"],
            "county_name": row["county_name"],
            "delay": delay,
            "state": state,
            "attempts": 0,
        }
        for row in rows
    )
    totals = {
        "counties": len(queue),
        "updated": 0,
        "failed": 0,
        "wellbores": 0,
        "missing_at_start": sum(row["missing"] for row in rows),
        "shard": shard,
        "shards": shards,
        "workers": max(1, int(workers)),
    }
    _log(
        f"fill-missing shard {shard}/{shards} workers={totals['workers']} "
        f"counties={len(queue)} blanks={totals['missing_at_start']}"
    )
    if totals["workers"] <= 1:
        while queue:
            payload = queue.popleft()
            _log(f"  start {payload['county_code']} {payload['county_name']}")
            result = _run_county(payload)
            _commit(result, totals)
    else:
        inflight: dict = {}
        done = 0
        with ProcessPoolExecutor(
            max_workers=totals["workers"],
            initializer=_set_prefix,
            initargs=(PREFIX,),
        ) as pool:
            while queue or inflight:
                while queue and len(inflight) < totals["workers"]:
                    payload = queue.popleft()
                    _log(
                        f"  start {payload['county_code']} {payload['county_name']} "
                        f"inflight={len(inflight) + 1}/{totals['workers']} queued={len(queue)}"
                    )
                    inflight[pool.submit(_run_county, payload)] = payload
                finished, _ = wait(inflight, return_when=FIRST_COMPLETED)
                for fut in finished:
                    payload = inflight.pop(fut)
                    result = fut.result()
                    if result.get("blocked") and int(payload.get("attempts") or 0) < 3:
                        payload["attempts"] = int(payload.get("attempts") or 0) + 1
                        queue.append(payload)
                        _log(f"  requeue {payload['county_code']} ({payload['attempts']}/3)")
                        continue
                    done += 1
                    _commit(result, totals)
                    if done % 5 == 0 or (not queue and not inflight):
                        _log(
                            f"  progress {done}/{totals['counties']} "
                            f"patched={totals['updated']} wellbores={totals['wellbores']} "
                            f"failed={totals['failed']}"
                        )
    now = utcnow()
    with session() as conn:
        set_cursor(conn, f"tx_fill_missing_shard_{shard}_at", now, now)
    totals["status"] = "failed" if totals["failed"] else "ok"
    _log(f"fill-missing finished {totals}")
    return totals


def _commit(result: dict, totals: dict) -> None:
    if result.get("ok"):
        totals["updated"] += int(result.get("updated") or 0)
        totals["wellbores"] += int(result.get("wellbores") or 0)
        _log(
            f"  ok {result['county_code']} {result.get('county_name')}: "
            f"{result.get('wellbores')} wellbores, {result.get('updated')} rows patched"
        )
        return
    totals["failed"] += 1
    _log(f"  failed {result['county_code']}: {result.get('error')}")


def load_fill_missing(
    *,
    workers: int = 4,
    delay: float = 0.15,
    shard: int | None = None,
    shards: int = 1,
) -> dict:
    if shard is None and shards > 1:
        return spawn_shards(shards=shards, workers=workers, delay=delay)
    return fill_missing(workers=workers, delay=delay, shard=shard or 0, shards=max(1, shards))


def spawn_shards(*, shards: int, workers: int, delay: float) -> dict:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    procs = []
    _log(f"spawning {shards} fill-missing shards x {workers} workers")
    for shard in range(shards):
        procs.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "wellnav.ingest",
                    "fill-missing",
                    "--shard",
                    str(shard),
                    "--shards",
                    str(shards),
                    "--workers",
                    str(workers),
                    "--delay",
                    str(delay),
                ],
                env=env,
            )
        )
    codes = [proc.wait() for proc in procs]
    return {"status": "ok" if not any(codes) else "failed", "exit_codes": codes}
