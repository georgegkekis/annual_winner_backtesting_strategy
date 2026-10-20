"""Shared data, calculation, and output helpers for backtest strategies."""

from __future__ import annotations

import argparse
from datetime import date
from io import StringIO
import json
import math
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd
import yfinance as yf


INDEXES = {
    "sp500": {
        "constituents_url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "constituent_column": "Symbol",
        "benchmark_ticker": "SPY",
        "benchmark_name": "S&P 500",
    },
    "nasdaq100": {
        "constituents_url": "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies",
        "constituent_column": "Ticker",
        "benchmark_ticker": "SPY",
        "benchmark_name": "S&P 500",
    },
    "russell2000": {
        "constituents_url": "https://stockanalysis.com/list/russell-2000-stocks/",
        "constituent_column": "Symbol",
        "page_size": 500,
        "benchmark_ticker": "^RUT",
        "benchmark_name": "Russell 2000 Index",
    },
    "russell1000": {
        "constituents_url": "https://en.wikipedia.org/wiki/List_of_Russell_1000_companies",
        "constituent_column": "Symbol",
        "benchmark_ticker": "^RUI",
        "benchmark_name": "Russell 1000 Index",
    },
    "athex": {
        "scanner_url": "https://scanner.tradingview.com/greece/scan",
        "scanner_index": "ATHEX:GD",
        "symbol_suffix": ".AT",
        "benchmark_ticker": "GD.AT",
        "benchmark_name": "Athens General Composite",
    },
}
DOWNLOAD_CHUNK_SIZE = 40
CACHE_DIR = Path(__file__).parent / "price_cache"
SP500_BENCHMARK_TICKER = "SPY"
DEFAULT_TOP_PERFORMER_COUNT = 1
MAX_YEAR_END_PRICE_AGE_DAYS = 90


def get_constituents(index_name: str) -> list[str]:
    config = INDEXES[index_name]
    if "scanner_url" in config:
        payload = json.dumps(
            {"columns": ["name", "indexes"], "range": [0, 1000]}
        ).encode("utf-8")
        request = Request(
            config["scanner_url"],
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Origin": "https://www.tradingview.com",
                "Referer": "https://www.tradingview.com/",
                "User-Agent": "annual-index-winner-backtest/1.0",
            },
            method="POST",
        )
        response = json.loads(urlopen(request, timeout=30).read().decode("utf-8"))
        rows = response.get("data")
        if not isinstance(rows, list):
            raise ValueError(
                f"Invalid constituents response from {config['scanner_url']}"
            )
        symbols = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            exchange_symbol = row.get("s")
            values = row.get("d")
            if (
                not isinstance(exchange_symbol, str)
                or not exchange_symbol.startswith("ATHEX:")
                or not isinstance(values, list)
                or len(values) < 2
                or not isinstance(values[1], list)
            ):
                continue
            if any(
                isinstance(index, dict)
                and index.get("proname") == config["scanner_index"]
                for index in values[1]
            ):
                symbol = exchange_symbol.partition(":")[2]
                symbols.append(f"{symbol}{config['symbol_suffix']}")
        if not symbols:
            raise ValueError(
                f"No constituents for {config['scanner_index']} were found at "
                f"{config['scanner_url']}"
            )
        return list(dict.fromkeys(symbols))

    page_size = config.get("page_size")
    symbols: list[str] = []
    page = 1

    while True:
        page_url = config["constituents_url"]
        if page > 1:
            page_url = f"{page_url}?page={page}"
        request = Request(
            page_url,
            headers={"User-Agent": "annual-index-winner-backtest/1.0"},
        )
        tables = pd.read_html(
            StringIO(urlopen(request, timeout=30).read().decode("utf-8"))
        )
        for table in tables:
            column = config["constituent_column"]
            if column in table.columns:
                page_symbols = (
                    table[column]
                    .dropna()
                    .astype(str)
                    .str.replace(".", "-", regex=False)
                    .unique()
                    .tolist()
                )
                if page_size is not None and page > 1 and not set(
                    page_symbols
                ).difference(symbols):
                    raise RuntimeError(
                        f"Constituent source did not return new symbols on page "
                        f"{page}: {page_url}"
                    )
                symbols.extend(page_symbols)
                break
        else:
            if page == 1:
                raise ValueError(
                    f"Could not find column {config['constituent_column']!r} on "
                    f"{page_url}"
                )
            raise ValueError(
                f"Could not find column {config['constituent_column']!r} on "
                f"constituent page {page}: {page_url}"
            )

        if page_size is None or len(page_symbols) < page_size:
            return list(dict.fromkeys(symbols))
        page += 1


def get_cached_constituents(
    index_name: str, cache_dir: Path, cache_month: str
) -> list[str]:
    cache_path = cache_dir / f"constituents_{index_name}.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("cache_month") == cache_month:
            symbols = cached.get("symbols")
            if isinstance(symbols, list) and all(
                isinstance(symbol, str) for symbol in symbols
            ):
                return symbols

    symbols = get_constituents(index_name)
    cache_dir.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps({"cache_month": cache_month, "symbols": symbols}, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(cache_path)
    return symbols


def download_adjusted_closes(
    symbols: list[str], start: date, end: date
) -> pd.DataFrame:
    ticker_to_symbol = {symbol.replace(".", "-"): symbol for symbol in symbols}
    frames = []

    for offset in range(0, len(symbols), DOWNLOAD_CHUNK_SIZE):
        chunk = symbols[offset : offset + DOWNLOAD_CHUNK_SIZE]
        data = yf.download(
            tickers=chunk,
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,
            progress=False,
            group_by="column",
            threads=True,
        )
        if data.empty:
            continue

        if isinstance(data.columns, pd.MultiIndex):
            close_level = next(
                (
                    level
                    for level in range(data.columns.nlevels)
                    if "Close" in data.columns.get_level_values(level)
                ),
                None,
            )
            if close_level is None:
                continue
            close = data.xs("Close", axis=1, level=close_level, drop_level=True)
        elif "Close" in data.columns:
            close = data[["Close"]].rename(columns={"Close": chunk[0]})
        else:
            continue

        if isinstance(close, pd.Series):
            close = close.to_frame()
        close.columns = [
            ticker_to_symbol.get(str(column), str(column)) for column in close.columns
        ]
        frames.append(close)

    if not frames:
        raise RuntimeError("Yahoo Finance returned no adjusted closing prices.")

    prices = pd.concat(frames, axis=1).sort_index()
    prices = prices.loc[:, ~prices.columns.duplicated()]
    if prices.empty:
        raise RuntimeError("No usable adjusted closing prices were downloaded.")
    return prices


def get_cached_adjusted_closes(
    index_name: str,
    symbols: list[str],
    start: date,
    end: date,
    cache_dir: Path,
    cache_month: str,
) -> pd.DataFrame:
    cache_path = cache_dir / f"adjusted_closes_{index_name}.csv"
    metadata_path = cache_dir / f"adjusted_closes_{index_name}.json"
    expected_metadata = {
        "cache_month": cache_month,
        "index": index_name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "symbols": sorted(symbols),
    }

    if cache_path.exists() and metadata_path.exists():
        cached_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if cached_metadata == expected_metadata:
            prices = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            if prices.empty:
                raise RuntimeError(f"The price cache is empty: {cache_path}")
            return prices

    prices = download_adjusted_closes(symbols, start, end)
    cache_dir.mkdir(parents=True, exist_ok=True)
    temporary_cache_path = cache_path.with_suffix(".csv.tmp")
    temporary_metadata_path = metadata_path.with_suffix(".json.tmp")
    prices.to_csv(temporary_cache_path)
    temporary_cache_path.replace(cache_path)
    temporary_metadata_path.write_text(
        json.dumps(expected_metadata, indent=2), encoding="utf-8"
    )
    temporary_metadata_path.replace(metadata_path)
    return prices


def get_year_end_prices(prices: pd.DataFrame) -> pd.DataFrame:
    year_end_prices = prices.groupby(prices.index.year).last()
    for year, year_prices in prices.groupby(prices.index.year):
        year_end_date = year_prices.index[-1]
        last_quote_dates = year_prices.apply(
            lambda column: column.last_valid_index()
        )
        stale_tickers = last_quote_dates[
            last_quote_dates.notna()
            & (
                year_end_date - last_quote_dates
                > pd.Timedelta(days=MAX_YEAR_END_PRICE_AGE_DAYS)
            )
        ].index
        year_end_prices.loc[year, stale_tickers] = float("nan")
    return year_end_prices


def get_backtest_window(
    years: int, today: date | None = None
) -> tuple[int, int, date, date, str]:
    as_of = today or date.today()
    completed_year = as_of.year - 1
    first_investment_year = completed_year - years + 1
    return (
        completed_year,
        first_investment_year,
        date(first_investment_year - 2, 1, 1),
        date(completed_year + 1, 1, 1),
        as_of.strftime("%Y-%m"),
    )


def select_top_performers(
    year_end_prices: pd.DataFrame,
    winner_year: int,
    constituent_symbols: list[str],
    count: int,
) -> pd.Series:
    prior_year_prices = year_end_prices.reindex([winner_year - 1, winner_year])
    if len(prior_year_prices) != 2:
        raise RuntimeError(
            f"Price history does not cover {winner_year - 1} and {winner_year}, "
            "which are needed to select that year's winner."
        )
    constituent_prices = prior_year_prices.loc[
        :, prior_year_prices.columns.isin(constituent_symbols)
    ]
    starting_prices = pd.to_numeric(constituent_prices.iloc[0], errors="coerce")
    ending_prices = pd.to_numeric(constituent_prices.iloc[1], errors="coerce")
    returns = ending_prices.div(starting_prices).sub(1)
    returns = returns.replace(
        [float("inf"), -float("inf")], float("nan")
    ).dropna()
    if returns.empty:
        raise RuntimeError(
            f"No index constituents have complete price data for {winner_year}."
        )
    top_performers = returns.nlargest(count)
    if len(top_performers) < count:
        raise RuntimeError(
            f"Only {len(top_performers)} constituents have complete price data "
            f"for selecting the top {count} in {winner_year}."
        )
    return top_performers


def run_benchmark(
    prices: pd.DataFrame,
    years: int,
    monthly_investment: float,
    benchmark_ticker: str = SP500_BENCHMARK_TICKER,
    completed_year: int | None = None,
) -> pd.DataFrame:
    if benchmark_ticker not in prices.columns:
        raise RuntimeError(
            f"Price history does not contain the benchmark {benchmark_ticker}."
        )

    if completed_year is None:
        completed_year = date.today().year - 1
    first_investment_year = completed_year - years + 1
    benchmark_prices = prices[benchmark_ticker].dropna()
    if benchmark_prices.empty:
        raise RuntimeError(
            f"No {benchmark_ticker} price data available for the backtest."
        )
    ending_prices = benchmark_prices[benchmark_prices.index.year == completed_year]
    if ending_prices.empty:
        raise RuntimeError(
            f"No {benchmark_ticker} price data available in {completed_year}."
        )
    ending_price = float(ending_prices.iloc[-1])
    purchases = []

    for investment_year in range(first_investment_year, completed_year + 1):
        for month in range(1, 13):
            month_prices = benchmark_prices[
                (benchmark_prices.index.year == investment_year)
                & (benchmark_prices.index.month == month)
            ]
            if month_prices.empty:
                month_start = pd.Timestamp(investment_year, month, 1)
                if month_start < benchmark_prices.index[0]:
                    continue
                month_prices = benchmark_prices[
                    (benchmark_prices.index.year == investment_year)
                    & (benchmark_prices.index >= month_start)
                ]
                if month_prices.empty:
                    continue
            purchase_price = float(month_prices.iloc[0])
            shares = monthly_investment / purchase_price
            purchases.append(
                {
                    "investment_year": investment_year,
                    "purchase_date": month_prices.index[0],
                    "investment": monthly_investment,
                    "purchase_price": purchase_price,
                    "shares": shares,
                    "ending_price": ending_price,
                    "ending_value": shares * ending_price,
                }
            )

    return pd.DataFrame(purchases)


def run_sp500_benchmark(
    prices: pd.DataFrame,
    years: int,
    monthly_investment: float,
    completed_year: int | None = None,
) -> pd.DataFrame:
    return run_benchmark(
        prices, years, monthly_investment, SP500_BENCHMARK_TICKER, completed_year
    )


def annualized_return(purchases: pd.DataFrame, valuation_date: pd.Timestamp) -> float:
    if purchases.empty or purchases["investment"].sum() <= 0:
        raise ValueError("Annualized return requires positive investments.")
    if (purchases["purchase_date"] >= valuation_date).any():
        raise ValueError("Valuation date must be after every purchase date.")

    years = (
        (valuation_date - pd.to_datetime(purchases["purchase_date"])).dt.days / 365
    )
    contributions = purchases["investment"].astype(float).tolist()
    durations = years.tolist()
    ending_value = float(purchases["ending_value"].sum())

    def discounted_value(rate: float) -> float:
        return sum(
            amount * (1 + rate) ** duration
            for amount, duration in zip(contributions, durations)
        )

    lower = -0.999999999
    upper = 1.0
    while discounted_value(upper) < ending_value:
        upper = upper * 2 + 1

    for _ in range(100):
        midpoint = (lower + upper) / 2
        if discounted_value(midpoint) < ending_value:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2 * 100


def format_portfolio_summary(
    name: str,
    total_invested: float,
    ending_value: float,
    annual_return_pct: float,
) -> str:
    profit_loss = ending_value - total_invested
    profit_loss_pct = profit_loss / total_invested * 100
    return (
        f"{name:<22} | "
        f"${total_invested:>14,.2f} | "
        f"${ending_value:>14,.2f} | "
        f"${profit_loss:>+14,.2f} | "
        f"{profit_loss_pct:>+8.2f}% | "
        f"{annual_return_pct:>+8.2f}%"
    )


def format_portfolio_header() -> str:
    return (
        f"{'Strategy':<22} | {'Invested':>15} | {'Ending':>15} | "
        f"{'Profit/Loss':>15} | {'Profit/Loss %':>13} | "
        f"{'Annual Return':>13}"
    )


def format_purchase_details(
    purchases: pd.DataFrame, valuation_date: pd.Timestamp
) -> list[str]:
    lines = [
        f"{'Company':<12} | {'Year':>6} | {'Years Held':>10} | "
        f"{'Ending Value':>14} | {'Profit/Loss %':>13} | "
        f"{'CAGR':>9}"
    ]
    grouped_purchases = purchases.groupby(
        ["investment_year", "selected_company"], sort=False
    )
    for (investment_year, company), company_purchases in grouped_purchases:
        investment = float(company_purchases["investment"].sum())
        ending_value = float(company_purchases["ending_value"].sum())
        profit_loss_pct = (ending_value - investment) / investment * 100
        first_purchase_date = pd.Timestamp(company_purchases["purchase_date"].min())
        holding_days = (valuation_date - first_purchase_date).days
        if holding_days <= 0:
            raise ValueError("Valuation date must be after each purchase date.")
        holding_years = holding_days / 365
        cagr_pct = annualized_return(company_purchases, valuation_date)

        lines.append(
            f"{company:<12} | "
            f"{investment_year:>6} | "
            f"{int(holding_years):>10} | "
            f"${ending_value:>13,.2f} | "
            f"{profit_loss_pct:>+12.2f}% | "
            f"{cagr_pct:>+8.2f}%"
        )
    return lines


def add_backtest_arguments(
    parser: argparse.ArgumentParser, top_performers_help: str
) -> argparse.ArgumentParser:
    parser.add_argument(
        "--index",
        choices=sorted(INDEXES),
        default="sp500",
        help="Index universe to scan (default: sp500).",
    )
    parser.add_argument(
        "--amount",
        type=float,
        default=1000,
        help="Amount contributed each month (default: 1000).",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=30,
        help="Number of completed investment years (default: 30).",
    )
    parser.add_argument(
        "--top-performers",
        type=int,
        default=DEFAULT_TOP_PERFORMER_COUNT,
        help=top_performers_help,
    )
    return parser


def validate_backtest_arguments(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> argparse.Namespace:
    if not math.isfinite(args.amount) or args.amount <= 0:
        parser.error("--amount must be a finite number greater than zero.")
    if args.years <= 0:
        parser.error("--years must be greater than zero.")
    if args.top_performers <= 0:
        parser.error("--top-performers must be greater than zero.")
    return args
