"""SQLite helper that runs inside the wellnav container. One JSON request, one JSON reply."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(os.environ.get("WELLNAV_DATA", "/app/data")).resolve()
SENSITIVE_COLUMNS = {"password_hash"}
MAX_CELL = 240


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def quote_string(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def format_size(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"


def is_internal(name: str) -> bool:
    lower = name.lower()
    return (
        lower.startswith("sqlite_")
        or lower.endswith("_rtree_node")
        or lower.endswith("_rtree_parent")
        or lower.endswith("_rtree_rowid")
    )


def safe_db(path: str) -> Path:
    raw = Path(path)
    resolved = raw.resolve() if raw.is_absolute() else (DATA_ROOT / raw).resolve()
    try:
        resolved.relative_to(DATA_ROOT)
    except ValueError as exc:
        raise ValueError(f"path outside {DATA_ROOT}") from exc
    if resolved.suffix != ".db":
        raise ValueError("not a sqlite database")
    return resolved


def safe_tmp(path: str) -> Path:
    resolved = Path(path).resolve()
    if resolved.parent not in {Path("/tmp"), Path("/var/tmp"), DATA_ROOT}:
        raise ValueError("temp path must be under /tmp or the data directory")
    return resolved


def display_row(row: dict, names: list[str]) -> dict:
    out = {}
    for name in names:
        value = row.get(name)
        if name in SENSITIVE_COLUMNS and value not in (None, ""):
            out[name] = "••••"
        elif isinstance(value, bytes):
            out[name] = f"<blob {len(value)} bytes>"
        elif isinstance(value, str) and len(value) > MAX_CELL:
            out[name] = value[:MAX_CELL] + "…"
        elif value is None:
            out[name] = ""
        else:
            out[name] = value
    return out


def jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return f"<blob {len(value)} bytes>"
    return str(value)


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def file_info(path: Path) -> dict:
    size = path.stat().st_size if path.exists() else 0
    wal = path.with_name(path.name + "-wal")
    shm = path.with_name(path.name + "-shm")
    wal_size = wal.stat().st_size if wal.exists() else 0
    journal = "WAL" if wal.exists() else "delete"
    tables = 0
    error = None
    if path.exists():
        try:
            conn = open_db(path)
            tables = conn.execute(
                "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()["n"]
            conn.close()
        except sqlite3.Error as exc:
            error = str(exc)
    return {
        "path": str(path),
        "name": path.name,
        "size": size,
        "size_label": format_size(size),
        "wal_size": wal_size,
        "shm_size": shm.stat().st_size if shm.exists() else 0,
        "total_label": format_size(size + wal_size),
        "journal": journal,
        "tables": tables,
        "error": error,
    }


def list_databases() -> list[dict]:
    rows = []
    for path in sorted(DATA_ROOT.glob("*.db"), key=lambda p: p.name.lower()):
        rows.append(file_info(path))
    return rows


def list_tables(path: Path, hide_empty: bool = True, hide_internal: bool = True) -> list[dict]:
    conn = open_db(path)
    items = conn.execute(
        """
        SELECT name, type, sql FROM sqlite_master
        WHERE type IN ('table', 'view')
        ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name COLLATE NOCASE
        """
    ).fetchall()
    out = []
    for row in items:
        name = row["name"]
        if hide_internal and is_internal(name):
            continue
        try:
            count = conn.execute(f"SELECT COUNT(*) AS n FROM {quote_ident(name)}").fetchone()["n"]
            error = None
        except sqlite3.Error as exc:
            count = None
            error = str(exc)
        if hide_empty and not count:
            continue
        out.append({"name": name, "type": row["type"], "count": count, "error": error, "sql": row["sql"] or ""})
    conn.close()
    return out


def columns(path: Path, table: str) -> list[dict]:
    conn = open_db(path)
    cols = []
    for row in conn.execute(f"PRAGMA table_info({quote_ident(table)})"):
        cols.append(
            {
                "cid": row["cid"],
                "name": row["name"],
                "type": row["type"] or "",
                "notnull": bool(row["notnull"]),
                "default": jsonable(row["dflt_value"]),
                "pk": int(row["pk"] or 0),
            }
        )
    conn.close()
    return cols


def _search_clause(cols: list[dict], names: list[str], search: str) -> tuple[str, list]:
    term = (search or "").strip()
    if not term:
        return "", []
    text_cols = [c["name"] for c in cols if (c["type"] or "").upper() in {"", "TEXT", "CHAR", "CLOB", "VARCHAR"}]
    if not text_cols:
        text_cols = names[:8]
    where = " WHERE " + " OR ".join(f"CAST({quote_ident(col)} AS TEXT) LIKE ?" for col in text_cols)
    return where, [f"%{term}%"] * len(text_cols)


def browse(path: Path, table: str, offset=0, limit=100, order_by=None, descending=False, search="") -> dict:
    cols = columns(path, table)
    names = [c["name"] for c in cols]
    where, args = _search_clause(cols, names, search)
    order = ""
    if order_by and order_by in names:
        order = f" ORDER BY {quote_ident(order_by)} {'DESC' if descending else 'ASC'}"
    conn = open_db(path)
    ident = quote_ident(table)
    total = conn.execute(f"SELECT COUNT(*) AS n FROM {ident}{where}", args).fetchone()["n"]
    rows = conn.execute(
        f"SELECT * FROM {ident}{where}{order} LIMIT ? OFFSET ?",
        [*args, int(limit), int(offset)],
    ).fetchall()
    raw = [{key: jsonable(row[key]) for key in names} for row in rows]
    conn.close()
    return {
        "columns": cols,
        "rows": [display_row(row, names) for row in raw],
        "raw": raw,
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def execute_sql(path: Path, sql: str, max_rows: int = 500) -> dict:
    script = (sql or "").strip()
    if not script:
        raise ValueError("SQL is empty")
    conn = open_db(path)
    cur = conn.execute(script)
    if cur.description:
        names = [item[0] for item in cur.description]
        fetched = cur.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = fetched[:max_rows]
        raw = [{key: jsonable(row[key] if isinstance(row, sqlite3.Row) else row[i]) for i, key in enumerate(names)} for row in rows]
        conn.close()
        return {
            "kind": "rows",
            "columns": names,
            "rows": [display_row(row, names) for row in raw],
            "raw": raw,
            "truncated": truncated,
            "rowcount": len(rows),
        }
    conn.commit()
    rowcount = cur.rowcount
    conn.close()
    return {"kind": "write", "rowcount": rowcount, "truncated": False}


def delete_rows(path: Path, table: str, rows: list[dict]) -> int:
    cols = columns(path, table)
    keys = [c["name"] for c in cols if c["pk"]]
    if not keys:
        raise ValueError(f"{table} has no primary key; delete via SQL instead")
    conn = open_db(path)
    deleted = 0
    for row in rows:
        clause = " AND ".join(f"{quote_ident(key)} IS ?" for key in keys)
        args = [row.get(key) for key in keys]
        cur = conn.execute(f"DELETE FROM {quote_ident(table)} WHERE {clause}", args)
        deleted += cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def export_csv(path: Path, table: str, dest: Path, search: str = "") -> int:
    cols = columns(path, table)
    names = [c["name"] for c in cols]
    where, args = _search_clause(cols, names, search)
    conn = open_db(path)
    cur = conn.execute(f"SELECT * FROM {quote_ident(table)}{where}", args)
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        for row in cur:
            writer.writerow({key: jsonable(row[key]) for key in names})
            written += 1
    conn.close()
    return written


def handle(req: dict):
    op = req.get("op")
    if op == "ping":
        return {"pong": True, "data_root": str(DATA_ROOT)}
    if op == "list_databases":
        return list_databases()
    path = safe_db(req["path"]) if "path" in req else None
    if op == "file_info":
        return file_info(path)
    if op == "list_tables":
        return list_tables(path, hide_empty=bool(req.get("hide_empty", True)))
    if op == "columns":
        return columns(path, req["table"])
    if op == "browse":
        return browse(
            path,
            req["table"],
            offset=int(req.get("offset") or 0),
            limit=int(req.get("limit") or 100),
            order_by=req.get("order_by"),
            descending=bool(req.get("descending")),
            search=req.get("search") or "",
        )
    if op == "execute_sql":
        return execute_sql(path, req.get("sql") or "", max_rows=int(req.get("max_rows") or 500))
    if op == "delete_rows":
        return delete_rows(path, req["table"], req.get("rows") or [])
    if op == "export_csv":
        dest = safe_tmp(req["dest"])
        return export_csv(path, req["table"], dest, search=req.get("search") or "")
    if op == "integrity":
        conn = open_db(path)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        return str(row[0] if row else "unknown")
    if op == "checkpoint":
        conn = open_db(path)
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        conn.close()
        return f"busy={row[0]} log={row[1]} checkpointed={row[2]}"
    if op == "vacuum":
        conn = open_db(path)
        conn.execute("VACUUM")
        conn.close()
        return "ok"
    if op == "backup":
        dest = safe_tmp(req["dest"])
        if dest.exists():
            dest.unlink()
        conn = open_db(path)
        conn.execute(f"VACUUM INTO {quote_string(str(dest))}")
        conn.close()
        return {"dest": str(dest), "size": dest.stat().st_size}
    if op == "replace_with":
        source = safe_tmp(req["source"])
        if not source.exists():
            raise FileNotFoundError(str(source))
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.before-restore-{stamp}.db")
        if path.exists():
            path.replace(backup)
        dest = path
        dest.write_bytes(source.read_bytes())
        for extra in (path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            if extra.exists():
                extra.unlink()
        return {"replaced": str(path), "backup": str(backup)}
    raise ValueError(f"unknown op {op!r}")


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print(json.dumps({"ok": False, "error": "empty request"}))
        return 1
    try:
        req = json.loads(raw)
        data = handle(req)
        print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
