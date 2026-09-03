from datetime import date
from io import BytesIO

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
    official_df, source_url = (
        download_official_c25()
    )

    official = set(
        official_df["Security Symbol"]
    )

    expected = get_expected_c25()

    added = sorted(
        official - expected
    )

    removed = sorted(
        expected - official
    )

    print("=" * 72)
    print("OFFICIAL OMXC25 CONSTITUENT CHECK")
    print("=" * 72)

    print()
    print("Source:")
    print(source_url)

    print()
    print(
        "Official Nasdaq components:",
        len(official),
    )

    print(
        "Expected active components:",
        len(expected),
    )

    print()

    if len(official) != 25:
        print(
            "WARNING: Nasdaq export does not "
            "contain exactly 25 securities."
        )
        print(
            "No automatic action should be taken."
        )

    print("ADDED:")
    if added:
        for ticker in added:
            print(f"  + {ticker}")
    else:
        print("  None")

    print()

    print("REMOVED:")
    if removed:
        for ticker in removed:
            print(f"  - {ticker}")
    else:
        print("  None")

    print()

    if (
        len(official) == 25
        and not added
        and not removed
    ):
        print(
            "STATUS: OK - official Nasdaq OMXC25 "
            "matches local membership history."
        )
    elif len(official) == 25:
        print(
            "STATUS: CHANGE DETECTED"
        )
        print(
            "Nasdaq OMXC25 differs from the "
            "local membership history."
        )
    else:
        print(
            "STATUS: INVALID COMPONENT COUNT"
        )

    print()
    print("OFFICIAL COMPONENTS")
    print("-" * 72)

    for _, row in official_df.sort_values(
        "Security Symbol"
    ).iterrows():
        print(
            f"{row['Security Symbol']:12} "
            f"{row['Company Name']}"
        )


if __name__ == "__main__":
    main()