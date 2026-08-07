from .db import connect, init
from .reference_period import active_period
from .db import ROOT

def main():
    init()
    start, end, label = active_period()
    con = connect()
    df = con.execute('''
        SELECT u.ticker, u.company, u.current_c25,
               COALESCE(SUM(d.turnover),0) AS accumulated_turnover,
               COUNT(d.date) AS trading_days
        FROM universe u
        LEFT JOIN daily_market d
          ON d.ticker=u.ticker AND d.date BETWEEN ? AND ?
        GROUP BY 1,2,3
        ORDER BY accumulated_turnover DESC
    ''',[start,end]).df()
    df["liquidity_rank"] = range(1,len(df)+1)
    df["reference_start"] = str(start)
    df["reference_end"] = str(end)
    df["review"] = label
    out = ROOT/"reports/current_ranking.csv"
    out.parent.mkdir(exist_ok=True)
    df.to_csv(out,index=False)
    print(df.to_string(index=False))
    print(f"Saved to {out}")
    con.close()

if __name__ == "__main__":
    main()
