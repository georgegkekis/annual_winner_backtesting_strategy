# Annual Index Winner Backtest

Backtests a strategy that contributes a fixed amount at the start of each year
to the index constituent with the highest return in the previous calendar year.
Every year's purchase is held until the backtest ends; earlier purchases are not
sold or reallocated.

The defaults scan the S&P 500, contribute `$1,000` each year, and simulate the
most recent 30 completed investment years. The index universe and amount of each
annual contribution can be changed from the command line.

## Important limitation

The constituent list is fetched from Wikipedia at run time and reflects the
index's **current** members, not its historical membership. Companies removed
from the index are therefore excluded from every historical year. This
survivorship bias can make the strategy's results look better than a backtest
using historically accurate constituent lists. The results are an approximation,
not a historically constituent-correct S&P 500 backtest.

Prices come from Yahoo Finance via `yfinance`, using adjusted close prices
(including dividends and splits). The strategy selects the winner using
calendar-year adjusted-close return, buys at the first available trading-day
close in the following year, and values all shares at the last available close
of the final completed investment year. It assumes fractional shares and
excludes fees and taxes.

## Run

Python 3.10 or later is recommended.

```bash
python -m pip install -r requirements.txt
python sp500_annual_winner_backtest.py
```

For example, to invest `$2,500` per year for 15 years:

```bash
python sp500_annual_winner_backtest.py --amount 2500 --years 15
```

The default universe is `sp500`; `--index nasdaq100` runs the same strategy
against the current Nasdaq-100 members:

```bash
python sp500_annual_winner_backtest.py --index nasdaq100
```

Annual pick, contribution, share, and ending-value details are written to
`results/annual_purchases.csv`. Use `--output PATH` to choose another file.

Downloaded index constituents and adjusted closing prices are cached in
`price_cache/`. The cache is reused throughout the current calendar month and
refreshed when the month changes. It is also refreshed if the requested index,
date range, or constituent list no longer matches the cached price data. The
cache files are local and excluded from Git.
