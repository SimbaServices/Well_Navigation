"""Confirm search HTML carries well status into the map tooltip dataset."""

from __future__ import annotations

import sqlite3
import urllib.parse
import urllib.request

from wellnav.db import connect
from wellnav.repository import WellRepository
from wellnav.well_status import status_label


def _pick(conn: sqlite3.Connection, symbol: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT api8, well_name, symbol, wellhead_lat, wellhead_lon
        FROM wells_tx
        WHERE symbol = ?
          AND wellhead_lat IS NOT NULL
          AND wellhead_lon IS NOT NULL
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()


def main() -> None:
    conn = connect()
    repo = WellRepository(conn)
    samples = [
        ("Plugged Oil Well", "PA · Plugged Oil Well"),
        ("Oil Well", "Producing · Oil Well"),
        ("Injection / Disposal from Oil", "Injection/disposal · Injection / Disposal from Oil"),
        ("Shut-In Oil", "Shut-in · Shut-In Oil"),
    ]
    for symbol, expected in samples:
        computed = status_label(symbol)
        if computed != expected:
            raise SystemExit(f"FAIL label {symbol!r} -> {computed!r} != {expected!r}")
        print("OK label", expected)

        row = _pick(conn, symbol)
        if not row:
            print("SKIP html", symbol, "(no sample with coords)")
            continue
        found = repo.search(api=row["api8"], include_permits=False)
        if not found["wells"]:
            raise SystemExit(f"FAIL search miss {row['api8']}")
        well = found["wells"][0]
        if well["status_label"] != expected:
            raise SystemExit(
                f"FAIL repo {row['api8']} {well['status_label']!r} != {expected!r}"
            )
        print("OK repo", row["api8"], well["status_label"])

        qs = urllib.parse.urlencode({"api": row["api8"], "use_api": "1"})
        html = urllib.request.urlopen(f"http://127.0.0.1:5050/search?{qs}").read().decode()
        needle = f'data-status="{expected}"'
        if needle not in html:
            raise SystemExit(f"FAIL html missing {needle} for {row['api8']}")
        print("OK html", row["api8"], expected)

    js = urllib.request.urlopen("http://127.0.0.1:5050/static/js/map.js?v=status1").read().decode()
    if "well.status" not in js or "Wellhead" not in js:
        raise SystemExit("FAIL map.js missing status popup")
    print("OK map.js popup")
    print("ALL_OK")


if __name__ == "__main__":
    main()
