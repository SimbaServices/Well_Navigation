#!/bin/bash
set -euo pipefail
curl -sS -o /tmp/nm_search.html -w "%{http_code} %{size_download}\n" "http://127.0.0.1:5050/search?q=DUSTIN&state=nm"
python3 - <<'PY'
from pathlib import Path
text = Path("/tmp/nm_search.html").read_text(encoding="utf-8", errors="replace")
print("has_dustin", "DUSTIN" in text)
print("has_login", "login" in text.lower())
print("snippet", " ".join(text[200:420].split()))
PY
rm -f /home/wellnav/Well_Navigation/nm_official.db
echo "removed nm_official.db"
