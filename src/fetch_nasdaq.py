from datetime import date, timedelta

import pandas as pd
import requests

from .db import ROOT, connect


UNIVERSE_FILE = ROOT / "config" / "universe.csv"
RAW_DIR = ROOT / "data" / "raw"

# Nye aktier får op til ca. 10 års historik.
HISTORY_DAYS = 3650

# Genhent de seneste dage for at fange eventuelle
# efterfølgende korrektioner fra Nasdaq.
OVERLAP_DAYS = 3


def get_universe():
    return pd.read_csv(UNIVERSE_FILE)


def get_latest_dates():
    """
    Returnerer seneste dato i daily_market for hver ticker.
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
        row["ticker"]: pd.to_datetime(
            row["latest_date"]
        ).date()
        for _, row in df.iterrows()
    }


def build_url(nasdaq_id, from_date, to_date):
    return (
        f"https://api.nasdaq.com/api/nordic/instruments/"
        f"{nasdaq_id}/chart/download"
        f"?assetClass=SHARES"
        f"&fromDate={from_date:%Y-%m-%d}"
        f"&toDate={to_date:%Y-%m-%d}"
    )


def clean_number(value):
    if value is None:
        return None

    text = str(value).strip()

    if text == "":
        return None

    return text.replace(",", "")


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
        # Helt ny aktie i databasen:
        # hent historik.
        from_date = (
            to_date
            - timedelta(days=HISTORY_DAYS)
        )

        mode = "historical"

    else:
        # Eksisterende aktie:
        # genhent få dage med overlap.
        from_date = (
            latest_date
            - timedelta(days=OVERLAP_DAYS)
        )

        mode = "incremental"

    # Sikkerhed hvis databasen mod forventning
    # indeholder en fremtidig dato.
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
        "Accept": "application/json",
        "Referer": "https://www.nasdaq.com/",
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
            .get("data", {})
            .get("charts", {})
            .get("rows", [])
        )

        if not rows:
            print(
                f"No data returned for {ticker}"
            )
            return None

        df = pd.DataFrame(rows)

        output = pd.DataFrame()

        output["Date"] = df["dateTime"]

        output["Bid"] = (
            df["bid"].apply(clean_number)
        )

        output["Ask"] = (
            df["ask"].apply(clean_number)
        )

        output["Opening price"] = (
            df["open"].apply(clean_number)
        )

        output["High price"] = (
            df["high"].apply(clean_number)
        )

        output["Low price"] = (
            df["low"].apply(clean_number)
        )

        output["Closing price"] = (
            df["close"].apply(clean_number)
        )

        output["Average price"] = (
            df["average"].apply(clean_number)
        )

        output["Total volume"] = (
            df["totalVolume"].apply(
                clean_number
            )
        )

        output["Turnover"] = (
            df["turnover"].apply(
                clean_number
            )
        )

        output["Trades"] = (
            df["trades"].apply(clean_number)
        )

        output["Date"] = pd.to_datetime(
            output["Date"]
        )

        output = output.sort_values(
            "Date"
        )

        output["Date"] = (
            output["Date"]
            .dt.strftime("%Y-%m-%d")
        )

        output_file = (
            RAW_DIR
            / (
                f"{ticker}_"
                f"{to_date:%Y%m%d}_"
                f"000000.csv"
            )
        )

        csv_content = output.to_csv(
            sep=";",
            index=False,
            lineterminator="\n",
        )

        output_file.write_text(
            "sep=;\n" + csv_content,
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
            f"Saved: {output_file}"
        )

        return output_file

    except Exception as exc:
        print(
            f"ERROR downloading "
            f"{ticker}: {exc}"
        )

        return None


def main():
    universe = get_universe()

    rows = universe.dropna(
        subset=["nasdaq_id"]
    )

    if rows.empty:
        print(
            "No Nasdaq IDs found "
            "in universe.csv"
        )
        return

    latest_dates = get_latest_dates()

    print(
        f"Securities to update: "
        f"{len(rows)}"
    )

    print(
        f"Securities already in database: "
        f"{len(latest_dates)}"
    )

    successful = 0
    failed = 0

    for _, row in rows.iterrows():

        ticker = row["ticker"]

        result = download_share(
            ticker=ticker,
            nasdaq_id=row["nasdaq_id"],
            latest_date=latest_dates.get(
                ticker
            ),
        )

        if result is not None:
            successful += 1
        else:
            failed += 1

    print()
    print("=" * 50)
    print("DOWNLOAD SUMMARY")
    print("=" * 50)
    print(
        f"Successful: {successful}"
    )
    print(
        f"No data / errors: {failed}"
    )


if __name__ == "__main__":
        main()