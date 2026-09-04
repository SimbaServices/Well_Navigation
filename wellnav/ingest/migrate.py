"""Move permits to legacy well tables once as-drilled GIS data exists."""

from __future__ import annotations

from wellnav.db import session
from wellnav.gis import LAYER_WELL_LOCATIONS, query_features
from wellnav.ingest.classify import build_record, utcnow
from wellnav.ingest.persist import upsert_wells
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS, PERMIT_SYMNUMS, TX_COUNTY_NAME, permits_table, wells_table


def expire_permits(state: str = "tx") -> int:
    now = utcnow()
    with session() as conn:
        table = permits_table(state)
        cur = conn.execute(
            f"""
            UPDATE {table}
            SET status = 'expired', updated_at = ?
            WHERE status IN ('approved', 'validated')
              AND expires_at IS NOT NULL
              AND expires_at < ?
            """,
            (now, now),
        )
        return cur.rowcount


def migrate_as_drilled(state: str = "tx", limit: int = 400, request_gis: bool = True) -> dict:
    """Promote permits whose GIS symbol is no longer a permitted location."""
    now = utcnow()
    moved = 0
    ready = 0
    with session() as conn:
        ptable = permits_table(state)
        rows = conn.execute(
            f"""
            SELECT * FROM {ptable}
            WHERE status NOT IN ('migrated', 'expired', 'cancelled')
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        for row in rows:
            api8 = row["api8"]
            as_drilled = None
            if request_gis:
                payload = query_features(LAYER_WELL_LOCATIONS, f"API='{api8}'", page_size=5)
                features = payload.get("features") or []
                if features:
                    attrs = features[0].get("attributes") or {}
                    try:
                        sym = int(attrs.get("SYMNUM")) if attrs.get("SYMNUM") is not None else None
                    except (TypeError, ValueError):
                        sym = None
                    if sym not in PERMIT_SYMNUMS and sym is not None:
                        as_drilled = features[0]
            existing = conn.execute(
                f"SELECT api8 FROM {wells_table(state)} WHERE api8 = ?", (api8,)
            ).fetchone()
            if as_drilled is None and existing is None:
                continue
            ready += 1
            if as_drilled is not None:
                record = build_record(
                    api8,
                    as_drilled,
                    None,
                    county_code=row["county_code"] or api8[:3],
                    county_name=row["county"] or TX_COUNTY_NAME.get(api8[:3], ""),
                    now=now,
                    expires_at=row["expires_at"] or now,
                    lifetime_days=row["lifetime_days"] or DEFAULT_PERMIT_LIFETIME_DAYS,
                )
                record["lease_name"] = row["lease_name"] or ""
                record["operator"] = row["operator"] or ""
                record["operator_number"] = row["operator_number"] or ""
                record["migrated_from_permit"] = 1
                record["source"] = "permit_migration"
                upsert_wells(conn, state, [record])
            conn.execute(
                f"""
                UPDATE {ptable}
                SET status = 'migrated', migrated_at = ?, as_drilled_ready = 1, updated_at = ?
                WHERE api8 = ? AND permit_no = ?
                """,
                (now, now, api8, row["permit_no"]),
            )
            moved += 1
    expired = expire_permits(state)
    return {"migrated": moved, "ready": ready, "expired": expired, "checked": len(rows)}
