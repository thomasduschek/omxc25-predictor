from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from .db import ROOT


UNIVERSE_FILE = ROOT / "config" / "universe.csv"
RAW_DIR = ROOT / "data" / "raw"


def get_universe():
    return pd.read_csv(UNIVERSE_FILE)


def build_url(nasdaq_id, from_date, to_date):
    return (
        f"https://api.nasdaq.com/api/nordic/instruments/{nasdaq_id}/chart/download"
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


def download_share(ticker, nasdaq_id):
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    to_date = date.today()

    # Første version henter ca. 10 års historik.
    # Senere ændrer vi dette til kun at hente nye handelsdage.
    from_date = to_date - timedelta(days=3650)

    url = build_url(nasdaq_id, from_date, to_date)

    print(f"Downloading {ticker}")
    print(url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120 Safari/537.36"
        ),
        "Accept": "application/json",
        "Referer": "https://www.nasdaq.com/",
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=60,
    )

    print(f"HTTP status: {response.status_code}")

    response.raise_for_status()

    payload = response.json()

    rows = payload["data"]["charts"]["rows"]

    if not rows:
        print(f"No data returned for {ticker}")
        return None

    df = pd.DataFrame(rows)

    output = pd.DataFrame()

    output["Date"] = df["dateTime"]
    output["Bid"] = df["bid"].apply(clean_number)
    output["Ask"] = df["ask"].apply(clean_number)
    output["Opening price"] = df["open"].apply(clean_number)
    output["High price"] = df["high"].apply(clean_number)
    output["Low price"] = df["low"].apply(clean_number)
    output["Closing price"] = df["close"].apply(clean_number)
    output["Average price"] = df["average"].apply(clean_number)
    output["Total volume"] = df["totalVolume"].apply(clean_number)
    output["Turnover"] = df["turnover"].apply(clean_number)
    output["Trades"] = df["trades"].apply(clean_number)

    # Sortér ældste dato først
    output["Date"] = pd.to_datetime(output["Date"])
    output = output.sort_values("Date")
    output["Date"] = output["Date"].dt.strftime("%Y-%m-%d")

    # Filnavnet passer til den eksisterende importers ticker-detektion
    output_file = (
        RAW_DIR /
        f"{ticker}_{to_date:%Y%m%d}_000000.csv"
    )

    # Nasdaq-formatet starter med "sep=;"
    csv_content = output.to_csv(
        sep=";",
        index=False,
        lineterminator="\n",
    )

    output_file.write_text(
        "sep=;\n" + csv_content,
        encoding="utf-8",
    )

    print(f"Rows downloaded: {len(output):,}")
    print(f"First date: {output['Date'].iloc[0]}")
    print(f"Last date: {output['Date'].iloc[-1]}")
    print(f"Saved: {output_file}")

    return output_file


def main():
    universe = get_universe()

    rows = universe.dropna(subset=["nasdaq_id"])

    if rows.empty:
        print("No Nasdaq IDs found in universe.csv")
        return

    for _, row in rows.iterrows():
        download_share(
            ticker=row["ticker"],
            nasdaq_id=row["nasdaq_id"],
        )


if __name__ == "__main__":
    main()