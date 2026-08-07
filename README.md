# OMXC25 Predictor v1.0

Starter project for a data-driven OMXC25 predictor.

Reference periods:
- 1 June–30 November -> December review
- 1 December–31 May -> June review

This version:
- uses Nasdaq-reported turnover/value traded as the primary liquidity input;
- stores daily market data in DuckDB;
- calculates accumulated turnover for the active reference period;
- produces a liquidity ranking;
- reserves modules for corporate actions and IPO monitoring.

Important: this is a transparent predictor, not a claim to reproduce Nasdaq's proprietary selection algorithm.

## Run

```bash
pip install -r requirements.txt
python -m src.init_db
python -m src.build_ranking
```

Put Nasdaq CSV exports in `data/raw/`. Map their exact column names in `config/columns.yml`.

Example Nasdaq page supplied by the user:
https://www.nasdaq.com/european-market-activity/shares/bava?id=TX2265
