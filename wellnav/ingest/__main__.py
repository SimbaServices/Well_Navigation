"""CLI: python -m wellnav.ingest <command>"""

from __future__ import annotations

import argparse
import time

from wellnav.db import DB_PATH, connect, get_cursor, get_meta, init_schema
from wellnav.ingest.migrate import migrate_as_drilled
from wellnav.ingest.runner import job_status, load_texas
from wellnav.repository import WellRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Well Navigation SQLite ingest")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Create per-state SQLite tables")

    load = sub.add_parser("load-texas", help="Partitioned full GIS load into wells_tx / permits_tx")
    load.add_argument("--workers", type=int, default=6)
    load.add_argument("--counties", nargs="*", help="Optional 3-digit county prefixes, e.g. 003 329")
    load.add_argument("--delay", type=float, default=0.15, help="Seconds between GIS requests (shared across workers)")
    load.add_argument("--max-retries", type=int, default=8, help="Re-queue a blocked partition this many times")
    load.add_argument(
        "--identity-only",
        action="store_true",
        help="Skip GIS; re-fetch EWA identity and COALESCE onto existing wells_tx/permits_tx rows",
    )

    refresh = sub.add_parser("refresh-permits", help="Pull newly approved permitted locations")
    refresh.add_argument("--workers", type=int, default=6)
    refresh.add_argument("--counties", nargs="*")
    refresh.add_argument("--delay", type=float, default=0.15)
    refresh.add_argument("--migrate", action="store_true", help="Also request as-drilled and migrate")

    enrich = sub.add_parser("enrich-texas", help="Attach lease/operator names for GIS rows missing them")
    enrich.add_argument("--counties", nargs="+", required=True)
    enrich.add_argument("--limit", type=int, default=50)

    mig = sub.add_parser("migrate", help="Request as-drilled GIS and move ready permits to wells_tx")
    mig.add_argument("--limit", type=int, default=400)

    watch = sub.add_parser("watch", help="Repeat permit refresh + migrate at a fixed interval")
    watch.add_argument("--hours", type=float, default=0, help="Override meta.permit_refresh_hours")
    watch.add_argument("--workers", type=int, default=4)
    watch.add_argument("--once", action="store_true")

    sub.add_parser("status", help="Show ingest jobs and row counts")

    args = parser.parse_args(argv)

    if args.cmd == "init":
        conn = connect()
        init_schema(conn)
        conn.commit()
        conn.close()
        print(f"Initialized {DB_PATH}")
        return 0

    if args.cmd == "load-texas":
        stats = load_texas(
            workers=args.workers,
            counties=args.counties,
            delay=args.delay,
            max_retries=args.max_retries,
            identity_only=args.identity_only,
        )
        print(stats)
        return 0 if stats["status"] == "ok" else 1

    if args.cmd == "refresh-permits":
        stats = load_texas(
            workers=args.workers,
            counties=args.counties,
            delay=args.delay,
            permit_only=True,
        )
        print("refresh", stats)
        if args.migrate:
            print("migrate", migrate_as_drilled())
        return 0 if stats["status"] == "ok" else 1

    if args.cmd == "enrich-texas":
        from wellnav.ingest.enrich import enrich_missing

        for code in args.counties:
            stats = enrich_missing(code.zfill(3), limit=args.limit or 50)
            print(stats)
        return 0

    if args.cmd == "migrate":
        print(migrate_as_drilled(limit=args.limit))
        return 0

    if args.cmd == "watch":
        conn = connect()
        init_schema(conn)
        hours = args.hours or float(get_meta(conn, "permit_refresh_hours", "24") or 24)
        conn.close()
        while True:
            print("refresh", load_texas(workers=args.workers, permit_only=True))
            print("migrate", migrate_as_drilled())
            if args.once:
                break
            print(f"sleeping {hours}h")
            time.sleep(max(hours, 0.01) * 3600)
        return 0

    if args.cmd == "status":
        repo = WellRepository()
        print("db", DB_PATH)
        print("counts", repo.counts("tx"))
        print("cursors", {
            "full": get_cursor(repo.conn, "tx_full_load_finished_at"),
            "permits": get_cursor(repo.conn, "tx_permits_refreshed_at"),
        })
        for job in job_status():
            print("job", dict(job))
        repo.close()
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
