from pathlib import Path
import json
import urllib.request

p = Path("/app/data/pipelines.db")
print("db_exists", p.exists(), "bytes", p.stat().st_size if p.exists() else 0)
try:
    from wellnav.pipelines import stats
    print("stats", stats())
except Exception as exc:
    print("stats_error", type(exc).__name__, exc)

for url in (
    "http://127.0.0.1:5050/pipelines?bbox=-102.2,31.7,-101.8,32.0&z=10&abandoned=0",
    "http://127.0.0.1:5050/pipelines?z=8&abandoned=0",
):
    try:
        with urllib.request.urlopen(url) as resp:
            body = resp.read()
            data = json.loads(body)
            meta = data.get("meta") or {}
            print("url", url)
            print("status", resp.status, "bytes", len(body), "features", len(data.get("features") or []), "stored", meta.get("stored"))
            if data.get("error"):
                print("error", data["error"])
    except Exception as exc:
        print("fetch_error", url, type(exc).__name__, exc)
