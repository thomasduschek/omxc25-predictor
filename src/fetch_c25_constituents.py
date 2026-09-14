from datetime import date
from io import BytesIO
import sys

import pandas as pd
import requests

from src.db import connect


INDEX_CODE = "OMXC25"

URL_TEMPLATE = (
    "https://indexes.nasdaq.com/"
    "Index/ExportWeightings/{index_code}"
    "?tradeDate={trade_date}T00:00:00.000"
    "&timeOfDay=SOD"
)


def download_official_c25():
    today = date.today()

    url = URL_TEMPLATE.format(
        index_code=INDEX_CODE,
        trade_date=today.strftime("%Y-%m-%d"),
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "Chrome/120 Safari/537.36"
        ),
        "Referer": "https://indexes.nasdaq.com/",
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=60,
    )

    response.raise_for_status()

    raw = pd.read_excel(
        BytesIO(response.content),
        sheet_name="Weightings",
        header=None,
    )

    # Find rækken med de faktiske kolonneoverskrifter.
    header_row = None

    for i, row in raw.iterrows():
        values = [
            str(value).strip()
            for value in row.tolist()
            if pd.notna(value)
        ]

        if (
            "Company Name" in values
            and "Security Symbol" in values
        ):
            header_row = i
            break

    if header_row is None:
        raise RuntimeError(
            "Could not find Nasdaq weighting headers."
        )

    df = pd.read_excel(
        BytesIO(response.content),
        sheet_name="Weightings",
        header=header_row,
    )

    if "Security Symbol" not in df.columns:
        raise RuntimeError(
            "Security Symbol column missing."
        )

    df = df[
        ["Company Name", "Security Symbol"]
    ].copy()

    df = df.dropna(
        subset=["Security Symbol"]
    )

    df["Security Symbol"] = (
        df["Security Symbol"]
        .astype(str)
        .str.strip()
    )

    df["Company Name"] = (
        df["Company Name"]
        .astype(str)
        .str.strip()
    )

    df = df[
        df["Security Symbol"] != ""
    ]

    df = df.drop_duplicates(
        subset=["Security Symbol"]
    )

    return df.reset_index(drop=True), url


def get_expected_c25():
    con = connect()

    rows = con.execute(
        """
        SELECT ticker
        FROM c25_membership_history
        WHERE valid_to IS NULL
        ORDER BY ticker
        """
    ).fetchall()

    con.close()

    return {
        row[0]
        for row in rows
    }


def main():
    init()
    con = connect()

    before = con.execute(
        """
        SELECT COUNT(*)
        FROM free_float_source_events
        """
    ).fetchone()[0]

    print("FREE FLOAT SOURCE COLLECTOR")
    print("=" * 72)
    print(f"Existing raw source events: {before}")
    print()

    nasdaq_events = collect_events()

    saved = 0

    for event in nasdaq_events:
        published = None

        if event.get("pub_date"):
            published = parsedate_to_datetime(
                event["pub_date"]
            ).date()

        save_event(
            con,
            ticker=event["ticker"],
            event_date=published,
            published_date=published,
            event_type=event["event_type"],
            title=event["title"],
            source_type="nasdaq_news",
            source_url=event["link"],
            source_id=event["guid"],
            raw_text=event.get("raw_text"),
            status="RAW",
            confidence=1.0,
        )

        saved += 1

    after = con.execute(
        """
        SELECT COUNT(*)
        FROM free_float_source_events
        """
    ).fetchone()[0]

    print(f"Nasdaq matched events:    {len(nasdaq_events)}")
    print(f"Nasdaq events processed:  {saved}")
    print(f"Events before:            {before}")
    print(f"Events after:             {after}")
    print(f"New database events:      {after - before}")

    con.close()

if __name__ == "__main__":
    main()