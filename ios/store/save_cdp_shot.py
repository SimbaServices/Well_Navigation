import base64
import json
import sys
from pathlib import Path

src = Path(sys.argv[1])
dest = Path(sys.argv[2])
data = json.loads(src.read_text(encoding="utf-8"))
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_bytes(base64.b64decode(data["data"]))
print(dest, dest.stat().st_size)
