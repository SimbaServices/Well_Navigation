"""Verify NM wells/permits/operators search against live wellnav.db."""

from __future__ import annotations

from wellnav.repository import WellRepository

repo = WellRepository()
print("counts", repo.counts("nm"))
print("tx_counts", repo.counts("tx"))
wells = repo.search(q="DUSTIN", state="nm")["wells"]
print("dustin", [(w["api"], w["well_name"], w["record_kind"], w["operator"]) for w in wells[:3]])
permits = repo.search(q="COLIBRI FEDERAL", state="nm", include_permits=True)["wells"]
print(
    "colibri",
    [(w["api"], w["record_kind"], w["status"], w["well_name"]) for w in permits[:5]],
)
print("ops", repo.search_operators("HILCORP", state="nm")[:3])
print("leases", repo.search_leases("DUSTIN", state="nm")[:2])
print("tx ops for hilcorp", repo.search_operators("HILCORP", state="tx")[:1])
repo.close()
