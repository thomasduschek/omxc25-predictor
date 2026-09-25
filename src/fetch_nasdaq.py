from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from .db import connect, ROOT


# =========================================================
# CONFIG
# =========================================================

UNIVERSE_FILE = ROOT / "config" / "universe.csv"
RAW_DIR = ROOT / "data" / "raw"

HISTORY_DAYS = 3650
OVERLAP_DAYS = 7

BASE_URL = (
    "https://api.nasdaq.com/api/nordic/"
    "instruments/{nasdaq_id}/chart/download"
)


# =========================================================
# UNIVERSE
# =========================================================

def get_universe():
    return pd.read_csv(
        UNIVERSE_FILE
    )


# =========================================================
# DATABASE STATUS
# =========================================================

def get_latest_dates():
    """
    Return latest actual trading row in daily_market
    for each ticker.

    Note:
    A ticker can legitimately have an older MAX(date)
    than the general market date if no trade occurred
    on subsequent exchange days.
    """

    con = connect()

    df = con.execute(
        """
        SELECT
            ticker,
            MAX(date) AS latest_date
        FROM daily_market
        GROUP BY ticker
        """
    ).df()

    con.close()

    if df.empty:
        return {}

    return {
        row["ticker"]:
            pd.to_datetime(
                row["latest_date"]
            ).date()

        for _, row in df.iterrows()
    }


# =========================================================
# URL
# =========================================================

def build_url(
    nasdaq_id,
    from_date,
    to_date,
):
    return (
        BASE_URL.format(
            nasdaq_id=nasdaq_id
        )
        + "?assetClass=SHARES"
        + f"&fromDate={from_date:%Y-%m-%d}"
        + f"&toDate={to_date:%Y-%m-%d}"
    )


# =========================================================
# VALUE CLEANING
# =========================================================

def clean_number(value):

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass

    text = str(value).strip()

    if text in (
        "",
        "-",
        "None",
        "null",
        "N/A",
    ):
        return None

    return text.replace(",", "")


def has_trade_value(value):
    """
    True only if Nasdaq supplied an actual turnover value.

    Empty turnover means that Nasdaq has a market-date row
    but no trade took place in the security.
    """

    cleaned = clean_number(value)

    return cleaned is not None


# =========================================================
# DOWNLOAD ONE SECURITY
# =========================================================

def download_share(
    ticker,
    nasdaq_id,
    latest_date=None,
):
    RAW_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    to_date = date.today()

    if latest_date is None:

        # Entire initial history.
        from_date = (
            to_date
            - timedelta(
                days=HISTORY_DAYS
            )
        )

        mode = "historical"

    else:

        # Re-fetch with overlap so corrections in recent
        # market-data can also be picked up.
        from_date = (
            latest_date
            - timedelta(
                days=OVERLAP_DAYS
            )
        )

        mode = "incremental"

    if from_date > to_date:
        from_date = to_date

    url = build_url(
        nasdaq_id,
        from_date,
        to_date,
    )

    print()
    print(
        f"Downloading {ticker} "
        f"({mode})"
    )

    print(
        f"Period: "
        f"{from_date:%Y-%m-%d} "
        f"-> {to_date:%Y-%m-%d}"
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "Chrome/120 Safari/537.36"
        ),
        "Accept":
            "application/json",
        "Referer":
            "https://www.nasdaq.com/",
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            timeout=60,
        )

        print(
            f"HTTP status: "
            f"{response.status_code}"
        )

        response.raise_for_status()

        payload = response.json()

        rows = (
            payload
            .get(
                "data",
                {},
            )
            .get(
                "charts",
                {},
            )
            .get(
                "rows",
                [],
            )
        )

        # -------------------------------------------------
        # Truly empty API result
        # -------------------------------------------------

        if not rows:

            print(
                f"No Nasdaq rows returned "
                f"for {ticker}"
            )

            return {
                "ticker":
                    ticker,

                "status":
                    "FAILED",

                "file":
                    None,

                "rows":
                    0,

                "new_trade_rows":
                    0,

                "latest_api_date":
                    None,

                "message":
                    "Empty Nasdaq API response",
            }

        df = pd.DataFrame(
            rows
        )

        # -------------------------------------------------
        # Determine what the API actually contains
        # before transforming the file.
        # -------------------------------------------------

        api_dates = pd.to_datetime(
            df["dateTime"],
            errors="coerce",
        ).dt.date

        valid_dates = (
            api_dates.dropna()
        )

        latest_api_date = (
            max(valid_dates)
            if not valid_dates.empty
            else None
        )

        new_trade_rows = 0
        new_no_trade_rows = 0

        for _, api_row in df.iterrows():

            try:
                row_date = pd.to_datetime(
                    api_row.get(
                        "dateTime"
                    )
                ).date()

            except Exception:
                continue

            if (
                latest_date is not None
                and row_date <= latest_date
            ):
                continue

            if has_trade_value(
                api_row.get(
                    "turnover"
                )
            ):
                new_trade_rows += 1

            else:
                new_no_trade_rows += 1

        # -------------------------------------------------
        # Build Nasdaq-compatible CSV
        # -------------------------------------------------

        output = pd.DataFrame()

        output["Date"] = (
            df["dateTime"]
        )

        output["Bid"] = (
            df["bid"].apply(
                clean_number
            )
        )

        output["Ask"] = (
            df["ask"].apply(
                clean_number
            )
        )

        output["Opening price"] = (
            df["open"].apply(
                clean_number
            )
        )

        output["High price"] = (
            df["high"].apply(
                clean_number
            )
        )

        output["Low price"] = (
            df["low"].apply(
                clean_number
            )
        )

        output["Closing price"] = (
            df["close"].apply(
                clean_number
            )
        )

        output["Average price"] = (
            df["average"].apply(
                clean_number
            )
        )

        output["Total volume"] = (
            df[
                "totalVolume"
            ].apply(
                clean_number
            )
        )

        output["Turnover"] = (
            df["turnover"].apply(
                clean_number
            )
        )

        output["Trades"] = (
            df["trades"].apply(
                clean_number
            )
        )

        output["Date"] = (
            pd.to_datetime(
                output["Date"],
                errors="coerce",
            )
        )

        output = (
            output
            .dropna(
                subset=["Date"]
            )
            .sort_values(
                "Date"
            )
        )

        output["Date"] = (
            output["Date"]
            .dt.strftime(
                "%Y-%m-%d"
            )
        )

        output_file = (
            RAW_DIR
            / (
                f"{ticker}_"
                f"{to_date:%Y%m%d}_"
                f"000000.csv"
            )
        )

        csv_content = (
            output.to_csv(
                sep=";",
                index=False,
                lineterminator="\n",
            )
        )

        output_file.write_text(
            "sep=;\n"
            + csv_content,
            encoding="utf-8",
        )

        print(
            f"Rows downloaded: "
            f"{len(output):,}"
        )

        print(
            f"First date: "
            f"{output['Date'].iloc[0]}"
        )

        print(
            f"Last date: "
            f"{output['Date'].iloc[-1]}"
        )

        print(
            f"Saved: "
            f"{output_file}"
        )

        # -------------------------------------------------
        # STATUS
        # -------------------------------------------------

        if latest_date is None:

            # Initial history fetch.
            status = "UPDATED"

            message = (
                "Historical data downloaded"
            )

        elif new_trade_rows > 0:

            status = "UPDATED"

            message = (
                f"{new_trade_rows} "
                f"new trading row(s)"
            )

        else:

            status = (
                "NO_NEW_TRADES"
            )

            if new_no_trade_rows > 0:

                message = (
                    f"{new_no_trade_rows} "
                    f"new market-date row(s), "
                    f"but no turnover"
                )

            else:

                message = (
                    "No market dates newer "
                    "than latest trading row"
                )

        print(
            f"Update status: "
            f"{status}"
        )

        print(
            f"New trade rows: "
            f"{new_trade_rows}"
        )

        print(
            f"New no-trade rows: "
            f"{new_no_trade_rows}"
        )

        return {
            "ticker":
                ticker,

            "status":
                status,

            "file":
                output_file,

            "rows":
                len(output),

            "new_trade_rows":
                new_trade_rows,

            "new_no_trade_rows":
                new_no_trade_rows,

            "latest_api_date":
                latest_api_date,

            "message":
                message,
        }

    except Exception as exc:

        print(
            f"ERROR downloading "
            f"{ticker}: {exc}"
        )

        return {
            "ticker":
                ticker,

            "status":
                "FAILED",

            "file":
                None,

            "rows":
                0,

            "new_trade_rows":
                0,

            "new_no_trade_rows":
                0,

            "latest_api_date":
                None,

            "message":
                (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
        }


# =========================================================
# MAIN
# =========================================================

def main():

    universe = get_universe()

    rows = universe.dropna(
        subset=[
            "nasdaq_id",
        ]
    )

    if rows.empty:

        print(
            "No Nasdaq IDs found "
            "in universe.csv"
        )

        return

    latest_dates = (
        get_latest_dates()
    )

    print(
        f"Securities to update: "
        f"{len(rows)}"
    )

    print(
        f"Securities already in database: "
        f"{len(latest_dates)}"
    )

    updated = 0
    no_new_trades = 0
    failed = 0

    status_rows = []

    for _, row in rows.iterrows():

        ticker = (
            row["ticker"]
        )

        result = download_share(
            ticker=ticker,
            nasdaq_id=row[
                "nasdaq_id"
            ],
            latest_date=(
                latest_dates.get(
                    ticker
                )
            ),
        )

        status_rows.append(
            result
        )

        status = (
            result[
                "status"
            ]
        )

        if status == "UPDATED":
            updated += 1

        elif (
            status
            == "NO_NEW_TRADES"
        ):
            no_new_trades += 1

        else:
            failed += 1

    # =====================================================
    # SUMMARY
    # =====================================================

    print()
    print("=" * 80)
    print("DOWNLOAD SUMMARY")
    print("=" * 80)

    print(
        f"Updated:        "
        f"{updated}"
    )

    print(
        f"No new trades:  "
        f"{no_new_trades}"
    )

    print(
        f"Failed:         "
        f"{failed}"
    )

    # -----------------------------------------------------
    # No-trade securities
    # -----------------------------------------------------

    no_trade_results = [
        result
        for result in status_rows
        if result[
            "status"
        ] == "NO_NEW_TRADES"
    ]

    if no_trade_results:

        print()
        print(
            "NO NEW TRADES"
        )
        print(
            "=" * 80
        )

        for result in (
            no_trade_results
        ):

            latest_api_date = (
                result[
                    "latest_api_date"
                ]
            )

            print(
                f"{result['ticker']:10} | "
                f"API through="
                f"{latest_api_date} | "
                f"{result['message']}"
            )

    # -----------------------------------------------------
    # Failures
    # -----------------------------------------------------

    failures = [
        result
        for result in status_rows
        if result[
            "status"
        ] == "FAILED"
    ]

    if failures:

        print()
        print(
            "FAILED DOWNLOADS"
        )
        print(
            "=" * 80
        )

        for result in failures:

            print(
                f"{result['ticker']:10} | "
                f"{result['message']}"
            )


if __name__ == "__main__":
    main()