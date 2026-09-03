from pathlib import Path
import re

import pandas as pd

from .db import connect, init, ROOT


def detect_ticker(filename: str) -> str:
    match = re.match(r"(.+)_\d{8}_\d{6}\.csv$", filename)

    if not match:
        raise ValueError(
            f"Could not determine ticker from filename: {filename}"
        )

    return match.group(1).strip().upper()

def parse_number(value):
    if pd.isna(value):
        return None

    text = str(value).strip()

    if text == "":
        return None

    text = text.replace(",", "")

    try:
        return float(text)
    except ValueError:
        return None


def import_file(path: Path):
    ticker = detect_ticker(path.name)

    df = pd.read_csv(
        path,
        sep=";",
        skiprows=1,
    )

    required_columns = [
        "Date",
        "Opening price",
        "High price",
        "Low price",
        "Closing price",
        "Average price",
        "Total volume",
        "Turnover",
        "Trades",
    ]

    missing = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing:
        raise ValueError(
            f"{path.name} is missing columns: {missing}"
        )

    result = pd.DataFrame()

    result["date"] = pd.to_datetime(
        df["Date"],
        errors="coerce",
    ).dt.date

    result["ticker"] = ticker

    result["turnover"] = df["Turnover"].apply(
        parse_number
    )

    result["volume"] = df["Total volume"].apply(
        parse_number
    )

    result["trades"] = df["Trades"].apply(
        parse_number
    )

    result["vwap"] = df["Average price"].apply(
        parse_number
    )

    result["close"] = df["Closing price"].apply(
        parse_number
    )

    result["source_file"] = path.name

    result = result.dropna(
        subset=["date", "turnover"]
    )

    result = result.drop_duplicates(
        subset=["date", "ticker"]
    )

    con = connect()

    con.register(
        "market_data",
        result,
    )

    con.execute("""
        INSERT OR REPLACE INTO daily_market
        (
            date,
            ticker,
            turnover,
            volume,
            trades,
            vwap,
            close,
            source_file
        )
        SELECT
            date,
            ticker,
            turnover,
            volume,
            trades,
            vwap,
            close,
            source_file
        FROM market_data
    """)

    con.close()

    return ticker, len(result)


def main():
    init()

    raw_directory = ROOT / "data" / "raw"

    files = sorted(
        raw_directory.glob("*.csv")
    )

    if not files:
        print(
            "No Nasdaq CSV files found in data/raw/"
        )
        return

    total_rows = 0

    for file in files:
        ticker, rows = import_file(file)

        print(
            f"{ticker}: imported {rows:,} rows "
            f"from {file.name}"
        )

        total_rows += rows

    print()
    print(
        f"Total imported rows: {total_rows:,}"
    )


if __name__ == "__main__":
    main()