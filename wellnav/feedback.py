"""Organization testing-feedback board.

Notes are visible to everyone in the same organization and to nobody else.
They stay on the board if the author later deletes their account, so a test
report is not lost with the login. There is no foreign key to users for that
reason: account deletion must keep working.
"""

from __future__ import annotations

from wellnav import accounts

KINDS = {
    "bug": "Bug",
    "idea": "Idea",
    "question": "Question",
    "note": "Note",
}
MAX_BODY = 2000
MAX_PLACE = 80
LIST_LIMIT = 100


def _conn():
    return accounts._conn()


def _clean(raw: object) -> str:
    return str(raw or "").replace("\x00", "").strip()


def _public(row: dict, user: dict) -> dict:
    kind = row["kind"] if row["kind"] in KINDS else "note"
    author_id = row.get("user_id")
    mine = author_id is not None and int(author_id) == int(user["id"])
    return {
        "id": int(row["id"]),
        "org_id": int(row["org_id"]),
        "user_id": int(author_id) if author_id is not None else None,
        "author": row["author_email"],
        "kind": kind,
        "kind_label": KINDS[kind],
        "place": row.get("place") or "",
        "body": row["body"],
        "created_at": accounts._public_stamp(row.get("created_at")),
        "mine": mine,
        "can_remove": mine or bool(user.get("is_admin")),
    }


class FeedbackBoard:
    def list_for(self, user: dict) -> tuple[list[dict], bool]:
        org_id = user.get("org_id")
        if not org_id:
            return [], False
        rows = _conn().execute(
            """
            SELECT id, org_id, user_id, author_email, kind, place, body, created_at
            FROM org_feedback
            WHERE org_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (int(org_id), LIST_LIMIT + 1),
        ).fetchall()
        truncated = len(rows) > LIST_LIMIT
        visible = rows[:LIST_LIMIT]
        return [_public(dict(row), user) for row in visible], truncated

    def post(
        self,
        user: dict,
        *,
        kind: str,
        body: str,
        place: str = "",
    ) -> tuple[dict | None, str | None]:
        org_id = user.get("org_id")
        if not org_id:
            return None, "This account is not in an organization yet."
        kind_key = _clean(kind).lower()
        if kind_key not in KINDS:
            return None, "Pick bug, idea, question, or note."
        text = _clean(body)
        if not text:
            return None, "Write what you found."
        if len(text) > MAX_BODY:
            return None, f"Keep the note under {MAX_BODY} characters."
        where = _clean(place)
        if len(where) > MAX_PLACE:
            return None, f"Keep the location under {MAX_PLACE} characters."
        author = _clean(user.get("email") or user.get("username")) or "unknown"
        conn = _conn()
        cur = conn.execute(
            """
            INSERT INTO org_feedback(
                org_id, user_id, author_email, kind, place, body, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (int(org_id), int(user["id"]), author, kind_key, where, text, accounts.utcnow_iso()),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, org_id, user_id, author_email, kind, place, body, created_at
            FROM org_feedback WHERE id = ?
            """,
            (int(cur.lastrowid),),
        ).fetchone()
        return _public(dict(row), user), None

    def delete(self, user: dict, message_id: int) -> tuple[bool, str | None]:
        org_id = user.get("org_id")
        if not org_id:
            return False, "This account is not in an organization yet."
        row = _conn().execute(
            "SELECT id, org_id, user_id FROM org_feedback WHERE id = ?",
            (int(message_id),),
        ).fetchone()
        if not row or int(row["org_id"]) != int(org_id):
            return False, "That note is not on your organization's board."
        author_id = row["user_id"]
        mine = author_id is not None and int(author_id) == int(user["id"])
        if not mine and not user.get("is_admin"):
            return False, "You can remove your own notes. An admin can remove any note."
        conn = _conn()
        conn.execute("DELETE FROM org_feedback WHERE id = ? AND org_id = ?", (int(message_id), int(org_id)))
        conn.commit()
        return True, None


FEEDBACK = FeedbackBoard()
