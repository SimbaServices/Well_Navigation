from wellnav.db import connect
from wellnav.states import APP_STATES, permits_table, wells_table

conn = connect()
for code in APP_STATES:
    for kind, table in (("wells", wells_table(code)), ("permits", permits_table(code))):
        try:
            split = conn.execute(
                f"""
                SELECT COUNT(*) AS n FROM (
                    SELECT TRIM(operator_number) AS number
                    FROM {table}
                    WHERE TRIM(COALESCE(operator_number, '')) != ''
                    GROUP BY 1
                    HAVING COUNT(DISTINCT operator) > 1
                )
                """
            ).fetchone()["n"]
            numbered = conn.execute(
                f"""
                SELECT COUNT(DISTINCT TRIM(operator_number)) AS n
                FROM {table}
                WHERE TRIM(COALESCE(operator_number, '')) != ''
                """
            ).fetchone()["n"]
        except Exception as exc:
            print(code, kind, "error", exc)
            continue
        print(f"{code} {kind}: {numbered} numbered operators, {split} still have multiple names")
