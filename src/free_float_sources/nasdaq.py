from pathlib import Path
import csv

import re
import xml.etree.ElementTree as ET

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_FILE = ROOT / "config" / "universe.csv"

RSS_URL = (
    "https://api.news.eu.nasdaq.com/"
    "news/rss/mainMarketNotices"
)

RELEVANT_KEYWORDS = (
    "own shares",
    "treasury shares",
    "share buy-back",
    "share buyback",
    "major shareholder",
    "major shareholding",
    "voting rights",
    "share capital",
    "capital increase",
    "capital reduction",
)


def clean_text(value):
    if not value:
        return ""

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def is_relevant(title, description):
    text = f"{title} {description}".lower()

    return any(
        keyword in text
        for keyword in RELEVANT_KEYWORDS
    )


def fetch_url(url):
    response = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent":
                "omxc25-predictor/1.0"
        },
    )

    response.raise_for_status()

    return response.text


def fetch_feed():
    response = requests.get(
        RSS_URL,
        timeout=30,
        headers={
            "User-Agent":
                "omxc25-predictor/1.0"
        },
    )

    response.raise_for_status()

    return response.content


def extract_symbol(text):
    patterns = [
        r"\bsymbol\s*:\s*([A-Z0-9 ._-]+)",
        r"\bSymbol\s+([A-Z0-9 ._-]+)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            return clean_text(
                match.group(1)
            ).upper()

    return None


def extract_isin(text):
    match = re.search(
        r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b",
        text,
    )

    if match:
        return match.group(0).upper()

    return None


def parse_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)

    events = []

    for item in root.findall(".//item"):
        title = clean_text(
            item.findtext("title")
        )

        description = clean_text(
            item.findtext("description")
        )

        link = clean_text(
            item.findtext("link")
        )

        guid = clean_text(
            item.findtext("guid")
        )

        pub_date = clean_text(
            item.findtext("pubDate")
        )

        if not is_relevant(
            title,
            description,
        ):
            continue

        events.append(
            {
                "title": title,
                "description": description,
                "link": link,
                "guid": guid or link,
                "pub_date": pub_date,
            }
        )

    return events

def enrich_event(event):
    html = fetch_url(
        event["link"]
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        html,
    )

    text = clean_text(text)

    event["symbol"] = extract_symbol(text)
    event["isin"] = extract_isin(text)
    event["raw_text"] = text

    return event

def normalize_company_name(name):
    if not name:
        return ""

    name = name.lower().strip()

    # Remove company suffixes only when they occur
    # as separate terms — never inside company names.
    patterns = [
        r"(?<!\w)a/s(?!\w)",
        r"(?<!\w)a\.s\.(?!\w)",
        r"(?<!\w)a\.s(?!\w)",
        r"(?<!\w)as(?!\w)",
        r"(?<!\w)plc(?!\w)",
        r"(?<!\w)ab(?!\w)",
        r"(?<!\w)\(publ\)(?!\w)",
    ]

    for pattern in patterns:
        name = re.sub(
            pattern,
            " ",
            name,
            flags=re.IGNORECASE,
        )

    name = " ".join(name.split())

    return name


def load_universe():
    by_isin = {}
    by_symbol = {}
    by_company = {}

    with open(
        UNIVERSE_FILE,
        newline="",
        encoding="utf-8",
    ) as f:
        reader = csv.DictReader(f)

        for row in reader:
            isin = (row.get("isin") or "").strip()
            symbol = (
                row.get("nasdaq_symbol")
                or row.get("ticker")
                or ""
            ).strip()

            company = normalize_company_name(
                row.get("company")
            )

            if isin:
                by_isin[isin] = row

            if symbol:
                by_symbol[symbol.upper()] = row

            if company:
                by_company.setdefault(
                    company, []
                ).append(row)

    return by_isin, by_symbol, by_company

def match_universe(
    event,
    by_isin,
    by_symbol,
    by_company=None,
):
    isin = (event.get("isin") or "").strip()

    if isin and isin in by_isin:
        return by_isin[isin]

    symbol = (
        event.get("symbol") or ""
    ).strip().upper()

    if symbol and symbol in by_symbol:
        return by_symbol[symbol]

    # Safe fallback:
    # company name is only accepted when it maps
    # to exactly one security in our universe.
    if by_company is not None:
        company = normalize_company_name(
            event.get("company")
        )

        matches = by_company.get(
            company,
            [],
        )

        if len(matches) == 1:
            return matches[0]

    if by_company is not None:
        company = normalize_company_name(
            event.get("company")
        )

        alias_ticker = COMPANY_ALIASES.get(
            company
        )

        if alias_ticker:
            for row in by_isin.values():
                if row.get("ticker") == alias_ticker:
                    return row
    return None

def classify_event(title):
    text = title.lower()

    if "own shares" in text or "treasury shares" in text:
        return "TREASURY_SHARES"

    if "share buy-back" in text or "share buyback" in text:
        return "SHARE_BUYBACK"

    if "major shareholder" in text or "major shareholding" in text:
        return "MAJOR_SHAREHOLDER"

    if "voting rights" in text:
        return "VOTING_RIGHTS"

    if "share capital" in text:
        return "SHARE_CAPITAL"

    if "capital increase" in text or "capital reduction" in text:
        return "SHARE_CAPITAL"

    return "UNKNOWN"

def collect_events():
    by_isin, by_symbol, by_company = load_universe()

    xml_bytes = fetch_feed()
    events = parse_feed(xml_bytes)

    matched = []

    for event in events:
        event = enrich_event(event)

        universe_row = match_universe(
            event,
            by_isin,
            by_symbol,
            by_company,
        )

        if not universe_row:
            continue

        event["ticker"] = universe_row["ticker"]
        event["company"] = universe_row["company"]
        event["event_type"] = classify_event(
            event["title"]
        )

        matched.append(event)

    return matched
from datetime import datetime


HISTORICAL_URL = (
    "https://api.news.eu.nasdaq.com/news/query.action"
)

FFF_CATEGORIES = {
    "Changes in company's own shares": "TREASURY_SHARES",
    "Major shareholder announcements": "MAJOR_SHAREHOLDER",
    "Total number of voting rights and capital": "SHARE_CAPITAL",
}

COMPANY_ALIASES = {
    "aktieselskabet schouw & co.": "SCHO",
    "novo nordisk": "NOVO B",
    "ambu": "AMBU B",
    "ringkjøbing landbobank": "RILBA",
    "norden": "DNORD",
}
def fetch_historical_events(
    from_date,
    to_date,
    limit=100,
    event_types=None,
):
    by_isin, by_symbol, by_company = load_universe()

    start_date = datetime.strptime(
        from_date, "%Y-%m-%d"
    ).date()

    end_date = datetime.strptime(
        to_date, "%Y-%m-%d"
    ).date()

    filtered = []
    offset = 0

    while True:
        params = {
            "type": "handleResponse",
            "showAttachments": "true",
            "showCnsSpecific": "true",
            "showCompany": "true",
            "countResults": "true",
            "freeText": "",
            "company": "",
            "market": "Main Market, Copenhagen",
            "cnscategory": "",
            "fromDate": from_date,
            "toDate": to_date,
            "globalGroup": "exchangeNotice",
            "globalName": "NordicMainMarkets",
            "displayLanguage": "en",
            "language": "en",
            "timeZone": "CET",
            "dateMask": "yyyy-MM-dd HH:mm:ss",
            "limit": str(limit),
            "start": str(offset),
            "dir": "DESC",
        }

        response = requests.get(
            HISTORICAL_URL,
            params=params,
            timeout=30,
            headers={
                "User-Agent": "omxc25-predictor/1.0"
            },
        )
        response.raise_for_status()

        data = response.json()

        items = (
            data.get("results", {})
            .get("item", [])
        )

        if not items:
            break

        for item in items:
            if item.get("market") != "Main Market, Copenhagen":
                continue

            release_time = item.get("releaseTime")

            if not release_time:
                continue

            release_date = datetime.strptime(
                release_time,
                "%Y-%m-%d %H:%M:%S",
            ).date()

            if release_date < start_date:
                continue

            if release_date > end_date:
                continue

            category = item.get("cnsCategory")

            if category not in FFF_CATEGORIES:
                continue

            event_type = FFF_CATEGORIES[category]

            if (
            event_types is not None
            and event_type not in event_types
            ):
                continue

            event = {
                "title": item.get("headline"),
                "company": item.get("company"),
                "pub_date": release_time,
                "link": item.get("messageUrl"),
                "guid": str(
                    item.get("disclosureId")
                ),
                "event_type": event_type,
                "category": category,
            }

            event = enrich_event(event)

            match = match_universe(
            event,
            by_isin,
            by_symbol,
            by_company,
            )

            if not match:
                continue

            event["ticker"] = match["ticker"]
            event["matched_company"] = match["company"]

            filtered.append(event)

        # Stop when Nasdaq gives us fewer
        # records than requested.
        if len(items) < limit:
            break

        offset += limit

        # Safety guard against runaway loops
        if offset >= 10000:
            break

    return filtered

def main():
    by_isin, by_symbol, by_company = load_universe()    

    xml_bytes = fetch_feed()
    events = parse_feed(xml_bytes)

    print("NASDAQ FREE-FLOAT RSS")
    print("=" * 72)
    print(
        f"Relevant events found: "
        f"{len(events)}"
    )
    print()

    matched = []
    ignored = []

    for event in events:
        event = enrich_event(event)

        universe_row = match_universe(
            event,
            by_isin,
            by_symbol,
            by_company,
        )

        if universe_row:
            event["ticker"] = (
                universe_row["ticker"]
            )

            event["company"] = (
                universe_row["company"]
            )

            matched.append(event)

        else:
            ignored.append(event)

    print(
        f"Matched OMXCPI events: "
        f"{len(matched)}"
    )

    print(
        f"Ignored external events: "
        f"{len(ignored)}"
    )

    print()

    for event in matched:
        print(
            f"Ticker: {event['ticker']}"
        )
        print(
            f"Company: {event['company']}"
        )
        print(
            f"Title: {event['title']}"
        )
        print(
            f"Symbol: {event['symbol']}"
        )
        print(
            f"ISIN: {event['isin']}"
        )
        print(
            f"Link: {event['link']}"
        )
        print("-" * 72)

    if ignored:
        print()
        print("IGNORED")
        print("=" * 72)

        for event in ignored:
            print(
                f"{event['symbol']} | "
                f"{event['isin']} | "
                f"{event['title']}"
            )


if __name__ == "__main__":
    main()