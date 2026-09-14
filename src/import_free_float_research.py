from pathlib import Path

import pandas as pd

from src.db import connect, init


ROOT = Path(__file__).resolve().parents[1]
RESEARCH_FILE = ROOT / "config" / "free_float_research.csv"


def as_bool(value):
    if pd.isna(value):
        return None

    value = str(value).strip().lower()

    if value in {"true", "1", "yes"}:
        return True

    if value in {"false", "0", "no"}:
        return False

    return None


def clean_float(value):
    if pd.isna(value) or value == "":
        return None

    return float(value)


def clean_int(value):
    if pd.isna(value) or value == "":
        return None

    return int(float(value))


def main():
    init()

    df = pd.read_csv(RESEARCH_FILE)

    required = {
        "ticker",
        "as_of_date",
        "data_type",
        "owner",
        "share_pct",
        "vote_pct",
        "threshold_operator",
        "threshold_pct",
        "owner_type",
        "include_in_float",
        "shares",
        "source_type",
        "source",
        "confidence",
        "notes",
    }

    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing columns: {sorted(missing)}"
        )

    con = connect()

    ownership_count = 0
    treasury_count = 0

    for _, row in df.iterrows():
        ticker = str(row["ticker"]).strip()
        as_of_date = str(row["as_of_date"]).strip()
        data_type = str(row["data_type"]).strip().lower()
        source = str(row["source"]).strip()

        if data_type == "ownership":
            owner = str(row["owner"]).strip()

            con.execute(
                """
                INSERT OR REPLACE INTO ownership_positions (
                    as_of_date,
                    ticker,
                    owner,
                    share_pct,
                    vote_pct,
                    threshold_operator,
                    threshold_pct,
                    owner_type,
                    include_in_float,
                    source_type,
                    source,
                    confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    as_of_date,
                    ticker,
                    owner,
                    clean_float(row["share_pct"]),
                    clean_float(row["vote_pct"]),
                    (
                        None
                        if pd.isna(row["threshold_operator"])
                        else str(row["threshold_operator"]).strip()
                    ),
                    clean_float(row["threshold_pct"]),
                    (
                        None
                        if pd.isna(row["owner_type"])
                        else str(row["owner_type"]).strip()
                    ),
                    as_bool(row["include_in_float"]),
                    (
                        None
                        if pd.isna(row["source_type"])
                        else str(row["source_type"]).strip()
                    ),
                    source,
                    clean_float(row["confidence"]),
                ],
            )

            ownership_count += 1

        elif data_type == "treasury":
            con.execute(
                """
                INSERT OR REPLACE INTO treasury_shares (
                    as_of_date,
                    ticker,
                    shares,
                    share_pct,
                    source_type,
                    source,
                    confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    as_of_date,
                    ticker,
                    clean_int(row["shares"]),
                    clean_float(row["share_pct"]),
                    (
                        None
                        if pd.isna(row["source_type"])
                        else str(row["source_type"]).strip()
                    ),
                    source,
                    clean_float(row["confidence"]),
                ],
            )

            treasury_count += 1

        else:
            raise ValueError(
                f"Unknown data_type '{data_type}' for {ticker}"
            )

    con.close()

    print("FREE FLOAT RESEARCH IMPORT")
    print("=" * 72)
    print(f"Ownership rows imported: {ownership_count}")
    print(f"Treasury rows imported:  {treasury_count}")
    print(f"Total rows processed:    {len(df)}")


if __name__ == "__main__":
    main()