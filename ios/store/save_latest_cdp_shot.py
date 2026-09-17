import base64
import json
import sys
from pathlib import Path

folder = Path(r"C:\Users\swppa\.cursor\browser-logs")
cands = sorted(folder.glob("cdp-response-Page.captureScreenshot-*.json"), key=lambda p: p.stat().st_mtime)
if not cands:
    raise SystemExit("no cdp screenshots")
data = json.loads(cands[-1].read_text(encoding="utf-8"))
raw = base64.b64decode(data["data"])
dest = Path(sys.argv[1])
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_bytes(raw)
print(dest, dest.stat().st_size)
