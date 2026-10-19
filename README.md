# Annual Index Winner Backtest

Backtests a strategy that invests a fixed contribution equally across a
configurable number of the index constituents with the highest returns in the
previous calendar year. By default, it buys just the top performer.
Every year's purchases are held until the backtest ends; earlier purchases are
not sold or reallocated.

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
(including dividends and splits). The strategy selects the requested number of
top companies using calendar-year adjusted-close return, splits the annual
investment equally among them, buys at the first available trading-day close in
the following year, and values all shares at the last available close of the
final completed investment year. It assumes fractional shares and excludes fees
and taxes.

The script also invests the same yearly amount in SPY, an S&P 500 ETF, on the
first available trading day of each year. It prints two lines, one per
strategy, showing total invested, ending value, profit/loss, and profit/loss
percentage, along with the annualized money-weighted return. The annualized
return uses the actual purchase dates and final valuation date, so it accounts
for each yearly contribution being invested for a different length of time. It
also prints a per-purchase list with each selected company, purchase year,
ending value, profit/loss percentage, and that purchase's CAGR. The list also
shows each purchase's total return after five and ten years; it uses the first
available adjusted close on or after each anniversary and shows `N/A` if the
full period is not yet available.

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

Set `--top-performers` to buy more than one previous-year winner. For example,
to split each annual contribution equally among the top five:

```bash
python sp500_annual_winner_backtest.py --top-performers 5
```

The default universe is `sp500`; `--index nasdaq100` runs the same strategy
against the current Nasdaq-100 members:

```bash
python sp500_annual_winner_backtest.py --index nasdaq100
```

Downloaded index constituents and adjusted closing prices are cached in
`price_cache/`. The cache is reused throughout the current calendar month and
refreshed when the month changes. It is also refreshed if the requested index,
date range, or constituent list no longer matches the cached price data. The
cache files are local and excluded from Git. No backtest result files are
written.
