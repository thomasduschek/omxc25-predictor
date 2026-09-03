from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.db import ROOT, connect, init, sync_current_c25


REVIEWS_FILE = ROOT / "config" / "c25_reviews.csv"


# Officiel OMXC25-startportefølje.
# Gældende fra 19. december 2016.
INITIAL_EFFECTIVE_DATE = date(2016, 12, 19)

INITIAL_MEMBERS = {
    "CARL B",
    "CHR",
    "COLO B",
    "DANSKE",
    "DENERG",
    "DSV",
    "FLS",
    "GEN",
    "GN",
    "ISS",
    "JYSK",
    "LUN",
    "MAERSK A",
    "MAERSK B",
    "NDA DKK",
    "NETS",
    "NKT",
    "NOVO B",
    "NZYM B",
    "PNDORA",
    "TDC",
    "TOP",
    "TRYG",
    "VWS",
    "WDH",
}


def split_tickers(value):
    if pd.isna(value):
        return []

    text = str(value).strip()

    if not text:
        return []

    return [
        ticker.strip()
        for ticker in text.split("|")
        if ticker.strip()
    ]


def load_reviews():
    df = pd.read_csv(
        REVIEWS_FILE,
        dtype=str,
        keep_default_na=False,
    )

    required = {
        "review",
        "effective_date",
        "added",
        "removed",
    }

    missing = required - set(df.columns)

    if missing:
        raise RuntimeError(
            f"Missing columns in c25_reviews.csv: "
            f"{sorted(missing)}"
        )

    df["effective_date"] = pd.to_datetime(
        df["effective_date"]
    ).dt.date

    return df.sort_values(
        "effective_date"
    ).reset_index(drop=True)


def validate_initial_members():
    if len(INITIAL_MEMBERS) != 25:
        raise RuntimeError(
            "Initial OMXC25 portfolio must contain "
            f"25 securities, found {len(INITIAL_MEMBERS)}."
        )


def build_history():
    validate_initial_members()

    reviews = load_reviews()

    current_members = set(INITIAL_MEMBERS)

    # For hvert aktivt medlemskab gemmer vi,
    # hvornår det begyndte.
    active_since = {
        ticker: INITIAL_EFFECTIVE_DATE
        for ticker in current_members
    }

    history = []

    print("=" * 72)
    print("BUILD OMXC25 MEMBERSHIP HISTORY")
    print("=" * 72)

    print()
    print(
        f"Initial portfolio "
        f"{INITIAL_EFFECTIVE_DATE}: "
        f"{len(current_members)} members"
    )

    for _, row in reviews.iterrows():

        review = row["review"]
        effective_date = row["effective_date"]

        added = split_tickers(
            row["added"]
        )

        removed = split_tickers(
            row["removed"]
        )

        # Ignorér reviews som ligger før eller på
        # den officielle startdato.
        if effective_date <= INITIAL_EFFECTIVE_DATE:
            continue

        print()
        print(
            f"{review} | effective "
            f"{effective_date}"
        )

        print(
            "Added:   "
            + (
                ", ".join(added)
                if added
                else "-"
            )
        )

        print(
            "Removed: "
            + (
                ", ".join(removed)
                if removed
                else "-"
            )
        )

        errors = []

        # Et selskab kan kun fjernes,
        # hvis det faktisk er medlem.
        for ticker in removed:
            if ticker not in current_members:
                errors.append(
                    f"{ticker} cannot be removed "
                    f"because it is not an active member."
                )

        # Et selskab kan kun tilføjes,
        # hvis det ikke allerede er medlem.
        for ticker in added:
            if ticker in current_members:
                errors.append(
                    f"{ticker} cannot be added "
                    f"because it is already an active member."
                )

        if errors:
            print()
            print("ERROR:")

            for error in errors:
                print(f"  - {error}")

            raise RuntimeError(
                f"Invalid membership change at "
                f"{review}."
            )

        # Luk medlemskaber for fjernede aktier.
        for ticker in removed:

            history.append(
                {
                    "ticker": ticker,
                    "valid_from": active_since[
                        ticker
                    ],
                    "valid_to": (
                        effective_date
                        - timedelta(days=1)
                    ),
                    "review": review,
                    "source": "Nasdaq",
                }
            )

            current_members.remove(
                ticker
            )

            del active_since[
                ticker
            ]

        # Åbn medlemskaber for nye aktier.
        for ticker in added:

            current_members.add(
                ticker
            )

            active_since[
                ticker
            ] = effective_date

        print(
            f"Members after change: "
            f"{len(current_members)}"
        )

        # Dette er den vigtigste kontrol.
        if (
        not review.endswith("-DELIST")
        and not review.endswith("-SPLIT")
        and not review.endswith("-MERGER")
        and not review.endswith("-RENAME")
        and len(current_members) != 25
        ):

            print()
            print("=" * 72)
            print("MEMBERSHIP VALIDATION FAILED")
            print("=" * 72)

            print(
                f"Expected 25 members, "
                f"found {len(current_members)} "
                f"after {review}."
            )

            print()
            print(
                "This normally means that the "
                "review file is missing a corporate "
                "action or another membership change."
            )

            print()
            print(
                "Current members:"
            )

            for ticker in sorted(
                current_members
            ):
                print(
                    f"  {ticker}"
                )

            raise RuntimeError(
                f"OMXC25 membership count is "
                f"{len(current_members)} "
                f"after {review}; expected 25."
            )

    # Aktive medlemskaber får valid_to = NULL.
    for ticker in sorted(
        current_members
    ):

        history.append(
            {
                "ticker": ticker,
                "valid_from": active_since[
                    ticker
                ],
                "valid_to": None,
                "review": "current",
                "source": "Nasdaq",
            }
        )

    history_df = pd.DataFrame(
        history
    )

    history_df = history_df.sort_values(
        [
            "valid_from",
            "ticker",
        ]
    ).reset_index(drop=True)

    return history_df


def write_database(history_df):

    init()

    con = connect()

    try:
        con.execute(
            "BEGIN TRANSACTION"
        )

        # Først når HELE historikken er valideret,
        # erstatter vi indholdet.
        con.execute(
            """
            DELETE FROM
                c25_membership_history
            """
        )

        con.register(
            "history_df",
            history_df,
        )

        con.execute(
            """
            INSERT INTO
                c25_membership_history
            (
                ticker,
                valid_from,
                valid_to,
                review,
                source
            )
            SELECT
                ticker,
                valid_from,
                valid_to,
                review,
                source
            FROM history_df
            """
        )

        con.execute(
            "COMMIT"
        )

    except Exception:
        con.execute(
            "ROLLBACK"
        )
        raise

    finally:
        con.close()


def main():

    history_df = build_history()

    print()
    print("=" * 72)
    print("HISTORY VALIDATED")
    print("=" * 72)

    print(
        f"Membership periods: "
        f"{len(history_df)}"
    )

    active = history_df[
        history_df["valid_to"].isna()
    ]

    print(
        f"Current members: "
        f"{len(active)}"
    )

    if len(active) != 25:
        raise RuntimeError(
            "Final active membership "
            "does not contain 25 securities."
        )

    write_database(
        history_df
    )

    sync_current_c25()

    print()
    print(
        "c25_membership_history "
        "written successfully."
    )

    print(
        "universe.current_c25 "
        "synchronized successfully."
    )

if __name__ == "__main__":
    main()