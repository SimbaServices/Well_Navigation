import urllib.request

from wellnav.db import connect


def page(path: str) -> str:
    return urllib.request.urlopen(f"http://127.0.0.1:5050{path}", timeout=10).read().decode()


def main() -> None:
    login = page("/login")
    if 'name="email"' not in login or "Email me a code" not in login:
        raise SystemExit("FAIL login")
    if "Mobile number" in login:
        raise SystemExit("FAIL login still asks for a phone")
    print("OK login")
    register = page("/register")
    if 'name="email"' not in register or "company domain" not in register:
        raise SystemExit("FAIL register")
    if "Mobile number" in register:
        raise SystemExit("FAIL register still asks for a phone")
    print("OK register")
    forgot = page("/login/forgot")
    if 'name="email"' not in forgot or "email code" not in forgot:
        raise SystemExit("FAIL forgot")
    print("OK forgot")
    conn = connect()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    for required in ("email", "org_id", "role"):
        if required not in cols:
            raise SystemExit(f"FAIL missing {required}")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "organizations" not in tables:
        raise SystemExit("FAIL organizations table")
    print("OK schema")
    print("ALL_OK")


if __name__ == "__main__":
    main()
