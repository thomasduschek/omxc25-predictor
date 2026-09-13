import html
import re


def parse_integer(value):
    if value is None:
        return None

    cleaned = re.sub(
        r"[^\d]",
        "",
        value,
    )

    if not cleaned:
        return None

    return int(cleaned)


def parse_percentage(value):
    if value is None:
        return None

    cleaned = value.strip().replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return None


# =========================================================
# TREASURY SHARES
# =========================================================

def parse_treasury_shares(raw_text):
    if not raw_text:
        return None

    text = html.unescape(raw_text)
    text = " ".join(text.split())

    number = r"([\d,.]+)"
    pct = r"([\d,.]+)"

    full_patterns = [
        rf"(?:holds|owns)\s+(?:a\s+)?total\s+of\s+"
        rf"{number}\s+(?:own|treasury)\s+shares"
        rf".{{0,180}}?(?:corresponding\s+to|equal\s+to)\s+"
        rf"(?:approximately\s+)?{pct}\s*(?:percent|%)",

        rf"(?:holds|owns)\s+"
        rf"{number}\s+(?:own|treasury)\s+shares"
        rf".{{0,180}}?(?:corresponding\s+to|equal\s+to)\s+"
        rf"(?:approximately\s+)?{pct}\s*(?:percent|%)",

        rf"(?:holds|owns|will own)\s+(?:a\s+)?total\s+of\s+"
        rf"{number}\s+(?:[AB]\s+)?(?:of\s+)?(?:treasury\s+)?shares"
        rf".{{0,180}}?(?:corresponding|equal)\s+to\s+"
        rf"(?:approximately\s+)?{pct}\s*(?:percent|%)",

        rf"holding\s+of\s+treasury\s+shares\s+amounts\s+to\s+"
        rf"{number}\s+shares"
        rf".{{0,180}}?corresponding\s+to\s+"
        rf"(?:approximately\s+)?{pct}\s*(?:percent|%)",

        rf"total\s+holding\s+of\s+own\s+shares\s+is\s+"
        rf"{number}\s+shares"
        rf".{{0,180}}?corresponding\s+to\s+"
        rf"(?:approximately\s+)?{pct}\s*(?:percent|%)",

        rf"owns\s+{number}\s+treasury\s+shares\s+in\s+total"
        rf".{{0,180}}?corresponding\s+to\s+"
        rf"(?:approximately\s+)?{pct}\s*(?:percent|%)",

        rf"treasu(?:ry|ty)\s+shares\s+"
        rf"{number}\s+"
        rf"{pct}\s*%\s+of\s+(?:the\s+)?share\s+capital",

        rf"held\s+{number}\s+treasury\s+shares"
        rf".{{0,180}}?(?:corresponding\s+to|equal\s+to)\s+"
        rf"(?:approximately\s+)?{pct}\s*(?:percent|%)",

        # EMBLA-style:
        # Following the transactions below, the Company holds
        # 2,605,231 shares, corresponding to 0.61%
        rf"(?:company\s+)?holds\s+"
        rf"{number}\s+shares"
        rf".{{0,180}}?corresponding\s+to\s+"
        rf"(?:approximately\s+)?{pct}\s*(?:percent|%)",

        # RILBA-style:
        # owns the following numbers of own shares ...
        # 666,570 shares ... corresponding to 2.74%
        rf"owns\s+the\s+following\s+numbers?\s+of\s+own\s+shares"
        rf".{{0,300}}?{number}\s+shares"
        rf".{{0,220}}?corresponding\s+to\s+"
        rf"(?:approximately\s+)?{pct}\s*(?:percent|%)",

        # DANSKE-style
        rf"total\s+accumulated\s+number\s+of\s+own\s+shares"
        rf".{{0,100}}?corresponds\s+to\s+"
        rf"{pct}\s*%"
    ]

    for pattern in full_patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        groups = match.groups()

        if len(groups) == 2:
            shares = parse_integer(groups[0])
            share_pct = parse_percentage(groups[1])

            if shares is None or shares <= 0:
                continue

        else:
            shares = None
            share_pct = parse_percentage(groups[0])

        if share_pct is None or not (0 <= share_pct <= 100):
            continue

        # Special handling for Danske:
        # extract total accumulated programme shares separately.
        if shares is None and re.search(
            r"total\s+accumulated\s+number\s+of\s+own\s+shares",
            match.group(0),
            flags=re.IGNORECASE,
        ):
            m = re.search(
                r"Total\s+accumulated\s+during\s+the\s+share\s+buyback\s+programme\s+"
                r"([\d,.]+)",
                text,
                flags=re.IGNORECASE,
            )

            if m:
                shares = parse_integer(m.group(1))

        return {
            "shares": shares,
            "share_pct": share_pct,
            "matched_text": match.group(0),
        }

    # MATAS-style percentage only
    m = re.search(
        r"owns\s+shares\s+corresponding\s+to\s+"
        r"([\d,.]+)\s*%",
        text,
        flags=re.IGNORECASE,
    )

    if m:
        share_pct = parse_percentage(m.group(1))

        if share_pct is not None:
            return {
                "shares": None,
                "share_pct": share_pct,
                "matched_text": m.group(0),
            }

    return None


def process_treasury_events(con):
    rows = con.execute(
        """
        SELECT
            event_id,
            published_date,
            ticker,
            source_url,
            raw_text
        FROM free_float_source_events
        WHERE event_type = 'TREASURY_SHARES'
          AND status = 'RAW'
        ORDER BY published_date, ticker
        """
    ).fetchall()

    parsed = 0
    review = 0

    for (
        event_id,
        published_date,
        ticker,
        source_url,
        raw_text,
    ) in rows:

        result = parse_treasury_shares(raw_text)

        if result is None:
            con.execute(
                """
                UPDATE free_float_source_events
                SET status = 'REVIEW'
                WHERE event_id = ?
                """,
                [event_id],
            )

            review += 1
            continue

        source = (
            source_url
            or f"nasdaq_event:{event_id}"
        )

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
                published_date,
                ticker,
                result["shares"],
                result["share_pct"],
                "nasdaq_news",
                source,
                1.0,
            ],
        )

        con.execute(
            """
            UPDATE free_float_source_events
            SET
                status = 'PARSED',
                shares = ?
            WHERE event_id = ?
            """,
            [
                result["shares"],
                event_id,
            ],
        )

        parsed += 1

    return {
        "processed": len(rows),
        "parsed": parsed,
        "review": review,
    }


# =========================================================
# MAJOR SHAREHOLDERS
# =========================================================

def clean_owner_name(owner):
    if not owner:
        return None

    owner = " ".join(owner.split()).strip()

    prefixes = [
        "that ",
        "from ",
        (
            "of the share capital and voting rights "
            "of matas a/s. "
        ),
        "of the share capital and voting rights of ",
    ]

    lower_owner = owner.lower()

    for prefix in prefixes:
        if lower_owner.startswith(prefix):
            owner = owner[len(prefix):].strip()
            lower_owner = owner.lower()

    return owner

def parse_position_date(text):
    if not text:
        return None

    month_map = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }

    patterns = [
        r"As\s+(?:of|per)\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        r"effective\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        r"\bon\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})"
        r"\s+directly\s+and\s+indirectly\s+controlled",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        day = int(match.group(1))
        month_name = match.group(2).lower()
        year = int(match.group(3))

        month = month_map.get(month_name)

        if month is None:
            continue

        from datetime import date

        try:
            return date(year, month, day)
        except ValueError:
            continue

    return None

def parse_major_shareholder(raw_text):
    if not raw_text:
        return None

    text = html.unescape(raw_text)
    text = " ".join(text.split())
    position_date = parse_position_date(text)

    patterns = [
        # MATAS-style
        {
            "pattern": (
                r"(?P<owner>"
                r"[A-ZÆØÅ][A-Za-zÆØÅæøå0-9 .&()/-]+?"
                r")"
                r"\s+disclosed\s+total\s+aggregated\s+"
                r"holding\s+of\s+"
                r"(?P<shares>[\d,.]+)\s+shares"
                r".{0,120}?corresponding\s+to\s+"
                r"(?P<pct>[\d,.]+)\s*%"
            ),
            "position_type": "shares_and_votes",
        },

        # BO-style
        {
            "pattern": (
                r"As\s+(?:of|per)\s+[^,]{1,40},\s+"
                r"(?P<owner>"
                r"[A-ZÆØÅ][A-Za-zÆØÅæøå0-9 .&()/-]+?"
                r")"
                r"\s+held\s+a\s+total\s+of\s+"
                r"(?P<shares>[\d,.]+)\s+shares\s+"
                r"and\s+voting\s+rights"
                r".{0,160}?corresponding\s+to\s+"
                r"(?P<pct>[\d,.]+)\s*(?:percent|%)"
            ),
            "position_type": "shares_and_votes",
        },

        # NETC-style
        {
            "pattern": (
                r"(?P<owner>"
                r"[A-ZÆØÅ][A-Za-zÆØÅæøå0-9 .&()/-]+?"
                r")"
                r"\s+on\s+\d{1,2}\s+[A-Za-z]+\s+\d{4}\s+"
                r"directly\s+and\s+indirectly\s+controlled\s+"
                r"(?P<shares>[\d,.]+)\s+voting\s+rights"
                r"\s+corresponding\s+to\s+"
                r"(?P<pct>[\d,.]+)\s*%"
            ),
            "position_type": "votes_only",
        },
    ]

    for item in patterns:
        match = re.search(
            item["pattern"],
            text,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        owner = clean_owner_name(
            match.group("owner")
        )

        shares = parse_integer(
            match.group("shares")
        )

        pct = parse_percentage(
            match.group("pct")
        )

        if not owner:
            continue

        if shares is None or shares <= 0:
            continue

        if pct is None or not (0 <= pct <= 100):
            continue

        position_type = item["position_type"]

        if position_type == "shares_and_votes":
            share_pct = pct
            vote_pct = pct

        elif position_type == "votes_only":
            share_pct = None
            vote_pct = pct

        else:
            continue

        return {
    "owner": owner,
    "shares": shares,
    "share_pct": share_pct,
    "vote_pct": vote_pct,
    "position_type": position_type,
    "position_date": position_date,
    "matched_text": match.group(0),
    }

    return None


def process_major_shareholder_events(con):
    rows = con.execute(
        """
        SELECT
            event_id,
            ticker,
            event_date,
            published_date,
            title,
            source_url,
            raw_text
        FROM free_float_source_events
        WHERE event_type = 'MAJOR_SHAREHOLDER'
          AND status IN ('RAW', 'REVIEW')
        ORDER BY published_date, ticker
        """
    ).fetchall()

    parsed = 0
    review = 0

    for (
        event_id,
        ticker,
        event_date,
        published_date,
        title,
        source_url,
        raw_text,
    ) in rows:

        result = parse_major_shareholder(
            raw_text
        )

        if not result:
            con.execute(
                """
                UPDATE free_float_source_events
                SET status = 'REVIEW'
                WHERE event_id = ?
                """,
                [event_id],
            )

            review += 1
            continue

        owner = result["owner"]
        position_type = result["position_type"]

        if position_type == "shares_and_votes":
            shares = result["shares"]
            voting_rights = result["shares"]
            share_pct = result["share_pct"]
            vote_pct = result["vote_pct"]

        elif position_type == "votes_only":
            shares = None
            voting_rights = result["shares"]
            share_pct = None
            vote_pct = result["vote_pct"]

        else:
            con.execute(
                """
                UPDATE free_float_source_events
                SET status = 'REVIEW'
                WHERE event_id = ?
                """,
                [event_id],
            )

            review += 1
            continue

        as_of_date = (
            result.get("position_date")
            or event_date
            or published_date
        )

        source = (
            source_url
            if source_url
            else (
                "Nasdaq major shareholder announcement: "
                f"{title}"
            )
        )

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
                confidence,
                shares,
                voting_rights,
                position_type
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                as_of_date,
                ticker,
                owner,
                share_pct,
                vote_pct,
                None,
                None,
                None,
                None,
                "nasdaq_news",
                source,
                1.0,
                shares,
                voting_rights,
                position_type,
            ],
        )

        con.execute(
            """
            UPDATE free_float_source_events
            SET
                status = 'PARSED',
                owner = ?,
                share_pct = ?,
                vote_pct = ?,
                shares = ?
            WHERE event_id = ?
            """,
            [
                owner,
                share_pct,
                vote_pct,
                result["shares"],
                event_id,
            ],
        )

        parsed += 1

    return {
        "processed": len(rows),
        "parsed": parsed,
        "review": review,
    }

def parse_share_capital(raw_text):
    if not raw_text:
        return None

    text = html.unescape(raw_text)
    text = " ".join(text.split())

    patterns = [
        # TORM-style:
        # share capital totals to USD ... divided into
        # 102,389,784 A-shares
        (
            r"share\s+capital\s+totals\s+to"
            r".{0,120}?"
            r"divided\s+into\s+"
            r"(?P<shares>[\d,.]+)\s+"
            r"(?:[A-Z]-)?shares"
        ),
        # Example:
        # is made up of 79,265,318 shares ...
        (
            r"(?:is\s+made\s+up\s+of|consists\s+of)\s+"
            r"(?P<shares>[\d,.]+)\s+shares"
        ),

        # Example:
        # total number of shares ... is 79,265,318
        (
            r"total\s+(?:number|amount)\s+of\s+shares"
            r".{0,80}?\bis\s+"
            r"(?P<shares>[\d,.]+)"
        ),

        # Example:
        # share capital ... represented by 79,265,318 shares
        (
            r"share\s+capital"
            r".{0,160}?"
            r"(?:represented\s+by|divided\s+into)\s+"
            r"(?P<shares>[\d,.]+)\s+shares"
        ),

        # Example:
        # after the capital increase ... 79,265,318 shares
        (
            r"after\s+the\s+capital\s+increase"
            r".{0,250}?"
            r"(?P<shares>[\d,.]+)\s+shares"
        ),
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        shares = parse_integer(
            match.group("shares")
        )

        if shares is None or shares <= 0:
            continue

        return {
            "total_shares": shares,
            "matched_text": match.group(0),
        }

    return None

def process_share_capital_events(con):
    rows = con.execute(
        """
        SELECT
            event_id,
            ticker,
            event_date,
            published_date,
            title,
            source_url,
            raw_text
        FROM free_float_source_events
        WHERE event_type = 'SHARE_CAPITAL'
          AND status IN ('RAW', 'REVIEW')
        ORDER BY published_date, ticker
        """
    ).fetchall()

    parsed = 0
    review = 0

    for (
        event_id,
        ticker,
        event_date,
        published_date,
        title,
        source_url,
        raw_text,
    ) in rows:

        result = parse_share_capital(raw_text)

        if not result:
            con.execute(
                """
                UPDATE free_float_source_events
                SET status = 'REVIEW'
                WHERE event_id = ?
                """,
                [event_id],
            )

            review += 1
            continue

        as_of_date = (
            event_date
            or published_date
        )

        share_class = None

        if ticker == "TRMD A":
            share_class = "A"

        source = (
            source_url
            or f"Nasdaq share capital announcement: {title}"
        )

        con.execute(
            """
            INSERT OR REPLACE INTO share_capital_history (
                as_of_date,
                ticker,
                total_shares,
                share_class,
                source_type,
                source,
                confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                as_of_date,
                ticker,
                result["total_shares"],
                share_class,
                "nasdaq_news",
                source,
                1.0,
            ],
        )

        con.execute(
            """
            UPDATE free_float_source_events
            SET
                status = 'PARSED',
                shares = ?
            WHERE event_id = ?
            """,
            [
                result["total_shares"],
                event_id,
            ],
        )

        parsed += 1

    return {
        "processed": len(rows),
        "parsed": parsed,
        "review": review,
    }