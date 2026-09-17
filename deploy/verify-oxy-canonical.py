from wellnav.db import connect
from wellnav.repository import WellRepository

conn = connect()
print("distinct names for #630591:")
for row in conn.execute(
    """
    SELECT operator, COUNT(*) AS n
    FROM wells_tx
    WHERE TRIM(COALESCE(operator_number, '')) = '630591'
    GROUP BY operator
    ORDER BY n DESC
    """
):
    print(f"  {row['n']}\t{row['operator']}")

print("unnumbered oxy-like leftovers:")
for row in conn.execute(
    """
    SELECT operator, COUNT(*) AS n
    FROM wells_tx
    WHERE TRIM(COALESCE(operator_number, '')) = ''
      AND UPPER(operator) LIKE '%OXY%'
    GROUP BY operator
    ORDER BY n DESC
    LIMIT 20
    """
):
    print(f"  {row['n']}\t{row['operator']}")

print("oxyrock names:")
for row in conn.execute(
    """
    SELECT COALESCE(operator_number, '') AS number, operator, COUNT(*) AS n
    FROM wells_tx
    WHERE UPPER(operator) LIKE '%OXYROCK%'
    GROUP BY 1, 2
    ORDER BY n DESC
    """
):
    print(f"  {row['n']}\t#{row['number']}\t{row['operator']}")

repo = WellRepository(conn)
print("search oxy:")
for row in repo.search_operators("oxy", state="tx"):
    print(f"  {row['number']}\t{row['name']}")
