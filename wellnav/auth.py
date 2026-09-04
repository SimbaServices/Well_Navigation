"""Session auth: scrypt password hashes and signed cookie sessions."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from starlette.requests import Request

from wellnav.db import ROOT

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,31}$")
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SESSION_COOKIE = "wellnav"


def session_secret() -> str:
    env = (os.environ.get("WELLNAV_SECRET") or "").strip()
    if env:
        return env
    path = ROOT / "data" / ".session_secret"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    path.write_text(secret, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return secret


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, salt_hex, digest_hex = stored.split("$", 2)
    except ValueError:
        return False
    if kind != "scrypt":
        return False
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt_hex),
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def validate_username(username: str) -> str | None:
    name = (username or "").strip()
    if not USERNAME_RE.match(name):
        return "Username must be 3–32 characters: start with a letter, then letters, digits, or _."
    return None


def validate_password(password: str) -> str | None:
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if len(password) > 128:
        return "Password is too long."
    return None


def current_user_id(request: Request) -> int | None:
    raw = request.session.get("uid")
    try:
        uid = int(raw)
    except (TypeError, ValueError):
        return None
    return uid if uid > 0 else None


def login_user(request: Request, user_id: int, username: str) -> None:
    request.session.clear()
    request.session["uid"] = user_id
    request.session["username"] = username


def logout_user(request: Request) -> None:
    request.session.clear()
