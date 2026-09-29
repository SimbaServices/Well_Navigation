"""Per-user search and location settings. Stored on the account, not the device."""

from __future__ import annotations

import sqlite3

DEFAULT_SEARCH_PREFS = {
    "state": "tx",
    "scope": "wells",
    "mode": "name",
    "pipe_mode": "operator",
    "disp_mode": "name",
}

_CHOICES = {
    "state": {"tx", "nm", "ok", "la", "all"},
    "scope": {"wells", "pipelines", "disposal"},
    "mode": {"name", "api", "operator"},
    "pipe_mode": {"operator", "name"},
    "disp_mode": {"name", "operator", "permit"},
}


def default_search_prefs() -> dict[str, str]:
    return dict(DEFAULT_SEARCH_PREFS)


def get_search_prefs(conn: sqlite3.Connection, user_id: int) -> dict[str, str]:
    prefs = default_search_prefs()
    try:
        row = conn.execute(
            "SELECT state, scope, mode, pipe_mode, disp_mode FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return prefs
    if not row:
        return prefs
    data = dict(row) if isinstance(row, sqlite3.Row) else {
        "state": row[0],
        "scope": row[1],
        "mode": row[2],
        "pipe_mode": row[3],
        "disp_mode": row[4],
    }
    for key, allowed in _CHOICES.items():
        value = str(data.get(key) or "").strip().lower()
        if value in allowed:
            prefs[key] = value
    return prefs


def save_search_prefs(conn: sqlite3.Connection, user_id: int, updates: dict) -> dict[str, str]:
    current = get_search_prefs(conn, user_id)
    for key, allowed in _CHOICES.items():
        if key not in updates:
            continue
        value = str(updates.get(key) or "").strip().lower()
        if value in allowed:
            current[key] = value
    conn.execute(
        """
        INSERT INTO user_settings(user_id, state, scope, mode, pipe_mode, disp_mode, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            state = excluded.state,
            scope = excluded.scope,
            mode = excluded.mode,
            pipe_mode = excluded.pipe_mode,
            disp_mode = excluded.disp_mode,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            current["state"],
            current["scope"],
            current["mode"],
            current["pipe_mode"],
            current["disp_mode"],
        ),
    )
    conn.commit()
    return current
