"""Write analytics IDs into the host .env without printing other values."""

from __future__ import annotations

import re
import sys
from pathlib import Path

HOTJAR_ID_RE = re.compile(r"^\d{5,12}$")
CLARITY_ID_RE = re.compile(r"^[a-fA-F0-9]{8,20}$")


def upsert(path: Path, name: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    prefix = f"{name}="
    written = False
    out = []
    for line in lines:
        if line.strip().startswith(prefix):
            if not written:
                out.append(f"{name}={value}")
                written = True
            continue
        out.append(line)
    if not written:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{name}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: set-hotjar-id.py <hotjar-site-id-or-clarity-tag>", file=sys.stderr)
        return 2
    value = sys.argv[1].strip()
    root = Path(__file__).resolve().parents[1]
    env = root / ".env"
    if HOTJAR_ID_RE.match(value):
        upsert(env, "WELLNAV_HOTJAR_ID", value)
        print("hotjar_id_set")
        return 0
    if CLARITY_ID_RE.match(value):
        upsert(env, "WELLNAV_CLARITY_ID", value.lower())
        print("clarity_id_set")
        return 0
    print("id must be a numeric Hotjar site ID or a Clarity tag", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
