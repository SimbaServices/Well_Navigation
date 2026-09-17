"""CLI: python -m wellnav.ingest <command>"""

from __future__ import annotations

import argparse
import time

from wellnav.db import DB_PATH, connect, get_cursor, get_meta, init_schema
from wellnav.ingest.migrate import migrate_as_drilled
from wellnav.ingest.operators import load_operators
from wellnav.ingest.permits import refresh_permits
from wellnav.ingest.runner import job_status, load_texas
from wellnav.repository import WellRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Well Navigation SQLite ingest")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Create per-state SQLite tables")

    load = sub.add_parser(
        "load-texas",
        help="Partitioned GIS + EWA identity load into wells_tx / permits_tx",
    )
    load.add_argument("--workers", type=int, default=6)
    load.add_argument("--counties", nargs="*", help="Optional 3-digit county prefixes, e.g. 003 329")
    load.add_argument("--delay", type=float, default=0.15, help="Seconds between GIS requests (shared across workers)")
    load.add_argument("--max-retries", type=int, default=8, help="Re-queue a blocked partition this many times")
    load.add_argument(
        "--identity-only",
        action="store_true",
        help="Skip GIS; patch lease/operator/district on existing rows by API",
    )

    refresh = sub.add_parser("refresh-permits", help="Pull newly approved W-1 permits from RRC EWA")
    refresh.add_argument(
        "--state",
        default="tx",
        choices=["tx", "ok", "nm"],
        help="tx = EWA W-1, ok = OCC ITD, nm = OCD Wells_Public APDs",
    )
    refresh.add_argument("--from-date", help="Approved-from MM/DD/YYYY (default: last permit request)")
    refresh.add_argument("--to-date", help="Approved-to MM/DD/YYYY (default: today)")
    refresh.add_argument(
        "--gis",
        action="store_true",
        help="Use county GIS permitted-location pull instead of the EWA W-1 query",
    )
    refresh.add_argument("--workers", type=int, default=6)
    refresh.add_argument("--counties", nargs="*")
    refresh.add_argument("--delay", type=float, default=0.15)
    refresh.add_argument("--migrate", action="store_true", help="Also request as-drilled and migrate")

    ops = sub.add_parser(
        "load-operators",
        help="Fetch oil/gas P-5 operators, then patch GIS wells from wellbore-by-operator",
    )
    ops.add_argument("--workers", type=int, default=4, help="Load-balanced subprocesses per shard")
    ops.add_argument("--shards", type=int, default=4, help="Independent partition processes")
    ops.add_argument("--shard", type=int, default=-1, help="Run only this 0-based shard; -1 launches all")
    ops.add_argument("--delay", type=float, default=0.15)
    ops.add_argument("--limit", type=int, default=0, help="Enrich only the first N queued operators")
    ops.add_argument("--fetch-only", action="store_true", help="Store operator list only")
    ops.add_argument(
        "--state",
        default="tx",
        choices=["tx", "ok", "la", "nm"],
        help="tx = RRC P-5, ok = OCC operator list, la = SONRIS catalog, nm = OCD OGRID catalog",
    )

    ok_load = sub.add_parser(
        "load-oklahoma",
        help="Load wells_ok, permits_ok, and operators_ok from public OCC files and GIS",
    )
    ok_load.add_argument("--delay", type=float, default=0.12)
    ok_load.add_argument("--limit", type=int, default=0, help="Wells to load, 0 = all")
    ok_load.add_argument("--db", help="Override wellnav.db path")

    nm_load = sub.add_parser(
        "load-new-mexico",
        help="Load wells_nm, permits_nm, and operators_nm from official OCD GIS",
    )
    nm_load.add_argument("--delay", type=float, default=0.12)
    nm_load.add_argument("--limit", type=int, default=0, help="Wells to load, 0 = all")
    nm_load.add_argument("--db", help="Override wellnav.db path")
    nm_load.add_argument(
        "--skip-fracfocus",
        action="store_true",
        help="Skip FracFocus NM fill after the official OCD pull",
    )

    enrich = sub.add_parser("enrich-texas", help="Attach lease/operator names for GIS rows missing them")
    enrich.add_argument("--counties", nargs="+", required=True)
    enrich.add_argument("--limit", type=int, default=50)

    fill = sub.add_parser(
        "fill-missing",
        help="County wellbore CSV dumps to patch wells that still lack an operator",
    )
    fill.add_argument("--workers", type=int, default=4)
    fill.add_argument("--shards", type=int, default=4)
    fill.add_argument("--shard", type=int, default=-1)
    fill.add_argument("--delay", type=float, default=0.15)

    fill_api = sub.add_parser(
        "fill-api",
        help="Per-API EWA wellbore lookup for wells still missing operator or lease",
    )
    fill_api.add_argument("--workers", type=int, default=6)
    fill_api.add_argument("--shards", type=int, default=4)
    fill_api.add_argument("--shard", type=int, default=-1)
    fill_api.add_argument("--delay", type=float, default=0.1)

    pipes = sub.add_parser(
        "load-pipelines",
        help="Download RRC county pipeline shapefiles and load a WGS84 overlay database",
    )
    pipes.add_argument("--skip-download", action="store_true", help="Rebuild SQLite from zips already on disk")
    pipes.add_argument("--limit", type=int, default=0, help="Download only the first N county zips")

    disposal = sub.add_parser(
        "load-disposal",
        help="Pull RRC commercial waste disposal sites (Public GIS layer 36) into disposal.db",
    )
    disposal.add_argument("--delay", type=float, default=0.15)

    neighbors = sub.add_parser(
        "load-neighbors",
        help="Load New Mexico, Oklahoma, and Louisiana wells, waste sites, and pipelines",
    )
    neighbors.add_argument("--states", nargs="*", default=["nm", "ok", "la"])
    neighbors.add_argument("--skip-wells", action="store_true")
    neighbors.add_argument("--skip-disposal", action="store_true")
    neighbors.add_argument("--skip-pipelines", action="store_true")
    neighbors.add_argument(
        "--skip-eia",
        action="store_true",
        help="Keep existing EIA/HIFLD rows and only refresh BSEE/BLM sources",
    )
    neighbors.add_argument("--limit", type=int, default=0, help="Wells per state, 0 = all")
    neighbors.add_argument("--delay", type=float, default=0.12)
    neighbors.add_argument("--db", help="Override wellnav.db path")
    neighbors.add_argument("--disposal-db", help="Override disposal.db path")
    neighbors.add_argument("--pipe-db", help="Override pipelines.db path")

    refresh_la = sub.add_parser(
        "refresh-la",
        help="Add current FracFocus Louisiana wells and restore BSEE names",
    )
    refresh_la.add_argument("--db", help="Override wellnav.db path")

    sonris = sub.add_parser(
        "load-sonris",
        help="Load a SONRIS Data Portal CSV export into wells_la and permits_la",
    )
    sonris.add_argument("--file", required=True, help="CSV from Actions > Download")
    sonris.add_argument("--db", help="Override wellnav.db path")

    sync_la = sub.add_parser(
        "sync-la",
        help="Move LA permit-only wells into permits_la and rebuild operators_la",
    )
    sync_la.add_argument("--db", help="Override wellnav.db path")

    mig = sub.add_parser("migrate", help="Request as-drilled GIS and move ready permits to wells_tx")
    mig.add_argument("--limit", type=int, default=400)

    watch = sub.add_parser("watch", help="Repeat permit refresh + migrate weekly")
    watch.add_argument("--hours", type=float, default=0, help="Override meta.permit_refresh_hours (168)")
    watch.add_argument("--workers", type=int, default=4)
    watch.add_argument("--once", action="store_true")
    watch.add_argument(
        "--gis",
        action="store_true",
        help="Use county GIS permitted-location pull instead of the EWA W-1 query",
    )

    canon = sub.add_parser(
        "canonicalize-operators",
        help="Rewrite well/permit operator names to the catalog name for each operator number",
    )
    canon.add_argument("--force", action="store_true", help="Re-run even if already completed")

    sub.add_parser("status", help="Show ingest jobs and row counts")

    args = parser.parse_args(argv)

    if args.cmd == "canonicalize-operators":
        from wellnav.operators import canonicalize_stored_operators

        conn = connect()
        init_schema(conn)
        changed = canonicalize_stored_operators(conn, force=args.force)
        conn.commit()
        conn.close()
        print({"status": "ok", "changed": changed})
        return 0

    if args.cmd == "init":
        conn = connect()
        init_schema(conn)
        conn.commit()
        conn.close()
        print(f"Initialized {DB_PATH}")
        return 0

    if args.cmd == "load-oklahoma":
        from wellnav.ingest.ok_wells import load_ok_wells

        well_conn = connect(args.db)
        init_schema(well_conn)
        stats = load_ok_wells(well_conn, delay=args.delay, limit=args.limit)
        well_conn.close()
        print(stats)
        return 0

    if args.cmd == "load-new-mexico":
        from wellnav.ingest.nm_wells import load_nm_wells

        well_conn = connect(args.db)
        init_schema(well_conn)
        stats = load_nm_wells(
            well_conn,
            delay=args.delay,
            limit=args.limit,
            skip_fracfocus=args.skip_fracfocus,
        )
        well_conn.close()
        print(stats)
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
        if args.gis:
            stats = load_texas(
                workers=args.workers,
                counties=args.counties,
                delay=args.delay,
                permit_only=True,
            )
        else:
            stats = refresh_permits(
                from_date=args.from_date,
                to_date=args.to_date,
                state=getattr(args, "state", "tx"),
            )
        print("refresh", stats)
        if args.migrate:
            print("migrate", migrate_as_drilled())
        return 0 if stats["status"] == "ok" else 1

    if args.cmd == "load-operators":
        from wellnav.ingest.operators import fetch_operators

        shard = None if args.shard < 0 else args.shard
        if args.fetch_only:
            print(fetch_operators(delay=args.delay, state=args.state))
            return 0
        stats = load_operators(
            workers=args.workers,
            delay=args.delay,
            limit=args.limit or None,
            shard=shard,
            shards=max(1, args.shards),
            state=args.state,
        )
        print(stats)
        return 0 if stats.get("status") == "ok" else 1

    if args.cmd == "load-pipelines":
        from wellnav.ingest.pipelines import load_pipelines

        result = load_pipelines(skip_download=args.skip_download, limit=args.limit or None)
        print(result)
        return 0 if result.get("status") == "ok" else 1

    if args.cmd == "load-disposal":
        from wellnav.ingest.disposal import load_disposal

        result = load_disposal(delay=args.delay)
        print(result)
        return 0 if result.get("status") == "ok" else 1

    if args.cmd == "load-neighbors":
        from wellnav.ingest.neighbors import load_neighbors

        result = load_neighbors(
            states=args.states,
            skip_wells=args.skip_wells,
            skip_disposal=args.skip_disposal,
            skip_pipelines=args.skip_pipelines,
            skip_eia=args.skip_eia,
            limit=args.limit,
            delay=args.delay,
            db_path=args.db,
            disposal_path=args.disposal_db,
            pipe_path=args.pipe_db,
        )
        print(result)
        return 0 if result.get("status") == "ok" else 1

    if args.cmd == "refresh-la":
        from wellnav.ingest.la_refresh import refresh_louisiana

        well_conn = connect(args.db)
        init_schema(well_conn)
        result = refresh_louisiana(well_conn)
        well_conn.close()
        print(result)
        return 0

    if args.cmd == "sync-la":
        from wellnav.ingest.la_operators import sync_louisiana_catalog

        well_conn = connect(args.db)
        init_schema(well_conn)
        result = sync_louisiana_catalog(well_conn)
        well_conn.close()
        print(result)
        return 0

    if args.cmd == "load-sonris":
        from wellnav.ingest.sonris_portal import load_sonris_export

        well_conn = connect(args.db)
        init_schema(well_conn)
        result = load_sonris_export(well_conn, args.file)
        well_conn.close()
        print(result)
        return 0

    if args.cmd == "fill-missing":
        from wellnav.ingest.fill_missing import load_fill_missing

        shard = None if args.shard < 0 else args.shard
        stats = load_fill_missing(
            workers=args.workers,
            delay=args.delay,
            shard=shard,
            shards=max(1, args.shards),
        )
        print(stats)
        return 0 if stats.get("status") == "ok" else 1

    if args.cmd == "fill-api":
        from wellnav.ingest.fill_api import fill_api

        stats = fill_api(
            workers=args.workers,
            delay=args.delay,
            shard=0 if args.shard < 0 else args.shard,
            shards=max(1, args.shards if args.shard >= 0 else 1),
        )
        print(stats)
        return 0 if stats.get("status") == "ok" else 1

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
        hours = args.hours or float(get_meta(conn, "permit_refresh_hours", "168") or 168)
        conn.close()
        while True:
            if args.gis:
                print("refresh", load_texas(workers=args.workers, permit_only=True))
            else:
                print("refresh", refresh_permits())
            print("refresh-ok", refresh_permits(state="ok"))
            print("refresh-nm", refresh_permits(state="nm"))
            print("migrate", migrate_as_drilled())
            if args.once:
                break
            print(f"sleeping {hours}h")
            time.sleep(max(hours, 0.01) * 3600)
        return 0

    if args.cmd == "status":
        repo = WellRepository()
        print("db", DB_PATH)
        print("counts", repo.counts("all"))
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
