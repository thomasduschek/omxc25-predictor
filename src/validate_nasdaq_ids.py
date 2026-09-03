from datetime import date, timedelta

import pandas as pd
import requests

from .db import ROOT


UNIVERSE_FILE = ROOT / "config" / "universe.csv"


def main():
    df = pd.read_csv(UNIVERSE_FILE)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120 Safari/537.36"
        ),
        "Accept": "application/json",
        "Referer": "https://www.nasdaq.com/",
    }

    to_date = date.today()
    from_date = to_date - timedelta(days=10)

    results = []

    for _, row in df.iterrows():
        ticker = row["ticker"]
        expected_company = row["company"]
        nasdaq_id = row["nasdaq_id"]

        url = (
            f"https://api.nasdaq.com/api/nordic/instruments/"
            f"{nasdaq_id}/chart/download"
            f"?assetClass=SHARES"
            f"&fromDate={from_date:%Y-%m-%d}"
            f"&toDate={to_date:%Y-%m-%d}"
        )

        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=30,
            )

            if response.status_code != 200:
                print(
                    f"ERROR  {ticker:<10} "
                    f"{nasdaq_id:<12} HTTP {response.status_code}"
                )

                results.append(
                    {
                        "ticker": ticker,
                        "expected_company": expected_company,
                        "nasdaq_id": nasdaq_id,
                        "nasdaq_symbol": None,
                        "nasdaq_company": None,
                        "isin": None,
                        "status": f"HTTP {response.status_code}",
                    }
                )

                continue

            payload = response.json()

            chart_data = payload["data"]["chartData"]

            nasdaq_symbol = chart_data.get("symbol")
            nasdaq_company = chart_data.get("company")
            isin = chart_data.get("isin")

            print(
                f"OK     {ticker:<10} "
                f"{nasdaq_id:<12} "
                f"Nasdaq symbol={nasdaq_symbol:<12} "
                f"{nasdaq_company}"
            )

            results.append(
                {
                    "ticker": ticker,
                    "expected_company": expected_company,
                    "nasdaq_id": nasdaq_id,
                    "nasdaq_symbol": nasdaq_symbol,
                    "nasdaq_company": nasdaq_company,
                    "isin": isin,
                    "status": "OK",
                }
            )

        except Exception as exc:
            print(
                f"ERROR  {ticker:<10} "
                f"{nasdaq_id:<12} {exc}"
            )

            results.append(
                {
                    "ticker": ticker,
                    "expected_company": expected_company,
                    "nasdaq_id": nasdaq_id,
                    "nasdaq_symbol": None,
                    "nasdaq_company": None,
                    "isin": None,
                    "status": str(exc),
                }
            )

    result_df = pd.DataFrame(results)

    output_file = ROOT / "reports" / "nasdaq_id_validation.csv"
    output_file.parent.mkdir(exist_ok=True)

    result_df.to_csv(
        output_file,
        index=False,
    )

    print()
    print("=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)
    print(f"Total securities: {len(result_df)}")
    print(
        "Successful: "
        f"{(result_df['status'] == 'OK').sum()}"
    )
    print(
        "Errors: "
        f"{(result_df['status'] != 'OK').sum()}"
    )
    print()
    print(f"Saved to {output_file}")


if __name__ == "__main__":
    main()