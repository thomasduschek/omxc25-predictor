from datetime import date

from src.db import connect


def audit_coverage(as_of_date, top_n=50):
    con = connect()

    rows = con.execute(
        """
        WITH latest_ff AS (
            SELECT
                f.ticker,
                f.market_cap_rank,
                f.market_cap,
                f.free_float_factor,
                f.free_float_market_cap,
                u.company,
                u.current_c25
            FROM free_float_snapshot f
            JOIN universe u
              ON u.ticker = f.ticker
            WHERE f.as_of_date = ?
        ),

        ownership AS (
            SELECT
                ticker,
                COUNT(*) AS ownership_rows,
                MAX(as_of_date) AS latest_owner_date
            FROM ownership_positions
            WHERE as_of_date <= ?
            GROUP BY ticker
        ),

        treasury AS (
            SELECT
                ticker,
                COUNT(*) AS treasury_rows,
                MAX(as_of_date) AS latest_treasury_date
            FROM treasury_shares
            WHERE as_of_date <= ?
            GROUP BY ticker
        ),

        major_events AS (
            SELECT
                ticker,
                COUNT(*) AS major_event_rows,
                MAX(
                    COALESCE(
                        event_date,
                        published_date
                    )
                ) AS latest_major_event_date
            FROM free_float_source_events
            WHERE event_type = 'MAJOR_SHAREHOLDER'
              AND COALESCE(
                    event_date,
                    published_date
                  ) <= ?
            GROUP BY ticker
        )

        SELECT
            f.market_cap_rank,
            f.ticker,
            f.company,
            f.current_c25,
            f.market_cap,
            f.free_float_factor,
            f.free_float_market_cap,
            COALESCE(o.ownership_rows, 0),
            o.latest_owner_date,
            COALESCE(t.treasury_rows, 0),
            t.latest_treasury_date,
            COALESCE(m.major_event_rows, 0),
            m.latest_major_event_date
        FROM latest_ff f
        LEFT JOIN ownership o
          ON o.ticker = f.ticker
        LEFT JOIN treasury t
          ON t.ticker = f.ticker
        LEFT JOIN major_events m
          ON m.ticker = f.ticker
        WHERE
            f.market_cap_rank <= ?
            OR f.current_c25 = TRUE
        ORDER BY
            f.market_cap_rank,
            f.ticker
        """,
        [
            as_of_date,
            as_of_date,
            as_of_date,
            as_of_date,
            top_n,
        ],
    ).fetchall()

    print()
    print("FREE-FLOAT COVERAGE AUDIT")
    print("=" * 150)
    print(f"As of date: {as_of_date}")
    print(f"Scope: Top {top_n} FF market cap + all current C25")
    print()

    missing = []
    covered = []

    for row in rows:
        (
            rank,
            ticker,
            company,
            current_c25,
            market_cap,
            fff,
            ff_market_cap,
            ownership_rows,
            latest_owner_date,
            treasury_rows,
            latest_treasury_date,
            major_event_rows,
            latest_major_event_date,
        ) = row

        issues = []

        if ownership_rows == 0:
            issues.append("NO_OWNERSHIP_DATA")

        if (
            fff == 1.0
            and ownership_rows == 0
        ):
            issues.append("FFF_100_UNVERIFIED")

        result = {
            "rank": rank,
            "ticker": ticker,
            "company": company,
            "current_c25": current_c25,
            "market_cap": market_cap,
            "fff": fff,
            "ff_market_cap": ff_market_cap,
            "ownership_rows": ownership_rows,
            "latest_owner_date": latest_owner_date,
            "treasury_rows": treasury_rows,
            "latest_treasury_date": latest_treasury_date,
            "major_event_rows": major_event_rows,
            "latest_major_event_date": latest_major_event_date,
            "issues": issues,
        }

        if issues:
            missing.append(result)
        else:
            covered.append(result)

    print("SUMMARY")
    print("=" * 150)
    print(f"Securities checked:       {len(rows)}")
    print(f"Ownership covered:        {len(covered)}")
    print(f"Needs ownership review:   {len(missing)}")

    print()
    print("OWNERSHIP COVERAGE REQUIRED")
    print("=" * 150)

    for item in missing:
        print(
            f"{item['rank']:3} "
            f"{item['ticker']:10} "
            f"C25={str(item['current_c25']):5} "
            f"FFF={item['fff']:.2f} "
            f"MC={item['market_cap'] / 1e9:9.3f}bn "
            f"OWN={item['ownership_rows']:2} "
            f"NEWS={item['major_event_rows']:2} "
            f"{','.join(item['issues'])}"
        )

    print()
    print("OWNERSHIP DATA PRESENT")
    print("=" * 150)

    for item in covered:
        print(
            f"{item['rank']:3} "
            f"{item['ticker']:10} "
            f"C25={str(item['current_c25']):5} "
            f"FFF={item['fff']:.2f} "
            f"OWN={item['ownership_rows']:2} "
            f"Latest={item['latest_owner_date']}"
        )

    con.close()

    return {
        "checked": len(rows),
        "covered": len(covered),
        "review": len(missing),
        "review_rows": missing,
    }


if __name__ == "__main__":
    audit_coverage(
        date(2026, 9, 7),
        top_n=50,
    )