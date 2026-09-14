from datetime import timedelta

import pandas as pd

from src.db import connect, init
from src.reference_period import active_period


CUTOFF_RANK = 25
WINDOW = 5


def latest_free_float_date(con):
    return con.execute("""
        SELECT MAX(as_of_date)
        FROM free_float_snapshot
    """).fetchone()[0]


def common_market_cutoff(con, tickers, start, end):
    """
    Use the latest date for which all relevant securities
    have market data.
    """
    rows = con.execute("""
        SELECT ticker, MAX(date)
        FROM daily_market
        WHERE ticker IN (
            SELECT UNNEST(?)
        )
          AND date BETWEEN ? AND ?
        GROUP BY ticker
    """, [tickers, start, end]).fetchall()

    dates = [
        row[1]
        for row in rows
        if row[1] is not None
    ]

    if not dates:
        raise RuntimeError(
            "No market data found."
        )

    return min(dates)


def remaining_business_days(after_date, end_date):
    """
    Temporary weekday approximation.
    We will replace this with a proper Nasdaq trading
    calendar later.
    """
    first_future_date = (
        after_date
        + timedelta(days=1)
    )

    if first_future_date > end_date:
        return 0

    return len(
        pd.bdate_range(
            start=first_future_date,
            end=end_date,
        )
    )


def build_table(con, start, cutoff, ff_date):
    df = con.execute("""
        SELECT
            u.ticker,
            u.company,
            u.current_c25,

            f.market_cap_rank
                AS ff_market_cap_rank,

            f.free_float_market_cap,

            COALESCE(
                SUM(d.turnover),
                0
            ) AS accumulated_turnover,

            COUNT(d.date)
                AS trading_days

        FROM universe u

        JOIN free_float_snapshot f
          ON f.ticker = u.ticker
         AND f.as_of_date = ?

        LEFT JOIN daily_market d
          ON d.ticker = u.ticker
         AND d.date BETWEEN ? AND ?

        WHERE f.top35_eligible = TRUE

        GROUP BY
            u.ticker,
            u.company,
            u.current_c25,
            f.market_cap_rank,
            f.free_float_market_cap

        ORDER BY
            accumulated_turnover DESC,
            u.ticker
    """, [ff_date, start, cutoff]).df()

    df["liquidity_rank"] = (
        range(1, len(df) + 1)
    )

    df["avg_daily_turnover"] = (
        df["accumulated_turnover"]
        / df["trading_days"].replace(0, pd.NA)
    )

    return df


def main():
    init()

    start, end, review = active_period()

    con = connect()

    ff_date = latest_free_float_date(con)

    eligible_tickers = [
        row[0]
        for row in con.execute("""
            SELECT ticker
            FROM free_float_snapshot
            WHERE as_of_date = ?
              AND top35_eligible = TRUE
        """, [ff_date]).fetchall()
    ]

    cutoff_date = common_market_cutoff(
        con,
        eligible_tickers,
        start,
        end,
    )

    df = build_table(
        con,
        start,
        cutoff_date,
        ff_date,
    )

    if len(df) < CUTOFF_RANK:
        raise RuntimeError(
            "Fewer than 25 Top-35 eligible securities."
        )

    cutoff = df.iloc[CUTOFF_RANK - 1]

    cutoff_turnover = float(
        cutoff["accumulated_turnover"]
    )

    cutoff_avg = float(
        cutoff["avg_daily_turnover"]
    )

    remaining_days = remaining_business_days(
        cutoff_date,
        end,
    )

    # -------------------------------------------------
    # Dynamic calculations
    # -------------------------------------------------

    df["gap_to_cutoff"] = (
        cutoff_turnover
        - df["accumulated_turnover"]
    )

    required_future = []

    required_uplift = []

    for _, row in df.iterrows():

        current = float(
            row["accumulated_turnover"]
        )

        current_avg = float(
            row["avg_daily_turnover"]
        )

        # Assume current #25 continues at its
        # historical average daily turnover.
        projected_cutoff = (
            cutoff_turnover
            + cutoff_avg * remaining_days
        )

        if remaining_days > 0:
            required_avg = (
                projected_cutoff
                - current
            ) / remaining_days
        else:
            required_avg = None

        if required_avg is not None:
            required_avg = max(
                0.0,
                required_avg,
            )

        required_future.append(
            required_avg
        )

        if (
            required_avg is not None
            and current_avg > 0
        ):
            uplift = (
                required_avg
                / current_avg
                - 1
            )
        else:
            uplift = None

        required_uplift.append(
            uplift
        )

    df["required_future_avg"] = (
        required_future
    )

    df["required_uplift"] = (
        required_uplift
    )

    # -------------------------------------------------
    # Display only securities around the cutoff
    # -------------------------------------------------

    lower = max(
        1,
        CUTOFF_RANK - WINDOW
    )

    upper = min(
        len(df),
        CUTOFF_RANK + WINDOW
    )

    around = df[
        df["liquidity_rank"].between(
            lower,
            upper,
        )
    ].copy()

    print()
    print("OMXC25 DYNAMIC LIQUIDITY CUTOFF")
    print("=" * 125)

    print(
        f"Review:                 {review}"
    )
    print(
        f"Reference period:       {start} -> {end}"
    )
    print(
        f"Market data cutoff:     {cutoff_date}"
    )
    print(
        f"Free-float snapshot:    {ff_date}"
    )
    print(
        f"Remaining weekdays:     {remaining_days}"
    )
    print(
        f"Liquidity cutoff:       Rank #{CUTOFF_RANK}"
    )

    print()
    print("CURRENT CUTOFF")
    print("=" * 125)

    print(
        f"#{CUTOFF_RANK} "
        f"{cutoff['ticker']} | "
        f"Turnover "
        f"{cutoff_turnover / 1e9:.3f}bn | "
        f"Avg/day "
        f"{cutoff_avg / 1e6:.2f}m"
    )

    print()
    print("CUTOFF BATTLE")
    print("=" * 125)

    for _, row in around.iterrows():

        rank = int(
            row["liquidity_rank"]
        )

        ticker = row["ticker"]

        turnover = float(
            row["accumulated_turnover"]
        )

        avg = float(
            row["avg_daily_turnover"]
        )

        gap = float(
            row["gap_to_cutoff"]
        )

        required = row[
            "required_future_avg"
        ]

        uplift = row[
            "required_uplift"
        ]

        if rank < CUTOFF_RANK:
            position = "IN"
        elif rank == CUTOFF_RANK:
            position = "CUTOFF"
        else:
            position = "OUT"

        gap_text = (
            f"{gap / 1e6:+8.1f}m"
        )

        required_text = (
            f"{required / 1e6:7.2f}m"
            if pd.notna(required)
            else "    N/A"
        )

        uplift_text = (
            f"{uplift:+7.1%}"
            if pd.notna(uplift)
            else "    N/A"
        )

        print(
            f"#{rank:2} "
            f"{ticker:10} "
            f"{position:6} | "
            f"Turnover={turnover / 1e9:7.3f}bn | "
            f"Avg={avg / 1e6:7.2f}m | "
            f"Gap={gap_text} | "
            f"Need={required_text}/day | "
            f"Uplift={uplift_text} | "
            f"C25={row['current_c25']}"
        )

    print()
    print("CHALLENGERS OUTSIDE TOP 25")
    print("=" * 125)

    challengers = df[
        df["liquidity_rank"]
        > CUTOFF_RANK
    ].head(5)

    for _, row in challengers.iterrows():

        gap = (
            cutoff_turnover
            - float(
                row["accumulated_turnover"]
            )
        )

        print(
            f"#{int(row['liquidity_rank']):2} "
            f"{row['ticker']:10} | "
            f"Gap={gap / 1e6:8.1f}m | "
            f"Need="
            f"{row['required_future_avg'] / 1e6:7.2f}m/day | "
            f"Uplift="
            f"{row['required_uplift']:+7.1%}"
        )

    con.close()


if __name__ == "__main__":
    main()