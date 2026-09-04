"""Query local per-state well and permit tables."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from wellnav.db import connect, init_schema
from wellnav.parsers import format_api, normalize_api
from wellnav.states import permits_table, wells_table


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_to_well(row: sqlite3.Row, *, record_kind: str) -> dict:
    api8 = row["api8"]
    lease = row["lease_name"] or ""
    well_no = row["well_no"] or ""
    well_name = row["well_name"] or (f"{lease} #{well_no}".strip(" #") if lease or well_no else format_api(api8))
    return {
        "api": api8,
        "api_display": format_api(api8),
        "well_name": well_name,
        "well_no": well_no,
        "lease_name": lease,
        "lease_no": row["lease_no"] or "",
        "county": row["county"] or "",
        "county_code": row["county_code"] or "",
        "district": row["district"] or "",
        "operator": row["operator"] or "",
        "operator_number": row["operator_number"] or "",
        "field": row["field"] if "field" in row.keys() else "",
        "record_kind": record_kind,
        "status": row["status"] if "status" in row.keys() else "as_drilled",
        "wellhead_lat": row["wellhead_lat"],
        "wellhead_lon": row["wellhead_lon"],
        "toe_lat": row["toe_lat"] if "toe_lat" in row.keys() else None,
        "toe_lon": row["toe_lon"] if "toe_lon" in row.keys() else None,
        "location_kind": row["location_kind"] if "location_kind" in row.keys() else None,
        "location_source": row["location_source"] if "location_source" in row.keys() else None,
        "wellhead_crs": row["wellhead_crs"] if "wellhead_crs" in row.keys() else None,
    }


class WellRepository:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._owned = conn is None
        self.conn = conn or connect()
        init_schema(self.conn)
        self.conn.commit()

    def close(self) -> None:
        if self._owned:
            self.conn.close()

    def counts(self, state: str = "tx") -> dict:
        w, p = wells_table(state), permits_table(state)
        wells = self.conn.execute(f"SELECT COUNT(*) AS n FROM {w}").fetchone()["n"]
        permits = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM {p} WHERE status NOT IN ('migrated')"
        ).fetchone()["n"]
        return {"wells": wells, "permits": permits, "total": wells + permits}

    def search(
        self,
        *,
        state: str = "tx",
        mode: str = "name",
        q: str = "",
        operator_number: str = "",
        lease_no: str = "",
        district: str = "",
        page_size: int = 50,
        offset: int = 0,
        include_permits: bool = True,
    ) -> dict:
        w, p = wells_table(state), permits_table(state)
        well_where, well_args = self._filters(
            mode, q, operator_number, lease_no=lease_no, district=district
        )
        permit_where, permit_args = self._filters(
            mode, q, operator_number, lease_no=lease_no, district=district
        )
        well_sql = (
            f"SELECT api8, well_name, well_no, lease_name, lease_no, county, county_code, "
            f"district, operator, operator_number, wellhead_lat, wellhead_lon, wellhead_crs, "
            f"toe_lat, toe_lon, location_kind, location_source, 'as_drilled' AS status, "
            f"'as_drilled' AS _kind FROM {w} WHERE {well_where}"
        )
        args: list = list(well_args)
        parts = [well_sql]
        if include_permits:
            parts.append(
                f"SELECT api8, well_name, well_no, lease_name, lease_no, county, county_code, "
                f"district, operator, operator_number, wellhead_lat, wellhead_lon, wellhead_crs, "
                f"NULL AS toe_lat, NULL AS toe_lon, NULL AS location_kind, NULL AS location_source, "
                f"status, 'permit' AS _kind FROM {p} "
                f"WHERE {permit_where} AND status NOT IN ('migrated')"
            )
            args.extend(permit_args)

        union = " UNION ALL ".join(parts)
        total = self.conn.execute(f"SELECT COUNT(*) AS n FROM ({union})", args).fetchone()["n"]
        rows = self.conn.execute(
            f"SELECT * FROM ({union}) ORDER BY well_name, api8 LIMIT ? OFFSET ?",
            [*args, page_size, offset],
        ).fetchall()
        wells = [_row_to_well(row, record_kind=row["_kind"]) for row in rows]
        start = offset + 1 if wells else 0
        end = offset + len(wells)
        return {
            "wells": wells,
            "total": total,
            "start": start,
            "end": end,
            "page_size": page_size,
            "offset": offset,
        }

    def search_operators(self, q: str, state: str = "tx", limit: int = 40) -> list[dict]:
        needle = (q or "").strip()
        if len(needle) < 2:
            return []
        w, p = wells_table(state), permits_table(state)
        like = f"%{needle}%"
        rows = self.conn.execute(
            f"""
            SELECT operator_number AS number, operator AS name FROM {w}
            WHERE operator LIKE ? AND operator_number IS NOT NULL AND operator_number != ''
            UNION
            SELECT operator_number, operator FROM {p}
            WHERE operator LIKE ? AND operator_number IS NOT NULL AND operator_number != ''
              AND status NOT IN ('migrated')
            ORDER BY name LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()
        seen = set()
        out = []
        for row in rows:
            key = (row["number"], row["name"])
            if key in seen or not row["name"]:
                continue
            seen.add(key)
            out.append({"number": row["number"], "name": row["name"]})
        return out

    def get_well(self, api: str, state: str = "tx") -> dict | None:
        _, _, eight = normalize_api(api)
        w, p = wells_table(state), permits_table(state)
        row = self.conn.execute(f"SELECT * FROM {w} WHERE api8 = ?", (eight,)).fetchone()
        if row:
            well = _row_to_well(row, record_kind="as_drilled")
            well["found"] = True
            return well
        row = self.conn.execute(
            f"SELECT * FROM {p} WHERE api8 = ? AND status NOT IN ('migrated') ORDER BY last_seen_at DESC",
            (eight,),
        ).fetchone()
        if row:
            well = _row_to_well(row, record_kind="permit")
            well["found"] = True
            return well
        return None

    def search_leases(self, q: str, state: str = "tx", limit: int = 40) -> list[dict]:
        needle = (q or "").strip()
        if len(needle) < 2:
            return []
        w, p = wells_table(state), permits_table(state)
        like = f"%{needle}%"
        rows = self.conn.execute(
            f"""
            SELECT lease_name AS name, lease_no, district, county
            FROM {w}
            WHERE lease_name LIKE ? AND lease_name IS NOT NULL AND lease_name != ''
            UNION
            SELECT lease_name, lease_no, district, county
            FROM {p}
            WHERE lease_name LIKE ? AND lease_name IS NOT NULL AND lease_name != ''
              AND status NOT IN ('migrated')
            ORDER BY name LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()
        seen = set()
        out = []
        for row in rows:
            key = (row["lease_no"], row["district"], row["name"])
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "name": row["name"],
                "lease_no": row["lease_no"] or "",
                "district": row["district"] or "",
                "county": row["county"] or "",
                "kind": "Lease",
            })
        return out

    def _filters(
        self,
        mode: str,
        q: str,
        operator_number: str,
        *,
        lease_no: str = "",
        district: str = "",
    ) -> tuple[str, list]:
        q = (q or "").strip()
        if lease_no:
            clauses = ["lease_no = ?"]
            args: list = [lease_no]
            if district:
                clauses.append("district = ?")
                args.append(district)
            return " AND ".join(clauses), args
        if mode == "api":
            try:
                _, _, eight = normalize_api(q)
            except ValueError:
                eight = "".join(ch for ch in q if ch.isdigit())[-8:]
            return "api8 LIKE ?", [f"%{eight}"] if eight else ("1=0", [])
        if mode == "operator":
            if operator_number:
                return "operator_number = ?", [operator_number]
            return "operator LIKE ?", [f"%{q}%"]
        if q:
            like = f"%{q}%"
            return (
                "(well_name LIKE ? OR lease_name LIKE ? OR well_no LIKE ? OR county LIKE ? OR operator LIKE ?)",
                [like, like, like, like, like],
            )
        return "1=1", []


REPO = WellRepository()
