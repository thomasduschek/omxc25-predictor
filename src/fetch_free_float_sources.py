from datetime import datetime
from email.utils import parsedate_to_datetime

from src.free_float_sources.nasdaq import (
    collect_events,
    fetch_historical_events,
)

from datetime import datetime, timezone
from hashlib import sha256

from src.db import connect, init


def make_event_id(
    ticker,
    source_type,
    source_id,
):
    value = "|".join(
        [
            str(ticker or ""),
            str(source_type or ""),
            str(source_id or ""),
        ]
    )

    return sha256(
        value.encode("utf-8")
    ).hexdigest()


def save_event(
    con,
    *,
    ticker,
    event_date=None,
    published_date=None,
    event_type="UNKNOWN",
    title=None,
    source_type=None,
    source_url=None,
    source_id=None,
    raw_text=None,
    owner=None,
    share_pct=None,
    vote_pct=None,
    shares=None,
    status="RAW",
    confidence=None,
):
    event_id = make_event_id(
        ticker,
        source_type,
        source_id,
    )

    fetched_at = datetime.now(timezone.utc)

    con.execute(
        """
        INSERT OR REPLACE INTO free_float_source_events (
            event_id,
            ticker,
            event_date,
            published_date,
            event_type,
            title,
            source_type,
            source_url,
            source_id,
            raw_text,
            owner,
            share_pct,
            vote_pct,
            shares,
            status,
            confidence,
            fetched_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            event_id,
            ticker,
            event_date,
            published_date,
            event_type,
            title,
            source_type,
            source_url,
            source_id,
            raw_text,
            owner,
            share_pct,
            vote_pct,
            shares,
            status,
            confidence,
            fetched_at,
        ],
    )

    return event_id

def parsedate_or_iso(value):
    try:
        return parsedate_to_datetime(value)
    except Exception:
        return datetime.strptime(
            value,
            "%Y-%m-%d %H:%M:%S",
        )
        
def backfill_nasdaq(con, from_date, to_date):
    events = fetch_historical_events(
        from_date,
        to_date,
    )

    print(
        f"Nasdaq historical FFF events: {len(events)}"
    )

    saved = 0

    for event in events:
        pub_dt = parsedate_or_iso(
            event["pub_date"]
        )

        ticker = event.get("ticker")

        # Historical API currently identifies company,
        # but ticker matching comes in next step.
        # Skip until matched to our OMXCPI universe.
        if not ticker:
            continue

        save_event(
            con=con,
            ticker=ticker,
            event_date=None,
            published_date=pub_dt.date(),
            event_type=event["event_type"],
            title=event["title"],
            source_type="nasdaq_news",
            source_url=event["link"],
            source_id=event["guid"],
            raw_text=None,
            status="RAW",
            confidence=1.0,
        )

        saved += 1

    print(
        f"Nasdaq historical matched/saved: {saved}"
    )

def parsedate_or_iso(value):
    try:
        return parsedate_to_datetime(value)
    except Exception:
        return datetime.strptime(
            value,
            "%Y-%m-%d %H:%M:%S",
        )


def backfill_nasdaq(con, from_date, to_date):
    events = fetch_historical_events(
        from_date,
        to_date,
    )

    print(
        f"Nasdaq historical matched events: {len(events)}"
    )

    before = con.execute(
        """
        SELECT COUNT(*)
        FROM free_float_source_events
        """
    ).fetchone()[0]

    for event in events:
        pub_dt = parsedate_or_iso(
            event["pub_date"]
        )

        save_event(
            con=con,
            ticker=event["ticker"],
            event_date=None,
            published_date=pub_dt.date(),
            event_type=event["event_type"],
            title=event["title"],
            source_type="nasdaq_news",
            source_url=event["link"],
            source_id=event["guid"],
            raw_text=event.get("raw_text"),
            status="RAW",
            confidence=1.0,
        )

    after = con.execute(
        """
        SELECT COUNT(*)
        FROM free_float_source_events
        """
    ).fetchone()[0]

    print(f"Events before: {before}")
    print(f"Events after:  {after}")
    print(f"New events:    {after - before}")
    
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