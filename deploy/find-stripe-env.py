"""Find Stripe-related env files on the host without printing secrets."""

from pathlib import Path

roots = [
    Path("/home/wellnav"),
    Path("/root"),
    Path("/etc"),
]
needles = ("STRIPE", "sk_live", "sk_test", "whsec_", "mpsolutions")
hits = []
for root in roots:
    if not root.exists():
        continue
    for path in root.rglob("*"):
        if not path.is_file() or path.stat().st_size > 200_000:
            continue
        name = path.name.lower()
        if not (
            name.startswith(".env")
            or "stripe" in name
            or name.endswith(".env")
            or "secret" in name
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(needle.lower() in text.lower() for needle in needles):
            hits.append(str(path))
print("hits=" + (",".join(hits) if hits else "none"))
