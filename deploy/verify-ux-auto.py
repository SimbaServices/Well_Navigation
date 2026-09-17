"""Confirm auto session recording is wired on the live host."""

from pathlib import Path

root = Path("/home/wellnav/Well_Navigation")
index = (root / "templates" / "index.html").read_text(encoding="utf-8")
js = (root / "static" / "js" / "ux-record.js").read_text(encoding="utf-8")
assert "Record UX" not in index
assert "data-ux-start" not in index
assert "ux_capture.html" in index
assert "rrweb.record" in js
assert "getDisplayMedia" not in js
assert (root / "static" / "vendor" / "rrweb.min.js").is_file()
print("host_templates_ok")
