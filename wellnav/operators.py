"""Canonical operator names so punctuation and legal suffixes do not fork filters."""

from __future__ import annotations

import re
import sqlite3

from wellnav.states import APP_STATES, US_STATES, operators_table, permits_table, wells_table

META_KEY = "operator_names_normalized"
CANON_META_KEY = "operator_names_canonical_v1"

_SUFFIX_RUNS = (
    ("INCORPORATED",),
    ("CORPORATION",),
    ("COMPANY",),
    ("LIMITED",),
    ("P", "L", "L", "C"),
    ("L", "L", "C"),
    ("L", "L", "P"),
    ("LLC",),
    ("LLP",),
    ("INC",),
    ("CORP",),
    ("LTD",),
    ("PLC",),
    ("PC",),
    ("LP",),
    ("CO",),
)


def normalize_operator_name(name: str | None) -> str:
    return " ".join((name or "").split()).upper()


def standardize_operator_name(name: str | None) -> str:
    """Display form: uppercase, collapsed space, tidy commas."""
    text = normalize_operator_name(name)
    if not text:
        return ""
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",+", ",", text)
    text = re.sub(r",\s*", ", ", text)
    return re.sub(r"\s+", " ", text).strip(" ,")


def operator_name_key(name: str | None) -> str:
    """Fingerprint so OXY USA INC. / OXY U.S.A.,INC. / OXY, USA match."""
    text = normalize_operator_name(name)
    if not text:
        return ""
    text = text.replace("&", " AND ")
    text = text.replace("U.S.A.", "USA").replace("U.S.", "US")
    text = text.replace(".", " ")
    text = re.sub(r"[^\w\s/+-]", " ", text)
    tokens = [tok for tok in text.split() if tok]
    changed = True
    while changed and tokens:
        changed = False
        for suffix in _SUFFIX_RUNS:
            n = len(suffix)
            if n <= len(tokens) and tuple(tokens[-n:]) == suffix:
                tokens = tokens[:-n]
                changed = True
                break
    return " ".join(tokens)


def operator_identity(number: str | None, name: str | None) -> str:
    return operator_name_key(name) or (number or "").strip()


def _row_value(row, key: str, default=""):
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def collapse_operators(rows, limit: int = 40) -> list[dict]:
    """One suggestion per company: catalog number wins, then official name."""
    items: list[dict] = []
    for row in rows:
        name = standardize_operator_name(_row_value(row, "name"))
        number = str(_row_value(row, "number") or "").strip()
        try:
            hits = int(_row_value(row, "hits", 1) or 1)
        except (TypeError, ValueError):
            hits = 1
        if not name and not number:
            continue
        items.append(
            {
                "number": number,
                "name": name,
                "hits": hits,
                "key": operator_name_key(name),
            }
        )

    official: dict[str, str] = {}
    for item in items:
        if item["number"] and item["name"] and item["number"] not in official:
            official[item["number"]] = item["name"]

    key_numbers: dict[str, set[str]] = {}
    for item in items:
        if item["number"] and item["key"]:
            key_numbers.setdefault(item["key"], set()).add(item["number"])
    unique_key = {
        key: next(iter(numbers))
        for key, numbers in key_numbers.items()
        if len(numbers) == 1
    }

    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for item in items:
        number = item["number"]
        key = item["key"]
        if not number and key and key in unique_key:
            number = unique_key[key]
        group_id = ("n", number) if number else ("k", key or item["name"])
        if group_id not in groups:
            groups[group_id] = {
                "number": number,
                "name": official.get(number, item["name"]),
                "hits": item["hits"],
            }
            order.append(group_id)
            continue
        groups[group_id]["hits"] += item["hits"]

    # Same display name under two IDs (BPX) is one chip; INC vs LLC stays split.
    name_host: dict[str, tuple] = {}
    kept: list[tuple] = []
    for group_id in order:
        group = groups.get(group_id)
        if not group:
            continue
        std = standardize_operator_name(group["name"])
        if std and std in name_host:
            host = groups[name_host[std]]
            host["hits"] += group["hits"]
            if group["number"] and not host["number"]:
                host["number"] = group["number"]
            del groups[group_id]
            continue
        if std:
            name_host[std] = group_id
        kept.append(group_id)

    out = []
    for group_id in kept:
        item = groups[group_id]
        out.append(
            {
                "number": item["number"] or item["name"],
                "name": item["name"] or item["number"],
            }
        )
        if len(out) >= limit:
            break
    return out


def normalize_stored_operators(conn: sqlite3.Connection) -> int:
    """Uppercase and collapse whitespace on stored operator names once."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (META_KEY,)).fetchone()
    except sqlite3.OperationalError:
        return 0
    if row and (row["value"] if isinstance(row, sqlite3.Row) else row[0]) == "1":
        return 0

    changed = 0
    targets: list[tuple[str, str]] = [("operators_tx", "operator_name")]
    for code in APP_STATES:
        targets.append((operators_table(code), "operator_name"))
    for code in US_STATES:
        targets.append((wells_table(code), "operator"))
        targets.append((permits_table(code), "operator"))

    for table, column in targets:
        try:
            rows = conn.execute(
                f"""
                SELECT rowid, {column} FROM {table}
                WHERE {column} IS NOT NULL AND TRIM({column}) != ''
                  AND (
                    {column} != UPPER({column})
                    OR {column} != TRIM({column})
                    OR {column} LIKE '%  %'
                  )
                """
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for rowid, value in rows:
            normalized = standardize_operator_name(value)
            if normalized == value:
                continue
            conn.execute(
                f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                (normalized, rowid),
            )
            changed += 1

    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (META_KEY, "1"),
    )
    return changed


def canonicalize_stored_operators(conn: sqlite3.Connection, *, force: bool = False) -> int:
    """Rewrite well/permit names to the catalog name for each operator number."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (CANON_META_KEY,)).fetchone()
    except sqlite3.OperationalError:
        return 0
    if not force and row and (row["value"] if isinstance(row, sqlite3.Row) else row[0]) == "1":
        return 0

    changed = 0
    for code in APP_STATES:
        changed += canonicalize_state(conn, code)

    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (CANON_META_KEY, "1"),
    )
    return changed


def canonicalize_state(conn: sqlite3.Connection, state: str) -> int:
    """Rewrite one state's well/permit names to the official name for each number."""
    ot = operators_table(state)
    try:
        catalog = list(conn.execute(f"SELECT operator_number, operator_name FROM {ot}"))
    except sqlite3.OperationalError:
        catalog = []

    changed = 0
    official: dict[str, str] = {}
    for row in catalog:
        number = (row["operator_number"] if isinstance(row, sqlite3.Row) else row[0] or "").strip()
        raw_name = row["operator_name"] if isinstance(row, sqlite3.Row) else row[1]
        name = standardize_operator_name(raw_name)
        if not number or not name:
            continue
        if name != (raw_name or ""):
            conn.execute(
                f"UPDATE {ot} SET operator_name = ? WHERE operator_number = ?",
                (name, number),
            )
            changed += 1
        official[number] = name

    tables = (
        (wells_table(state), "operator", "operator_number"),
        (permits_table(state), "operator", "operator_number"),
    )
    extras: dict[str, list[tuple[str, int]]] = {}
    live_tables: list[tuple[str, str, str]] = []
    for table, name_col, number_col in tables:
        try:
            conn.execute(f"SELECT {name_col} FROM {table} LIMIT 1")
        except sqlite3.OperationalError:
            continue
        live_tables.append((table, name_col, number_col))
        rows = conn.execute(
            f"""
            SELECT TRIM({number_col}) AS number, {name_col} AS name, COUNT(*) AS n
            FROM {table}
            WHERE TRIM(COALESCE({number_col}, '')) != ''
            GROUP BY 1, 2
            """
        ).fetchall()
        for row in rows:
            number = (row["number"] if isinstance(row, sqlite3.Row) else row[0] or "").strip()
            raw = row["name"] if isinstance(row, sqlite3.Row) else row[1]
            count = row["n"] if isinstance(row, sqlite3.Row) else row[2]
            if number and number not in official:
                extras.setdefault(number, []).append((raw, int(count or 0)))

    for number, variants in extras.items():
        picked = _preferred_operator_name(variants)
        if picked:
            official[number] = picked

    key_to_official: dict[str, tuple[str, str]] = {}
    for number, name in official.items():
        key = operator_name_key(name)
        if not key:
            continue
        prev = key_to_official.get(key)
        if prev and prev[0] != number:
            key_to_official[key] = ("", "")
        else:
            key_to_official[key] = (number, name)

    conn.execute("DROP TABLE IF EXISTS _op_canon")
    conn.execute(
        "CREATE TEMP TABLE _op_canon (operator_number TEXT PRIMARY KEY, operator_name TEXT NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO _op_canon(operator_number, operator_name) VALUES (?, ?)",
        list(official.items()),
    )

    for table, name_col, number_col in live_tables:
        cur = conn.execute(
            f"""
            UPDATE {table}
            SET {name_col} = (
                SELECT operator_name FROM _op_canon
                WHERE _op_canon.operator_number = TRIM({table}.{number_col})
            )
            WHERE TRIM(COALESCE({number_col}, '')) != ''
              AND EXISTS (
                SELECT 1 FROM _op_canon
                WHERE _op_canon.operator_number = TRIM({table}.{number_col})
                  AND _op_canon.operator_name != COALESCE({table}.{name_col}, '')
              )
            """
        )
        changed += max(cur.rowcount, 0)

        nameless = conn.execute(
            f"""
            SELECT DISTINCT {name_col} AS operator
            FROM {table}
            WHERE TRIM(COALESCE({number_col}, '')) = ''
              AND TRIM(COALESCE({name_col}, '')) != ''
            """
        ).fetchall()
        attach: list[tuple[str, str, str]] = []
        tidy: list[tuple[str, str]] = []
        for row in nameless:
            raw = row["operator"] if isinstance(row, sqlite3.Row) else row[0]
            hit = key_to_official.get(operator_name_key(raw))
            if hit and hit[0]:
                attach.append((hit[0], hit[1], raw))
                continue
            cleaned = standardize_operator_name(raw)
            if cleaned and cleaned != raw:
                tidy.append((cleaned, raw))
        if attach:
            conn.execute("DROP TABLE IF EXISTS _op_attach")
            conn.execute(
                "CREATE TEMP TABLE _op_attach "
                "(raw TEXT PRIMARY KEY, operator_number TEXT NOT NULL, operator_name TEXT NOT NULL)"
            )
            conn.executemany(
                "INSERT OR IGNORE INTO _op_attach(raw, operator_number, operator_name) VALUES (?, ?, ?)",
                [(raw, number, name) for number, name, raw in attach],
            )
            cur = conn.execute(
                f"""
                UPDATE {table}
                SET {number_col} = (
                    SELECT operator_number FROM _op_attach WHERE _op_attach.raw = {table}.{name_col}
                ),
                {name_col} = (
                    SELECT operator_name FROM _op_attach WHERE _op_attach.raw = {table}.{name_col}
                )
                WHERE TRIM(COALESCE({number_col}, '')) = ''
                  AND EXISTS (SELECT 1 FROM _op_attach WHERE _op_attach.raw = {table}.{name_col})
                """
            )
            changed += max(cur.rowcount, 0)
            conn.execute("DROP TABLE IF EXISTS _op_attach")
        if tidy:
            conn.execute("DROP TABLE IF EXISTS _op_tidy")
            conn.execute("CREATE TEMP TABLE _op_tidy (raw TEXT PRIMARY KEY, operator_name TEXT NOT NULL)")
            conn.executemany(
                "INSERT OR IGNORE INTO _op_tidy(raw, operator_name) VALUES (?, ?)",
                [(raw, name) for name, raw in tidy],
            )
            cur = conn.execute(
                f"""
                UPDATE {table}
                SET {name_col} = (
                    SELECT operator_name FROM _op_tidy WHERE _op_tidy.raw = {table}.{name_col}
                )
                WHERE TRIM(COALESCE({number_col}, '')) = ''
                  AND EXISTS (SELECT 1 FROM _op_tidy WHERE _op_tidy.raw = {table}.{name_col})
                """
            )
            changed += max(cur.rowcount, 0)
            conn.execute("DROP TABLE IF EXISTS _op_tidy")

    conn.execute("DROP TABLE IF EXISTS _op_canon")
    return changed


def _preferred_operator_name(variants: list[tuple[str | None, int]]) -> str:
    picked = ""
    best = (-1, -1, "")
    for raw, count in variants:
        std = standardize_operator_name(raw)
        if not std:
            continue
        score = (int(count or 0), len(std), std)
        if score > best:
            best = score
            picked = std
    return picked
