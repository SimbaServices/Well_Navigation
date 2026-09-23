"""Texas-style well identity shared by every state table.

``well_name`` is the display name (``{lease} #{well_no}`` when both exist).
``well_no`` is the number without a leading ``#``. ``lease_name`` is the lease
only. Search and the well list read ``well_name``, so a number left only in
``well_no`` never reaches the UI.
"""

from __future__ import annotations

import re
import sqlite3
import sys

from wellnav.states import US_STATES, permits_table, wells_table

META_KEY = "well_name_style_v1"
_TABLE_RE = re.compile(r"^(wells|permits)_[a-z]{2}$")
_BATCH = 5000


def compose_well_name(lease: str, well_no: str) -> str:
    """Same display string Texas identity writes: ``LEASE #NO``."""
    lease = (lease or "").strip()
    well_no = (well_no or "").strip()
    if lease and well_no:
        return f"{lease} #{well_no}".strip(" #")
    return (lease or well_no).strip(" #")


def normalize_well_identity(
    well_name: str | None,
    well_no: str | None,
    lease_name: str | None,
) -> tuple[str, str, str]:
    """Return ``(well_name, well_no, lease_name)`` in Texas form.

    A number already visible in ``well_name`` is not appended again. A ``#``
    that does not match ``well_no`` (a parenthetical alias, a longer number)
    stays in the name so the stored text is not doubled.
    """
    name = " ".join((well_name or "").split())
    number = _strip_hash(well_no)
    lease = " ".join((lease_name or "").split())

    if not name and not number:
        return "", "", lease

    if number and _same(name, number):
        return number, number, lease

    suffix = _hash_suffix(name)
    if suffix and (not number or _same(suffix, number)) and " " not in suffix:
        peeled = _peel_hash(name)
        if not number:
            number = suffix
        if peeled:
            if not lease:
                lease = peeled
            return compose_well_name(peeled, number), number, lease
        return number, number, lease

    if number and "#" not in name and _space_suffix(name, number):
        peeled = name[: name.upper().rfind(" " + number.upper())].strip()
        if peeled:
            if not lease or _same(lease, name):
                lease = peeled
            return compose_well_name(peeled, number), number, lease

    if number and "#" not in name:
        if name:
            if not lease:
                lease = name
            return compose_well_name(name, number), number, lease
        return number, number, lease

    if not lease and name and "#" not in name:
        lease = name
    return name, number, lease


def normalize_stored_well_names(conn: sqlite3.Connection) -> int:
    """Rewrite stored wells, permits, and saved wells once. Idempotent."""
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?",
            (META_KEY,),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    if row and (row["value"] if isinstance(row, sqlite3.Row) else row[0]) == "1":
        return 0

    previous = conn.execute("PRAGMA busy_timeout").fetchone()
    conn.execute("PRAGMA busy_timeout=600000")
    try:
        changed = 0
        tables = ["saved_wells"]
        for code in sorted(US_STATES):
            tables.append(wells_table(code))
            tables.append(permits_table(code))
        for table in tables:
            n = _normalize_table(conn, table)
            changed += n
            if n:
                print(f"{table} {n}", file=sys.stderr, flush=True)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (META_KEY, "1"),
        )
        return changed
    finally:
        timeout = previous[0] if previous else 60000
        conn.execute(f"PRAGMA busy_timeout={int(timeout)}")


def _normalize_table(conn: sqlite3.Connection, table: str) -> int:
    if table != "saved_wells" and not _TABLE_RE.fullmatch(table):
        raise ValueError(f"unexpected well table {table}")
    try:
        rows = conn.execute(
            f"""
            SELECT rowid, well_name, well_no, lease_name
            FROM {table}
            WHERE TRIM(COALESCE(well_no, '')) LIKE '#%'
               OR TRIM(COALESCE(well_no, '')) LIKE ' #%'
               OR (
                    TRIM(COALESCE(lease_name, '')) = ''
                    AND TRIM(COALESCE(well_name, '')) != ''
                  )
               OR (
                    TRIM(COALESCE(well_no, '')) != ''
                    AND INSTR(COALESCE(well_name, ''), '#') = 0
                    AND UPPER(TRIM(COALESCE(well_name, ''))) != UPPER(TRIM(well_no))
                  )
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return 0

    updates: list[tuple[str, str, str, int]] = []
    for rowid, well_name, well_no, lease_name in rows:
        new_name, new_no, new_lease = normalize_well_identity(
            well_name, well_no, lease_name
        )
        if (
            new_name == (well_name or "")
            and new_no == (well_no or "")
            and new_lease == (lease_name or "")
        ):
            continue
        updates.append((new_name, new_no, new_lease, rowid))
    if not updates:
        return 0

    sql = (
        f"UPDATE {table} SET well_name = ?, well_no = ?, lease_name = ? "
        "WHERE rowid = ?"
    )
    for start in range(0, len(updates), _BATCH):
        conn.executemany(sql, updates[start : start + _BATCH])
    return len(updates)


def _strip_hash(value: str | None) -> str:
    text = " ".join((value or "").split())
    while text.startswith("#"):
        text = text[1:].strip()
    return text


def _hash_suffix(name: str) -> str:
    if "#" not in name:
        return ""
    return name.rsplit("#", 1)[1].strip()


def _peel_hash(name: str) -> str:
    if "#" not in name:
        return name.strip()
    return name.rsplit("#", 1)[0].strip()


def _space_suffix(name: str, number: str) -> bool:
    if not name or not number:
        return False
    return name.upper().endswith(" " + number.upper())


def _same(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return " ".join(left.upper().split()) == " ".join(right.upper().split())
