"""Attach lease/operator identity. County-wide wellbore search exceeds RRC's cap."""

from __future__ import annotations

from wellnav.db import session
from wellnav.ingest.persist import update_identity
from wellnav.rrc import RrcClient
from wellnav.states import wells_table


def apply_identity(state: str, well: dict) -> bool:
    with session() as conn:
        wells, permits = update_identity(conn, state, [well])
        return bool(wells or permits)


def enrich_api(api8: str, *, state: str = "tx", client: RrcClient | None = None) -> dict | None:
    client = client or RrcClient()
    page = client.search_wellbores(api=api8, page_size=10, schedule="Both")
    wells = page.get("wells") or []
    if not wells:
        return None
    well = wells[0]
    apply_identity(state, well)
    return well


def enrich_missing(county_code: str, *, state: str = "tx", limit: int = 50) -> dict:
    client = RrcClient()
    with session() as conn:
        rows = conn.execute(
            f"""
            SELECT api8 FROM {wells_table(state)}
            WHERE county_code = ? AND (lease_name IS NULL OR lease_name = '')
            LIMIT ?
            """,
            (county_code, limit),
        ).fetchall()
    updated = 0
    for row in rows:
        if enrich_api(row["api8"], state=state, client=client):
            updated += 1
    return {"county_code": county_code, "candidates": len(rows), "updated": updated}
