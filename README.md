# Annual Index Winner Backtest

Backtests a strategy that invests a fixed contribution equally across a
configurable number of the index constituents with the highest returns in the
previous calendar year. By default, it buys just the top performer.
Every year's purchases are held until the backtest ends; earlier purchases are
not sold or reallocated.

The defaults scan the S&P 500, contribute `$1,000` each month, and simulate the
most recent 30 completed investment years. Each year's selected companies are
based on the previous calendar year's performance and remain fixed for all
monthly purchases that year. The index, monthly amount, backtest duration, and
number of companies to buy can be changed from the command line.

## Important limitation

The constituent list is fetched from Wikipedia at run time and reflects the
index's **current** members, not its historical membership. Companies removed
from the index are therefore excluded from every historical year. This
survivorship bias can make the strategy's results look better than a backtest
using historically accurate constituent lists. The results are an approximation,
not a historically constituent-correct S&P 500 backtest.

Prices come from Yahoo Finance via `yfinance`, using adjusted close prices
(including dividends and splits). The strategy selects the requested number of
top companies using calendar-year adjusted-close return, splits each monthly
investment equally among them, buys at the first available trading-day close of
each month in the following year, and defers a contribution to the next available
quote in that investment year if the month has no quote. It values all shares at
the last available close of the final completed investment year. It assumes
fractional shares and excludes fees and taxes. Annual selection ignores a
constituent's year-end price if its latest quote is more than 90 days before
that year's final market date.

The script also invests the same monthly amount in SPY, an S&P 500 ETF, on the
first available trading day of each month. It prints two lines, one per
strategy, showing total invested, ending value, profit/loss, and profit/loss
percentage, along with the annualized money-weighted return. The annualized
return uses the actual purchase dates and final valuation date, so it accounts
for each monthly contribution being invested for a different length of time. It
also prints a company-by-year list that groups monthly purchases into one
position per company per year. It shows the combined ending value,
profit/loss percentage, and CAGR.

## Run

Python 3.10 or later is recommended.

```bash
python -m pip install -r requirements.txt
python sp500_annual_winner_backtest.py
```

For example, to invest `$2,500` per month for 15 years:

```bash
python sp500_annual_winner_backtest.py --amount 2500 --years 15
```

Set `--top-performers` to buy more than one previous-year winner. For example,
to split each monthly contribution equally among the top five:

```bash
python sp500_annual_winner_backtest.py --top-performers 5
```

The default universe is `sp500`; `--index nasdaq100` runs the same strategy
against the current Nasdaq-100 members:

```bash
python sp500_annual_winner_backtest.py --index nasdaq100
```

Use `--index russell2000` to scan the current Russell 2000 constituent list.
The report compares the strategy with the Russell 2000 index (`^RUT`), whose
Yahoo Finance history covers the 30-year default backtest. IWM is an ETF
benchmark alternative, but its price history only begins in 2000, so it cannot
cover the default period beginning in 1996. The index comparison reflects price
returns and does not include dividends. Contributions before benchmark history
begins are omitted; if an in-period month has no quote, that contribution is
deferred to the next available quote in the same year:

```bash
python sp500_annual_winner_backtest.py --index russell2000
python annual_top_performer_rotation.py --index russell2000 --top-performers 5
```

Use `--index russell1000` to scan the current Russell 1000 constituent list.
The benchmark is the Russell 1000 index (`^RUI`):

```bash
python sp500_annual_winner_backtest.py --index russell1000
python annual_top_performer_rotation.py --index russell1000 --top-performers 5
```

Use `--index athex` to scan the current members of Greece's Athens General
Composite (ATHEX:GD). Constituent symbols are read from TradingView's Greece
market scanner and converted to Yahoo Finance `.AT` tickers. The benchmark is
Yahoo Finance's `GD.AT`; its available history begins in 1997, so benchmark
contributions before its first available quote are omitted, and any in-period
month with no quote is deferred to the next available quote in that year. Its
reported invested amount reflects any contributions that could not be made:

```bash
python sp500_annual_winner_backtest.py --index athex
python annual_top_performer_rotation.py --index athex --top-performers 5
```

## Annual rotation strategy

The separate `annual_top_performer_rotation.py` script uses the same index,
monthly contribution, duration, and top-performer options. It invests each
month in the previous calendar year's selected top performers. At the start of
each later investment year, it sells the entire holding in the weakest stock
that was already held through the previous calendar year, ranking stocks by
that year's adjusted-close return. The proceeds are split equally among the
new year's selected top performers; the regular monthly contributions then
continue as usual, deferring a contribution to the next available quote in the
same year if its month has no quote. If no held stock has complete prior-year
data, no rotation is made that year.
If a held company's price history has ended and it has no quotes in a later
investment year, its position is written off at $0 and reported separately.

It prints an annual portfolio performance table regardless of the number of
selected stocks. Each year's return is money-weighted using the portfolio's
starting value, monthly contributions, and ending portfolio value; proceeds
reinvested during a rotation are transfers within the portfolio, not new
contributions. It also prints a year-by-year list of companies sold and written
off, plus a final holdings table. When `--top-performers` is greater than one, a company-by-year
purchase cohort report is included as supplemental detail; its forward-return
columns refer to the original selected companies rather than the reallocated
portfolio.

```bash
python annual_top_performer_rotation.py
python annual_top_performer_rotation.py --top-performers 10 --amount 1000 --years 30
```

This is an annual, start-of-year rebalance using only the previous completed
calendar year's returns. As with the original script, current index constituents
are used for the historical backtest, and transactions assume fractional shares
with no fees or taxes.

Downloaded index constituents and adjusted closing prices are cached in
`price_cache/`. The cache is reused throughout the current calendar month and
refreshed when the month changes. It is also refreshed if the requested index,
date range, or constituent list no longer matches the cached price data. The
cache files are local and excluded from Git. No backtest result files are
written. Both strategies share their data, selection, benchmark, return,
formatting, and command-line helpers in `backtest_common.py`.

The Russell 2000 constituent list is fetched from Stock Analysis and, like the
other constituent lists, reflects current rather than historical membership.
