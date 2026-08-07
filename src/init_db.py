import pandas as pd
from .db import connect, init, ROOT

def main():
    init()
    con = connect()
    u = pd.read_csv(ROOT / "config/universe.csv")
    con.register("u", u)
    con.execute("DELETE FROM universe")
    con.execute("INSERT INTO universe SELECT ticker, company, isin, current_c25 FROM u")
    con.close()
    print("Database initialized.")

if __name__ == "__main__":
    main()
