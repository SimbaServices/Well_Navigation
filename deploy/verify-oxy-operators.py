from wellnav.repository import WellRepository

repo = WellRepository()
rows = repo.search_operators("oxy", state="tx")
print("count", len(rows))
for row in rows:
    print(f"{row['number']}\t{row['name']}")
