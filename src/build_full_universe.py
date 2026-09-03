from pathlib import Path
from io import BytesIO
import re

import pandas as pd
import requests

from src.db import ROOT


OUTPUT_FILE = ROOT / "config" / "universe.csv"
BACKUP_FILE = ROOT / "config" / "universe_40_backup.csv"

OMXCPI_URL = (
    "https://indexes.nasdaq.com/Index/ExportWeightings/"
    "OMXCPI"
    "?tradeDate=2026-09-03T00:00:00.000"
    "&timeOfDay=SOD"
)

SCREENER_URL = (
    "https://api.nasdaq.com/api/nordic/screener/shares"
    "?category=MAIN_MARKET"
    "&tableonly=true"
    "&page=1"
    "&size=1000"
    "&segment=LARGE_CAP"
    "&segment=MID_CAP"
    "&segment=SPAC"
    "&segment=SMALL_CAP"
    "&lang=en"
)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": "https://www.nasdaq.com/",
}


CURRENT_C25 = {
    "MAERSK A",
    "MAERSK B",
    "ALSYDB",
    "AMBU B",
    "CARL B",
    "COLO B",
    "DSV",
    "DANSKE",
    "DEMANT",
    "FLS",
    "GN",
    "GMAB",
    "ISS",
    "JYSK",
    "NKT",
    "NDA DK",
    "NOVO B",
    "NSIS B",
    "ORSTED",
    "PNDORA",
    "ROCK B",
    "RBREW",
    "TRYG",
    "VWS",
    "ZEAL",
}


def normalize_symbol(value):
    if value is None:
        return ""

    text = str(value).strip().upper()

    text = re.sub(r"\s+", " ", text)

    return text


def find_value(row, candidates):
    """
    Finder en værdi i Nasdaq JSON uanset små forskelle
    i feltnavne/store-små bogstaver.
    """

    lowered = {
        str(key).lower(): value
        for key, value in row.items()
    }

    for candidate in candidates:
        key = candidate.lower()

        if key in lowered:
            value = lowered[key]

            if value is not None and str(value).strip():
                return value

    return None


def download_omxcpi():
    print("Downloading official OMXCPI constituents...")

    response = requests.get(
        OMXCPI_URL,
        headers=HEADERS,
        timeout=60,
    )

    print(
        f"OMXCPI HTTP status: "
        f"{response.status_code}"
    )

    response.raise_for_status()

    content = BytesIO(response.content)

    excel = pd.ExcelFile(content)

    print(
        f"Excel sheets: {excel.sheet_names}"
    )

    # Læs først arket helt uden at antage,
    # hvor header-rækken ligger.
    content.seek(0)

    raw = pd.read_excel(
        content,
        sheet_name="Weightings",
        header=None,
    )

    header_row = None

    # Find automatisk rækken med de rigtige
    # Nasdaq-kolonneoverskrifter.
    for index, row in raw.iterrows():

        values = [
            str(value).strip()
            for value in row.tolist()
            if pd.notna(value)
        ]

        if (
            "Company Name" in values
            and "Security Symbol" in values
        ):
            header_row = index
            break

    if header_row is None:
        print()
        print("First rows received from Nasdaq:")
        print(raw.head(15).to_string(index=True))

        raise RuntimeError(
            "Could not locate OMXCPI column headers."
        )

    print(
        f"Header row found: {header_row}"
    )

    # Brug den fundne række som kolonnenavne.
    headers = [
        str(value).strip()
        if pd.notna(value)
        else f"unnamed_{i}"
        for i, value in enumerate(
            raw.iloc[header_row].tolist()
        )
    ]

    df = raw.iloc[
        header_row + 1:
    ].copy()

    df.columns = headers

    required = {
        "Company Name",
        "Security Symbol",
    }

    if not required.issubset(df.columns):
        raise RuntimeError(
            "Unexpected OMXCPI Excel format. "
            f"Columns: {list(df.columns)}"
        )

    df = df[
        [
            "Company Name",
            "Security Symbol",
        ]
    ].copy()

    df = df.dropna(
        subset=["Security Symbol"]
    )

    df["ticker"] = (
        df["Security Symbol"]
        .map(normalize_symbol)
    )

    df["company"] = (
        df["Company Name"]
        .astype(str)
        .str.strip()
    )

    df = df[
        df["ticker"] != ""
    ]

    df = df.drop_duplicates(
        subset=["ticker"]
    )

    print(
        f"OMXCPI securities found: "
        f"{len(df)}"
    )

    return df[
        ["ticker", "company"]
    ].reset_index(drop=True)

def download_screener():
    print()
    print(
        "Downloading Nasdaq Nordic "
        "Main Market instrument list..."
    )

    response = requests.get(
        SCREENER_URL,
        headers=HEADERS,
        timeout=60,
    )

    print(
        f"Screener HTTP status: "
        f"{response.status_code}"
    )

    response.raise_for_status()

    payload = response.json()

    rows = (
        payload
        .get("data", {})
        .get("instrumentListing", {})
        .get("rows", [])
    )

    if not rows:
        raise RuntimeError(
            "No instrument rows returned "
            "from Nasdaq screener."
        )

    print(
        f"Nordic instruments returned: "
        f"{len(rows)}"
    )

    result = []

    for row in rows:

        symbol = find_value(
            row,
            [
                "symbol",
                "ticker",
                "shortName",
                "shortname",
            ],
        )

        orderbook = find_value(
            row,
            [
                "orderBookId",
                "orderbookId",
                "orderBookID",
                "orderbookID",
                "instrumentId",
                "instrumentID",
            ],
        )

        isin = find_value(
            row,
            [
                "isin",
                "ISIN",
            ],
        )

        market = find_value(
            row,
            [
                "market",
                "marketName",
                "exchange",
                "mic",
                "listingMarket",
                "marketplace",
            ],
        )

        name = find_value(
            row,
            [
                "name",
                "companyName",
                "instrumentName",
                "fullName",
            ],
        )

        if symbol is None:
            continue

        result.append(
            {
                "ticker": normalize_symbol(
                    symbol
                ),
                "orderbook": orderbook,
                "isin": isin,
                "market": market,
                "nasdaq_name": name,
                "raw": row,
            }
        )

    return result


def format_tx(value):
    if value is None:
        return ""

    text = str(value).strip()

    if not text:
        return ""

    if text.upper().startswith("TX"):
        return text.upper()

    # Nasdaq API bruger OrderBook ID'et med TX-prefix
    # i chart-endpointet for danske aktier.
    if re.fullmatch(r"\d+", text):
        return f"TX{text}"

    return text


def choose_match(ticker, matches):
    if len(matches) == 1:
        return matches[0], "exact"

    if not matches:
        return None, "missing"

    # Hvis samme ticker findes flere steder i Norden,
    # prioriter Copenhagen/XCSE/DK.
    copenhagen = []

    for match in matches:
        haystack = " ".join(
            [
                str(match.get("market") or ""),
                str(match.get("isin") or ""),
                str(match.get("nasdaq_name") or ""),
            ]
        ).upper()

        if (
            "COPENHAGEN" in haystack
            or "XCSE" in haystack
            or "DENMARK" in haystack
            or "DANMARK" in haystack
        ):
            copenhagen.append(match)

    if len(copenhagen) == 1:
        return copenhagen[0], "copenhagen"

    return None, "ambiguous"


def main():

    # Bevar vores nuværende 40-liste som backup.
    if (
        OUTPUT_FILE.exists()
        and not BACKUP_FILE.exists()
    ):
        BACKUP_FILE.write_bytes(
            OUTPUT_FILE.read_bytes()
        )

        print(
            f"Backup created: "
            f"{BACKUP_FILE}"
        )

    omxcpi = download_omxcpi()

    if len(omxcpi) != 115:
        print()
        print(
            "WARNING: Expected 115 OMXCPI "
            f"securities, got {len(omxcpi)}."
        )

    screener = download_screener()

    by_symbol = {}

    for item in screener:
        by_symbol.setdefault(
            item["ticker"],
            [],
        ).append(item)

    output_rows = []
    unresolved = []

    print()
    print("=" * 70)
    print("OMXCPI -> NASDAQ ORDERBOOK MATCHING")
    print("=" * 70)

    for _, security in omxcpi.iterrows():

        ticker = security["ticker"]
        company = security["company"]

        matches = by_symbol.get(
            ticker,
            [],
        )

        match, status = choose_match(
            ticker,
            matches,
        )

        if match is None:

            tx = ""
            isin = ""

            unresolved.append(
                {
                    "ticker": ticker,
                    "company": company,
                    "status": status,
                    "matches": matches,
                }
            )

            print(
                f"? {ticker:<12} "
                f"{status}"
            )

        else:

            tx = format_tx(
                match["orderbook"]
            )

            isin = (
                str(match["isin"]).strip()
                if match["isin"]
                else ""
            )

            print(
                f"✓ {ticker:<12} "
                f"{tx:<14} "
                f"{isin}"
            )

        current_c25 = (
            1
            if ticker in CURRENT_C25
            else 0
        )

        tier = (
            0
            if current_c25
            else 1
        )

        output_rows.append(
            {
                "ticker": ticker,
                "company": company,
                "current_c25": current_c25,
                "tier": tier,

                # beholdes af hensyn til
                # eksisterende struktur
                "nasdaq_symbol": ticker.lower(),

                "nasdaq_id": tx,
                "isin": isin,
            }
        )

    output = pd.DataFrame(
        output_rows,
        columns=[
            "ticker",
            "company",
            "current_c25",
            "tier",
            "nasdaq_symbol",
            "nasdaq_id",
            "isin",
        ],
    )

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(
        f"OMXCPI securities: "
        f"{len(output)}"
    )

    print(
        f"Current C25: "
        f"{output['current_c25'].sum()}"
    )

    resolved = (
        output["nasdaq_id"]
        .astype(str)
        .str.startswith("TX")
        .sum()
    )

    print(
        f"TX IDs resolved: "
        f"{resolved}/{len(output)}"
    )

    print(
        f"Unresolved: "
        f"{len(unresolved)}"
    )

    if unresolved:

        print()
        print(
            "UNRESOLVED / AMBIGUOUS:"
        )

        for item in unresolved:
            print(
                f"- {item['ticker']}: "
                f"{item['status']}"
            )

        print()
        print(
            "universe.csv NOT overwritten "
            "because some securities "
            "could not be safely matched."
        )

        return

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    print()
    print(
        f"Complete universe written to:"
    )
    print(
        OUTPUT_FILE
    )


if __name__ == "__main__":
    main()