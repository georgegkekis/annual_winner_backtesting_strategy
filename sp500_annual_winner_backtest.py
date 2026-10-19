#!/usr/bin/env python3
"""Backtest annual investments in the previous year's top index performers."""

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
    },
    "nasdaq100": {
        "constituents_url": "https://en.wikipedia.org/wiki/Nasdaq-100",
        "constituent_column": "Ticker",
    },
}
DOWNLOAD_CHUNK_SIZE = 40
CACHE_DIR = Path(__file__).parent / "price_cache"
SP500_BENCHMARK_TICKER = "SPY"
DEFAULT_TOP_PERFORMER_COUNT = 1


def get_constituents(index_name: str) -> list[str]:
    config = INDEXES[index_name]
    request = Request(
        config["constituents_url"],
        headers={"User-Agent": "annual-index-winner-backtest/1.0"},
    )
    tables = pd.read_html(
        StringIO(urlopen(request, timeout=30).read().decode("utf-8"))
    )
    for table in tables:
        if config["constituent_column"] in table.columns:
            return (
                table[config["constituent_column"]]
                .dropna()
                .astype(str)
                .str.replace(".", "-", regex=False)
                .unique()
                .tolist()
            )
    raise ValueError(
        f"Could not find column {config['constituent_column']!r} on "
        f"{config['constituents_url']}"
    )


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


def run_backtest(
    prices: pd.DataFrame,
    years: int,
    monthly_investment: float,
    constituent_symbols: list[str],
    top_performer_count: int = DEFAULT_TOP_PERFORMER_COUNT,
) -> pd.DataFrame:
    if top_performer_count <= 0:
        raise ValueError("top_performer_count must be greater than zero.")
    completed_year = date.today().year - 1
    first_investment_year = completed_year - years + 1
    year_end_prices = prices.groupby(prices.index.year).last()
    if completed_year not in year_end_prices.index:
        raise RuntimeError(f"No price data available in {completed_year}.")
    purchases = []

    for investment_year in range(first_investment_year, completed_year + 1):
        winner_year = investment_year - 1
        prior_year_prices = year_end_prices.reindex([winner_year - 1, winner_year])
        if len(prior_year_prices) != 2:
            raise RuntimeError(
                f"Price history does not cover {winner_year - 1} and {winner_year}, "
                "which are needed to select that year's winner."
            )
        constituent_prices = prior_year_prices.loc[
            :, prior_year_prices.columns.isin(constituent_symbols)
        ]
        returns = constituent_prices.iloc[1] / constituent_prices.iloc[0] - 1
        returns = returns.replace([float("inf"), -float("inf")], pd.NA).dropna()
        if returns.empty:
            raise RuntimeError(
                f"No index constituents have complete price data for {winner_year}."
            )

        top_performers = returns.nlargest(top_performer_count)
        if len(top_performers) < top_performer_count:
            raise RuntimeError(
                f"Only {len(top_performers)} constituents have complete price data "
                f"for selecting the top {top_performer_count} in {winner_year}."
            )

        investment_per_company = monthly_investment / top_performer_count
        for month in range(1, 13):
            for ticker, winner_return in top_performers.items():
                winner_prices = prices[ticker].dropna()
                month_prices = winner_prices[
                    (winner_prices.index.year == investment_year)
                    & (winner_prices.index.month == month)
                ]
                if month_prices.empty:
                    raise RuntimeError(
                        f"No purchase price available for {ticker} in "
                        f"{investment_year}-{month:02d}."
                    )
                purchase_date = month_prices.index[0]
                purchase_price = month_prices.iloc[0]
                ending_price = year_end_prices.loc[completed_year].get(ticker)
                if pd.isna(purchase_price) or pd.isna(ending_price):
                    raise RuntimeError(
                        f"Missing purchase or ending price for {ticker} "
                        f"({investment_year}-{completed_year})."
                    )

                shares = investment_per_company / float(purchase_price)
                purchases.append(
                    {
                        "investment_year": investment_year,
                        "winner_of_year": winner_year,
                        "selected_company": str(ticker),
                        "winner_year_return_pct": float(winner_return) * 100,
                        "purchase_date": purchase_date,
                        "investment": investment_per_company,
                        "purchase_price": float(purchase_price),
                        "shares": shares,
                        "ending_price": float(ending_price),
                        "ending_value": shares * float(ending_price),
                        "return_5y_pct": forward_return_pct(
                            winner_prices, purchase_date, 5, prices.index[-1]
                        ),
                        "return_10y_pct": forward_return_pct(
                            winner_prices, purchase_date, 10, prices.index[-1]
                        ),
                    }
                )

    return pd.DataFrame(purchases)


def forward_return_pct(
    prices: pd.Series,
    purchase_date: pd.Timestamp,
    years: int,
    valuation_date: pd.Timestamp,
) -> float | None:
    target_date = pd.Timestamp(purchase_date) + pd.DateOffset(years=years)
    if target_date > valuation_date:
        return None
    target_prices = prices.loc[target_date:valuation_date].dropna()
    if target_prices.empty:
        return None
    purchase_price = prices.asof(purchase_date)
    if pd.isna(purchase_price):
        return None
    return (float(target_prices.iloc[0]) / float(purchase_price) - 1) * 100


def run_sp500_benchmark(
    prices: pd.DataFrame, years: int, monthly_investment: float
) -> pd.DataFrame:
    if SP500_BENCHMARK_TICKER not in prices.columns:
        raise RuntimeError(
            f"Price history does not contain the S&P 500 benchmark "
            f"{SP500_BENCHMARK_TICKER}."
        )

    completed_year = date.today().year - 1
    first_investment_year = completed_year - years + 1
    benchmark_prices = prices[SP500_BENCHMARK_TICKER].dropna()
    ending_prices = benchmark_prices[benchmark_prices.index.year == completed_year]
    if ending_prices.empty:
        raise RuntimeError(
            f"No {SP500_BENCHMARK_TICKER} price data available in {completed_year}."
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
                raise RuntimeError(
                    f"No {SP500_BENCHMARK_TICKER} price data available in "
                    f"{investment_year}-{month:02d}."
                )
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


def annualized_return(purchases: pd.DataFrame, valuation_date: pd.Timestamp) -> float:
    if purchases.empty or purchases["investment"].sum() <= 0:
        raise ValueError("Annualized return requires positive investments.")
    if (purchases["purchase_date"] >= valuation_date).any():
        raise ValueError("Valuation date must be after every purchase date.")

    years = (
        (valuation_date - pd.to_datetime(purchases["purchase_date"])).dt.days
        / 365
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


def format_purchase_details(
    purchases: pd.DataFrame, valuation_date: pd.Timestamp
) -> list[str]:
    lines = [
        f"{'Company':<12} | {'Year':>6} | {'Years Held':>10} | "
        f"{'Ending Value':>14} | {'Profit/Loss %':>13} | "
        f"{'CAGR':>9} | {'Next 5Y %':>10} | {'Next 10Y %':>11}"
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

        forward_returns = []
        for column in ("return_5y_pct", "return_10y_pct"):
            returns = company_purchases[column]
            if returns.isna().any():
                forward_returns.append("N/A")
            else:
                weighted_return = float(
                    (returns * company_purchases["investment"]).sum() / investment
                )
                forward_returns.append(f"{weighted_return:+.2f}%")

        lines.append(
            f"{company:<12} | "
            f"{investment_year:>6} | "
            f"{int(holding_years):>10} | "
            f"${ending_value:>13,.2f} | "
            f"{profit_loss_pct:>+12.2f}% | "
            f"{cagr_pct:>+8.2f}% | "
            f"{forward_returns[0]:>10} | "
            f"{forward_returns[1]:>11}"
        )
    return lines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Invest monthly in the top index members by previous-calendar-year "
            "return, holding each purchase through the end of the backtest."
        )
    )
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
        help=(
            "Number of previous-year top performers to buy each month "
            f"(default: {DEFAULT_TOP_PERFORMER_COUNT})."
        ),
    )
    args = parser.parse_args()
    if not math.isfinite(args.amount) or args.amount <= 0:
        parser.error("--amount must be a finite number greater than zero.")
    if args.years <= 0:
        parser.error("--years must be greater than zero.")
    if args.top_performers <= 0:
        parser.error("--top-performers must be greater than zero.")
    return args


def main() -> None:
    args = parse_args()
    completed_year = date.today().year - 1
    first_investment_year = completed_year - args.years + 1
    download_start = date(first_investment_year - 2, 1, 1)
    download_end = date(completed_year + 1, 1, 1)

    cache_month = date.today().strftime("%Y-%m")
    constituents = get_cached_constituents(args.index, CACHE_DIR, cache_month)
    prices = get_cached_adjusted_closes(
        args.index,
        [*constituents, SP500_BENCHMARK_TICKER],
        download_start,
        download_end,
        CACHE_DIR,
        cache_month,
    )
    winner_purchases = run_backtest(
        prices,
        args.years,
        args.amount,
        constituent_symbols=constituents,
        top_performer_count=args.top_performers,
    )
    winner_value = float(winner_purchases["ending_value"].sum())
    benchmark_purchases = run_sp500_benchmark(prices, args.years, args.amount)
    sp500_value = float(benchmark_purchases["ending_value"].sum())
    valuation_date = prices.index[prices.index.year == completed_year][-1]
    total_invested = args.years * 12 * args.amount

    print(
        f"{'Strategy':<22} | {'Invested':>15} | {'Ending':>15} | "
        f"{'Profit/Loss':>15} | {'Profit/Loss %':>13} | "
        f"{'Annual Return':>13}"
    )
    print(
        format_portfolio_summary(
            "S&P 500",
            total_invested,
            sp500_value,
            annualized_return(benchmark_purchases, valuation_date),
        )
    )
    print(
        format_portfolio_summary(
            "Previous-year winners",
            total_invested,
            winner_value,
            annualized_return(winner_purchases, valuation_date),
        )
    )
    print(f"\nMonthly purchases in each year's top {args.top_performers}:")
    print("\n".join(format_purchase_details(winner_purchases, valuation_date)))


if __name__ == "__main__":
    main()
