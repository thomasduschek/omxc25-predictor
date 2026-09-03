import pandas as pd

from .db import connect, init, ROOT


def main():
    init()

    universe_file = ROOT / "config" / "universe.csv"
    df = pd.read_csv(universe_file)

    required = [
        "ticker",
        "company",
        "isin",
        "current_c25",
    ]

    missing = [
        column
        for column in required
        if column not in df.columns
    ]

    if missing:
        raise ValueError(
            f"universe.csv is missing columns: {missing}"
        )

    con = connect()

    con.register("u", df)

    con.execute("DELETE FROM universe")

    con.execute("""
        INSERT INTO universe
        (
            ticker,
            company,
            isin,
            current_c25,
            free_float_market_cap,
            market_cap_rank,
            top35_eligible
        )
        SELECT
            ticker,
            company,
            isin,
            current_c25,
            NULL,
            NULL,
            NULL
        FROM u
    """)

    count = con.execute(
        "SELECT COUNT(*) FROM universe"
    ).fetchone()[0]

    con.close()

    print(f"Universe loaded: {count} securities")


if __name__ == "__main__":
    main()