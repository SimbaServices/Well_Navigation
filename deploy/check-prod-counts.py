from wellnav.repository import REPO

for state in ("tx", "nm", "ok", "la", "all"):
    print(state, REPO.counts(state))
