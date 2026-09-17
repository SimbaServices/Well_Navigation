"""Run inside the wellnav container."""

from wellnav.repository import WellRepository

repo = WellRepository()
print("tx_counts", repo.counts("tx"))
print("la_counts", repo.counts("la"))
print("tx_ops", repo.search_operators("PIONEER", state="tx")[:2])
print("la_ops", repo.search_operators("bpx", state="la")[:2])
print("la_permit", repo.search(state="la", operator_numbers=["0024"])["total"])
repo.close()
