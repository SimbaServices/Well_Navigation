"""Local SQLite operations for the desktop database manager."""

from __future__ import annotations

import csv
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from wellnav.db import ROOT

DATA_DIR = ROOT / "data"
SENSITIVE_COLUMNS = {"password_hash"}
INTERNAL_PREFIXES = ("sqlite_", "idx_")
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
    return lower.startswith("sqlite_") or lower.endswith("_rtree_node") or lower.endswith("_rtree_parent") or lower.endswith(
        "_rtree_rowid"
    )


class LocalStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir or DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._conns: dict[str, sqlite3.Connection] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            for conn in self._conns.values():
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._conns.clear()

    def close_db(self, path: Path) -> None:
        key = str(path.resolve())
        with self._lock:
            conn = self._conns.pop(key, None)
        if conn is not None:
            conn.close()

    def conn(self, path: Path) -> sqlite3.Connection:
        key = str(path.resolve())
        with self._lock:
            conn = self._conns.get(key)
            if conn is None:
                conn = sqlite3.connect(str(path), timeout=60, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=60000")
                conn.execute("PRAGMA foreign_keys=ON")
                self._conns[key] = conn
            return conn

    def list_databases(self, extra: list[Path] | None = None) -> list[dict]:
        seen: set[str] = set()
        rows: list[dict] = []
        paths = list(self.data_dir.glob("*.db"))
        if extra:
            paths.extend(extra)
        for path in sorted(paths, key=lambda p: p.name.lower()):
            resolved = str(path.resolve())
            if resolved in seen or not path.exists():
                continue
            seen.add(resolved)
            rows.append(self.file_info(path))
        return rows

    def file_info(self, path: Path) -> dict:
        path = path.resolve()
        size = path.stat().st_size if path.exists() else 0
        wal = path.with_name(path.name + "-wal")
        shm = path.with_name(path.name + "-shm")
        wal_size = wal.stat().st_size if wal.exists() else 0
        shm_size = shm.stat().st_size if shm.exists() else 0
        journal = "WAL" if wal.exists() else "delete"
        try:
            conn = self.conn(path)
            tables = conn.execute(
                "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()["n"]
        except sqlite3.Error as exc:
            return {
                "path": path,
                "name": path.name,
                "size": size,
                "size_label": format_size(size),
                "wal_size": wal_size,
                "shm_size": shm_size,
                "total_label": format_size(size + wal_size),
                "journal": journal,
                "tables": 0,
                "error": str(exc),
            }
        return {
            "path": path,
            "name": path.name,
            "size": size,
            "size_label": format_size(size),
            "wal_size": wal_size,
            "shm_size": shm_size,
            "total_label": format_size(size + wal_size),
            "journal": journal,
            "tables": tables,
            "error": None,
        }

    def list_tables(self, path: Path, *, hide_empty: bool = True, hide_internal: bool = True) -> list[dict]:
        conn = self.conn(path)
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
            except sqlite3.Error as exc:
                count = None
                error = str(exc)
            else:
                error = None
            if hide_empty and not count:
                continue
            out.append(
                {
                    "name": name,
                    "type": row["type"],
                    "count": count,
                    "error": error,
                    "sql": row["sql"] or "",
                }
            )
        return out

    def columns(self, path: Path, table: str) -> list[dict]:
        conn = self.conn(path)
        cols = []
        for row in conn.execute(f"PRAGMA table_info({quote_ident(table)})"):
            cols.append(
                {
                    "cid": row["cid"],
                    "name": row["name"],
                    "type": row["type"] or "",
                    "notnull": bool(row["notnull"]),
                    "default": row["dflt_value"],
                    "pk": int(row["pk"] or 0),
                }
            )
        return cols

    def browse(
        self,
        path: Path,
        table: str,
        *,
        offset: int = 0,
        limit: int = 100,
        order_by: str | None = None,
        descending: bool = False,
        search: str = "",
    ) -> dict:
        cols = self.columns(path, table)
        names = [c["name"] for c in cols]
        where = ""
        args: list = []
        term = (search or "").strip()
        if term:
            text_cols = [c["name"] for c in cols if (c["type"] or "").upper() in {"", "TEXT", "CHAR", "CLOB", "VARCHAR"}]
            if not text_cols:
                text_cols = names[:8]
            clauses = [f"CAST({quote_ident(col)} AS TEXT) LIKE ?" for col in text_cols]
            where = " WHERE " + " OR ".join(clauses)
            args = [f"%{term}%"] * len(text_cols)
        order = ""
        if order_by and order_by in names:
            direction = "DESC" if descending else "ASC"
            order = f" ORDER BY {quote_ident(order_by)} {direction}"
        conn = self.conn(path)
        ident = quote_ident(table)
        total = conn.execute(f"SELECT COUNT(*) AS n FROM {ident}{where}", args).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM {ident}{where}{order} LIMIT ? OFFSET ?",
            [*args, int(limit), int(offset)],
        ).fetchall()
        return {
            "columns": cols,
            "rows": [self._display_row(dict(row), names) for row in rows],
            "raw": [dict(row) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def execute_sql(self, path: Path, sql: str, *, max_rows: int = 500) -> dict:
        script = sql.strip()
        if not script:
            raise ValueError("SQL is empty")
        conn = self.conn(path)
        cur = conn.execute(script)
        if cur.description:
            names = [item[0] for item in cur.description]
            fetched = cur.fetchmany(max_rows + 1)
            truncated = len(fetched) > max_rows
            rows = fetched[:max_rows]
            return {
                "kind": "rows",
                "columns": names,
                "rows": [self._display_row(dict(zip(names, row)), names) for row in rows],
                "raw": [dict(zip(names, row)) for row in rows],
                "truncated": truncated,
                "rowcount": len(rows),
            }
        conn.commit()
        return {"kind": "write", "rowcount": cur.rowcount, "truncated": False}

    def integrity(self, path: Path) -> str:
        row = self.conn(path).execute("PRAGMA integrity_check").fetchone()
        return str(row[0] if row else "unknown")

    def checkpoint(self, path: Path) -> str:
        row = self.conn(path).execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return f"busy={row[0]} log={row[1]} checkpointed={row[2]}"

    def vacuum(self, path: Path) -> None:
        self.conn(path).execute("VACUUM")

    def backup(self, path: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        target = str(dest.resolve()).replace("\\", "/")
        self.conn(path).execute(f"VACUUM INTO {quote_string(target)}")

    def replace_with(self, path: Path, source: Path) -> None:
        if not source.exists():
            raise FileNotFoundError(source)
        self.close_db(path)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.before-restore-{stamp}.db")
        if path.exists():
            path.replace(backup)
        _copy_file(source, path)
        for extra in (path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            if extra.exists():
                extra.unlink()

    def export_csv(self, path: Path, table: str, dest: Path, *, search: str = "") -> int:
        cols = self.columns(path, table)
        names = [c["name"] for c in cols]
        where = ""
        args: list = []
        term = (search or "").strip()
        if term:
            text_cols = [c["name"] for c in cols if (c["type"] or "").upper() in {"", "TEXT", "CHAR", "CLOB", "VARCHAR"}]
            if not text_cols:
                text_cols = names[:8]
            where = " WHERE " + " OR ".join(
                f"CAST({quote_ident(col)} AS TEXT) LIKE ?" for col in text_cols
            )
            args = [f"%{term}%"] * len(text_cols)
        dest.parent.mkdir(parents=True, exist_ok=True)
        conn = self.conn(path)
        cur = conn.execute(f"SELECT * FROM {quote_ident(table)}{where}", args)
        written = 0
        with dest.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
            writer.writeheader()
            for row in cur:
                writer.writerow({key: row[key] for key in names})
                written += 1
        return written

    def delete_rows(self, path: Path, table: str, rows: list[dict]) -> int:
        cols = self.columns(path, table)
        keys = [c["name"] for c in cols if c["pk"]]
        if not keys:
            raise ValueError(f"{table} has no primary key; delete via SQL instead")
        conn = self.conn(path)
        deleted = 0
        for row in rows:
            clause = " AND ".join(f"{quote_ident(key)} IS ?" for key in keys)
            args = [row.get(key) for key in keys]
            cur = conn.execute(f"DELETE FROM {quote_ident(table)} WHERE {clause}", args)
            deleted += cur.rowcount
        conn.commit()
        return deleted

    def _display_row(self, row: dict, names: list[str]) -> dict:
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


def _copy_file(source: Path, dest: Path) -> None:
    dest.write_bytes(Path(source).read_bytes())
