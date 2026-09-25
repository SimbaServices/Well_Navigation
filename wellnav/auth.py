"""Session auth: scrypt password hashes, email OTP, and signed cookie sessions."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from starlette.requests import Request

from wellnav.db import ROOT

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,31}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PUBLIC_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.uk",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "mail.com",
        "yandex.com",
        "zoho.com",
        "fastmail.com",
        "icloud.com",
    }
)
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SESSION_COOKIE = "wellnav"
# Stay signed in across browser and app restarts until the user signs out.
# Browsers cap a persistent cookie at 400 days. SessionMiddleware reissues it
# on each response, so an active session keeps rolling forward.
SESSION_MAX_AGE = 60 * 60 * 24 * 400
OTP_TTL_SECONDS = 10 * 60
OTP_RESEND_SECONDS = 60
OTP_MAX_SENDS_PER_HOUR = 5
OTP_MAX_ATTEMPTS = 5
OTP_SESSION_KEYS = ("otp_id", "otp_purpose", "pending_signup", "pending_uid", "auth_next")
PUBLIC_PATHS = {
    "/login",
    "/login/verify",
    "/login/resend",
    "/login/forgot",
    "/login/forgot/verify",
    "/login/forgot/resend",
    "/login/forgot/password",
    "/register",
    "/register/verify",
    "/register/resend",
    "/logout",
    "/healthz",
    "/privacy",
    "/terms",
    "/sw.js",
    "/manifest.webmanifest",
    "/billing/webhook",
    "/billing/success",
}
PUBLIC_PREFIXES = ("/static/",)
JSON_PATHS = {"/pipelines", "/disposal", "/account/wait-prefs"}
JSON_PREFIXES = ("/pipelines/owner", "/pipelines/segment", "/disposal/wait/")
DUMMY_PASSWORD_HASH = "scrypt$" + ("00" * 16) + "$" + ("00" * 32)


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


def normalize_email(raw: str) -> str | None:
    text = (raw or "").strip().lower()
    if len(text) > 254 or not EMAIL_RE.match(text):
        return None
    local, domain = text.split("@", 1)
    if not local or domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return None
    return text


def validate_email(raw: str) -> tuple[str | None, str | None]:
    email = normalize_email(raw)
    if not email:
        return None, "Enter a valid email address."
    return email, None


def validate_username(username: str) -> str | None:
    _, error = validate_email(username)
    return error


def email_domain(email: str) -> str:
    text = (email or "").strip().lower()
    if "@" not in text:
        return ""
    return text.split("@", 1)[1]


def org_key(email: str) -> str:
    domain = email_domain(email)
    if not domain or domain in PUBLIC_EMAIL_DOMAINS:
        return (email or "").strip().lower()
    return domain


def mask_email(email: str | None) -> str:
    text = (email or "").strip()
    if "@" not in text:
        return "your email"
    local, domain = text.split("@", 1)
    hidden = local[0] + ("•" * min(3, max(1, len(local) - 1)))
    return f"{hidden}@{domain}"


def validate_password(password: str) -> str | None:
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if len(password) > 128:
        return "Password is too long."
    return None


def normalize_phone(raw: str) -> str | None:
    text = (raw or "").strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if text.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    return None


def validate_phone(raw: str) -> tuple[str | None, str | None]:
    phone = normalize_phone(raw)
    if not phone:
        return None, "Enter a valid mobile number, including area code."
    return phone, None


def mask_phone(phone: str | None) -> str:
    if phone and "@" in phone:
        return mask_email(phone)
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 4:
        return "your phone"
    return f"the number ending in {digits[-4:]}"


def hash_otp(code: str) -> str:
    salt = os.urandom(16)
    digest = hmac.new(session_secret().encode("utf-8"), salt + code.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"otp${salt.hex()}${digest}"


def verify_otp(code: str, stored: str) -> bool:
    try:
        kind, salt_hex, digest = stored.split("$", 2)
    except ValueError:
        return False
    if kind != "otp" or not re.fullmatch(r"\d{6}", (code or "").strip()):
        return False
    expected = hmac.new(
        session_secret().encode("utf-8"),
        bytes.fromhex(salt_hex) + code.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, digest)


def new_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def wants_json(request: Request) -> bool:
    path = request.url.path
    if path in JSON_PATHS or any(path.startswith(prefix) for prefix in JSON_PREFIXES):
        return True
    # /disposal/{site_id}/wait summary + create
    if path.startswith("/disposal/") and path.endswith("/wait"):
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


def safe_next(raw: str | None) -> str:
    path = (raw or "/").strip() or "/"
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path


def clear_otp_session(request: Request) -> None:
    for key in OTP_SESSION_KEYS:
        request.session.pop(key, None)


def current_user_id(request: Request) -> int | None:
    raw = request.session.get("uid")
    try:
        uid = int(raw)
    except (TypeError, ValueError):
        return None
    return uid if uid > 0 else None


def login_user(request: Request, user_id: int, username: str) -> None:
    request.session.clear()
    from wellnav.accounts import USERS
    version = USERS.rotate_session_version(user_id)
    request.session["uid"] = user_id
    request.session["username"] = username
    request.session["sv"] = version
    USERS.record_login(user_id)


def session_matches(request: Request, user: dict | None) -> bool:
    if not user:
        return False
    try:
        cookie = int(request.session.get("sv"))
    except (TypeError, ValueError):
        return False
    return cookie == int(user.get("session_version") or 0)


def logout_user(request: Request) -> None:
    request.session.clear()
