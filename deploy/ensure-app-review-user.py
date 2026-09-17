"""Create or reset the complimentary App Review login. Password is written to a side file, not stdout."""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

from wellnav.accounts import USERS
from wellnav.auth import validate_password

EMAIL = "appreview@simba.services"
SECRET_PATH = Path("/tmp/wellnav-app-review-login.txt")


def main() -> int:
    password = secrets.token_urlsafe(10) + "-Wn1"
    error = validate_password(password)
    if error:
        print(error, file=sys.stderr)
        return 2
    existing = USERS.by_username(EMAIL)
    if existing:
        user, error = USERS.set_password(int(existing["id"]), password)
        if error or not user:
            print(error or "reset_failed", file=sys.stderr)
            return 1
        status = "reset"
    else:
        user, error = USERS.register(EMAIL, password)
        if error or not user:
            print(error or "create_failed", file=sys.stderr)
            return 1
        status = "created"
    SECRET_PATH.write_text(f"email={EMAIL}\npassword={password}\n", encoding="utf-8")
    SECRET_PATH.chmod(0o600)
    print(f"app_review_user={status}")
    print(f"app_review_email={EMAIL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
