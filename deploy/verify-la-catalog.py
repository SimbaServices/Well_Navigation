"""Verify local LA wells/permits/operators after catalog sync."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from wellnav.db import DB_PATH
from wellnav.repository import WellRepository

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
print("wells_la", conn.execute("SELECT COUNT(*) FROM wells_la").fetchone()[0])
print("permits_la", conn.execute("SELECT COUNT(*) FROM permits_la").fetchone()[0])
print(
    "permits_by_status",
    dict(conn.execute("SELECT status, COUNT(*) FROM permits_la GROUP BY 1")),
)
print("operators_la", conn.execute("SELECT COUNT(*) FROM operators_la").fetchone()[0])
print("sources", dict(conn.execute("SELECT IFNULL(source,''), COUNT(*) FROM wells_la GROUP BY 1")))
print(
    "bpx well",
    dict(
        conn.execute(
            "SELECT api, well_name, operator, operator_number, symbol FROM wells_la WHERE api='1703127357'"
        ).fetchone()
        or {}
    ),
)
print(
    "bpx op",
    dict(
        conn.execute(
            "SELECT operator_number, operator_name, wells, oil, gas, status FROM operators_la "
            "WHERE operator_name LIKE '%BPX%'"
        ).fetchone()
        or {}
    ),
)
print(
    "permit sample",
    dict(
        conn.execute(
            "SELECT api, permit_no, status, approved_at, expires_at, operator FROM permits_la "
            "WHERE approved_at IS NOT NULL AND approved_at != '' LIMIT 1"
        ).fetchone()
        or {}
    ),
)
conn.close()

repo = WellRepository()
print("search_ops", repo.search_operators("bpx", state="la")[:3])
print("search_bpx_total", repo.search(state="la", operator_names=["BPX OPERATING COMPANY"])["total"])
print("counts", repo.counts("la"))
repo.close()
