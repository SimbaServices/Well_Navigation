"""Punctuation-insensitive text matching for search ("Buffalo 2LL" finds "BUFFALO #2LL")."""

from __future__ import annotations

import re
import sqlite3

# Anything that is not a letter, digit or whitespace (any script, incl. smart quotes/dashes)
# is deleted; underscore counts as punctuation.
_PUNCT = re.compile(r"[^\w\s]|_")


def norm_text(value) -> str | None:
    """Lowercase, delete punctuation/special characters, collapse whitespace, trim."""
    if value is None:
        return None
    return " ".join(_PUNCT.sub("", str(value).lower()).split())


def register(conn: sqlite3.Connection) -> None:
    conn.create_function("wn_norm", 1, norm_text, deterministic=True)


def match_sql(column: str, needle: str) -> tuple[str, list]:
    """SQL (and args) for: column contains needle, ignoring punctuation and case.

    Matches if EITHER the normalized text matches (fast LIKE pre-check on the needle's
    ASCII letters/digits as an in-order subsequence, then wn_norm() on the survivors)
    OR the original plain case-insensitive LIKE on the raw text matches, so results
    never fall below the pre-normalization behavior.
    """
    raw = (needle or "").strip()
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    raw_sql = f"{column} LIKE ? ESCAPE '\\'"
    norm = norm_text(raw) or ""
    if not norm:
        return raw_sql, [f"%{escaped}%"]
    ascii_key = "".join(ch for ch in norm if ch.isascii() and ch != " ")
    pre = "%" + "%".join(ascii_key) + "%" if ascii_key else "%"
    sql = f"(({column} LIKE ? AND wn_norm({column}) LIKE ?) OR {raw_sql})"
    return sql, [pre, f"%{norm}%", f"%{escaped}%"]
