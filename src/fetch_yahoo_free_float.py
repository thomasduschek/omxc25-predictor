import time

from datetime import date

import yfinance as yf

from src.db import connect


# =========================================================
# SYMBOL MAPPING
# =========================================================

def make_yahoo_symbol(ticker):
    """
    Convert internal OMX Copenhagen ticker to Yahoo Finance symbol.

    Examples:
        DANSKE    -> DANSKE.CO
        NOVO B    -> NOVO-B.CO
        NDA DK    -> NDA-DK.CO
        MAERSK A  -> MAERSK-A.CO
        TRMD A    -> TRMD-A.CO
    """

    symbol = ticker.strip().upper()
    symbol = symbol.replace(" ", "-")

    return f"{symbol}.CO"


# Known exceptions can be added here when needed.
YAHOO_SYMBOL_OVERRIDES = {
    # Example:
    # "SOME TICKER": "ACTUAL-YAHOO-SYMBOL.CO",
}


def get_yahoo_symbol(ticker):
    return YAHOO_SYMBOL_OVERRIDES.get(
        ticker,
        make_yahoo_symbol(ticker),
    )


# =========================================================
# DATABASE SETUP
# =========================================================

def ensure_table(con):
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS external_free_float_snapshot (
            as_of_date DATE,
            ticker VARCHAR,
            provider VARCHAR,
            provider_symbol VARCHAR,
            free_float_pct DOUBLE,
            free_float_shares BIGINT,
            total_shares BIGINT,
            company_owned_shares BIGINT,
            held_percent_insiders DOUBLE,
            held_percent_institutions DOUBLE,
            market_cap DOUBLE,
            quality_status VARCHAR,
            source_url VARCHAR,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (
                as_of_date,
                ticker,
                provider
            )
        )
        """
    )

    columns = {
        row[1]
        for row in con.execute(
            "PRAGMA table_info('external_free_float_snapshot')"
        ).fetchall()
    }

    additions = [
        ("provider_symbol", "VARCHAR"),
        ("held_percent_insiders", "DOUBLE"),
        ("held_percent_institutions", "DOUBLE"),
        ("market_cap", "DOUBLE"),
        ("quality_status", "VARCHAR"),
    ]

    for column, datatype in additions:
        if column not in columns:
            con.execute(
                f"""
                ALTER TABLE external_free_float_snapshot
                ADD COLUMN {column} {datatype}
                """
            )


# =========================================================
# FETCH ONE SECURITY
# =========================================================

def fetch_yahoo_record(
    internal_ticker,
    yahoo_symbol,
):
    info = yf.Ticker(yahoo_symbol).info

    shares = info.get(
        "sharesOutstanding"
    )

    float_shares = info.get(
        "floatShares"
    )

    insiders = info.get(
        "heldPercentInsiders"
    )

    institutions = info.get(
        "heldPercentInstitutions"
    )

    market_cap = info.get(
        "marketCap"
    )

    short_name = info.get(
        "shortName"
    )

    symbol = info.get(
        "symbol"
    )

    free_float_pct = None
    company_owned_shares = None

    if (
        shares is not None
        and float_shares is not None
        and shares > 0
    ):
        free_float_pct = (
            float_shares
            / shares
            * 100.0
        )

        company_owned_shares = (
            shares
            - float_shares
        )

    # =====================================================
    # QUALITY STATUS
    # =====================================================

    if not symbol and not short_name:
        quality_status = "NO_YAHOO_DATA"

    elif shares is None:
        quality_status = "MISSING_SHARES"

    elif float_shares is None:
        quality_status = "MISSING_FLOAT"

    elif shares <= 0:
        quality_status = "INVALID_SHARES"

    elif float_shares < 0:
        quality_status = "INVALID_FLOAT"

    elif float_shares > shares:
        quality_status = "FLOAT_GT_SHARES"

    elif (
        free_float_pct is not None
        and not (0 <= free_float_pct <= 100)
    ):
        quality_status = "INVALID_FLOAT"

    else:
        quality_status = "OK"

    return {
        "ticker": internal_ticker,
        "provider_symbol": yahoo_symbol,
        "total_shares": shares,
        "free_float_shares": float_shares,
        "free_float_pct": free_float_pct,
        "company_owned_shares": company_owned_shares,
        "held_percent_insiders": (
            insiders * 100.0
            if insiders is not None
            else None
        ),
        "held_percent_institutions": (
            institutions * 100.0
            if institutions is not None
            else None
        ),
        "market_cap": market_cap,
        "quality_status": quality_status,
        "source_url": (
            "https://finance.yahoo.com/quote/"
            f"{yahoo_symbol}/key-statistics/"
        ),
        "short_name": short_name,
        "yahoo_symbol_returned": symbol,
    }


# =========================================================
# SAVE
# =========================================================

def save_record(
    con,
    as_of_date,
    record,
):
    con.execute(
        """
        INSERT OR REPLACE INTO external_free_float_snapshot (
            as_of_date,
            ticker,
            provider,
            provider_symbol,
            free_float_pct,
            free_float_shares,
            total_shares,
            company_owned_shares,
            held_percent_insiders,
            held_percent_institutions,
            market_cap,
            quality_status,
            source_url
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            as_of_date,
            record["ticker"],
            "YAHOO",
            record["provider_symbol"],
            record["free_float_pct"],
            record["free_float_shares"],
            record["total_shares"],
            record["company_owned_shares"],
            record["held_percent_insiders"],
            record["held_percent_institutions"],
            record["market_cap"],
            record["quality_status"],
            record["source_url"],
        ],
    )


# =========================================================
# FETCH MULTIPLE SECURITIES
# =========================================================

def fetch_all(
    as_of_date,
    limit=None,
):
    con = connect()

    ensure_table(con)

    tickers = [
        row[0]
        for row in con.execute(
            """
            SELECT ticker
            FROM universe
            ORDER BY ticker
            """
        ).fetchall()
    ]

    if limit is not None:
        tickers = tickers[:limit]

    print()
    print("YAHOO FREE-FLOAT FETCH")
    print("=" * 130)
    print(f"As of date: {as_of_date}")
    print(f"Securities requested: {len(tickers)}")
    print()

    results = []
    errors = []

    for internal_ticker in tickers:
        yahoo_symbol = get_yahoo_symbol(
            internal_ticker
        )

        try:
            record = fetch_yahoo_record(
                internal_ticker,
                yahoo_symbol,
            )

            save_record(
                con,
                as_of_date,
                record,
            )

            results.append(record)

            ff = record["free_float_pct"]

            ff_text = (
                f"{ff:.2f}%"
                if ff is not None
                else "N/A"
            )

            shares_text = (
                f"{record['total_shares']:,}"
                if record["total_shares"] is not None
                else "N/A"
            )

            float_text = (
                f"{record['free_float_shares']:,}"
                if record["free_float_shares"] is not None
                else "N/A"
            )

            print(
                f"{internal_ticker:10} | "
                f"{yahoo_symbol:15} | "
                f"FF={ff_text:8} | "
                f"shares={shares_text:15} | "
                f"float={float_text:15} | "
                f"{record['quality_status']}"
            )

        except Exception as exc:
            errors.append(
                {
                    "ticker": internal_ticker,
                    "symbol": yahoo_symbol,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

            print(
                f"{internal_ticker:10} | "
                f"{yahoo_symbol:15} | "
                f"ERROR | "
                f"{type(exc).__name__}: {exc}"
            )

    # =====================================================
    # SUMMARY
    # =====================================================

    print()
    print("=" * 130)
    print("SUMMARY")
    print("=" * 130)

    print(
        f"Requested: {len(tickers)}"
    )

    print(
        f"Fetched:   {len(results)}"
    )

    print(
        f"Errors:    {len(errors)}"
    )

    status_counts = {}

    for record in results:
        status = record[
            "quality_status"
        ]

        status_counts[status] = (
            status_counts.get(
                status,
                0,
            )
            + 1
        )

    print()
    print("QUALITY STATUS")
    print("-" * 130)

    for status, count in sorted(
        status_counts.items()
    ):
        print(
            f"{status:25} "
            f"{count:4}"
        )

    # =====================================================
    # DATABASE CHECK
    # =====================================================

    print()
    print("DATABASE CHECK")
    print("=" * 130)

    rows = con.execute(
        """
        SELECT
            ticker,
            provider_symbol,
            free_float_pct,
            total_shares,
            free_float_shares,
            held_percent_insiders,
            held_percent_institutions,
            market_cap,
            quality_status
        FROM external_free_float_snapshot
        WHERE as_of_date = ?
          AND provider = 'YAHOO'
        ORDER BY ticker
        """,
        [as_of_date],
    ).fetchall()

    for row in rows:
        print(row)

    # =====================================================
    # ERRORS
    # =====================================================

    if errors:
        print()
        print("ERRORS")
        print("=" * 130)

        for item in errors:
            print(
                f"{item['ticker']:10} | "
                f"{item['symbol']:15} | "
                f"{item['error_type']} | "
                f"{item['error']}"
            )

    con.close()

    return {
        "requested": len(tickers),
        "fetched": len(results),
        "errors": len(errors),
        "status_counts": status_counts,
        "error_rows": errors,
    }


# =========================================================
# COMMAND LINE
# =========================================================

if __name__ == "__main__":
    # Start with 20 securities.
    # Change limit=None when we are ready for all 115.
    fetch_all(
        as_of_date=date.today(),
        limit=None,
    )