from wellnav.repository import REPO

print("all", REPO.counts("all"))
for state in ("tx", "nm", "ok", "la"):
    print(state, REPO.counts(state))
