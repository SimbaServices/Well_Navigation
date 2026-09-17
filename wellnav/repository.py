"""Query local per-state well and permit tables."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from wellnav.db import connect, init_schema
from wellnav.parsers import format_api, normalize_api
from wellnav.states import (
    APP_STATES,
    api_prefix,
    normalize_state,
    operators_table,
    parse_states,
    permits_table,
    state_from_api,
    wells_table,
)
from wellnav.operators import collapse_operators, normalize_operator_name
from wellnav.well_status import status_label


def _states_arg(state: str | list[str] | None) -> list[str]:
    if isinstance(state, (list, tuple)):
        out: list[str] = []
        for item in state:
            key = (item or "").strip().lower()
            if key in APP_STATES and key not in out:
                out.append(key)
        return out or ["tx"]
    text = (state or "tx").strip().lower()
    if text == "all" or "," in text:
        return parse_states(text)
    try:
        key = normalize_state(text)
    except ValueError:
        return ["tx"]
    return [key] if key in APP_STATES else ["tx"]


def _api8(value: str) -> str:
    try:
        _, _, eight = normalize_api(value)
        return eight
    except ValueError:
        return "".join(ch for ch in (value or "") if ch.isdigit())[-8:]


_SORT_COLUMNS = {
    "name": "well_name",
    "api": "api8",
    "status": "symbol",
    "lease": "lease_name",
    "operator": "operator",
    "county": "county",
}


def _order_sql(sort: str, direction: str) -> str:
    column = _SORT_COLUMNS.get((sort or "").strip().lower(), "well_name")
    suffix = " DESC" if (direction or "").strip().lower() == "desc" else ""
    if column == "api8":
        return f"api8{suffix}, well_name"
    return f"{column}{suffix}, api8"


def _unique_operator_numbers(values: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        number = (raw or "").strip()
        if not number or number in seen:
            continue
        seen.add(number)
        out.append(number)
    return out


def _unique_operator_names(values: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        name = normalize_operator_name(raw)
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_symbol(row: sqlite3.Row) -> str:
    if "symbol" in row.keys() and row["symbol"]:
        return str(row["symbol"]).strip()
    if "well_type" in row.keys() and row["well_type"]:
        return str(row["well_type"]).strip()
    return ""


def _status_for_row(row: sqlite3.Row, record_kind: str) -> str:
    permit_status = row["status"] if record_kind == "permit" and "status" in row.keys() else None
    well_type = ""
    if "well_type" in row.keys() and row["well_type"]:
        well_type = str(row["well_type"]).strip()
    return status_label(
        _row_symbol(row),
        record_kind=record_kind,
        permit_status=permit_status,
        well_type=well_type,
    )


def _row_to_well(row: sqlite3.Row, *, record_kind: str, state: str | None = None) -> dict:
    api8 = row["api8"]
    api_full = ""
    if "api" in row.keys() and row["api"]:
        api_full = "".join(ch for ch in str(row["api"]) if ch.isdigit())
    row_state = ""
    if "state" in row.keys() and row["state"]:
        row_state = str(row["state"]).strip().lower()
    st = state or row_state or state_from_api(api_full) or "tx"
    if len(api_full) < 10:
        api_full = f"{api_prefix(st)}{api8}"
    else:
        api_full = api_full[:10]
    lease = row["lease_name"] or ""
    well_no = row["well_no"] or ""
    well_name = row["well_name"] or (
        f"{lease} #{well_no}".strip(" #") if lease or well_no else format_api(api_full, st)
    )
    return {
        "api": api8,
        "api_full": api_full,
        "state": st,
        "api_display": format_api(api_full, st),
        "well_name": well_name,
        "well_no": well_no,
        "lease_name": lease,
        "lease_no": row["lease_no"] or "",
        "county": row["county"] or "",
        "county_code": row["county_code"] or "",
        "district": row["district"] or "",
        "operator": normalize_operator_name(row["operator"] or "") or (row["operator"] or ""),
        "operator_number": row["operator_number"] or "",
        "field": row["field"] if "field" in row.keys() else "",
        "record_kind": record_kind,
        "status": row["status"] if "status" in row.keys() else "as_drilled",
        "symbol": _row_symbol(row),
        "status_label": _status_for_row(row, record_kind),
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
        """Well and permit totals. ``state='all'`` sums every app state (TX/NM/OK/LA)."""
        totals = {"wells": 0, "permits": 0, "live_permits": 0, "total": 0}
        for code in _states_arg(state):
            w, p = wells_table(code), permits_table(code)
            wells = self.conn.execute(f"SELECT COUNT(*) AS n FROM {w}").fetchone()["n"]
            permits = self.conn.execute(
                f"SELECT COUNT(*) AS n FROM {p} WHERE status NOT IN ('migrated')"
            ).fetchone()["n"]
            live = self.conn.execute(
                f"""
                SELECT COUNT(*) AS n FROM {p}
                WHERE LOWER(TRIM(COALESCE(status, '')))
                      NOT IN ('migrated', 'expired', 'cancelled', 'canceled')
                """
            ).fetchone()["n"]
            totals["wells"] += wells
            totals["permits"] += permits
            totals["live_permits"] += live
            totals["total"] += wells + permits
        return totals

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
        operator_numbers: list[str] | None = None,
        operator_names: list[str] | None = None,
        name: str = "",
        api: str = "",
        sort: str = "name",
        direction: str = "asc",
    ) -> dict:
        states = _states_arg(state)
        well_where, well_args = self._filters(
            mode,
            q,
            operator_number,
            lease_no=lease_no,
            district=district,
            operator_numbers=operator_numbers,
            operator_names=operator_names,
            name=name,
            api=api,
        )
        permit_where, permit_args = self._filters(
            mode,
            q,
            operator_number,
            lease_no=lease_no,
            district=district,
            operator_numbers=operator_numbers,
            operator_names=operator_names,
            name=name,
            api=api,
        )
        parts: list[str] = []
        args: list = []
        for code in states:
            w, p = wells_table(code), permits_table(code)
            parts.append(
                f"SELECT api, api8, well_name, well_no, lease_name, lease_no, county, county_code, "
                f"district, operator, operator_number, wellhead_lat, wellhead_lon, wellhead_crs, "
                f"toe_lat, toe_lon, location_kind, location_source, 'as_drilled' AS status, "
                f"COALESCE(symbol, well_type, '') AS symbol, COALESCE(well_type, '') AS well_type, "
                f"'as_drilled' AS _kind, '{code}' AS state FROM {w} WHERE {well_where}"
            )
            args.extend(well_args)
            if include_permits:
                parts.append(
                    f"SELECT api, api8, well_name, well_no, lease_name, lease_no, county, county_code, "
                    f"district, operator, operator_number, wellhead_lat, wellhead_lon, wellhead_crs, "
                    f"NULL AS toe_lat, NULL AS toe_lon, NULL AS location_kind, NULL AS location_source, "
                    f"status, COALESCE(symbol, '') AS symbol, '' AS well_type, "
                    f"'permit' AS _kind, '{code}' AS state FROM {p} "
                    f"WHERE {permit_where} AND status NOT IN ('migrated')"
                )
                args.extend(permit_args)

        union = " UNION ALL ".join(parts)
        total = self.conn.execute(f"SELECT COUNT(*) AS n FROM ({union})", args).fetchone()["n"]
        order_sql = _order_sql(sort, direction)
        rows = self.conn.execute(
            f"SELECT * FROM ({union}) ORDER BY {order_sql} LIMIT ? OFFSET ?",
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
        like = f"%{needle}%"
        states = _states_arg(state)
        rows: list[sqlite3.Row] = []
        fetch_limit = max(limit * 8, 80)
        for code in states:
            catalog = self._search_operators_table(code, like, fetch_limit)
            if catalog:
                rows.extend(catalog)
            rows.extend(self._search_operators_from_wells(code, like, fetch_limit))
        return collapse_operators(rows, limit)

    def wells_for_apis(self, apis: list[str], state: str = "tx") -> list[dict]:
        if not apis:
            return []
        eights: list[str] = []
        seen: set[str] = set()
        for raw in apis[:500]:
            eight = _api8(raw)
            if len(eight) < 8 or eight in seen:
                continue
            seen.add(eight)
            eights.append(eight)
        if not eights:
            return []
        states = _states_arg(state)
        placeholders = ",".join("?" * len(eights))
        well_by_api: dict[str, sqlite3.Row] = {}
        permit_by_api: dict[str, sqlite3.Row] = {}
        for code in states:
            w, p = wells_table(code), permits_table(code)
            well_rows = self.conn.execute(
                f"SELECT * FROM {w} WHERE api8 IN ({placeholders})",
                eights,
            ).fetchall()
            for row in well_rows:
                well_by_api.setdefault(row["api8"], row)
            missing = [eight for eight in eights if eight not in well_by_api]
            if missing:
                permit_rows = self.conn.execute(
                    f"SELECT * FROM {p} WHERE api8 IN ({','.join('?' * len(missing))}) "
                    f"AND status NOT IN ('migrated') ORDER BY last_seen_at DESC",
                    missing,
                ).fetchall()
                for row in permit_rows:
                    permit_by_api.setdefault(row["api8"], row)
        out = []
        for eight in eights:
            row = well_by_api.get(eight) or permit_by_api.get(eight)
            if row is None:
                continue
            kind = "as_drilled" if eight in well_by_api else "permit"
            out.append(_row_to_well(row, record_kind=kind))
        return out

    def get_well(self, api: str, state: str = "tx") -> dict | None:
        _, _, eight = normalize_api(api)
        order: list[str] = []
        detected = state_from_api(api)
        if detected:
            order.append(detected)
        for code in _states_arg(state):
            if code not in order:
                order.append(code)
        for code in APP_STATES:
            if code not in order:
                order.append(code)
        for code in order:
            w, p = wells_table(code), permits_table(code)
            row = self.conn.execute(f"SELECT * FROM {w} WHERE api8 = ?", (eight,)).fetchone()
            if row:
                well = _row_to_well(row, record_kind="as_drilled", state=code)
                well["found"] = True
                return well
            row = self.conn.execute(
                f"SELECT * FROM {p} WHERE api8 = ? AND status NOT IN ('migrated') "
                f"ORDER BY last_seen_at DESC",
                (eight,),
            ).fetchone()
            if row:
                well = _row_to_well(row, record_kind="permit", state=code)
                well["found"] = True
                return well
        return None

    def search_leases(self, q: str, state: str = "tx", limit: int = 40) -> list[dict]:
        needle = (q or "").strip()
        if len(needle) < 2:
            return []
        like = f"%{needle}%"
        parts: list[str] = []
        args: list = []
        for code in _states_arg(state):
            w, p = wells_table(code), permits_table(code)
            parts.append(
                f"SELECT lease_name AS name, lease_no, district, county FROM {w} "
                f"WHERE lease_name LIKE ? AND lease_name IS NOT NULL AND lease_name != ''"
            )
            args.append(like)
            parts.append(
                f"SELECT lease_name, lease_no, district, county FROM {p} "
                f"WHERE lease_name LIKE ? AND lease_name IS NOT NULL AND lease_name != '' "
                f"AND status NOT IN ('migrated')"
            )
            args.append(like)
        rows = self.conn.execute(
            f"{' UNION '.join(parts)} ORDER BY name LIMIT ?",
            (*args, limit),
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

    def _search_operators_tx(self, like: str, limit: int) -> list[sqlite3.Row] | None:
        return self._search_operators_table("tx", like, limit)

    def _search_operators_table(self, state: str, like: str, limit: int) -> list[sqlite3.Row] | None:
        table = operators_table(state)
        try:
            count = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        except sqlite3.OperationalError:
            return None
        if not count:
            return None
        return self.conn.execute(
            f"""
            SELECT operator_number AS number, operator_name AS name, 1 AS hits
            FROM {table}
            WHERE operator_name LIKE ?
            ORDER BY name
            LIMIT ?
            """,
            (like, limit),
        ).fetchall()

    def _search_operators_from_wells(self, state: str, like: str, limit: int) -> list[sqlite3.Row]:
        w, p = wells_table(state), permits_table(state)
        return self.conn.execute(
            f"""
            SELECT operator_number AS number, operator AS name, COUNT(*) AS hits FROM {w}
            WHERE operator LIKE ? AND TRIM(COALESCE(operator, '')) != ''
            GROUP BY operator_number, operator
            UNION ALL
            SELECT operator_number, operator, COUNT(*) FROM {p}
            WHERE operator LIKE ? AND TRIM(COALESCE(operator, '')) != ''
              AND status NOT IN ('migrated')
            GROUP BY operator_number, operator
            ORDER BY name LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()

    def _filters(
        self,
        mode: str,
        q: str,
        operator_number: str,
        *,
        lease_no: str = "",
        district: str = "",
        operator_numbers: list[str] | None = None,
        operator_names: list[str] | None = None,
        name: str = "",
        api: str = "",
    ) -> tuple[str, list]:
        q = (q or "").strip()
        name = (name or "").strip()
        api = (api or "").strip()
        stacked_ops = _unique_operator_numbers(operator_numbers)
        stacked_names = _unique_operator_names(operator_names)
        if name or api or stacked_ops or stacked_names:
            singular = (operator_number or "").strip()
            if singular and singular not in stacked_ops:
                stacked_ops.append(singular)
            return self._stacked_filters(
                stacked_ops,
                operator_names=stacked_names,
                name=name,
                api=api,
                lease_no=lease_no,
                district=district,
            )
        if lease_no:
            clauses = ["lease_no = ?"]
            args: list = [lease_no]
            if district:
                clauses.append("district = ?")
                args.append(district)
            return " AND ".join(clauses), args
        if mode == "api":
            eight = _api8(q)
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

    def _stacked_filters(
        self,
        operator_numbers: list[str],
        *,
        operator_names: list[str] | None = None,
        name: str,
        api: str,
        lease_no: str,
        district: str,
    ) -> tuple[str, list]:
        clauses: list[str] = []
        args: list = []
        names = _unique_operator_names(operator_names)
        if operator_numbers or names:
            parts: list[str] = []
            if operator_numbers:
                placeholders = ",".join("?" * len(operator_numbers))
                parts.append(f"operator_number IN ({placeholders})")
                args.extend(operator_numbers)
            if names:
                name_ph = ",".join("?" * len(names))
                parts.append(f"UPPER(TRIM(operator)) IN ({name_ph})")
                args.extend(names)
            clauses.append(f"({' OR '.join(parts)})")
        if name:
            like = f"%{name}%"
            clauses.append(
                "(well_name LIKE ? OR lease_name LIKE ? OR well_no LIKE ? OR county LIKE ?)"
            )
            args.extend([like, like, like, like])
        if api:
            eight = _api8(api)
            if eight:
                clauses.append("api8 LIKE ?")
                args.append(f"%{eight}")
            else:
                clauses.append("1=0")
        if lease_no:
            clauses.append("lease_no = ?")
            args.append(lease_no)
            if district:
                clauses.append("district = ?")
                args.append(district)
        return " AND ".join(clauses) if clauses else "1=1", args


REPO = WellRepository()
