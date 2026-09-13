from datetime import date

from src.db import connect


# =========================================================
# CONFIG
# =========================================================

YAHOO_PROVIDER = "YAHOO"

DIRECT_YAHOO_STATUSES = {
    "OK",
}

FLOAT_SHARE_FALLBACK_STATUSES = {
    "FLOAT_GT_SHARES",
    "MISSING_SHARES",
}

MULTI_CLASS_SUFFIXES = {
    "A",
    "B",
}


# =========================================================
# GENERAL HELPERS
# =========================================================

def round_free_float_factor(raw_free_float_pct):
    """
    Round free float to nearest whole percentage point.

    Examples:
        94.89% -> 0.95
        82.15% -> 0.82
        100.00% -> 1.00
    """

    if raw_free_float_pct is None:
        return None

    raw_free_float_pct = max(
        0.0,
        min(
            100.0,
            raw_free_float_pct,
        ),
    )

    return int(
        raw_free_float_pct + 0.5
    ) / 100.0


def split_share_class(ticker):
    """
    Detect simple A/B share-class tickers.

    Examples:
        CARL A   -> ("CARL", "A")
        CARL B   -> ("CARL", "B")
        MAERSK A -> ("MAERSK", "A")
        NOVO B   -> ("NOVO", "B")

    This function does not by itself prove that a company is
    multi-class. The sibling class must also exist in universe.
    """

    parts = ticker.strip().rsplit(
        " ",
        1,
    )

    if len(parts) != 2:
        return None, None

    base, suffix = parts

    if suffix not in MULTI_CLASS_SUFFIXES:
        return None, None

    return base, suffix


# =========================================================
# NASDAQ MARKET DATA
# =========================================================

def latest_market_snapshot(
    con,
    ticker,
    as_of_date,
):
    """
    Nasdaq-derived market snapshot.

    Authoritative source for:
    - market cap
    - fallback share count
    """

    return con.execute(
        """
        SELECT
            snapshot_date,
            shares,
            market_cap,
            source
        FROM company_market_snapshot
        WHERE ticker = ?
          AND snapshot_date <= ?
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        [
            ticker,
            as_of_date,
        ],
    ).fetchone()


def latest_share_capital(
    con,
    ticker,
    as_of_date,
):
    """
    Authoritative share count.

    Priority:
    1. Parsed Nasdaq share-capital history
    2. Nasdaq-derived company_market_snapshot

    Yahoo share counts are NEVER used as the authoritative
    number of shares.
    """

    row = con.execute(
        """
        SELECT
            as_of_date,
            total_shares,
            share_class,
            source_type,
            source
        FROM share_capital_history
        WHERE ticker = ?
          AND as_of_date <= ?
        ORDER BY as_of_date DESC
        LIMIT 1
        """,
        [
            ticker,
            as_of_date,
        ],
    ).fetchone()

    if row is not None:
        return {
            "date": row[0],
            "shares": row[1],
            "share_class": row[2],
            "source_type": row[3],
            "source": row[4],
            "method": "NASDAQ_SHARE_CAPITAL",
        }

    market = latest_market_snapshot(
        con,
        ticker,
        as_of_date,
    )

    if market is None:
        return None

    (
        market_date,
        shares,
        market_cap,
        source,
    ) = market

    if shares is None:
        return None

    return {
        "date": market_date,
        "shares": shares,
        "share_class": None,
        "source_type": "company_market_snapshot",
        "source": source,
        "method": "NASDAQ_MARKET_SNAPSHOT",
    }


# =========================================================
# YAHOO DATA
# =========================================================

def latest_yahoo_float(
    con,
    ticker,
    as_of_date,
):
    """
    Latest Yahoo observation.

    Free-float fields may be used.

    Yahoo total_shares and market_cap are diagnostic only.
    """

    row = con.execute(
        """
        SELECT
            as_of_date,
            provider_symbol,
            free_float_pct,
            free_float_shares,
            total_shares,
            company_owned_shares,
            held_percent_insiders,
            held_percent_institutions,
            market_cap,
            quality_status,
            source_url,
            fetched_at
        FROM external_free_float_snapshot
        WHERE ticker = ?
          AND provider = ?
          AND as_of_date <= ?
        ORDER BY
            as_of_date DESC,
            fetched_at DESC
        LIMIT 1
        """,
        [
            ticker,
            YAHOO_PROVIDER,
            as_of_date,
        ],
    ).fetchone()

    if row is None:
        return None

    return {
        "date": row[0],
        "provider_symbol": row[1],
        "free_float_pct": row[2],
        "free_float_shares": row[3],

        # Diagnostic only
        "yahoo_total_shares": row[4],
        "company_owned_shares": row[5],
        "held_percent_insiders": row[6],
        "held_percent_institutions": row[7],
        "yahoo_market_cap": row[8],

        "quality_status": row[9],
        "source_url": row[10],
        "fetched_at": row[11],
    }


# =========================================================
# MULTI-CLASS HELPERS
# =========================================================

def get_multi_class_group(
    con,
    ticker,
):
    """
    Return sibling A/B tickers if both exist in universe.

    Example:
        MAERSK A -> ["MAERSK A", "MAERSK B"]

    Returns None if no valid A/B sibling pair exists.
    """

    base, suffix = split_share_class(
        ticker
    )

    if base is None:
        return None

    class_a = f"{base} A"
    class_b = f"{base} B"

    rows = con.execute(
        """
        SELECT ticker
        FROM universe
        WHERE ticker IN (?, ?)
        ORDER BY ticker
        """,
        [
            class_a,
            class_b,
        ],
    ).fetchall()

    tickers = [
        row[0]
        for row in rows
    ]

    if (
        class_a in tickers
        and class_b in tickers
    ):
        return [
            class_a,
            class_b,
        ]

    return None


def calculate_multi_class_proxy(
    con,
    ticker,
    as_of_date,
):
    """
    Build a company-level free-float proxy for A/B structures.

    Yahoo sometimes supplies the same floatShares value to both
    A and B securities.

    In that case:

        company free float pct =
            Yahoo company-level floatShares
            /
            Nasdaq A shares + Nasdaq B shares

    The resulting company-level percentage is then used as a
    proxy FFF for both share classes.

    This is intentionally labelled as a proxy.
    """

    group = get_multi_class_group(
        con,
        ticker,
    )

    if not group:
        return None

    total_group_shares = 0

    share_dates = []
    yahoo_rows = []

    for sibling in group:
        shares_info = latest_share_capital(
            con,
            sibling,
            as_of_date,
        )

        if (
            shares_info is None
            or shares_info["shares"] is None
            or shares_info["shares"] <= 0
        ):
            return None

        total_group_shares += (
            shares_info["shares"]
        )

        share_dates.append(
            shares_info["date"]
        )

        yahoo = latest_yahoo_float(
            con,
            sibling,
            as_of_date,
        )

        if yahoo is not None:
            yahoo_rows.append(
                yahoo
            )

    if total_group_shares <= 0:
        return None

    float_candidates = [
        row["free_float_shares"]
        for row in yahoo_rows
        if (
            row["free_float_shares"]
            is not None
            and row["free_float_shares"] > 0
        )
    ]

    if not float_candidates:
        return None

    # If Yahoo repeats the same company-level floatShares
    # across classes, use that one value.
    #
    # If values differ slightly, use the largest candidate
    # as the best company-level estimate for now.
    company_float_shares = max(
        float_candidates
    )

    raw_free_float_pct = (
        company_float_shares
        / total_group_shares
        * 100.0
    )

    if not (
        0.0
        < raw_free_float_pct
        <= 100.0
    ):
        return None

    yahoo_dates = [
        row["date"]
        for row in yahoo_rows
        if row["date"] is not None
    ]

    yahoo_date = (
        max(yahoo_dates)
        if yahoo_dates
        else None
    )

    return {
        "group": group,
        "group_total_shares": (
            total_group_shares
        ),
        "company_float_shares": (
            company_float_shares
        ),
        "raw_free_float_pct": (
            raw_free_float_pct
        ),
        "yahoo_date": yahoo_date,
        "share_dates": share_dates,
    }


# =========================================================
# TREASURY FALLBACK
# =========================================================

def latest_treasury_snapshot(
    con,
    ticker,
    as_of_date,
):
    return con.execute(
        """
        SELECT
            as_of_date,
            shares,
            share_pct,
            source_type,
            source,
            confidence
        FROM treasury_shares
        WHERE ticker = ?
          AND as_of_date <= ?
        ORDER BY
            as_of_date DESC,
            confidence DESC NULLS LAST
        LIMIT 1
        """,
        [
            ticker,
            as_of_date,
        ],
    ).fetchone()


def unresolved_treasury_events(
    con,
    ticker,
    as_of_date,
):
    latest_treasury = (
        latest_treasury_snapshot(
            con,
            ticker,
            as_of_date,
        )
    )

    latest_parsed_date = None

    if latest_treasury is not None:
        latest_parsed_date = (
            latest_treasury[0]
        )

    rows = con.execute(
        """
        SELECT
            event_id,
            event_date,
            published_date,
            title,
            status
        FROM free_float_source_events
        WHERE ticker = ?
          AND event_type = 'TREASURY_SHARES'
          AND status IN ('RAW', 'REVIEW')
          AND COALESCE(event_date, published_date) <= ?
        ORDER BY
            COALESCE(event_date, published_date) DESC,
            published_date DESC
        """,
        [
            ticker,
            as_of_date,
        ],
    ).fetchall()

    unresolved = []

    for (
        event_id,
        event_date,
        published_date,
        title,
        status,
    ) in rows:

        effective_date = (
            event_date
            if event_date is not None
            else published_date
        )

        if (
            latest_parsed_date is None
            or effective_date
            >= latest_parsed_date
        ):
            unresolved.append(
                {
                    "event_id": event_id,
                    "date": effective_date,
                    "title": title,
                    "status": status,
                }
            )

    return unresolved


# =========================================================
# OWNERSHIP FALLBACK
# =========================================================

def latest_ownership_positions(
    con,
    ticker,
    as_of_date,
):
    return con.execute(
        """
        WITH ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY owner
                    ORDER BY
                        as_of_date DESC,
                        confidence DESC NULLS LAST
                ) AS rn
            FROM ownership_positions
            WHERE ticker = ?
              AND as_of_date <= ?
        )
        SELECT
            owner,
            share_pct,
            vote_pct,
            threshold_operator,
            threshold_pct,
            owner_type,
            include_in_float,
            as_of_date,
            source,
            classification_status,
            classification_reason,
            position_type
        FROM ranked
        WHERE rn = 1
        ORDER BY owner
        """,
        [
            ticker,
            as_of_date,
        ],
    ).fetchall()


def calculate_nasdaq_fallback(
    con,
    ticker,
    as_of_date,
    total_shares,
):
    """
    Existing ownership/treasury model.

    This is the lowest-priority fallback.
    """

    treasury = latest_treasury_snapshot(
        con,
        ticker,
        as_of_date,
    )

    treasury_reviews = (
        unresolved_treasury_events(
            con,
            ticker,
            as_of_date,
        )
    )

    treasury_pct = 0.0
    treasury_date = None

    if treasury is not None:
        (
            treasury_date,
            treasury_shares,
            stored_treasury_pct,
            treasury_source_type,
            treasury_source,
            treasury_confidence,
        ) = treasury

        if stored_treasury_pct is not None:
            treasury_pct = (
                stored_treasury_pct
            )

        elif treasury_shares is not None:
            treasury_pct = (
                treasury_shares
                / total_shares
                * 100.0
            )

    owners = latest_ownership_positions(
        con,
        ticker,
        as_of_date,
    )

    excluded_owner_pct = 0.0
    unresolved_owners = []

    for owner in owners:
        (
            owner_name,
            share_pct,
            vote_pct,
            threshold_operator,
            threshold_pct,
            owner_type,
            include_in_float,
            owner_date,
            source,
            classification_status,
            classification_reason,
            position_type,
        ) = owner

        relevant_pct = max(
            share_pct or 0.0,
            vote_pct or 0.0,
            threshold_pct or 0.0,
        )

        if include_in_float is True:
            continue

        if include_in_float is False:
            if share_pct is None:
                unresolved_owners.append(
                    f"{owner_name}: "
                    f"no capital percentage"
                )
                continue

            excluded_owner_pct += (
                share_pct
            )

            continue

        if relevant_pct >= 5.0:
            unresolved_owners.append(
                f"{owner_name}: "
                f"{relevant_pct:.2f}%"
            )

    raw_free_float_pct = (
        100.0
        - treasury_pct
        - excluded_owner_pct
    )

    raw_free_float_pct = max(
        0.0,
        min(
            100.0,
            raw_free_float_pct,
        ),
    )

    if treasury_reviews:
        quality = (
            "TREASURY_REVIEW"
        )

    elif unresolved_owners:
        quality = (
            "OWNERSHIP_REVIEW"
        )

    elif owners:
        quality = (
            "MODEL_CLASSIFIED"
        )

    else:
        quality = (
            "MODEL_UNVERIFIED"
        )

    return {
        "raw_free_float_pct": (
            raw_free_float_pct
        ),
        "free_float_factor": (
            round_free_float_factor(
                raw_free_float_pct
            )
        ),
        "treasury_pct": treasury_pct,
        "excluded_owner_pct": (
            excluded_owner_pct
        ),
        "treasury_date": treasury_date,
        "quality": quality,
        "unresolved_owners": (
            unresolved_owners
        ),
        "treasury_reviews": (
            treasury_reviews
        ),
    }


# =========================================================
# SNAPSHOT TABLE
# =========================================================

def ensure_snapshot_columns(
    con,
):
    columns = {
        row[1]
        for row in con.execute(
            "PRAGMA table_info("
            "'free_float_snapshot'"
            ")"
        ).fetchall()
    }

    additions = [
        (
            "raw_free_float_pct",
            "DOUBLE",
        ),
        (
            "calculation_status",
            "VARCHAR",
        ),
        (
            "free_float_source",
            "VARCHAR",
        ),
        (
            "free_float_source_date",
            "DATE",
        ),
        (
            "free_float_source_quality",
            "VARCHAR",
        ),
        (
            "share_count_source",
            "VARCHAR",
        ),
        (
            "share_count_date",
            "DATE",
        ),
        (
            "yahoo_free_float_pct",
            "DOUBLE",
        ),
    ]

    for column, datatype in additions:
        if column not in columns:
            con.execute(
                f"""
                ALTER TABLE
                    free_float_snapshot
                ADD COLUMN
                    {column} {datatype}
                """
            )


# =========================================================
# RESULT BUILDER
# =========================================================

def make_complete_result(
    ticker,
    as_of_date,
    total_shares,
    market_cap,
    raw_free_float_pct,
    method,
    quality,
    share_capital,
    market_date,
    source_date,
    yahoo_pct=None,
    treasury_pct=None,
    excluded_owner_pct=None,
    source_summary=None,
):
    free_float_factor = (
        round_free_float_factor(
            raw_free_float_pct
        )
    )

    free_float_market_cap = (
        market_cap
        * free_float_factor
    )

    return {
        "ticker": ticker,
        "status": "COMPLETE",
        "as_of_date": as_of_date,

        "total_shares": total_shares,
        "market_cap": market_cap,

        "excluded_owner_pct": (
            excluded_owner_pct
        ),
        "treasury_pct": treasury_pct,

        "raw_free_float_pct": (
            raw_free_float_pct
        ),
        "free_float_factor": (
            free_float_factor
        ),
        "free_float_market_cap": (
            free_float_market_cap
        ),

        "calculation_method": method,
        "free_float_source": (
            "YAHOO"
            if method.startswith("YAHOO")
            else "NASDAQ_MODEL"
        ),
        "free_float_source_date": (
            source_date
        ),
        "free_float_source_quality": (
            quality
        ),

        "share_count_source": (
            share_capital["method"]
        ),
        "share_count_date": (
            share_capital["date"]
        ),

        "yahoo_free_float_pct": (
            yahoo_pct
        ),

        "source_summary": (
            source_summary
        ),
    }


# =========================================================
# CALCULATE ONE SECURITY
# =========================================================

def calculate_snapshot(
    con,
    ticker,
    as_of_date,
):
    # -----------------------------------------------------
    # NASDAQ MARKET CAP
    # -----------------------------------------------------

    market = latest_market_snapshot(
        con,
        ticker,
        as_of_date,
    )

    if market is None:
        return {
            "ticker": ticker,
            "status": (
                "MISSING_MARKET_DATA"
            ),
        }

    (
        market_date,
        market_snapshot_shares,
        market_cap,
        market_source,
    ) = market

    if (
        market_cap is None
        or market_cap <= 0
    ):
        return {
            "ticker": ticker,
            "status": (
                "MISSING_MARKET_CAP"
            ),
        }

    # -----------------------------------------------------
    # NASDAQ SHARES
    # -----------------------------------------------------

    share_capital = (
        latest_share_capital(
            con,
            ticker,
            as_of_date,
        )
    )

    if share_capital is None:
        return {
            "ticker": ticker,
            "status": (
                "MISSING_NASDAQ_SHARES"
            ),
        }

    total_shares = (
        share_capital["shares"]
    )

    if (
        total_shares is None
        or total_shares <= 0
    ):
        return {
            "ticker": ticker,
            "status": (
                "INVALID_NASDAQ_SHARES"
            ),
        }

    # -----------------------------------------------------
    # YAHOO
    # -----------------------------------------------------

    yahoo = latest_yahoo_float(
        con,
        ticker,
        as_of_date,
    )

    yahoo_pct = None
    yahoo_quality = None
    yahoo_date = None
    yahoo_float_shares = None

    if yahoo is not None:
        yahoo_pct = yahoo[
            "free_float_pct"
        ]

        yahoo_quality = yahoo[
            "quality_status"
        ]

        yahoo_date = yahoo[
            "date"
        ]

        yahoo_float_shares = yahoo[
            "free_float_shares"
        ]

    # =====================================================
    # METHOD 1: YAHOO DIRECT
    # =====================================================

    multi_class_group = (
        get_multi_class_group(
            con,
            ticker,
        )
    )

    if (
        yahoo is not None
        and not multi_class_group
        and yahoo_quality
        in DIRECT_YAHOO_STATUSES
        and yahoo_pct is not None
        and 0.0 <= yahoo_pct <= 100.0
    ):
        source_summary = (
            f"market_cap=Nasdaq"
            f"[{market_date}]; "
            f"total_shares=Nasdaq"
            f"[{share_capital['date']};"
            f"{share_capital['method']}]; "
            f"free_float=YahooDirect"
            f"[{yahoo_date};"
            f"{yahoo_pct:.4f}%]"
        )

        return make_complete_result(
            ticker=ticker,
            as_of_date=as_of_date,
            total_shares=total_shares,
            market_cap=market_cap,
            raw_free_float_pct=(
                yahoo_pct
            ),
            method=(
                "YAHOO_DIRECT"
            ),
            quality="OK",
            share_capital=share_capital,
            market_date=market_date,
            source_date=yahoo_date,
            yahoo_pct=yahoo_pct,
            source_summary=(
                source_summary
            ),
        )

    # =====================================================
    # METHOD 2: YAHOO FLOAT SHARES / NASDAQ SHARES
    # =====================================================

    if (
        yahoo is not None
        and not multi_class_group
        and yahoo_float_shares
        is not None
        and yahoo_float_shares > 0
        and (
            yahoo_quality
            in FLOAT_SHARE_FALLBACK_STATUSES
        )
    ):
        raw_pct = (
            yahoo_float_shares
            / total_shares
            * 100.0
        )

        if (
            0.0
            < raw_pct
            <= 101.5
        ):
            capped_pct = min(
                100.0,
                raw_pct,
            )

            source_summary = (
                f"market_cap=Nasdaq"
                f"[{market_date}]; "
                f"total_shares=Nasdaq"
                f"[{share_capital['date']};"
                f"{share_capital['method']}]; "
                f"free_float="
                f"YahooFloatShares/"
                f"NasdaqShares"
                f"[{yahoo_date};"
                f"{capped_pct:.4f}%]"
            )

            return make_complete_result(
                ticker=ticker,
                as_of_date=as_of_date,
                total_shares=(
                    total_shares
                ),
                market_cap=market_cap,
                raw_free_float_pct=(
                    capped_pct
                ),
                method=(
                    "YAHOO_FLOAT_SHARES_"
                    "NASDAQ_DENOMINATOR"
                ),
                quality=(
                    yahoo_quality
                ),
                share_capital=(
                    share_capital
                ),
                market_date=market_date,
                source_date=(
                    yahoo_date
                ),
                yahoo_pct=(
                    yahoo_pct
                ),
                source_summary=(
                    source_summary
                ),
            )

    # =====================================================
    # METHOD 3: MULTI-CLASS PROXY
    # =====================================================

    if multi_class_group:
        proxy = (
            calculate_multi_class_proxy(
                con,
                ticker,
                as_of_date,
            )
        )

        if proxy is not None:
            raw_pct = proxy[
                "raw_free_float_pct"
            ]

            source_summary = (
                f"market_cap=Nasdaq"
                f"[{market_date}]; "
                f"total_shares=Nasdaq"
                f"[{share_capital['date']};"
                f"{share_capital['method']}]; "
                f"free_float="
                f"YahooMultiClassProxy; "
                f"group="
                f"{','.join(proxy['group'])}; "
                f"group_shares="
                f"{proxy['group_total_shares']}; "
                f"Yahoo_float_shares="
                f"{proxy['company_float_shares']}; "
                f"proxy_pct="
                f"{raw_pct:.4f}%"
            )

            return make_complete_result(
                ticker=ticker,
                as_of_date=as_of_date,
                total_shares=(
                    total_shares
                ),
                market_cap=market_cap,
                raw_free_float_pct=(
                    raw_pct
                ),
                method=(
                    "YAHOO_MULTI_CLASS_PROXY"
                ),
                quality=(
                    "MULTI_CLASS_PROXY"
                ),
                share_capital=(
                    share_capital
                ),
                market_date=market_date,
                source_date=(
                    proxy["yahoo_date"]
                ),
                yahoo_pct=(
                    yahoo_pct
                ),
                source_summary=(
                    source_summary
                ),
            )

    # =====================================================
    # METHOD 4: NASDAQ MODEL FALLBACK
    # =====================================================

    fallback = (
        calculate_nasdaq_fallback(
            con,
            ticker,
            as_of_date,
            total_shares,
        )
    )

    if yahoo is None:
        yahoo_reason = (
            "NO_YAHOO_DATA"
        )

    elif multi_class_group:
        yahoo_reason = (
            "MULTI_CLASS_PROXY_FAILED"
        )

    else:
        yahoo_reason = (
            yahoo_quality
            or "UNUSABLE_YAHOO_DATA"
        )

    source_summary = (
        f"market_cap=Nasdaq"
        f"[{market_date}]; "
        f"total_shares=Nasdaq"
        f"[{share_capital['date']};"
        f"{share_capital['method']}]; "
        f"free_float="
        f"NasdaqModelFallback; "
        f"YahooRejected="
        f"{yahoo_reason}; "
        f"FallbackQuality="
        f"{fallback['quality']}"
    )

    return make_complete_result(
        ticker=ticker,
        as_of_date=as_of_date,
        total_shares=total_shares,
        market_cap=market_cap,
        raw_free_float_pct=(
            fallback[
                "raw_free_float_pct"
            ]
        ),
        method=(
            "NASDAQ_MODEL_FALLBACK"
        ),
        quality=(
            fallback["quality"]
        ),
        share_capital=(
            share_capital
        ),
        market_date=market_date,
        source_date=as_of_date,
        yahoo_pct=yahoo_pct,
        treasury_pct=(
            fallback[
                "treasury_pct"
            ]
        ),
        excluded_owner_pct=(
            fallback[
                "excluded_owner_pct"
            ]
        ),
        source_summary=(
            source_summary
        ),
    )


# =========================================================
# SAVE SNAPSHOT
# =========================================================

def save_snapshot(
    con,
    result,
):
    con.execute(
        """
        INSERT OR REPLACE INTO
            free_float_snapshot (
                as_of_date,
                ticker,
                total_shares,
                market_cap,
                excluded_owner_pct,
                treasury_pct,
                free_float_factor,
                free_float_market_cap,
                market_cap_rank,
                top35_eligible,
                calculation_method,
                source_summary,
                raw_free_float_pct,
                calculation_status,
                free_float_source,
                free_float_source_date,
                free_float_source_quality,
                share_count_source,
                share_count_date,
                yahoo_free_float_pct
            )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            result["as_of_date"],
            result["ticker"],
            result["total_shares"],
            result["market_cap"],
            result[
                "excluded_owner_pct"
            ],
            result[
                "treasury_pct"
            ],
            result[
                "free_float_factor"
            ],
            result[
                "free_float_market_cap"
            ],
            None,
            None,
            result[
                "calculation_method"
            ],
            result[
                "source_summary"
            ],
            result[
                "raw_free_float_pct"
            ],
            result[
                "status"
            ],
            result[
                "free_float_source"
            ],
            result[
                "free_float_source_date"
            ],
            result[
                "free_float_source_quality"
            ],
            result[
                "share_count_source"
            ],
            result[
                "share_count_date"
            ],
            result[
                "yahoo_free_float_pct"
            ],
        ],
    )


# =========================================================
# BUILD ONE
# =========================================================

def build_snapshot(
    ticker,
    as_of_date,
):
    con = connect()

    ensure_snapshot_columns(
        con
    )

    result = calculate_snapshot(
        con,
        ticker,
        as_of_date,
    )

    if result["status"] == "COMPLETE":
        save_snapshot(
            con,
            result,
        )

        print()
        print("FREE FLOAT SNAPSHOT")
        print("=" * 100)

        print(
            f"Ticker:                  "
            f"{ticker}"
        )

        print(
            f"As of date:              "
            f"{as_of_date}"
        )

        print(
            f"Nasdaq total shares:     "
            f"{result['total_shares']:,}"
        )

        print(
            f"Nasdaq market cap:       "
            f"{result['market_cap']:,.0f}"
        )

        print(
            f"Method:                  "
            f"{result['calculation_method']}"
        )

        print(
            f"Raw free float:          "
            f"{result['raw_free_float_pct']:.2f}%"
        )

        print(
            f"Free float factor:       "
            f"{result['free_float_factor']:.2%}"
        )

        print(
            f"Free-float market cap:   "
            f"{result['free_float_market_cap']:,.0f}"
        )

        print(
            f"Quality:                 "
            f"{result['free_float_source_quality']}"
        )

    else:
        print(
            f"{ticker}: "
            f"{result['status']}"
        )

    con.close()

    return result


# =========================================================
# BUILD FULL UNIVERSE
# =========================================================

def build_all_snapshots(
    as_of_date,
):
    con = connect()

    ensure_snapshot_columns(
        con
    )

    # Full rebuild.
    con.execute(
        """
        DELETE FROM free_float_snapshot
        WHERE as_of_date = ?
        """,
        [
            as_of_date,
        ],
    )

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

    results = []

    for ticker in tickers:
        result = calculate_snapshot(
            con,
            ticker,
            as_of_date,
        )

        results.append(
            result
        )

        if (
            result["status"]
            == "COMPLETE"
        ):
            save_snapshot(
                con,
                result,
            )

    # =====================================================
    # RANKING
    # =====================================================

    rows = con.execute(
        """
        SELECT
            ticker,
            free_float_market_cap
        FROM free_float_snapshot
        WHERE as_of_date = ?
          AND free_float_market_cap
              IS NOT NULL
        ORDER BY
            free_float_market_cap DESC,
            ticker
        """,
        [
            as_of_date,
        ],
    ).fetchall()

    for rank, (
        ticker,
        ff_market_cap,
    ) in enumerate(
        rows,
        start=1,
    ):
        con.execute(
            """
            UPDATE free_float_snapshot
            SET
                market_cap_rank = ?,
                top35_eligible = ?
            WHERE as_of_date = ?
              AND ticker = ?
            """,
            [
                rank,
                rank <= 35,
                as_of_date,
                ticker,
            ],
        )

    # =====================================================
    # SUMMARIES
    # =====================================================

    method_counts = {}
    quality_counts = {}

    for result in results:
        if (
            result["status"]
            != "COMPLETE"
        ):
            continue

        method = result[
            "calculation_method"
        ]

        quality = result[
            "free_float_source_quality"
        ]

        method_counts[
            method
        ] = (
            method_counts.get(
                method,
                0,
            )
            + 1
        )

        quality_counts[
            quality
        ] = (
            quality_counts.get(
                quality,
                0,
            )
            + 1
        )

    print()
    print("FREE FLOAT UNIVERSE")
    print("=" * 125)

    print(
        f"As of date: {as_of_date}"
    )

    print(
        f"Securities: {len(tickers)}"
    )

    print(
        f"Ranked:     {len(rows)}"
    )

    print()
    print("CALCULATION METHODS")
    print("=" * 125)

    for method in sorted(
        method_counts
    ):
        print(
            f"{method:45} "
            f"{method_counts[method]:4}"
        )

    print()
    print("QUALITY")
    print("=" * 125)

    for quality in sorted(
        quality_counts
    ):
        print(
            f"{quality:45} "
            f"{quality_counts[quality]:4}"
        )

    # =====================================================
    # TOP 40
    # =====================================================

    print()
    print(
        "TOP 40 FREE-FLOAT MARKET CAP"
    )
    print("=" * 165)

    ranking = con.execute(
        """
        SELECT
            f.market_cap_rank,
            f.ticker,
            u.company,
            f.market_cap,
            f.raw_free_float_pct,
            f.free_float_factor,
            f.free_float_market_cap,
            f.calculation_method,
            f.free_float_source_quality,
            u.current_c25
        FROM free_float_snapshot f
        LEFT JOIN universe u
          ON u.ticker = f.ticker
        WHERE f.as_of_date = ?
        ORDER BY
            f.market_cap_rank
        LIMIT 40
        """,
        [
            as_of_date,
        ],
    ).fetchall()

    for row in ranking:
        (
            rank,
            ticker,
            company,
            market_cap,
            raw_ff,
            fff,
            ff_market_cap,
            method,
            quality,
            current_c25,
        ) = row

        short_method = {
            "YAHOO_DIRECT":
                "Y-DIRECT",

            (
                "YAHOO_FLOAT_SHARES_"
                "NASDAQ_DENOMINATOR"
            ):
                "Y-SHARES",

            "YAHOO_MULTI_CLASS_PROXY":
                "Y-MULTI",

            "NASDAQ_MODEL_FALLBACK":
                "FALLBACK",

        }.get(
            method,
            method,
        )

        print(
            f"{rank:3} "
            f"{ticker:10} "
            f"MC={market_cap / 1e9:9.3f}bn "
            f"RawFF={raw_ff:6.2f}% "
            f"FFF={fff:5.2f} "
            f"FFMC={ff_market_cap / 1e9:9.3f}bn "
            f"{short_method:9} "
            f"{quality:20} "
            f"C25={current_c25}"
        )

    # =====================================================
    # REMAINING FALLBACK
    # =====================================================

    print()
    print("NASDAQ MODEL FALLBACK")
    print("=" * 125)

    fallback_results = [
        result
        for result in results
        if (
            result.get(
                "calculation_method"
            )
            == "NASDAQ_MODEL_FALLBACK"
        )
    ]

    if not fallback_results:
        print("None")

    else:
        for result in fallback_results:
            print(
                f"{result['ticker']:10} | "
                f"FF="
                f"{result['raw_free_float_pct']:.2f}% | "
                f"Quality="
                f"{result['free_float_source_quality']} | "
                f"Yahoo="
                f"{result.get('yahoo_free_float_pct')}"
            )

    con.close()

    return results


# =========================================================
# COMMAND LINE
# =========================================================

if __name__ == "__main__":
    build_all_snapshots(
        date.today()
    )