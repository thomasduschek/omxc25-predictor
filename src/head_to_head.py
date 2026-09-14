import argparse
from datetime import timedelta

import pandas as pd

from src.db import connect, init
from src.reference_period import active_period


# =========================================================
# HELPERS
# =========================================================

def latest_free_float_date(con):
    row = con.execute(
        """
        SELECT MAX(as_of_date)
        FROM free_float_snapshot
        """
    ).fetchone()

    if row is None or row[0] is None:
        raise RuntimeError(
            "No free_float_snapshot found."
        )

    return row[0]


def latest_market_date(
    con,
    ticker,
    start,
    end,
):
    row = con.execute(
        """
        SELECT MAX(date)
        FROM daily_market
        WHERE ticker = ?
          AND date BETWEEN ? AND ?
        """,
        [
            ticker,
            start,
            end,
        ],
    ).fetchone()

    if row is None:
        return None

    return row[0]


def remaining_business_days(
    after_date,
    end_date,
):
    """
    Count weekdays after the latest included market date
    through the end of the reference period.

    For the current Sep-Nov 2026 period this is a useful
    practical proxy for remaining Nasdaq Copenhagen
    trading days.
    """

    start_date = (
        after_date
        + timedelta(days=1)
    )

    if start_date > end_date:
        return 0

    days = pd.bdate_range(
        start=start_date,
        end=end_date,
    )

    return len(days)


# =========================================================
# CALCULATE CURRENT LIQUIDITY TABLE
# =========================================================

def build_liquidity_table(
    con,
    start,
    cutoff,
    ff_date,
):
    df = con.execute(
        """
        SELECT
            u.ticker,
            u.company,
            u.current_c25,
            f.market_cap_rank
                AS ff_market_cap_rank,
            f.top35_eligible,
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
            f.top35_eligible
        """,
        [
            ff_date,
            start,
            cutoff,
        ],
    ).df()

    df = df.sort_values(
        [
            "accumulated_turnover",
            "ticker",
        ],
        ascending=[
            False,
            True,
        ],
    ).reset_index(
        drop=True
    )

    df["liquidity_rank"] = (
        range(
            1,
            len(df) + 1,
        )
    )

    return df


# =========================================================
# SIMULATION
# =========================================================

def run_simulation(
    target,
    competitor,
):
    init()

    start, end, review = (
        active_period()
    )

    con = connect()

    ff_date = (
        latest_free_float_date(
            con
        )
    )

    target_last = (
        latest_market_date(
            con,
            target,
            start,
            end,
        )
    )

    competitor_last = (
        latest_market_date(
            con,
            competitor,
            start,
            end,
        )
    )

    if target_last is None:
        raise RuntimeError(
            f"No market data for {target}"
        )

    if competitor_last is None:
        raise RuntimeError(
            f"No market data for {competitor}"
        )

    # -----------------------------------------------------
    # COMMON CUTOFF
    #
    # Ensures both securities are compared through the
    # exact same market date.
    # -----------------------------------------------------

    cutoff = min(
        target_last,
        competitor_last,
    )

    df = build_liquidity_table(
        con,
        start,
        cutoff,
        ff_date,
    )

    if target not in set(
        df["ticker"]
    ):
        raise RuntimeError(
            f"{target} is not Top-35 eligible."
        )

    if competitor not in set(
        df["ticker"]
    ):
        raise RuntimeError(
            f"{competitor} is not Top-35 eligible."
        )

    target_row = (
        df[
            df["ticker"]
            == target
        ]
        .iloc[0]
    )

    competitor_row = (
        df[
            df["ticker"]
            == competitor
        ]
        .iloc[0]
    )

    target_turnover = float(
        target_row[
            "accumulated_turnover"
        ]
    )

    competitor_turnover = float(
        competitor_row[
            "accumulated_turnover"
        ]
    )

    target_days = int(
        target_row[
            "trading_days"
        ]
    )

    competitor_days = int(
        competitor_row[
            "trading_days"
        ]
    )

    target_avg = (
        target_turnover
        / target_days
        if target_days
        else 0.0
    )

    competitor_avg = (
        competitor_turnover
        / competitor_days
        if competitor_days
        else 0.0
    )

    remaining_days = (
        remaining_business_days(
            cutoff,
            end,
        )
    )

    current_gap = (
        competitor_turnover
        - target_turnover
    )

    # -----------------------------------------------------
    # REQUIRED DAILY OUTPERFORMANCE
    #
    # If competitor continues to trade at X per day,
    # target must trade X + gap/days per day.
    # -----------------------------------------------------

    if remaining_days > 0:
        extra_per_day = (
            current_gap
            / remaining_days
        )

        required_target_avg = (
            competitor_avg
            + extra_per_day
        )

    else:
        extra_per_day = None
        required_target_avg = None

    # -----------------------------------------------------
    # CURRENT RUN-RATE PROJECTION
    # -----------------------------------------------------

    projected_target = (
        target_turnover
        + target_avg
        * remaining_days
    )

    projected_competitor = (
        competitor_turnover
        + competitor_avg
        * remaining_days
    )

    projected_gap = (
        projected_competitor
        - projected_target
    )

    # -----------------------------------------------------
    # SCENARIOS
    # -----------------------------------------------------

    scenarios = []

    for multiplier in [
        0.8,
        1.0,
        1.2,
    ]:
        future_competitor_avg = (
            competitor_avg
            * multiplier
        )

        if remaining_days:
            required = (
                (
                    competitor_turnover
                    + future_competitor_avg
                    * remaining_days
                    - target_turnover
                )
                / remaining_days
            )

            required = max(
                0.0,
                required,
            )

        else:
            required = None

        scenarios.append(
            (
                multiplier,
                future_competitor_avg,
                required,
            )
        )

    # =====================================================
    # OUTPUT
    # =====================================================

    print()
    print("OMXC25 HEAD-TO-HEAD")
    print("=" * 105)

    print(
        f"Review:                 "
        f"{review}"
    )

    print(
        f"Reference period:       "
        f"{start} -> {end}"
    )

    print(
        f"Common data cutoff:     "
        f"{cutoff}"
    )

    print(
        f"Free-float snapshot:    "
        f"{ff_date}"
    )

    print(
        f"Remaining weekdays:     "
        f"{remaining_days}"
    )

    print()
    print("CURRENT POSITION")
    print("=" * 105)

    print(
        f"{target:10} | "
        f"Liq rank "
        f"{int(target_row['liquidity_rank']):2} | "
        f"Turnover "
        f"{target_turnover / 1e9:8.3f}bn | "
        f"Avg/day "
        f"{target_avg / 1e6:8.2f}m"
    )

    print(
        f"{competitor:10} | "
        f"Liq rank "
        f"{int(competitor_row['liquidity_rank']):2} | "
        f"Turnover "
        f"{competitor_turnover / 1e9:8.3f}bn | "
        f"Avg/day "
        f"{competitor_avg / 1e6:8.2f}m"
    )

    print()
    print(
        f"Current turnover gap:   "
        f"{current_gap / 1e6:,.1f}m DKK"
    )

    if (
        remaining_days > 0
        and current_gap > 0
    ):
        print(
            f"Required extra/day:     "
            f"{extra_per_day / 1e6:,.2f}m DKK"
        )

        print(
            f"Required {target} avg: "
            f"{required_target_avg / 1e6:,.2f}m DKK/day"
        )

        if target_avg > 0:
            uplift = (
                required_target_avg
                / target_avg
                - 1
            )

            print(
                f"Required uplift vs "
                f"{target} run-rate: "
                f"{uplift:.1%}"
            )

    elif current_gap <= 0:
        print(
            f"{target} is already ahead "
            f"of {competitor}."
        )

    print()
    print("CURRENT RUN-RATE PROJECTION")
    print("=" * 105)

    print(
        f"{target:10} projected: "
        f"{projected_target / 1e9:8.3f}bn"
    )

    print(
        f"{competitor:10} projected: "
        f"{projected_competitor / 1e9:8.3f}bn"
    )

    print(
        f"Projected gap:          "
        f"{projected_gap / 1e6:,.1f}m DKK"
    )

    print()
    print("COMPETITOR SCENARIOS")
    print("=" * 105)

    for (
        multiplier,
        future_competitor_avg,
        required,
    ) in scenarios:

        if required is None:
            continue

        uplift = (
            required
            / target_avg
            - 1
            if target_avg
            else None
        )

        print(
            f"{competitor} at "
            f"{multiplier:>3.0%} of current run-rate | "
            f"Competitor avg "
            f"{future_competitor_avg / 1e6:8.2f}m/day | "
            f"{target} needs "
            f"{required / 1e6:8.2f}m/day"
            + (
                f" | uplift "
                f"{uplift:7.1%}"
                if uplift is not None
                else ""
            )
        )

    con.close()


# =========================================================
# COMMAND LINE
# =========================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "target",
        nargs="?",
        default="BAVA",
    )

    parser.add_argument(
        "competitor",
        nargs="?",
        default="TRMD A",
    )

    args = parser.parse_args()

    run_simulation(
        args.target,
        args.competitor,
    )