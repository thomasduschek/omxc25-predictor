import sys
from pathlib import Path
from datetime import date

import pandas as pd
import streamlit as st


# =========================================================
# PATH
# =========================================================

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# =========================================================
# PROJECT IMPORTS
# =========================================================

from src.db import connect, init
from src.reference_period import active_period

from src.projected_cutoff import (
    build_forecast,
    simulate_one_ticker,
    critical_run_rate,
    SCENARIO_MULTIPLIERS,
)


# =========================================================
# PAGE
# =========================================================

st.set_page_config(
    page_title="OMXC25 Predictor",
    page_icon="📊",
    layout="wide",
)

st.markdown(
    """
    <style>
        .block-container {
            padding-top: 1.6rem;
            padding-bottom: 3rem;
            max-width: 1500px;
        }

        h1 {
            margin-bottom: 0.15rem;
        }

        div[data-testid="stMetric"] {
            background: rgba(128,128,128,0.07);
            border: 1px solid rgba(128,128,128,0.18);
            padding: 12px 16px;
            border-radius: 10px;
            min-height: 92px;
        }

        div[data-testid="stMetricLabel"] {
            font-size: 0.88rem;
        }

        div[data-testid="stMetricValue"] {
            font-size: 1.38rem;
            line-height: 1.25;
        }

        div[data-testid="stMetricValue"] > div {
            font-size: 1.38rem;
        }

        div[data-testid="stDataFrame"] {
            border: 1px solid rgba(128,128,128,0.15);
            border-radius: 8px;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


st.title("OMXC25 Predictor")

st.caption(
    "Forecast baseret på OMXCPI-universet, "
    "free-float market cap / Top-35 eligibility "
    "og Nasdaqs officielle Turnover-data."
)


# =========================================================
# DATABASE
# =========================================================

init()
con = connect()


db_range = con.execute(
    """
    SELECT
        MIN(date) AS min_date,
        MAX(date) AS max_date
    FROM daily_market
    """
).df()


if (
    db_range.empty
    or pd.isna(db_range.loc[0, "min_date"])
    or pd.isna(db_range.loc[0, "max_date"])
):
    st.error(
        "Databasen indeholder ingen market-data."
    )
    con.close()
    st.stop()


min_db_date = pd.to_datetime(
    db_range.loc[0, "min_date"]
).date()

max_db_date = pd.to_datetime(
    db_range.loc[0, "max_date"]
).date()


# =========================================================
# DATA STATUS
# =========================================================

data_status = con.execute(
    """
    SELECT
        u.ticker,
        MAX(d.date) AS latest_date
    FROM universe u

    LEFT JOIN daily_market d
      ON d.ticker = u.ticker

    GROUP BY
        u.ticker

    ORDER BY
        u.ticker
    """
).df()


data_status["latest_date"] = pd.to_datetime(
    data_status["latest_date"]
).dt.date


latest_market_date = (
    data_status["latest_date"].max()
)

total_securities = len(
    data_status
)

latest_day_securities = int(
    (
        data_status["latest_date"]
        == latest_market_date
    ).sum()
)


st.markdown(
    "#### Datastatus"
)

status_cols = st.columns(3)

status_cols[0].metric(
    "Seneste market-data",
    latest_market_date.strftime(
        "%d-%m-%Y"
    ),
)

status_cols[1].metric(
    "OMXCPI securities",
    total_securities,
)

status_cols[2].metric(
    "Med observation på seneste dato",
    (
        f"{latest_day_securities}"
        f"/{total_securities}"
    ),
)

if (
    latest_day_securities
    < total_securities
):
    st.caption(
        "En aktie uden observation på den seneste "
        "handelsdato markeres ikke automatisk som en "
        "datafejl. For illikvide aktier kan årsagen være, "
        "at der ikke blev handlet den pågældende dag."
    )


# =========================================================
# REFERENCE PERIODS
# =========================================================

def build_reference_periods():

    periods = []

    for year in range(
        min_db_date.year,
        max_db_date.year + 2,
    ):

        # June review
        june_start = date(
            year - 1,
            12,
            1,
        )

        june_end = date(
            year,
            5,
            31,
        )

        if (
            june_end >= min_db_date
            and june_start <= max_db_date
        ):
            periods.append(
                {
                    "label":
                        f"Juni {year}",
                    "start":
                        june_start,
                    "end":
                        june_end,
                }
            )

        # December review
        december_start = date(
            year,
            6,
            1,
        )

        december_end = date(
            year,
            11,
            30,
        )

        if (
            december_end >= min_db_date
            and december_start <= max_db_date
        ):
            periods.append(
                {
                    "label":
                        f"December {year}",
                    "start":
                        december_start,
                    "end":
                        december_end,
                }
            )

    return sorted(
        periods,
        key=lambda p: p["start"],
        reverse=True,
    )


reference_periods = (
    build_reference_periods()
)

(
    current_start,
    current_end,
    current_review,
) = active_period()


# =========================================================
# SIDEBAR
# =========================================================

st.sidebar.title(
    "Indstillinger"
)


period_type = st.sidebar.radio(
    "Periode",
    [
        "Aktuel referenceperiode",
        "Tidligere referenceperiode",
        "Brugerdefineret periode",
    ],
)


if (
    period_type
    == "Aktuel referenceperiode"
):

    selected_start = (
        current_start
    )

    selected_end = (
        current_end
    )

    period_label = (
        current_review
    )


elif (
    period_type
    == "Tidligere referenceperiode"
):

    current_label = (
        f"December {current_start.year}"
        if current_start.month == 6
        else f"Juni {current_end.year}"
    )

    historical = [
        p
        for p in reference_periods
        if p["label"]
        != current_label
    ]

    if not historical:
        st.sidebar.warning(
            "Ingen tidligere referenceperioder "
            "er tilgængelige."
        )

        selected_start = (
            current_start
        )

        selected_end = (
            current_end
        )

        period_label = (
            current_review
        )

    else:

        selected_label = (
            st.sidebar.selectbox(
                "Referenceperiode",
                [
                    p["label"]
                    for p in historical
                ],
            )
        )

        selected_period = next(
            p
            for p in historical
            if p["label"]
            == selected_label
        )

        selected_start = (
            selected_period["start"]
        )

        selected_end = (
            selected_period["end"]
        )

        period_label = (
            selected_label
        )


else:

    selected_start = (
        st.sidebar.date_input(
            "Fra dato",
            value=max(
                min_db_date,
                current_start,
            ),
            min_value=min_db_date,
            max_value=max_db_date,
        )
    )

    selected_end = (
        st.sidebar.date_input(
            "Til dato",
            value=max_db_date,
            min_value=min_db_date,
            max_value=max_db_date,
        )
    )

    period_label = (
        "Brugerdefineret periode"
    )

    if (
        selected_start
        > selected_end
    ):
        st.error(
            "'Fra dato' skal være før "
            "'Til dato'."
        )

        con.close()
        st.stop()


st.sidebar.divider()

st.sidebar.subheader(
    "Trend"
)

trend_days = (
    st.sidebar.selectbox(
        "Trendperiode",
        [
            10,
            20,
            40,
            60,
        ],
        index=1,
        format_func=lambda x:
            f"{x} handelsdage",
    )
)

neutral_threshold = (
    st.sidebar.selectbox(
        "Neutral zone",
        [
            5,
            10,
            15,
            20,
        ],
        index=1,
        format_func=lambda x:
            f"± {x} %",
    )
)

st.sidebar.caption(
    "Trend sammenligner den gennemsnitlige "
    "handelsværdi i første og anden halvdel "
    "af den valgte trendperiode."
)


# =========================================================
# PERIOD HEADER
# =========================================================

header_cols = st.columns(4)

header_cols[0].metric(
    "Periode",
    period_label,
)

header_cols[1].metric(
    "Fra → til",
    (
        f"{selected_start:%d-%m-%Y} "
        f"→ {selected_end:%d-%m-%Y}"
    ),
)

header_cols[2].metric(
    "Seneste market-data",
    latest_market_date.strftime(
        "%d-%m-%Y"
    ),
)

header_cols[3].metric(
    "Aktier i OMXCPI-universet",
    total_securities,
)


# =========================================================
# ACTIVE FORECAST
# =========================================================

is_active_period = (
    selected_start
    == current_start
    and selected_end
    == current_end
)


if is_active_period:

    st.divider()

    st.header(
        "OMXC25 forecast"
    )

    try:

        forecast = (
            build_forecast()
        )

        forecast_df = (
            forecast[
                "table"
            ].copy()
        )

        st.caption(
            f"Market-data til og med "
            f"{forecast['market_cutoff']:%d-%m-%Y} · "
            f"Free-float snapshot "
            f"{forecast['free_float_date']:%d-%m-%Y} · "
            f"{forecast['elapsed_days']} observerede "
            f"handelsdage · "
            f"{forecast['remaining_days']} estimerede "
            f"handelsdage tilbage"
        )

        # -------------------------------------------------
        # MAIN FORECAST METRICS
        # -------------------------------------------------

        f1, f2, f3, f4 = (
            st.columns(4)
        )

        f1.metric(
            "Current #25",
            forecast[
                "current_cutoff_ticker"
            ],
        )

        f2.metric(
            "Current #26",
            forecast[
                "current_first_out_ticker"
            ],
            delta=(
                f"{forecast['current_margin'] / 1e6:,.1f} "
                f"mio. DKK bag cutoff"
            ),
            delta_color="off",
        )

        f3.metric(
            "Projected #25",
            forecast[
                "projected_cutoff_ticker"
            ],
        )

        f4.metric(
            "Projected #26",
            forecast[
                "projected_first_out_ticker"
            ],
            delta=(
                f"{forecast['projected_margin'] / 1e6:,.1f} "
                f"mio. DKK bag cutoff"
            ),
            delta_color="off",
        )

        # -------------------------------------------------
        # EXPECTED CHANGES
        # -------------------------------------------------

        st.subheader(
            "Forventede indeksændringer"
        )

        entrants = (
            forecast[
                "entrants"
            ]
        )

        exits = (
            forecast[
                "exits"
            ]
        )

        change_in, change_out = (
            st.columns(2)
        )

        with change_in:

            st.markdown(
                "**Forventede entrants**"
            )

            if entrants.empty:

                st.info(
                    "Ingen forventede entrants."
                )

            else:

                for _, row in (
                    entrants.iterrows()
                ):

                    st.success(
                        f"**{row['ticker']}** · "
                        f"projected liquidity rank "
                        f"#{int(row['projected_liquidity_rank'])} · "
                        f"projected turnover "
                        f"{row['projected_total_turnover'] / 1e9:.3f} "
                        f"mia. DKK"
                    )

        with change_out:

            st.markdown(
                "**Forventede exits**"
            )

            if exits.empty:

                st.info(
                    "Ingen forventede exits."
                )

            else:

                for _, row in (
                    exits.iterrows()
                ):

                    st.error(
                        f"**{row['ticker']}** · "
                        f"projected liquidity rank "
                        f"#{int(row['projected_liquidity_rank'])} · "
                        f"projected turnover "
                        f"{row['projected_total_turnover'] / 1e9:.3f} "
                        f"mia. DKK"
                    )

        # -------------------------------------------------
        # FORECAST TABS
        # -------------------------------------------------

        (
            forecast_tab1,
            forecast_tab2,
            forecast_tab3,
        ) = st.tabs(
            [
                "🎯 Cutoff battle",
                "📐 Scenarier",
                "🏆 Top-35 free float",
            ]
        )

        # =================================================
        # FORECAST TAB 1
        # =================================================

        with forecast_tab1:

            st.subheader(
                "Kampen omkring liquidity cutoff"
            )

            battle = (
                forecast[
                    "battle"
                ].copy()
            )

            battle_display = (
                pd.DataFrame(
                    {
                        "Ticker":
                            battle[
                                "ticker"
                            ],

                        "C25 nu":
                            battle[
                                "current_c25"
                            ],

                        "FF rank":
                            battle[
                                "ff_market_cap_rank"
                            ],

                        "Liq. rank nu":
                            battle[
                                "current_liquidity_rank"
                            ],

                        "Projected rank":
                            battle[
                                "projected_liquidity_rank"
                            ],

                        "Status":
                            battle[
                                "prediction_status"
                            ],

                        "Turnover nu (mia. DKK)":
                            battle[
                                "accumulated_turnover"
                            ]
                            / 1e9,

                        "Projected turnover (mia. DKK)":
                            battle[
                                "projected_total_turnover"
                            ]
                            / 1e9,

                        "Run-rate (mio./dag)":
                            battle[
                                "avg_daily_turnover"
                            ]
                            / 1e6,

                        "Krævet run-rate (mio./dag)":
                            battle[
                                "required_future_avg"
                            ]
                            / 1e6,

                        "Krævet ændring (%)":
                            battle[
                                "required_uplift"
                            ]
                            * 100,

                        "Skal slå":
                            battle[
                                "projected_cutoff_competitor"
                            ],
                    }
                )
            )

            st.dataframe(
                battle_display,
                use_container_width=True,
                hide_index=True,
                height=510,
                column_config={
                    "C25 nu":
                        st.column_config.CheckboxColumn(
                            "C25 nu",
                        ),

                    "FF rank":
                        st.column_config.NumberColumn(
                            "FF rank",
                            format="%d",
                        ),

                    "Liq. rank nu":
                        st.column_config.NumberColumn(
                            "Liq. rank nu",
                            format="%d",
                        ),

                    "Projected rank":
                        st.column_config.NumberColumn(
                            "Projected rank",
                            format="%d",
                        ),

                    "Turnover nu (mia. DKK)":
                        st.column_config.NumberColumn(
                            "Turnover nu (mia. DKK)",
                            format="%.3f",
                        ),

                    "Projected turnover (mia. DKK)":
                        st.column_config.NumberColumn(
                            "Projected turnover (mia. DKK)",
                            format="%.3f",
                        ),

                    "Run-rate (mio./dag)":
                        st.column_config.NumberColumn(
                            "Run-rate (mio./dag)",
                            format="%.1f",
                        ),

                    "Krævet run-rate (mio./dag)":
                        st.column_config.NumberColumn(
                            "Krævet run-rate (mio./dag)",
                            format="%.1f",
                        ),

                    "Krævet ændring (%)":
                        st.column_config.NumberColumn(
                            "Krævet ændring (%)",
                            format="%.1f",
                        ),
                },
            )

            st.subheader(
                "Challengers uden for Top 25"
            )

            challengers = (
                forecast[
                    "challengers"
                ].copy()
            )

            challenger_display = (
                pd.DataFrame(
                    {
                        "Rank":
                            challengers[
                                "current_liquidity_rank"
                            ],

                        "Ticker":
                            challengers[
                                "ticker"
                            ],

                        "Turnover (mia. DKK)":
                            challengers[
                                "accumulated_turnover"
                            ]
                            / 1e9,

                        "Run-rate nu (mio./dag)":
                            challengers[
                                "avg_daily_turnover"
                            ]
                            / 1e6,

                        "Krævet run-rate (mio./dag)":
                            challengers[
                                "required_future_avg"
                            ]
                            / 1e6,

                        "Krævet uplift (%)":
                            challengers[
                                "required_uplift"
                            ]
                            * 100,

                        "Skal slå":
                            challengers[
                                "projected_cutoff_competitor"
                            ],
                    }
                )
            )

            st.dataframe(
                challenger_display,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Rank":
                        st.column_config.NumberColumn(
                            "Rank",
                            format="%d",
                        ),

                    "Turnover (mia. DKK)":
                        st.column_config.NumberColumn(
                            "Turnover (mia. DKK)",
                            format="%.3f",
                        ),

                    "Run-rate nu (mio./dag)":
                        st.column_config.NumberColumn(
                            "Run-rate nu (mio./dag)",
                            format="%.1f",
                        ),

                    "Krævet run-rate (mio./dag)":
                        st.column_config.NumberColumn(
                            "Krævet run-rate (mio./dag)",
                            format="%.1f",
                        ),

                    "Krævet uplift (%)":
                        st.column_config.NumberColumn(
                            "Krævet uplift (%)",
                            format="%.1f",
                        ),
                },
            )

        # =================================================
        # FORECAST TAB 2
        # =================================================

        with forecast_tab2:

            st.subheader(
                "Run-rate scenarie"
            )

            scenario_candidates = (
                forecast_df[
                    forecast_df[
                        "current_liquidity_rank"
                    ].between(
                        20,
                        32,
                    )
                ]
                .sort_values(
                    "current_liquidity_rank"
                )
            )

            candidate_list = (
                scenario_candidates[
                    "ticker"
                ].tolist()
            )

            default_index = 0

            if (
                "BAVA"
                in candidate_list
            ):
                default_index = (
                    candidate_list.index(
                        "BAVA"
                    )
                )

            scenario_ticker = (
                st.selectbox(
                    "Aktie",
                    candidate_list,
                    index=default_index,
                    key=(
                        "forecast_scenario_"
                        "ticker"
                    ),
                )
            )

            selected_security = (
                forecast_df[
                    forecast_df[
                        "ticker"
                    ]
                    == scenario_ticker
                ]
                .iloc[0]
            )

            critical = critical_run_rate(
                forecast_df,
                scenario_ticker,
                forecast["remaining_days"],
            )

            s1, s2, s3, s4 = (
                st.columns(4)
            )

            s1.metric(
                "Nuværende liquidity rank",
                f"#{int(selected_security['current_liquidity_rank'])}",
            )

            s2.metric(
                "Baseline projected rank",
                f"#{int(selected_security['projected_liquidity_rank'])}",
            )

            s3.metric(
                "Nuværende run-rate",
                (
                    f"{selected_security['avg_daily_turnover'] / 1e6:.1f} "
                    f"mio. DKK/dag"
                ),
            )

            if critical is not None:
                critical_pct = (
                    critical["multiplier"] * 100
                    if critical["multiplier"] is not None
                    else None
                )
                critical_label = (
                    "Minimum for Top 25"
                    if critical["currently_inside"]
                    else "Krævet for Top 25"
                )
                if critical_pct is None:
                    critical_delta = None
                elif critical["currently_inside"]:
                    reduction_pct = max(0.0, 100.0 - critical_pct)
                    critical_delta = (
                        f"{critical_pct:.0f}% af nuværende · "
                        f"kan falde {reduction_pct:.0f}%"
                    )
                else:
                    uplift_pct = critical_pct - 100.0
                    critical_delta = (
                        f"{critical_pct:.0f}% af nuværende · "
                        f"kræver {uplift_pct:+.0f}%"
                    )
                s4.metric(
                    critical_label,
                    f"{critical['required_avg'] / 1e6:.1f} mio. DKK/dag",
                    delta=critical_delta,
                    delta_color="off",
                )
            else:
                s4.metric("Kritisk run-rate", "–")

            scenario_rows = []

            for multiplier in (
                SCENARIO_MULTIPLIERS
            ):

                result = (
                    simulate_one_ticker(
                        forecast_df,
                        scenario_ticker,
                        multiplier,
                        forecast[
                            "remaining_days"
                        ],
                    )
                )

                if result is None:
                    continue

                scenario_rows.append(
                    {
                        "Run-rate":
                            f"{multiplier:.0%}",

                        "Projected rank":
                            result[
                                "rank"
                            ],

                        "Projected turnover (mia. DKK)":
                            result[
                                "turnover"
                            ]
                            / 1e9,

                        "Hurdle":
                            result[
                                "cutoff_ticker"
                            ],

                        "Hurdle turnover (mia. DKK)":
                            result[
                                "cutoff_turnover"
                            ]
                            / 1e9,

                        "Margin (mio. DKK)":
                            result[
                                "gap_to_cutoff"
                            ]
                            / 1e6,

                        "Status":
                            (
                                "IN"
                                if result[
                                    "inside"
                                ]
                                else "OUT"
                            ),
                    }
                )

            if (
                critical is not None
                and critical["multiplier"] is not None
            ):
                critical_result = simulate_one_ticker(
                    forecast_df,
                    scenario_ticker,
                    critical["multiplier"] + 1e-9,
                    forecast["remaining_days"],
                )

                if critical_result is not None:
                    critical_name = (
                        "Minimum Top 25"
                        if critical["currently_inside"]
                        else "Krævet Top 25"
                    )
                    scenario_rows.append(
                        {
                            "Run-rate": (
                                f"{critical_name} "
                                f"({critical['multiplier']:.0%})"
                            ),
                            "Projected rank": critical_result["rank"],
                            "Projected turnover (mia. DKK)": (
                                critical_result["turnover"] / 1e9
                            ),
                            "Hurdle": critical_result["cutoff_ticker"],
                            "Hurdle turnover (mia. DKK)": (
                                critical_result["cutoff_turnover"] / 1e9
                            ),
                            "Margin (mio. DKK)": (
                                critical_result["gap_to_cutoff"] / 1e6
                            ),
                            "Status": "THRESHOLD",
                        }
                    )

            scenario_df = (
                pd.DataFrame(
                    scenario_rows
                )
            )

            st.dataframe(
                scenario_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Projected rank":
                        st.column_config.NumberColumn(
                            "Projected rank",
                            format="%d",
                        ),

                    "Projected turnover (mia. DKK)":
                        st.column_config.NumberColumn(
                            "Projected turnover (mia. DKK)",
                            format="%.3f",
                        ),

                    "Hurdle turnover (mia. DKK)":
                        st.column_config.NumberColumn(
                            "Hurdle turnover (mia. DKK)",
                            format="%.3f",
                        ),

                    "Margin (mio. DKK)":
                        st.column_config.NumberColumn(
                            "Margin (mio. DKK)",
                            format="%.1f",
                        ),
                },
            )

            st.caption(
                "80/100/120 %-scenarierne ændrer kun den valgte "
                "akties fremtidige run-rate. Alle øvrige Top-35-aktier "
                "fortsætter på deres baseline run-rate. 'Krævet Top 25' "
                "viser den minimum run-rate, en aktie uden for Top 25 "
                "skal have resten af referenceperioden for at komme ind. "
                "'Minimum Top 25' viser tilsvarende, hvor langt en aktie "
                "inden for Top 25 kan reducere sin run-rate og stadig "
                "fastholde en Top-25-position."
            )

        # =================================================
        # FORECAST TAB 3
        # =================================================

        with forecast_tab3:

            st.subheader(
                "Top-35 efter free-float market cap"
            )

            top35 = (
                forecast_df
                .sort_values(
                    "ff_market_cap_rank"
                )
                .copy()
            )

            top35_display = (
                pd.DataFrame(
                    {
                        "FF rank":
                            top35[
                                "ff_market_cap_rank"
                            ],

                        "Ticker":
                            top35[
                                "ticker"
                            ],

                        "Selskab":
                            top35[
                                "company"
                            ],

                        "C25 nu":
                            top35[
                                "current_c25"
                            ],

                        "FF market cap (mia. DKK)":
                            top35[
                                "free_float_market_cap"
                            ]
                            / 1e9,

                        "Liq. rank":
                            top35[
                                "current_liquidity_rank"
                            ],

                        "Projected liq. rank":
                            top35[
                                "projected_liquidity_rank"
                            ],

                        "Forecast":
                            top35[
                                "prediction_status"
                            ],
                    }
                )
            )

            st.dataframe(
                top35_display,
                use_container_width=True,
                hide_index=True,
                height=1000,
                column_config={
                    "FF rank":
                        st.column_config.NumberColumn(
                            "FF rank",
                            format="%d",
                        ),

                    "C25 nu":
                        st.column_config.CheckboxColumn(
                            "C25 nu",
                        ),

                    "FF market cap (mia. DKK)":
                        st.column_config.NumberColumn(
                            "FF market cap (mia. DKK)",
                            format="%.3f",
                        ),

                    "Liq. rank":
                        st.column_config.NumberColumn(
                            "Liq. rank",
                            format="%d",
                        ),

                    "Projected liq. rank":
                        st.column_config.NumberColumn(
                            "Projected liq. rank",
                            format="%d",
                        ),
                },
            )

            st.caption(
                "Kun de 35 største OMXCPI-securities "
                "efter free-float market cap indgår i den "
                "efterfølgende liquidity-ranking."
            )

    except Exception as exc:

        st.error(
            "Forecast kunne ikke beregnes: "
            f"{type(exc).__name__}: {exc}"
        )


else:

    st.divider()

    st.info(
        "OMXC25 forecast vises kun for den aktive "
        "referenceperiode. Den valgte periode nedenfor "
        "vises som historisk/rå market-data-analyse."
    )


# =========================================================
# RAW / HISTORICAL TURNOVER DATA
# =========================================================

st.divider()

st.header(
    "Market-data analyse"
)

st.caption(
    "Denne sektion viser den rå turnover-ranking for den "
    "valgte periode. Den er ikke OMXC25 forecastet, fordi "
    "den ikke begrænses til Top-35 efter free-float "
    "market cap."
)


# =========================================================
# RAW RANKING
# =========================================================

ranking = con.execute(
    """
    WITH securities AS (

        SELECT ticker
        FROM universe

        UNION

        SELECT ticker
        FROM c25_membership_history

        WHERE valid_from <= ?

          AND (
              valid_to IS NULL
              OR valid_to >= ?
          )
    )

    SELECT
        s.ticker,

        COALESCE(
            u.company,
            s.ticker
        ) AS company,

        CASE

            WHEN EXISTS (

                SELECT 1

                FROM c25_membership_history h

                WHERE h.ticker
                    = s.ticker

                  AND h.valid_from
                    <= ?

                  AND (
                      h.valid_to
                        IS NULL

                      OR h.valid_to
                        >= ?
                  )
            )

            THEN TRUE

            ELSE FALSE

        END AS current_c25,

        COALESCE(
            SUM(d.turnover),
            0
        ) AS accumulated_turnover,

        COUNT(d.date)
            AS trading_days,

        MAX(d.date)
            AS latest_date

    FROM securities s

    LEFT JOIN universe u
      ON u.ticker
       = s.ticker

    LEFT JOIN daily_market d
      ON d.ticker
       = s.ticker

     AND d.date
         BETWEEN ?
         AND ?

    GROUP BY
        s.ticker,
        u.company

    ORDER BY
        accumulated_turnover DESC
    """,
    [
        selected_end,
        selected_end,
        selected_end,
        selected_end,
        selected_start,
        selected_end,
    ],
).df()


ranking[
    "raw_turnover_rank"
] = (
    ranking[
        "accumulated_turnover"
    ]
    .rank(
        method="first",
        ascending=False,
    )
    .astype(int)
)


ranking[
    "avg_daily_turnover"
] = (
    ranking[
        "accumulated_turnover"
    ]
    / ranking[
        "trading_days"
    ].replace(
        0,
        pd.NA,
    )
)


# =========================================================
# TREND DATA
# =========================================================

trend_data = con.execute(
    """
    SELECT
        ticker,
        date,
        turnover

    FROM daily_market

    WHERE date BETWEEN ? AND ?

    ORDER BY
        ticker,
        date
    """,
    [
        selected_start,
        selected_end,
    ],
).df()


trend_rows = []


for ticker, group in (
    trend_data.groupby(
        "ticker"
    )
):

    group = (
        group
        .sort_values(
            "date"
        )
        .tail(
            trend_days
        )
    )

    if len(group) < 4:

        trend_rows.append(
            {
                "ticker":
                    ticker,

                "trend":
                    "–",

                "trend_pct":
                    None,
            }
        )

        continue

    split = (
        len(group)
        // 2
    )

    first_half = (
        group
        .iloc[
            :split
        ][
            "turnover"
        ]
        .mean()
    )

    second_half = (
        group
        .iloc[
            split:
        ][
            "turnover"
        ]
        .mean()
    )

    if (
        not first_half
        or first_half == 0
    ):

        trend_rows.append(
            {
                "ticker":
                    ticker,

                "trend":
                    "–",

                "trend_pct":
                    None,
            }
        )

        continue

    change = (
        (
            second_half
            / first_half
        )
        - 1
    ) * 100

    if (
        change
        > neutral_threshold
    ):
        trend = (
            "↑ Op"
        )

    elif (
        change
        < -neutral_threshold
    ):
        trend = (
            "↓ Ned"
        )

    else:
        trend = (
            "→ Neutral"
        )

    trend_rows.append(
        {
            "ticker":
                ticker,

            "trend":
                trend,

            "trend_pct":
                change,
        }
    )


trend_df = (
    pd.DataFrame(
        trend_rows
    )
)


if not trend_df.empty:

    ranking = (
        ranking.merge(
            trend_df,
            on="ticker",
            how="left",
        )
    )

else:

    ranking["trend"] = (
        "–"
    )

    ranking["trend_pct"] = (
        None
    )


ranking[
    "turnover_bn"
] = (
    ranking[
        "accumulated_turnover"
    ]
    / 1_000_000_000
)


ranking[
    "avg_daily_m"
] = (
    ranking[
        "avg_daily_turnover"
    ]
    / 1_000_000
)


ranking = (
    ranking.sort_values(
        "raw_turnover_rank"
    )
)


# =========================================================
# RAW DISPLAY TABLE
# =========================================================

display = (
    ranking[
        [
            "raw_turnover_rank",
            "ticker",
            "company",
            "current_c25",
            "turnover_bn",
            "avg_daily_m",
            "trend",
            "trend_pct",
            "trading_days",
        ]
    ].copy()
)


display.columns = [
    "Raw turnover rank",
    "Ticker",
    "Selskab",
    "C25 ved periodeslut",
    "Handelsværdi (mia. DKK)",
    "Gns./dag (mio. DKK)",
    "Trend",
    "Trend (%)",
    "Handelsdage",
]


raw_column_config = {

    "Raw turnover rank":
        st.column_config.NumberColumn(
            "Raw turnover rank",
            format="%d",
            width="small",
        ),

    "Ticker":
        st.column_config.TextColumn(
            "Ticker",
            width="small",
        ),

    "Selskab":
        st.column_config.TextColumn(
            "Selskab",
            width="medium",
        ),

    "C25 ved periodeslut":
        st.column_config.CheckboxColumn(
            "C25 ved periodeslut",
            width="small",
        ),

    "Handelsværdi (mia. DKK)":
        st.column_config.NumberColumn(
            "Handelsværdi (mia. DKK)",
            format="%.3f",
        ),

    "Gns./dag (mio. DKK)":
        st.column_config.NumberColumn(
            "Gns./dag (mio. DKK)",
            format="%.1f",
        ),

    "Trend":
        st.column_config.TextColumn(
            "Trend",
            width="small",
        ),

    "Trend (%)":
        st.column_config.NumberColumn(
            "Trend (%)",
            format="%.1f",
        ),

    "Handelsdage":
        st.column_config.NumberColumn(
            "Handelsdage",
            format="%d",
        ),
}


# =========================================================
# RAW DATA TABS
# =========================================================

(
    raw_tab1,
    raw_tab2,
    raw_tab3,
) = st.tabs(
    [
        "📊 Samlet turnover-ranking",
        "🎯 Omkring rå #25",
        "📈 Momentum",
    ]
)


# ---------------------------------------------------------
# RAW TAB 1
# ---------------------------------------------------------

with raw_tab1:

    st.subheader(
        "Rå ranking efter "
        "akkumuleret handelsværdi"
    )

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        height=1080,
        column_config=(
            raw_column_config
        ),
    )


# ---------------------------------------------------------
# RAW TAB 2
# ---------------------------------------------------------

with raw_tab2:

    st.subheader(
        "Omkring rå turnover-rank #25"
    )

    raw_cutoff = display[
        (
            display[
                "Raw turnover rank"
            ]
            >= 18
        )
        &
        (
            display[
                "Raw turnover rank"
            ]
            <= 32
        )
    ]

    st.dataframe(
        raw_cutoff,
        use_container_width=True,
        hide_index=True,
        height=565,
        column_config=(
            raw_column_config
        ),
    )

    st.caption(
        "Dette er alene den rå turnover-ranking "
        "blandt securities i data-universet. "
        "Den må ikke forveksles med OMXC25 "
        "liquidity cutoff, som kun beregnes "
        "blandt Top-35 efter free-float market cap."
    )


# ---------------------------------------------------------
# RAW TAB 3
# ---------------------------------------------------------

with raw_tab3:

    st.subheader(
        f"Momentum – seneste "
        f"{trend_days} handelsdage"
    )

    trend_display = (
        display[
            [
                "Raw turnover rank",
                "Ticker",
                "Selskab",
                "Handelsværdi (mia. DKK)",
                "Gns./dag (mio. DKK)",
                "Trend",
                "Trend (%)",
            ]
        ].copy()
    )

    trend_display = (
        trend_display.sort_values(
            "Trend (%)",
            ascending=False,
            na_position="last",
        )
    )

    st.dataframe(
        trend_display,
        use_container_width=True,
        hide_index=True,
        height=1000,
        column_config={
            "Raw turnover rank":
                st.column_config.NumberColumn(
                    "Raw turnover rank",
                    format="%d",
                ),

            "Handelsværdi (mia. DKK)":
                st.column_config.NumberColumn(
                    "Handelsværdi (mia. DKK)",
                    format="%.3f",
                ),

            "Gns./dag (mio. DKK)":
                st.column_config.NumberColumn(
                    "Gns./dag (mio. DKK)",
                    format="%.1f",
                ),

            "Trend (%)":
                st.column_config.NumberColumn(
                    "Trend (%)",
                    format="%.1f",
                ),
        },
    )

    st.info(
        "Trend måler udviklingen i handelsaktiviteten "
        "– ikke aktiekursen. En positiv trend betyder, "
        "at den gennemsnitlige daglige handelsværdi er "
        "højere i den seneste halvdel af trendperioden."
    )


# =========================================================
# FOOTNOTES
# =========================================================

st.divider()


if (
    period_type
    == "Tidligere referenceperiode"
):

    st.warning(
        "Historiske rankings er endnu ikke komplette. "
        "Databasen indeholder ikke alle tidligere noterede "
        "OMXCPI-securities. Historisk coverage udbygges "
        "senere."
    )


st.caption(
    "Metode: Forecastet tager udgangspunkt i OMXCPI-universet. "
    "De 35 største securities efter estimeret free-float market "
    "cap går videre til liquidity-ranking, hvor Nasdaqs officielle "
    "Turnover-felt anvendes. Baseline-projektionen antager, at den "
    "hidtidige gennemsnitlige daglige handelsværdi fortsætter resten "
    "af referenceperioden. Free-float-estimater og fremskrivninger "
    "er modelinput og kan afvige fra Nasdaqs endelige reviewdata."
)


con.close()