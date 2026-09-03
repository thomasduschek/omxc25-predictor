import pandas as pd
import requests

from .db import ROOT


UNIVERSE_FILE = ROOT / "config" / "universe.csv"


def search_instrument(query):
    url = "https://api.nasdaq.com/api/autocomplete/slookup/10"

    params = {
        "search": query,
    }

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
        params=params,
        headers=headers,
        timeout=30,
    )

    print()
    print(f"Query: {query}")
    print(f"HTTP status: {response.status_code}")

    response.raise_for_status()

    return response.json()


def main():
    df = pd.read_csv(UNIVERSE_FILE)

    test_tickers = [
        "NOVO B",
        "DSV",
        "DANSKE",
        "GN",
        "NDA DK",
    ]

    for ticker in test_tickers:
        row = df[df["ticker"] == ticker]

        if row.empty:
            print(f"{ticker}: not found in universe.csv")
            continue

        company = row.iloc[0]["company"]

        print("=" * 70)
        print(f"{ticker} — {company}")

        payload = search_instrument(company)

        print(payload)


if __name__ == "__main__":
    main()