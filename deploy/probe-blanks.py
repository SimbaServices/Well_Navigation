from wellnav.db import connect

conn = connect()
print(
    "wells",
    conn.execute("SELECT COUNT(*) FROM wells_tx").fetchone()[0],
)
print(
    "no_operator",
    conn.execute(
        "SELECT COUNT(*) FROM wells_tx WHERE operator IS NULL OR operator = ''"
    ).fetchone()[0],
)
print(
    "no_lease",
    conn.execute(
        "SELECT COUNT(*) FROM wells_tx WHERE lease_name IS NULL OR lease_name = ''"
    ).fetchone()[0],
)
print(
    "no_either",
    conn.execute(
        """
        SELECT COUNT(*) FROM wells_tx
        WHERE (operator IS NULL OR operator = '')
          AND (lease_name IS NULL OR lease_name = '')
        """
    ).fetchone()[0],
)
print(
    "op_no_lease",
    conn.execute(
        """
        SELECT COUNT(*) FROM wells_tx
        WHERE operator IS NOT NULL AND operator != ''
          AND (lease_name IS NULL OR lease_name = '')
        """
    ).fetchone()[0],
)
print(
    "lease_no_op",
    conn.execute(
        """
        SELECT COUNT(*) FROM wells_tx
        WHERE lease_name IS NOT NULL AND lease_name != ''
          AND (operator IS NULL OR operator = '')
        """
    ).fetchone()[0],
)
print("top blank counties")
for row in conn.execute(
    """
    SELECT county_code, county, COUNT(*) AS n
    FROM wells_tx
    WHERE operator IS NULL OR operator = ''
    GROUP BY county_code
    ORDER BY n DESC
    LIMIT 12
    """
):
    print(dict(row))

print("blank by source/symbol")
for row in conn.execute(
    """
    SELECT COALESCE(source,''), COALESCE(symbol,''), COALESCE(CAST(symnum AS TEXT),''), COUNT(*)
    FROM wells_tx
    WHERE operator IS NULL OR operator = ''
    GROUP BY 1, 2, 3
    ORDER BY 4 DESC
    LIMIT 15
    """
):
    print(tuple(row))

print("permit has identity for blank well")
print(
    conn.execute(
        """
        SELECT COUNT(*)
        FROM wells_tx w
        JOIN permits_tx p ON p.api8 = w.api8
        WHERE (w.operator IS NULL OR w.operator = '')
          AND p.operator IS NOT NULL AND p.operator != ''
        """
    ).fetchone()[0]
)

print("samples")
for row in conn.execute(
    """
    SELECT api8, county_code, well_name, well_no, symbol, symnum, source
    FROM wells_tx
    WHERE operator IS NULL OR operator = ''
    ORDER BY api8
    LIMIT 8
    """
):
    print(dict(row))
