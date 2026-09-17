from pathlib import Path

from wellnav.db import ROOT
from wellnav.recordings import clarity_project_id, hotjar_site_id

env = ROOT / ".env"
print("root", ROOT)
print("env_exists", env.is_file())
print("env_has_clarity", any(line.startswith("WELLNAV_CLARITY_ID=") for line in env.read_text(encoding="utf-8").splitlines()) if env.is_file() else False)
print("clarity_set", bool(clarity_project_id()))
print("hotjar_set", bool(hotjar_site_id()))
print("clarity_len", len(clarity_project_id()))
