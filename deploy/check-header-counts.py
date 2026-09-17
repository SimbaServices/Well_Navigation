"""Print all-state header counts. Run inside the wellnav container."""

from wellnav.repository import WellRepository
from wellnav.states import APP_STATES

repo = WellRepository()
print("all", repo.counts("all"))
for state in APP_STATES:
    print(state, repo.counts(state))
repo.close()
