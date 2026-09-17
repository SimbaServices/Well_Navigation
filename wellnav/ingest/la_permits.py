"""Split Louisiana permit-only rows into permits_la (same columns as permits_tx)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from wellnav.ingest.classify import utcnow
from wellnav.ingest.persist import upsert_permits
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS, permits_table, wells_table

NEVER_PERMIT_SOURCES = frozenset({"la_fracfocus", "la_bsee"})

PERMIT_MARKERS = (
    "PERMIT EXPIRED",
    "PERMIT CANCEL",
    "PERMIT CANCELED",
    "PERMIT CANCELLED",
    "APPLICATION PENDING",
    "APPLICATION ",
    "WAITING TO DRILL",
    "WAITING ON",
    "NOT DRILLED",
    "PERMITTED LOCATION",
    "PERMITTED -",
    "PERMIT APPROVED",
    "PERMIT ISSUED",
)

DRILLED_MARKERS = (
    "PRODUC",
    "PLUGGED",
    "INJECT",
    "SHUT-IN",
    "SHUT IN",
    "SHUTIN",
    "ABANDON",
    "COMPLETED",
    "DRY AND",
    "ORPHAN",
    "FLOWING",
    "TEMPORARILY",
    "PERMANENTLY",
)

_MONTHS = {
    name: index
    for index, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"),
        start=1,
    )
}

_DATE_FMTS = (
    "%m/%d/%Y",
    "%Y-%m-%d",
    "%m-%d-%Y",
    "%Y%m%d",
    "%d/%m/%Y",
)


def parse_sonris_date(value: object) -> str:
    """Normalize SONRIS dates such as 17-DEC-2018 or 12/17/2018 to YYYY-MM-DD."""
    text = str(value or "").strip()
    if not text:
        return ""
    parts = text.replace("/", "-").split("-")
    if len(parts) == 3 and parts[1].isalpha():
        month = _MONTHS.get(parts[1][:3].upper())
        if month:
            try:
                day = int(parts[0])
                year = int(parts[2])
                if year < 100:
                    year += 2000 if year < 70 else 1900
                return date(year, month, day).isoformat()
            except ValueError:
                pass
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _status_blob(well: dict) -> str:
    return " ".join(
        str(well.get(name) or "")
        for name in ("symbol", "well_type", "status")
    ).upper()


def is_la_permit_only(well: dict) -> bool:
    """True when the row is an undrilled / expired / cancelled permit, not a wellbore."""
    source = str(well.get("source") or "").strip().lower()
    if source in NEVER_PERMIT_SOURCES:
        return False
    if well.get("_spud_at") or well.get("spud_at"):
        return False
    blob = _status_blob(well)
    if not blob:
        return False
    drilled = any(marker in blob for marker in DRILLED_MARKERS)
    permit_like = any(marker in blob for marker in PERMIT_MARKERS) or (
        "PERMITTED" in blob and "UNPERMITTED" not in blob
    )
    return permit_like and not drilled


def permit_status_from_well(well: dict) -> str:
    blob = _status_blob(well)
    if "CANCEL" in blob:
        return "cancelled"
    if "EXPIRE" in blob:
        return "expired"
    return "approved"


def _permit_no(well: dict) -> str:
    loc = str(well.get("location_source") or "")
    if loc.startswith("la_serial:"):
        serial = loc.split(":", 1)[1].strip()
        if serial:
            return serial
    lease_no = str(well.get("lease_no") or "").strip()
    if lease_no and not lease_no.upper().startswith("GIS-"):
        return lease_no
    return f"SONRIS-{well['api8']}"


def permit_from_well(well: dict, *, now: str | None = None) -> dict:
    """Map a wells_la-shaped dict onto PERMIT_FIELDS used by permits_tx."""
    stamp = now or utcnow()
    approved = parse_sonris_date(well.get("approved_at") or well.get("_approved_at") or "")
    expires = parse_sonris_date(well.get("expires_at") or well.get("_expires_at") or "")
    submitted = parse_sonris_date(well.get("submitted_at") or well.get("_submitted_at") or "")
    if not expires and approved:
        try:
            expires = (
                datetime.fromisoformat(approved) + timedelta(days=DEFAULT_PERMIT_LIFETIME_DAYS)
            ).date().isoformat()
        except ValueError:
            expires = ""
    return {
        "api": well["api"],
        "api8": well["api8"],
        "permit_no": _permit_no(well),
        "status": permit_status_from_well(well),
        "well_name": well.get("well_name") or "",
        "well_no": well.get("well_no") or "",
        "lease_name": well.get("lease_name") or "",
        "lease_no": well.get("lease_no") or "",
        "county": well.get("county") or "",
        "county_code": well.get("county_code") or "",
        "district": well.get("district") or "",
        "operator": well.get("operator") or "",
        "operator_number": well.get("operator_number") or "",
        "profile": well.get("profile") or "",
        "symbol": well.get("symbol") or well.get("well_type") or "",
        "symnum": well.get("symnum"),
        "wellhead_lat": well.get("wellhead_lat"),
        "wellhead_lon": well.get("wellhead_lon"),
        "wellhead_crs": well.get("wellhead_crs") or "EPSG:4326",
        "approved_at": approved or None,
        "submitted_at": submitted or None,
        "expires_at": expires or None,
        "lifetime_days": DEFAULT_PERMIT_LIFETIME_DAYS,
        "as_drilled_ready": 0,
        "migrated_at": None,
        "source": well.get("source") or "la_sonris",
        "first_seen_at": well.get("first_seen_at") or stamp,
        "last_seen_at": stamp,
        "updated_at": stamp,
    }


def split_la_permit_rows(conn) -> dict:
    """Move permit-only wells_la rows into permits_la and delete the well copies."""
    now = utcnow()
    rows = conn.execute(
        f"""
        SELECT * FROM {wells_table("la")}
        WHERE LOWER(COALESCE(source, '')) NOT IN ('la_fracfocus', 'la_bsee')
          AND (
            UPPER(COALESCE(symbol, '')) LIKE '%PERMIT%'
            OR UPPER(COALESCE(well_type, '')) LIKE '%PERMIT%'
            OR UPPER(COALESCE(symbol, '')) LIKE '%APPLICATION%'
            OR UPPER(COALESCE(well_type, '')) LIKE '%APPLICATION%'
            OR UPPER(COALESCE(symbol, '')) LIKE '%WAITING%'
            OR UPPER(COALESCE(well_type, '')) LIKE '%NOT DRILLED%'
          )
        """
    ).fetchall()
    moved: list[str] = []
    batch: list[dict] = []
    for row in rows:
        well = dict(row)
        if not is_la_permit_only(well):
            continue
        batch.append(permit_from_well(well, now=now))
        moved.append(well["api"])
        if len(batch) >= 500:
            upsert_permits(conn, "la", batch)
            batch = []
    if batch:
        upsert_permits(conn, "la", batch)
    if moved:
        conn.executemany(
            f"DELETE FROM {wells_table('la')} WHERE api = ?",
            [(api,) for api in moved],
        )
    return {"moved": len(moved), "permits": len(moved)}


def migrate_la_permits(conn) -> int:
    """Mark permits_la migrated once the same API exists in wells_la."""
    now = utcnow()
    cur = conn.execute(
        f"""
        UPDATE {permits_table("la")}
        SET status = 'migrated', migrated_at = ?, as_drilled_ready = 1, updated_at = ?
        WHERE status NOT IN ('migrated', 'expired', 'cancelled')
          AND api8 IN (SELECT api8 FROM {wells_table("la")})
        """,
        (now, now),
    )
    return cur.rowcount


def expire_la_permits(conn) -> int:
    now = utcnow()
    today = now[:10]
    cur = conn.execute(
        f"""
        UPDATE {permits_table("la")}
        SET status = 'expired', updated_at = ?
        WHERE status IN ('approved', 'validated')
          AND expires_at IS NOT NULL
          AND expires_at != ''
          AND expires_at < ?
        """,
        (now, today),
    )
    return cur.rowcount
