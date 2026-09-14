from datetime import date
from pathlib import Path
import time

import pandas as pd
import requests

from src.db import connect, init


ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_FILE = ROOT / "config" / "universe.csv"

BASE_URL = (
    "https://api.nasdaq.com/api/nordic/"
    "instruments/{nasdaq_id}/summary"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "Chrome/120 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.nasdaq.com/",
}


def parse_number(value):
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    value = value.replace(",", "")

    try:
        return float(value)
    except ValueError:
        return None


def fetch_summary(nasdaq_id):
    url = BASE_URL.format(
        nasdaq_id=nasdaq_id
    )

    response = requests.get(
        url,
        params={
            "assetClass": "SHARES",
        },
        headers=HEADERS,
        timeout=60,
    )

    response.raise_for_status()

    payload = response.json()

    if (
        payload.get("data") is None
        or payload["data"].get("summaryData") is None
    ):
        return None

    return payload["data"]["summaryData"]


def main():
    init()

    universe = pd.read_csv(
        UNIVERSE_FILE
    )

    con = connect()

    snapshot_date = date.today()

    successful = 0
    failed = []

    print(
        f"Fetching Nasdaq summary data "
        f"for {len(universe)} securities"
    )

    print(
        f"Snapshot date: {snapshot_date}"
    )

    print("=" * 72)

    for _, row in universe.iterrows():
        ticker = row["ticker"]
        nasdaq_id = row["nasdaq_id"]

        try:
            summary = fetch_summary(
                nasdaq_id
            )

            if summary is None:
                failed.append(
                    (ticker, "No summary data")
                )
                continue

            shares = parse_number(
                summary.get(
                    "shares",
                    {}
                ).get("value")
            )

            market_cap = parse_number(
                summary.get(
                    "marketCap",
                    {}
                ).get("value")
            )

            if (
                shares is None
                or market_cap is None
            ):
                failed.append(
                    (
                        ticker,
                        "Missing shares or market cap",
                    )
                )
                continue

            con.execute(
                """
                INSERT OR REPLACE INTO
                    company_market_snapshot
                (
                    snapshot_date,
                    ticker,
                    shares,
                    market_cap,
                    source
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    snapshot_date,
                    ticker,
                    int(shares),
                    market_cap,
                    (
                        "Nasdaq Nordic "
                        "instrument summary"
                    ),
                ],
            )

            successful += 1

            print(
                f"{ticker:12} "
                f"shares={int(shares):>12,} "
                f"market_cap={market_cap:>18,.0f}"
            )

            time.sleep(0.05)

        except Exception as exc:
            failed.append(
                (
                    ticker,
                    str(exc),
                )
            )

    con.close()

    print()
    print("=" * 72)
    print(
        f"Successful: {successful}"
    )

    print(
        f"Failed: {len(failed)}"
    )

    if failed:
        print()
        print("FAILED SECURITIES")
        print("=" * 72)

        for ticker, error in failed:
            print(
                f"{ticker:12} {error}"
            )


if __name__ == "__main__":
    main()