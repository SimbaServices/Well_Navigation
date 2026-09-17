import json
import urllib.request

urls = [
    "http://127.0.0.1:5050/pipelines?z=6&abandoned=0",
    "http://127.0.0.1:5050/pipelines?z=8&abandoned=0",
    "http://127.0.0.1:5050/pipelines?bbox=-102.5,31.5,-101.5,32.2&z=10&abandoned=0",
]
for url in urls:
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read())
        meta = data.get("meta") or {}
        print(
            url,
            "features",
            len(data.get("features") or []),
            "stored",
            meta.get("stored"),
            "truncated",
            meta.get("truncated"),
        )
