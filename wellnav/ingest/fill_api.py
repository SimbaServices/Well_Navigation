"""Per-API EWA wellbore lookup for wells still missing operator or lease."""

from __future__ import annotations

import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from wellnav.db import connect, session, set_cursor
from wellnav.http_client import BlockedRequest
from wellnav.ingest.classify import utcnow
from wellnav.ingest.persist import update_identities
from wellnav.rrc import RrcClient

PREFIX = ""
BATCH = 40


def _log(message: str) -> None:
    print(f"{PREFIX}{message}", flush=True)


def _set_prefix(prefix: str) -> None:
    global PREFIX
    PREFIX = prefix


def _shard_of(api8: str, shards: int) -> int:
    try:
        return (int(api8) // 2) % shards
    except ValueError:
        return sum(ord(ch) for ch in api8) % shards


def _pick_well(page: dict) -> dict | None:
    best = None
    best_score = (-1, -1)
    for well in page.get("wells") or []:
        score = (
            int(bool((well.get("operator") or "").strip())),
            int(bool((well.get("lease_name") or "").strip())),
        )
        if score > best_score:
            best = well
            best_score = score
    if best_score == (0, 0):
        return None
    return best


def _run_batch(payload: dict) -> dict:
    apis: list[str] = payload["apis"]
    delay = float(payload.get("delay") or 0.1)
    state = payload.get("state") or "tx"
    identities: dict[str, dict] = {}
    tried = 0
    try:
        client = RrcClient()
        for api8 in apis:
            tried += 1
            well = None
            if delay:
                time.sleep(delay)
            page = client.search_wellbores(api=api8, page_size=10, schedule="")
            well = _pick_well(page)
            if well:
                well["api"] = api8
                identities[api8] = well
        updated = 0
        if identities:
            with session() as conn:
                updated = update_identities(conn, state, identities)
        return {
            "ok": True,
            "blocked": False,
            "tried": tried,
            "found": len(identities),
            "updated": updated,
            "error": None,
        }
    except BlockedRequest as exc:
        return {
            "ok": False,
            "blocked": True,
            "tried": tried,
            "found": 0,
            "updated": 0,
            "error": str(exc),
            "retry_after": exc.retry_after,
        }
    except Exception as exc:
        return {
            "ok": False,
            "blocked": False,
            "tried": tried,
            "found": 0,
            "updated": 0,
            "error": str(exc),
        }


def _blank_apis(state: str = "tx") -> list[str]:
    conn = connect()
    rows = conn.execute(
        """
        SELECT api8 FROM wells_tx
        WHERE operator IS NULL OR operator = ''
           OR lease_name IS NULL OR lease_name = ''
        ORDER BY api8
        """
    ).fetchall()
    conn.close()
    return [row["api8"] for row in rows if row["api8"]]


def fill_api(
    *,
    workers: int = 6,
    delay: float = 0.1,
    shard: int = 0,
    shards: int = 1,
    state: str = "tx",
) -> dict:
    shards = max(1, int(shards))
    shard = max(0, int(shard)) % shards
    _set_prefix(f"[p{shard}] " if shards > 1 else "")
    apis = [api for api in _blank_apis(state) if _shard_of(api, shards) == shard]
    batches = [apis[i : i + BATCH] for i in range(0, len(apis), BATCH)]
    queue = deque({"apis": batch, "delay": delay, "state": state, "attempts": 0} for batch in batches)
    totals = {
        "apis": len(apis),
        "tried": 0,
        "found": 0,
        "updated": 0,
        "failed": 0,
        "shard": shard,
        "shards": shards,
        "workers": max(1, int(workers)),
    }
    _log(f"fill-api shard {shard}/{shards} workers={totals['workers']} blanks={len(apis)} batches={len(queue)}")
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
                inflight[pool.submit(_run_batch, payload)] = payload
            finished, _ = wait(inflight, return_when=FIRST_COMPLETED)
            for fut in finished:
                payload = inflight.pop(fut)
                result = fut.result()
                if result.get("blocked") and int(payload.get("attempts") or 0) < 3:
                    payload["attempts"] = int(payload.get("attempts") or 0) + 1
                    queue.append(payload)
                    _log(f"  requeue batch ({payload['attempts']}/3) {result.get('error')}")
                    continue
                done += 1
                if result.get("ok"):
                    totals["tried"] += int(result.get("tried") or 0)
                    totals["found"] += int(result.get("found") or 0)
                    totals["updated"] += int(result.get("updated") or 0)
                else:
                    totals["failed"] += 1
                    _log(f"  failed batch: {result.get('error')}")
                if done % 10 == 0 or (not queue and not inflight):
                    _log(
                        f"  progress {done}/{len(batches)} tried={totals['tried']} "
                        f"found={totals['found']} patched={totals['updated']} failed={totals['failed']}"
                    )
    now = utcnow()
    with session() as conn:
        set_cursor(conn, f"tx_fill_api_shard_{shard}_at", now, now)
    totals["status"] = "failed" if totals["failed"] else "ok"
    _log(f"fill-api finished {totals}")
    return totals
