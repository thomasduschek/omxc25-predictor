import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
    
import streamlit as st
import pandas as pd
from datetime import date

from src.db import connect, init
from src.reference_period import active_period


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
    "Nasdaq handelsværdi i den valgte periode • "
    "Free-float/top-35 indarbejdes senere"
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

min_db_date = pd.to_datetime(
    db_range.loc[0, "min_date"]
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
        ON u.ticker = d.ticker
    GROUP BY u.ticker
    ORDER BY u.ticker
    """
).df()

data_status["latest_date"] = pd.to_datetime(
    data_status["latest_date"]
).dt.date

latest_market_date = data_status["latest_date"].max()

total_securities = len(data_status)

updated_mask = (
    data_status["latest_date"]
    == latest_market_date
)

updated_securities = int(
    updated_mask.sum()
)

lagging_tickers = data_status.loc[
    ~updated_mask,
    "ticker"
].tolist()


st.markdown("#### Datastatus")

if updated_securities == total_securities:

    st.success(
        f"✓ {updated_securities}/{total_securities} aktier "
        f"opdateret til "
        f"{latest_market_date:%d-%m-%Y}"
    )

else:

    st.warning(
        f"⚠ {updated_securities}/{total_securities} aktier "
        f"opdateret til "
        f"{latest_market_date:%d-%m-%Y}. "
        f"Mangler: {', '.join(lagging_tickers)}"
    )
    
max_db_date = pd.to_datetime(
    db_range.loc[0, "max_date"]
).date()


# =========================================================
# REFERENCE PERIODS
# =========================================================

def build_reference_periods():
    periods = []

    for year in range(
        min_db_date.year,
        max_db_date.year + 2
    ):
        # June review
        june_start = date(year - 1, 12, 1)
        june_end = date(year, 5, 31)

        if (
            june_end >= min_db_date
            and june_start <= max_db_date
        ):
            periods.append(
                {
                    "label": f"Juni {year}",
                    "start": june_start,
                    "end": june_end,
                }
            )

        # December review
        december_start = date(year, 6, 1)
        december_end = date(year, 11, 30)

        if (
            december_end >= min_db_date
            and december_start <= max_db_date
        ):
            periods.append(
                {
                    "label": f"December {year}",
                    "start": december_start,
                    "end": december_end,
                }
            )

    return sorted(
        periods,
        key=lambda p: p["start"],
        reverse=True,
    )


reference_periods = build_reference_periods()

current_start, current_end, current_review = active_period()


# =========================================================
# SIDEBAR
# =========================================================

st.sidebar.title("Indstillinger")

period_type = st.sidebar.radio(
    "Periode",
    [
        "Aktuel referenceperiode",
        "Tidligere referenceperiode",
        "Brugerdefineret periode",
    ],
)


if period_type == "Aktuel referenceperiode":

    selected_start = current_start
    selected_end = current_end
    period_label = current_review


elif period_type == "Tidligere referenceperiode":

    current_label = (
        f"December {current_start.year}"
        if current_start.month == 6
        else f"Juni {current_end.year}"
    )

    historical = [
        p
        for p in reference_periods
        if p["label"] != current_label
    ]

    selected_label = st.sidebar.selectbox(
        "Referenceperiode",
        [p["label"] for p in historical],
    )

    selected_period = next(
        p
        for p in historical
        if p["label"] == selected_label
    )

    selected_start = selected_period["start"]
    selected_end = selected_period["end"]
    period_label = selected_label


else:

    selected_start = st.sidebar.date_input(
        "Fra dato",
        value=max(
            min_db_date,
            current_start,
        ),
        min_value=min_db_date,
        max_value=max_db_date,
    )

    selected_end = st.sidebar.date_input(
        "Til dato",
        value=max_db_date,
        min_value=min_db_date,
        max_value=max_db_date,
    )

    period_label = "Brugerdefineret periode"

    if selected_start > selected_end:
        st.error(
            "'Fra dato' skal være før 'Til dato'."
        )
        st.stop()


st.sidebar.divider()

st.sidebar.subheader("Trend")

trend_days = st.sidebar.selectbox(
    "Trendperiode",
    [10, 20, 40, 60],
    index=1,
    format_func=lambda x: f"{x} handelsdage",
)

neutral_threshold = st.sidebar.selectbox(
    "Neutral zone",
    [5, 10, 15, 20],
    index=1,
    format_func=lambda x: f"± {x} %",
)

st.sidebar.caption(
    "Trend sammenligner den gennemsnitlige "
    "handelsværdi i første og anden halvdel "
    "af den valgte trendperiode."
)


# =========================================================
# RANKING
# =========================================================

ranking = con.execute(
    """
    SELECT
        u.ticker,
        u.company,
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM c25_membership_history h
                WHERE h.ticker = u.ticker
                  AND h.valid_from <= ?
                  AND (
                      h.valid_to IS NULL
                      OR h.valid_to >= ?
                  )
            )
            THEN TRUE
            ELSE FALSE
        END AS current_c25,
        COALESCE(SUM(d.turnover), 0) AS accumulated_turnover,
        COUNT(d.date) AS trading_days,
        MAX(d.date) AS latest_date
    FROM universe u
    LEFT JOIN daily_market d
      ON d.ticker = u.ticker
     AND d.date BETWEEN ? AND ?
    GROUP BY
        u.ticker,
        u.company
    ORDER BY accumulated_turnover DESC
    """,
    [
        selected_end,
        selected_end,
        selected_start,
        selected_end,
    ],
).df()

ranking["rank"] = (
    ranking["accumulated_turnover"]
    .rank(
        method="first",
        ascending=False,
    )
    .astype(int)
)

ranking["avg_daily_turnover"] = (
    ranking["accumulated_turnover"]
    / ranking["trading_days"].replace(0, pd.NA)
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
    ORDER BY ticker, date
    """,
    [selected_start, selected_end],
).df()


trend_rows = []

for ticker, group in trend_data.groupby("ticker"):

    group = (
        group
        .sort_values("date")
        .tail(trend_days)
    )

    if len(group) < 4:
        trend_rows.append(
            {
                "ticker": ticker,
                "trend": "–",
                "trend_pct": None,
            }
        )
        continue

    split = len(group) // 2

    first_half = (
        group
        .iloc[:split]["turnover"]
        .mean()
    )

    second_half = (
        group
        .iloc[split:]["turnover"]
        .mean()
    )

    if not first_half or first_half == 0:
        trend_rows.append(
            {
                "ticker": ticker,
                "trend": "–",
                "trend_pct": None,
            }
        )
        continue

    change = (
        (second_half / first_half) - 1
    ) * 100

    if change > neutral_threshold:
        trend = "↑ Op"

    elif change < -neutral_threshold:
        trend = "↓ Ned"

    else:
        trend = "→ Neutral"

    trend_rows.append(
        {
            "ticker": ticker,
            "trend": trend,
            "trend_pct": change,
        }
    )


trend_df = pd.DataFrame(trend_rows)

if not trend_df.empty:
    ranking = ranking.merge(
        trend_df,
        on="ticker",
        how="left",
    )
else:
    ranking["trend"] = "–"
    ranking["trend_pct"] = None


ranking["turnover_bn"] = (
    ranking["accumulated_turnover"]
    / 1_000_000_000
)

ranking["avg_daily_m"] = (
    ranking["avg_daily_turnover"]
    / 1_000_000
)

ranking = ranking.sort_values("rank")


# =========================================================
# DATE INFORMATION
# =========================================================

available_dates = (
    ranking["latest_date"]
    .dropna()
)

if len(available_dates) > 0:
    latest_market_date = pd.to_datetime(
        available_dates.max()
    ).date()
else:
    latest_market_date = None


# =========================================================
# HEADER METRICS
# =========================================================

c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "Periode",
    period_label,
)

c2.metric(
    "Fra → til",
    (
        f"{selected_start:%d-%m-%Y} "
        f"→ {selected_end:%d-%m-%Y}"
    ),
)

c3.metric(
    "Seneste handelsdato",
    (
        latest_market_date.strftime("%d-%m-%Y")
        if latest_market_date
        else "Ingen data"
    ),
)

c4.metric(
    "Aktier i modellen",
    len(ranking),
)


# =========================================================
# TOP-25 STATUS
# =========================================================

st.divider()

rank25 = ranking[
    ranking["rank"] == 25
]

rank26 = ranking[
    ranking["rank"] == 26
]

if not rank25.empty and not rank26.empty:

    r25 = rank25.iloc[0]
    r26 = rank26.iloc[0]

    gap = (
        r25["accumulated_turnover"]
        - r26["accumulated_turnover"]
    )

    gap_pct = (
        gap
        / r26["accumulated_turnover"]
        * 100
        if r26["accumulated_turnover"] > 0
        else 0
    )

    a, b, c = st.columns(3)

    a.metric(
    "Nr. 25",
    f"{r25['ticker']} · {r25['turnover_bn']:.3f} mia. DKK",
)

b.metric(
    "Nr. 26",
    f"{r26['ticker']} · {r26['turnover_bn']:.3f} mia. DKK",
)

c.metric(
    "Afstand 25 → 26",
    f"{gap / 1_000_000:.1f} mio. DKK · {gap_pct:.1f} %",
)


# =========================================================
# DISPLAY TABLE
# =========================================================

display = ranking[
    [
        "rank",
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

display.columns = [
    "Rank",
    "Ticker",
    "Selskab",
    "C25 nu",
    "Handelsværdi (mia. DKK)",
    "Gns./dag (mio. DKK)",
    "Trend",
    "Trend (%)",
    "Handelsdage",
]


column_config = {
    "Rank": st.column_config.NumberColumn(
        "Rank",
        format="%d",
        width="small",
    ),
    "Ticker": st.column_config.TextColumn(
        "Ticker",
        width="small",
    ),
    "Selskab": st.column_config.TextColumn(
        "Selskab",
        width="medium",
    ),
    "C25 nu": st.column_config.CheckboxColumn(
        "C25 nu",
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
    "Trend": st.column_config.TextColumn(
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
# TABS
# =========================================================

tab1, tab2, tab3 = st.tabs(
    [
        "📊 Samlet ranking",
        "🎯 Top-25 kampen",
        "📈 Trend",
    ]
)


# ---------------------------------------------------------
# TAB 1
# ---------------------------------------------------------

with tab1:

    st.subheader(
        "Ranking efter akkumuleret handelsværdi"
    )

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        height=1080,
        column_config=column_config,
    )


# ---------------------------------------------------------
# TAB 2
# ---------------------------------------------------------

with tab2:

    st.subheader(
        "Omkring top-25-grænsen"
    )

    cutoff = display[
        (display["Rank"] >= 18)
        & (display["Rank"] <= 32)
    ]

    st.dataframe(
        cutoff,
        use_container_width=True,
        hide_index=True,
        height=565,
        column_config=column_config,
    )

    st.caption(
        "Viser position 18–32, så både de nederste "
        "C25-aktier og de nærmeste challengers kan "
        "følges samlet."
    )


# ---------------------------------------------------------
# TAB 3
# ---------------------------------------------------------

with tab3:

    st.subheader(
        f"Momentum – seneste {trend_days} handelsdage"
    )

    trend_display = display[
        [
            "Rank",
            "Ticker",
            "Selskab",
            "Handelsværdi (mia. DKK)",
            "Gns./dag (mio. DKK)",
            "Trend",
            "Trend (%)",
        ]
    ].copy()

    trend_display = trend_display.sort_values(
        "Trend (%)",
        ascending=False,
        na_position="last",
    )

    st.dataframe(
        trend_display,
        use_container_width=True,
        hide_index=True,
        height=1000,
        column_config={
            "Rank":
                st.column_config.NumberColumn(
                    "Rank",
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
        "Trend er et mål for udviklingen i "
        "handelsaktiviteten – ikke aktiekursen. "
        "En positiv trend betyder, at den gennemsnitlige "
        "daglige handelsværdi er højere i den seneste "
        "halvdel af trendperioden."
    )


# =========================================================
# FOOTNOTES
# =========================================================

st.divider()

if period_type == "Tidligere referenceperiode":
    st.warning(
        "Historiske rankings er endnu ikke komplette. "
        "Databasen indeholder ikke alle tidligere noterede "
        "OMXCPI-aktier. Det historiske univers udbygges senere."
    )

st.caption(
    "Datagrundlag: Nasdaqs officielle Turnover-felt. "
    "Rankingen viser handelsværdi og er endnu ikke den "
    "endelige OMXC25-forudsigelse. Free-float market cap "
    "og top-35-kriteriet tilføjes som næste modeltrin."
)

con.close()