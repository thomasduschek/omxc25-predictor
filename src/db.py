from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "db" / "omxc25.duckdb"

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH))

def init():
    con = connect()
    con.execute("CREATE TABLE IF NOT EXISTS universe (ticker VARCHAR PRIMARY KEY,company VARCHAR,isin VARCHAR,current_c25 BOOLEAN,free_float_market_cap DOUBLE,market_cap_rank INTEGER,top35_eligible BOOLEAN)")
    con.execute("CREATE TABLE IF NOT EXISTS daily_market (date DATE, ticker VARCHAR, turnover DOUBLE, volume DOUBLE, trades DOUBLE, vwap DOUBLE, close DOUBLE, source_file VARCHAR, UNIQUE(date,ticker))")
    con.execute("CREATE TABLE IF NOT EXISTS corporate_actions (event_date DATE, ticker VARCHAR, action_type VARCHAR, description VARCHAR, expected_effect VARCHAR, confidence DOUBLE, source VARCHAR)")
    con.execute("CREATE TABLE IF NOT EXISTS ipo_watch (company VARCHAR, ticker VARCHAR, expected_date DATE, estimated_market_cap DOUBLE, estimated_free_float DOUBLE, c25_horizon VARCHAR, confidence DOUBLE, source VARCHAR)")
    con.close()
