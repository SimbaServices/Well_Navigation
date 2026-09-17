"""Write the Contentsquare tag ID into the host .env without printing other values."""

from __future__ import annotations

import re
import sys
from pathlib import Path

CONTENTSQUARE_ID_RE = re.compile(r"^[a-fA-F0-9]{10,16}$")


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
        print("usage: set-contentsquare-id.py <contentsquare-tag-id>", file=sys.stderr)
        return 2
    value = sys.argv[1].strip().lower()
    if not CONTENTSQUARE_ID_RE.match(value):
        print("id must be a Contentsquare tag (10-16 hex chars)", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    upsert(root / ".env", "WELLNAV_CONTENTSQUARE_ID", value)
    print("contentsquare_id_set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
