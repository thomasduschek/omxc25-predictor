from pathlib import Path
import duckdb


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "db" / "omxc25.duckdb"


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH))


def init():
    con = connect()

    con.execute(
        "CREATE TABLE IF NOT EXISTS company_market_snapshot ("
        "snapshot_date DATE, "
        "ticker VARCHAR, "
        "shares BIGINT, "
        "market_cap DOUBLE, "
        "source VARCHAR, "
        "PRIMARY KEY (snapshot_date, ticker))"
    )
    
    con.execute(
        "CREATE TABLE IF NOT EXISTS ownership_positions ("
        "as_of_date DATE, "
        "ticker VARCHAR, "
        "owner VARCHAR, "
        "share_pct DOUBLE, "
        "vote_pct DOUBLE, "
        "threshold_operator VARCHAR, "
        "threshold_pct DOUBLE, "
        "owner_type VARCHAR, "
        "include_in_float BOOLEAN, "
        "source_type VARCHAR, "
        "source VARCHAR, "
        "confidence DOUBLE, "
        "PRIMARY KEY (as_of_date, ticker, owner, source))"
    )

    con.execute(
        "CREATE TABLE IF NOT EXISTS treasury_shares ("
        "as_of_date DATE, "
        "ticker VARCHAR, "
        "shares BIGINT, "
        "share_pct DOUBLE, "
        "source_type VARCHAR, "
        "source VARCHAR, "
        "confidence DOUBLE, "
        "PRIMARY KEY (as_of_date, ticker, source))"
    )

    con.execute(
        "CREATE TABLE IF NOT EXISTS free_float_snapshot ("
        "as_of_date DATE, "
        "ticker VARCHAR, "
        "total_shares BIGINT, "
        "market_cap DOUBLE, "
        "excluded_owner_pct DOUBLE, "
        "treasury_pct DOUBLE, "
        "free_float_factor DOUBLE, "
        "free_float_market_cap DOUBLE, "
        "market_cap_rank INTEGER, "
        "top35_eligible BOOLEAN, "
        "calculation_method VARCHAR, "
        "source_summary VARCHAR, "
        "PRIMARY KEY (as_of_date, ticker))"
    )

    con.execute(
    "CREATE TABLE IF NOT EXISTS free_float_source_events ("
    "event_id VARCHAR PRIMARY KEY, "
    "ticker VARCHAR, "
    "event_date DATE, "
    "published_date DATE, "
    "event_type VARCHAR, "
    "title VARCHAR, "
    "source_type VARCHAR, "
    "source_url VARCHAR, "
    "source_id VARCHAR, "
    "raw_text VARCHAR, "
    "owner VARCHAR, "
    "share_pct DOUBLE, "
    "vote_pct DOUBLE, "
    "shares BIGINT, "
    "status VARCHAR, "
    "confidence DOUBLE, "
    "fetched_at TIMESTAMP"
    ")"
    )
    
    con.execute(
    """
    CREATE TABLE IF NOT EXISTS share_capital_history (
        as_of_date DATE,
        ticker VARCHAR,
        total_shares BIGINT,
        share_class VARCHAR,
        source_type VARCHAR,
        source VARCHAR,
        confidence DOUBLE,
        PRIMARY KEY (
            as_of_date,
            ticker,
            source
        )
    )
    """
)

    con.execute(
        "CREATE TABLE IF NOT EXISTS universe ("
        "ticker VARCHAR PRIMARY KEY,"
        "company VARCHAR,"
        "isin VARCHAR,"
        "current_c25 BOOLEAN,"
        "free_float_market_cap DOUBLE,"
        "market_cap_rank INTEGER,"
        "top35_eligible BOOLEAN)"
    )

    con.execute(
        "CREATE TABLE IF NOT EXISTS daily_market ("
        "date DATE, "
        "ticker VARCHAR, "
        "turnover DOUBLE, "
        "volume DOUBLE, "
        "trades DOUBLE, "
        "vwap DOUBLE, "
        "close DOUBLE, "
        "source_file VARCHAR, "
        "UNIQUE(date,ticker))"
    )

    con.execute(
        "CREATE TABLE IF NOT EXISTS corporate_actions ("
        "event_date DATE, "
        "ticker VARCHAR, "
        "action_type VARCHAR, "
        "description VARCHAR, "
        "expected_effect VARCHAR, "
        "confidence DOUBLE, "
        "source VARCHAR)"
    )

    con.execute(
        "CREATE TABLE IF NOT EXISTS ipo_watch ("
        "company VARCHAR, "
        "ticker VARCHAR, "
        "expected_date DATE, "
        "estimated_market_cap DOUBLE, "
        "estimated_free_float DOUBLE, "
        "c25_horizon VARCHAR, "
        "confidence DOUBLE, "
        "source VARCHAR)"
    )

    con.execute(
        "CREATE TABLE IF NOT EXISTS c25_membership_history ("
        "ticker VARCHAR, "
        "valid_from DATE, "
        "valid_to DATE, "
        "review VARCHAR, "
        "source VARCHAR, "
        "PRIMARY KEY (ticker, valid_from))"
    )

    con.close()


def sync_current_c25():
    con = connect()

    con.execute("""
        UPDATE universe
        SET current_c25 = FALSE
    """)

    con.execute("""
        UPDATE universe
        SET current_c25 = TRUE
        WHERE ticker IN (
            SELECT ticker
            FROM c25_membership_history
            WHERE valid_to IS NULL
        )
    """)

    con.close()