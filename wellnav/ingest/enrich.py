"""Attach lease/operator identity. County-wide wellbore search exceeds RRC's cap."""

from __future__ import annotations

from wellnav.db import session
from wellnav.ingest.classify import utcnow
from wellnav.rrc import RrcClient
from wellnav.states import permits_table, wells_table


def apply_identity(state: str, well: dict) -> bool:
    api8 = well["api"]
    now = utcnow()
    incoming = {
        "well_name": well.get("well_name") or "",
        "well_no": well.get("well_no") or "",
        "lease_name": well.get("lease_name") or "",
        "lease_no": well.get("lease_no") or "",
        "county": well.get("county") or "",
        "district": well.get("district") or "",
        "operator": well.get("operator") or "",
        "operator_number": well.get("operator_number") or "",
        "field": well.get("field") or "",
    }
    with session() as conn:
        wcur = conn.execute(
            f"""
            UPDATE {wells_table(state)}
            SET well_name=COALESCE(NULLIF(?, ''), well_name),
                well_no=COALESCE(NULLIF(?, ''), well_no),
                lease_name=COALESCE(NULLIF(?, ''), lease_name),
                lease_no=COALESCE(NULLIF(?, ''), lease_no),
                county=COALESCE(NULLIF(?, ''), county),
                district=COALESCE(NULLIF(?, ''), district),
                operator=COALESCE(NULLIF(?, ''), operator),
                operator_number=COALESCE(NULLIF(?, ''), operator_number),
                field=COALESCE(NULLIF(?, ''), field),
                updated_at=?
            WHERE api8=?
            """,
            (
                incoming["well_name"], incoming["well_no"], incoming["lease_name"],
                incoming["lease_no"], incoming["county"], incoming["district"],
                incoming["operator"], incoming["operator_number"], incoming["field"],
                now, api8,
            ),
        )
        pcur = conn.execute(
            f"""
            UPDATE {permits_table(state)}
            SET well_name=COALESCE(NULLIF(?, ''), well_name),
                well_no=COALESCE(NULLIF(?, ''), well_no),
                lease_name=COALESCE(NULLIF(?, ''), lease_name),
                lease_no=COALESCE(NULLIF(?, ''), lease_no),
                county=COALESCE(NULLIF(?, ''), county),
                district=COALESCE(NULLIF(?, ''), district),
                operator=COALESCE(NULLIF(?, ''), operator),
                operator_number=COALESCE(NULLIF(?, ''), operator_number),
                updated_at=?
            WHERE api8=? AND status NOT IN ('migrated')
            """,
            (
                incoming["well_name"], incoming["well_no"], incoming["lease_name"],
                incoming["lease_no"], incoming["county"], incoming["district"],
                incoming["operator"], incoming["operator_number"], now, api8,
            ),
        )
        return bool(wcur.rowcount or pcur.rowcount)


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
