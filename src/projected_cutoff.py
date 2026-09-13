import pandas as pd

from src.db import connect, init
from src.reference_period import active_period


CUTOFF_RANK = 25
DISPLAY_FROM = 20
DISPLAY_TO = 32

SCENARIO_MULTIPLIERS = [
    0.8,
    1.0,
    1.2,
]


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
    start,
    end,
):
    row = con.execute(
        """
        SELECT MAX(date)
        FROM daily_market
        WHERE date BETWEEN ? AND ?
        """,
        [
            start,
            end,
        ],
    ).fetchone()

    if row is None or row[0] is None:
        raise RuntimeError(
            "No daily market data in reference period."
        )

    return row[0]


def observed_market_days(
    con,
    start,
    cutoff,
):
    row = con.execute(
        """
        SELECT COUNT(DISTINCT date)
        FROM daily_market
        WHERE date BETWEEN ? AND ?
        """,
        [
            start,
            cutoff,
        ],
    ).fetchone()

    return int(row[0])


def remaining_business_days(
    cutoff,
    end,
):
    if cutoff >= end:
        return 0

    future = pd.bdate_range(
        start=cutoff
        + pd.Timedelta(days=1),
        end=end,
    )

    return len(future)


# =========================================================
# CURRENT DATA
# =========================================================

def build_current_table(
    con,
    start,
    cutoff,
    ff_date,
    elapsed_days,
):
    df = con.execute(
        """
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
            ) AS accumulated_turnover

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

    df["current_liquidity_rank"] = (
        range(
            1,
            len(df) + 1,
        )
    )

    df["avg_daily_turnover"] = (
        df["accumulated_turnover"]
        / elapsed_days
    )

    return df


# =========================================================
# BASELINE PROJECTION
# =========================================================

def add_projection(
    df,
    remaining_days,
):
    df = df.copy()

    df["projected_future_turnover"] = (
        df["avg_daily_turnover"]
        * remaining_days
    )

    df["projected_total_turnover"] = (
        df["accumulated_turnover"]
        + df["projected_future_turnover"]
    )

    projected = df.sort_values(
        [
            "projected_total_turnover",
            "ticker",
        ],
        ascending=[
            False,
            True,
        ],
    ).copy()

    projected["projected_liquidity_rank"] = (
        range(
            1,
            len(projected) + 1,
        )
    )

    rank_map = dict(
        zip(
            projected["ticker"],
            projected[
                "projected_liquidity_rank"
            ],
        )
    )

    df["projected_liquidity_rank"] = (
        df["ticker"].map(
            rank_map
        )
    )

    df["projected_c25"] = (
        df["projected_liquidity_rank"]
        <= CUTOFF_RANK
    )

    return df


# =========================================================
# REQUIRED RUN-RATE
# =========================================================

def add_required_run_rate(
    df,
    remaining_days,
):
    df = df.copy()

    required_avg = []
    required_uplift = []
    thresholds = []
    threshold_tickers = []

    for _, row in df.iterrows():

        ticker = row["ticker"]

        others = df[
            df["ticker"] != ticker
        ].copy()

        others = others.sort_values(
            [
                "projected_total_turnover",
                "ticker",
            ],
            ascending=[
                False,
                True,
            ],
        )

        threshold_row = others.iloc[
            CUTOFF_RANK - 1
        ]

        threshold = float(
            threshold_row[
                "projected_total_turnover"
            ]
        )

        threshold_ticker = (
            threshold_row[
                "ticker"
            ]
        )

        thresholds.append(
            threshold
        )

        threshold_tickers.append(
            threshold_ticker
        )

        if remaining_days <= 0:
            required = None

        else:
            required = (
                threshold
                - float(
                    row[
                        "accumulated_turnover"
                    ]
                )
            ) / remaining_days

            required = max(
                0.0,
                required,
            )

        required_avg.append(
            required
        )

        current_avg = float(
            row[
                "avg_daily_turnover"
            ]
        )

        if (
            required is not None
            and current_avg > 0
        ):
            uplift = (
                required
                / current_avg
                - 1
            )
        else:
            uplift = None

        required_uplift.append(
            uplift
        )

    df[
        "projected_cutoff_turnover"
    ] = thresholds

    df[
        "projected_cutoff_competitor"
    ] = threshold_tickers

    df[
        "required_future_avg"
    ] = required_avg

    df[
        "required_uplift"
    ] = required_uplift

    return df


# =========================================================
# STATUS
# =========================================================

def prediction_status(row):

    current = bool(
        row["current_c25"]
    )

    projected = bool(
        row["projected_c25"]
    )

    if current and projected:
        return "STAYS"

    if (
        not current
        and projected
    ):
        return "ENTRANT"

    if (
        current
        and not projected
    ):
        return "EXIT"

    return "OUT"


# =========================================================
# SCENARIO ENGINE
# =========================================================

def simulate_one_ticker(
    df,
    ticker,
    multiplier,
    remaining_days,
):
    """
    Change only the selected ticker's future run-rate.

    All other securities remain at baseline run-rate.

    The target is compared with rank #25 among the OTHER
    securities. This avoids the target being reported as
    its own cutoff competitor.
    """

    scenario = df.copy()

    mask = (
        scenario["ticker"]
        == ticker
    )

    if not mask.any():
        return None

    # Target gets the scenario run-rate.
    scenario.loc[
        mask,
        "scenario_future_turnover",
    ] = (
        scenario.loc[
            mask,
            "avg_daily_turnover",
        ]
        * multiplier
        * remaining_days
    )

    # Everyone else stays at baseline.
    scenario.loc[
        ~mask,
        "scenario_future_turnover",
    ] = (
        scenario.loc[
            ~mask,
            "avg_daily_turnover",
        ]
        * remaining_days
    )

    scenario[
        "scenario_total_turnover"
    ] = (
        scenario[
            "accumulated_turnover"
        ]
        + scenario[
            "scenario_future_turnover"
        ]
    )

    # Full ranking including target.
    ranked = scenario.sort_values(
        [
            "scenario_total_turnover",
            "ticker",
        ],
        ascending=[
            False,
            True,
        ],
    ).reset_index(
        drop=True
    )

    ranked[
        "scenario_rank"
    ] = range(
        1,
        len(ranked) + 1,
    )

    target = ranked[
        ranked["ticker"]
        == ticker
    ].iloc[0]

    target_rank = int(
        target["scenario_rank"]
    )

    target_turnover = float(
        target[
            "scenario_total_turnover"
        ]
    )

    # -------------------------------------------------
    # TRUE HURDLE:
    # Remove target and find #25 among everyone else.
    # -------------------------------------------------

    others = ranked[
        ranked["ticker"]
        != ticker
    ].copy()

    hurdle = others.iloc[
        CUTOFF_RANK - 1
    ]

    hurdle_ticker = (
        hurdle["ticker"]
    )

    hurdle_turnover = float(
        hurdle[
            "scenario_total_turnover"
        ]
    )

    margin = (
        target_turnover
        - hurdle_turnover
    )

    return {
        "ticker": ticker,
        "multiplier": multiplier,
        "rank": target_rank,
        "turnover": target_turnover,

        "cutoff_ticker": (
            hurdle_ticker
        ),

        "cutoff_turnover": (
            hurdle_turnover
        ),

        "gap_to_cutoff": (
            margin
        ),

        "inside": (
            target_rank
            <= CUTOFF_RANK
        ),
    }

# =========================================================
# PRINT SCENARIOS
# =========================================================

def print_scenarios(
    df,
    remaining_days,
):
    """
    Focus on the securities closest to the current
    and projected cutoff.
    """

    focus = df[
        (
            df[
                "current_liquidity_rank"
            ].between(
                23,
                30,
            )
        )
        |
        (
            df[
                "projected_liquidity_rank"
            ].between(
                23,
                30,
            )
        )
    ].copy()

    focus = focus.sort_values(
        "current_liquidity_rank"
    )

    print()
    print("RUN-RATE SCENARIOS")
    print("=" * 150)

    for _, row in focus.iterrows():

        ticker = row["ticker"]

        print()
        print(
            f"{ticker} "
            f"(current #{int(row['current_liquidity_rank'])}, "
            f"baseline projected #{int(row['projected_liquidity_rank'])})"
        )
        print("-" * 150)

        for multiplier in (
            SCENARIO_MULTIPLIERS
        ):
            result = simulate_one_ticker(
                df,
                ticker,
                multiplier,
                remaining_days,
            )

            if result is None:
                continue

            status = (
                "IN"
                if result["inside"]
                else "OUT"
            )

            print(
                f"{multiplier:>4.0%} run-rate | "
                f"Proj rank #{result['rank']:>2} | "
                f"Proj turnover="
                f"{result['turnover'] / 1e9:7.3f}bn | "
                f"Cutoff="
                f"{result['cutoff_ticker']:10} "
                f"{result['cutoff_turnover'] / 1e9:7.3f}bn | "
                f"Margin="
                f"{result['gap_to_cutoff'] / 1e6:+8.1f}m | "
                f"{status}"
            )


# =========================================================
# MAIN
# =========================================================

def build_forecast():
    """
    Build the complete OMXC25 cutoff forecast for use
    by the dashboard and other modules.
    """

    init()

    start, end, review = active_period()
    con = connect()

    try:
        ff_date = latest_free_float_date(con)

        cutoff = latest_market_date(
            con,
            start,
            end,
        )

        elapsed_days = observed_market_days(
            con,
            start,
            cutoff,
        )

        remaining_days = remaining_business_days(
            cutoff,
            end,
        )

        df = build_current_table(
            con,
            start,
            cutoff,
            ff_date,
            elapsed_days,
        )

        df = add_projection(
            df,
            remaining_days,
        )

        df = add_required_run_rate(
            df,
            remaining_days,
        )

        df["prediction_status"] = df.apply(
            prediction_status,
            axis=1,
        )

        current_sorted = df.sort_values(
            [
                "accumulated_turnover",
                "ticker",
            ],
            ascending=[
                False,
                True,
            ],
        ).reset_index(drop=True)

        projected_sorted = df.sort_values(
            [
                "projected_total_turnover",
                "ticker",
            ],
            ascending=[
                False,
                True,
            ],
        ).reset_index(drop=True)

        current_cutoff = current_sorted.iloc[
            CUTOFF_RANK - 1
        ]

        current_first_out = current_sorted.iloc[
            CUTOFF_RANK
        ]

        projected_cutoff = projected_sorted.iloc[
            CUTOFF_RANK - 1
        ]

        projected_first_out = projected_sorted.iloc[
            CUTOFF_RANK
        ]

        current_margin = (
            float(
                current_cutoff[
                    "accumulated_turnover"
                ]
            )
            - float(
                current_first_out[
                    "accumulated_turnover"
                ]
            )
        )

        projected_margin = (
            float(
                projected_cutoff[
                    "projected_total_turnover"
                ]
            )
            - float(
                projected_first_out[
                    "projected_total_turnover"
                ]
            )
        )

        entrants = df[
            df["prediction_status"]
            == "ENTRANT"
        ].sort_values(
            "projected_liquidity_rank"
        ).copy()

        exits = df[
            df["prediction_status"]
            == "EXIT"
        ].sort_values(
            "projected_liquidity_rank"
        ).copy()

        challengers = df[
            df["current_liquidity_rank"]
            > CUTOFF_RANK
        ].sort_values(
            "current_liquidity_rank"
        ).head(7).copy()

        battle = df[
            (
                df["current_liquidity_rank"].between(
                    DISPLAY_FROM,
                    DISPLAY_TO,
                )
            )
            |
            (
                df["projected_liquidity_rank"].between(
                    DISPLAY_FROM,
                    DISPLAY_TO,
                )
            )
        ].copy()

        battle = battle.sort_values(
            "projected_liquidity_rank"
        )

        return {
            "review": review,
            "reference_start": start,
            "reference_end": end,
            "market_cutoff": cutoff,
            "free_float_date": ff_date,
            "elapsed_days": elapsed_days,
            "remaining_days": remaining_days,

            "current_cutoff_ticker":
                current_cutoff["ticker"],

            "current_first_out_ticker":
                current_first_out["ticker"],

            "current_margin":
                current_margin,

            "projected_cutoff_ticker":
                projected_cutoff["ticker"],

            "projected_first_out_ticker":
                projected_first_out["ticker"],

            "projected_cutoff_turnover":
                float(
                    projected_cutoff[
                        "projected_total_turnover"
                    ]
                ),

            "projected_margin":
                projected_margin,

            "table": df,
            "battle": battle,
            "entrants": entrants,
            "exits": exits,
            "challengers": challengers,
        }

    finally:
        con.close()
        
def main():

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

    cutoff = latest_market_date(
        con,
        start,
        end,
    )

    elapsed_days = (
        observed_market_days(
            con,
            start,
            cutoff,
        )
    )

    remaining_days = (
        remaining_business_days(
            cutoff,
            end,
        )
    )

    df = build_current_table(
        con,
        start,
        cutoff,
        ff_date,
        elapsed_days,
    )

    df = add_projection(
        df,
        remaining_days,
    )

    df = add_required_run_rate(
        df,
        remaining_days,
    )

    df["prediction_status"] = (
        df.apply(
            prediction_status,
            axis=1,
        )
    )

    projected_sorted = (
        df.sort_values(
            [
                "projected_total_turnover",
                "ticker",
            ],
            ascending=[
                False,
                True,
            ],
        )
        .reset_index(
            drop=True
        )
    )

    current_sorted = (
        df.sort_values(
            [
                "accumulated_turnover",
                "ticker",
            ],
            ascending=[
                False,
                True,
            ],
        )
        .reset_index(
            drop=True
        )
    )

    projected_cutoff = (
        projected_sorted.iloc[
            CUTOFF_RANK - 1
        ]
    )

    projected_first_out = (
        projected_sorted.iloc[
            CUTOFF_RANK
        ]
    )

    current_cutoff = (
        current_sorted.iloc[
            CUTOFF_RANK - 1
        ]
    )

    current_first_out = (
        current_sorted.iloc[
            CUTOFF_RANK
        ]
    )

    current_margin = (
        float(
            current_cutoff[
                "accumulated_turnover"
            ]
        )
        - float(
            current_first_out[
                "accumulated_turnover"
            ]
        )
    )

    projected_margin = (
        float(
            projected_cutoff[
                "projected_total_turnover"
            ]
        )
        - float(
            projected_first_out[
                "projected_total_turnover"
            ]
        )
    )

    # =====================================================
    # HEADER
    # =====================================================

    print()
    print(
        "OMXC25 SIMULTANEOUS CUTOFF PROJECTION"
    )
    print("=" * 150)

    print(
        f"Review:                     "
        f"{review}"
    )

    print(
        f"Reference period:           "
        f"{start} -> {end}"
    )

    print(
        f"Market data cutoff:         "
        f"{cutoff}"
    )

    print(
        f"Free-float snapshot:        "
        f"{ff_date}"
    )

    print(
        f"Elapsed trading days:       "
        f"{elapsed_days}"
    )

    print(
        f"Remaining trading days:     "
        f"{remaining_days}"
    )

    print()

    print(
        f"Current #25:                "
        f"{current_cutoff['ticker']}"
    )

    print(
        f"Current #26:                "
        f"{current_first_out['ticker']}"
    )

    print(
        f"Current cutoff margin:      "
        f"{current_margin / 1e6:,.1f}m DKK"
    )

    print()

    print(
        f"Projected #25:              "
        f"{projected_cutoff['ticker']}"
    )

    print(
        f"Projected #26:              "
        f"{projected_first_out['ticker']}"
    )

    print(
        f"Projected cutoff turnover:  "
        f"{projected_cutoff['projected_total_turnover'] / 1e9:.3f}bn"
    )

    print(
        f"Projected cutoff margin:    "
        f"{projected_margin / 1e6:,.1f}m DKK"
    )

    # =====================================================
    # PROJECTED BATTLE
    # =====================================================

    print()
    print(
        "PROJECTED CUTOFF BATTLE"
    )
    print("=" * 150)

    battle = df[
        (
            df[
                "current_liquidity_rank"
            ].between(
                DISPLAY_FROM,
                DISPLAY_TO,
            )
        )
        |
        (
            df[
                "projected_liquidity_rank"
            ].between(
                DISPLAY_FROM,
                DISPLAY_TO,
            )
        )
    ].copy()

    battle = battle.sort_values(
        "projected_liquidity_rank"
    )

    for _, row in battle.iterrows():

        print(
            f"{row['ticker']:10} "
            f"Now=#{int(row['current_liquidity_rank']):2} "
            f"Proj=#{int(row['projected_liquidity_rank']):2} "
            f"{row['prediction_status']:7} | "
            f"Now="
            f"{row['accumulated_turnover'] / 1e9:7.3f}bn | "
            f"Proj="
            f"{row['projected_total_turnover'] / 1e9:7.3f}bn | "
            f"RunRate="
            f"{row['avg_daily_turnover'] / 1e6:7.2f}m/day | "
            f"Need="
            f"{row['required_future_avg'] / 1e6:7.2f}m/day | "
            f"Uplift="
            f"{row['required_uplift']:+7.1%} | "
            f"Beat="
            f"{row['projected_cutoff_competitor']}"
        )

    # =====================================================
    # ENTRANTS / EXITS
    # =====================================================

    entrants = df[
        df[
            "prediction_status"
        ]
        == "ENTRANT"
    ].sort_values(
        "projected_liquidity_rank"
    )

    exits = df[
        df[
            "prediction_status"
        ]
        == "EXIT"
    ].sort_values(
        "projected_liquidity_rank"
    )

    print()
    print("PROJECTED ENTRANTS")
    print("=" * 100)

    if entrants.empty:
        print("None")

    else:
        for _, row in entrants.iterrows():
            print(
                f"{row['ticker']:10} | "
                f"Now "
                f"#{int(row['current_liquidity_rank'])} -> "
                f"Projected "
                f"#{int(row['projected_liquidity_rank'])} | "
                f"Turnover "
                f"{row['projected_total_turnover'] / 1e9:.3f}bn"
            )

    print()
    print("PROJECTED EXITS")
    print("=" * 100)

    if exits.empty:
        print("None")

    else:
        for _, row in exits.iterrows():
            print(
                f"{row['ticker']:10} | "
                f"Now "
                f"#{int(row['current_liquidity_rank'])} -> "
                f"Projected "
                f"#{int(row['projected_liquidity_rank'])} | "
                f"Turnover "
                f"{row['projected_total_turnover'] / 1e9:.3f}bn"
            )

    # =====================================================
    # CHALLENGERS
    # =====================================================

    print()
    print(
        "CHALLENGERS - REQUIRED FUTURE RUN-RATE"
    )
    print("=" * 125)

    challengers = df[
        df[
            "current_liquidity_rank"
        ]
        > CUTOFF_RANK
    ].sort_values(
        "current_liquidity_rank"
    ).head(7)

    for _, row in challengers.iterrows():

        print(
            f"#{int(row['current_liquidity_rank']):2} "
            f"{row['ticker']:10} | "
            f"Current avg="
            f"{row['avg_daily_turnover'] / 1e6:7.2f}m/day | "
            f"Need="
            f"{row['required_future_avg'] / 1e6:7.2f}m/day | "
            f"Uplift="
            f"{row['required_uplift']:+7.1%} | "
            f"Hurdle="
            f"{row['projected_cutoff_competitor']}"
        )

    # =====================================================
    # SCENARIO ANALYSIS
    # =====================================================

    print_scenarios(
        df,
        remaining_days,
    )

    con.close()


if __name__ == "__main__":
    main()