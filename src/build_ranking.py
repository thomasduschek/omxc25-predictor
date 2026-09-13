import pandas as pd

from .db import connect, init, ROOT
from .reference_period import active_period


def latest_free_float_date(con):
    """
    Use the latest available free-float snapshot.

    The snapshot is our current estimate of free-float
    market-cap eligibility for the OMXC25 review.
    """

    row = con.execute(
        """
        SELECT MAX(as_of_date)
        FROM free_float_snapshot
        """
    ).fetchone()

    if row is None or row[0] is None:
        raise RuntimeError(
            "No free_float_snapshot data found. "
            "Run src.build_free_float_snapshot first."
        )

    return row[0]


def main():
    init()

    start, end, label = active_period()

    con = connect()

    ff_date = latest_free_float_date(
        con
    )

    # =====================================================
    # BASE DATA
    # =====================================================

    df = con.execute(
        """
        SELECT
            u.ticker,
            u.company,
            u.current_c25,

            COALESCE(
                SUM(d.turnover),
                0
            ) AS accumulated_turnover,

            COUNT(d.date) AS trading_days,

            f.market_cap,
            f.raw_free_float_pct,
            f.free_float_factor,
            f.free_float_market_cap,
            f.market_cap_rank AS ff_market_cap_rank,
            f.top35_eligible,
            f.calculation_method AS ff_calculation_method,
            f.free_float_source_quality AS ff_quality

        FROM universe u

        LEFT JOIN daily_market d
          ON d.ticker = u.ticker
         AND d.date BETWEEN ? AND ?

        LEFT JOIN free_float_snapshot f
          ON f.ticker = u.ticker
         AND f.as_of_date = ?

        GROUP BY
            u.ticker,
            u.company,
            u.current_c25,
            f.market_cap,
            f.raw_free_float_pct,
            f.free_float_factor,
            f.free_float_market_cap,
            f.market_cap_rank,
            f.top35_eligible,
            f.calculation_method,
            f.free_float_source_quality

        ORDER BY
            accumulated_turnover DESC,
            u.ticker
        """,
        [
            start,
            end,
            ff_date,
        ],
    ).df()

    # =====================================================
    # TURNOVER DATA AVAILABILITY
    # =====================================================

    has_data = (
        df["trading_days"] > 0
    )

    # =====================================================
    # OVERALL OMXCPI TURNOVER RANK
    #
    # Diagnostic only.
    # =====================================================

    df[
        "overall_turnover_rank"
    ] = pd.Series(
        pd.NA,
        index=df.index,
        dtype="Int64",
    )

    df.loc[
        has_data,
        "overall_turnover_rank",
    ] = (
        df.loc[
            has_data,
            "accumulated_turnover",
        ]
        .rank(
            method="first",
            ascending=False,
        )
        .astype("Int64")
    )

    # =====================================================
    # TOP-35 FREE-FLOAT ELIGIBILITY
    # =====================================================

    df["top35_eligible"] = (
        df["top35_eligible"]
        .fillna(False)
        .astype(bool)
    )

    eligible_has_data = (
        has_data
        & df["top35_eligible"]
    )

    # =====================================================
    # OMXC25 LIQUIDITY RANK
    #
    # Rank turnover ONLY among the 35 securities that passed
    # the free-float market-cap screen.
    # =====================================================

    df[
        "liquidity_rank"
    ] = pd.Series(
        pd.NA,
        index=df.index,
        dtype="Int64",
    )

    df.loc[
        eligible_has_data,
        "liquidity_rank",
    ] = (
        df.loc[
            eligible_has_data,
            "accumulated_turnover",
        ]
        .rank(
            method="first",
            ascending=False,
        )
        .astype("Int64")
    )

    # =====================================================
    # PREDICTED OMXC25
    # =====================================================

    df["predicted_c25"] = (
        df["top35_eligible"]
        & df["liquidity_rank"].notna()
        & (
            df["liquidity_rank"]
            <= 25
        )
    )

    # =====================================================
    # CHANGE STATUS
    # =====================================================

    def change_status(row):
        current = bool(
            row["current_c25"]
        )

        predicted = bool(
            row["predicted_c25"]
        )

        if current and predicted:
            return "STAYS"

        if (
            not current
            and predicted
        ):
            return "ENTRANT"

        if (
            current
            and not predicted
        ):
            return "EXIT"

        return "OUT"

    df["prediction_status"] = (
        df.apply(
            change_status,
            axis=1,
        )
    )

    # =====================================================
    # REVIEW INFORMATION
    # =====================================================

    df["reference_start"] = str(
        start
    )

    df["reference_end"] = str(
        end
    )

    df["review"] = label

    df["free_float_snapshot_date"] = str(
        ff_date
    )

    # =====================================================
    # DISPLAY / OUTPUT ORDER
    #
    # Eligible securities first, ranked by liquidity.
    # Remaining OMXCPI securities follow by turnover.
    # =====================================================

    df["_eligible_sort"] = (
        ~df["top35_eligible"]
    )

    df["_liq_sort"] = (
        df["liquidity_rank"]
        .fillna(999999)
    )

    df["_overall_sort"] = (
        df["overall_turnover_rank"]
        .fillna(999999)
    )

    df = df.sort_values(
        by=[
            "_eligible_sort",
            "_liq_sort",
            "_overall_sort",
            "ticker",
        ]
    ).drop(
        columns=[
            "_eligible_sort",
            "_liq_sort",
            "_overall_sort",
        ]
    )

    # =====================================================
    # SAVE FULL REPORT
    # =====================================================

    out = (
        ROOT
        / "reports"
        / "current_ranking.csv"
    )

    out.parent.mkdir(
        exist_ok=True
    )

    df.to_csv(
        out,
        index=False,
    )

    # =====================================================
    # CONSOLE SUMMARY
    # =====================================================

    print()
    print(
        "OMXC25 PREDICTOR"
    )
    print("=" * 150)

    print(
        f"Review:                 {label}"
    )

    print(
        f"Reference period:       "
        f"{start} -> {end}"
    )

    print(
        f"Free-float snapshot:    "
        f"{ff_date}"
    )

    print(
        f"OMXCPI securities:      "
        f"{len(df)}"
    )

    print(
        f"Top-35 eligible:        "
        f"{int(df['top35_eligible'].sum())}"
    )

    print(
        f"Predicted C25:          "
        f"{int(df['predicted_c25'].sum())}"
    )

    # =====================================================
    # TOP-35 LIQUIDITY TABLE
    # =====================================================

    print()
    print(
        "TOP-35 FREE-FLOAT ELIGIBLE "
        "- RANKED BY TURNOVER"
    )
    print("=" * 150)

    eligible = df[
        df["top35_eligible"]
    ].copy()

    eligible = eligible.sort_values(
        [
            "liquidity_rank",
            "ticker",
        ]
    )

    for _, row in eligible.iterrows():

        liquidity_rank = (
            int(row["liquidity_rank"])
            if pd.notna(
                row["liquidity_rank"]
            )
            else None
        )

        ff_rank = (
            int(
                row["ff_market_cap_rank"]
            )
            if pd.notna(
                row["ff_market_cap_rank"]
            )
            else None
        )

        turnover_bn = (
            row[
                "accumulated_turnover"
            ]
            / 1e9
        )

        ffmc_bn = (
            row[
                "free_float_market_cap"
            ]
            / 1e9
            if pd.notna(
                row[
                    "free_float_market_cap"
                ]
            )
            else 0.0
        )

        print(
            f"Liq={str(liquidity_rank):>3} "
            f"FFRank={str(ff_rank):>3} "
            f"{row['ticker']:<10} "
            f"Turnover="
            f"{turnover_bn:>9.3f}bn "
            f"FFMC="
            f"{ffmc_bn:>9.3f}bn "
            f"Current="
            f"{str(bool(row['current_c25'])):<5} "
            f"Predicted="
            f"{str(bool(row['predicted_c25'])):<5} "
            f"{row['prediction_status']}"
        )

    # =====================================================
    # EXPECTED ENTRANTS / EXITS
    # =====================================================

    entrants = df[
        df[
            "prediction_status"
        ]
        == "ENTRANT"
    ].sort_values(
        "liquidity_rank"
    )

    exits = df[
        df[
            "prediction_status"
        ]
        == "EXIT"
    ].sort_values(
        "overall_turnover_rank"
    )

    print()
    print(
        "PREDICTED ENTRANTS"
    )
    print("=" * 100)

    if entrants.empty:
        print("None")
    else:
        for _, row in entrants.iterrows():
            print(
                f"{row['ticker']:10} | "
                f"Liquidity rank "
                f"{int(row['liquidity_rank'])} | "
                f"FF rank "
                f"{int(row['ff_market_cap_rank'])}"
            )

    print()
    print(
        "PREDICTED EXITS"
    )
    print("=" * 100)

    if exits.empty:
        print("None")
    else:
        for _, row in exits.iterrows():

            liquidity = (
                str(
                    int(
                        row[
                            "liquidity_rank"
                        ]
                    )
                )
                if pd.notna(
                    row[
                        "liquidity_rank"
                    ]
                )
                else "N/A"
            )

            ff_rank = (
                str(
                    int(
                        row[
                            "ff_market_cap_rank"
                        ]
                    )
                )
                if pd.notna(
                    row[
                        "ff_market_cap_rank"
                    ]
                )
                else "N/A"
            )

            print(
                f"{row['ticker']:10} | "
                f"Liquidity rank "
                f"{liquidity} | "
                f"FF rank "
                f"{ff_rank}"
            )

    print()
    print(
        f"Saved to {out}"
    )

    con.close()

if __name__ == "__main__":
    main()