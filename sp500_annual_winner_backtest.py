#!/usr/bin/env python3
"""Backtest annual investments in the previous year's best-performing index member."""

from __future__ import annotations

import argparse
from datetime import date
from io import StringIO
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


def run_backtest(
    prices: pd.DataFrame, years: int, annual_investment: float
) -> pd.DataFrame:
    completed_year = date.today().year - 1
    first_investment_year = completed_year - years + 1
    year_end_prices = prices.groupby(prices.index.year).last()
    year_first_prices = prices.groupby(prices.index.year).first()
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
        returns = prior_year_prices.iloc[1] / prior_year_prices.iloc[0] - 1
        returns = returns.replace([float("inf"), -float("inf")], pd.NA).dropna()
        if returns.empty:
            raise RuntimeError(
                f"No index constituents have complete price data for {winner_year}."
            )

        winner = str(returns.idxmax())
        if investment_year not in year_first_prices.index:
            raise RuntimeError(f"No price data available in {investment_year}.")
        purchase_price = year_first_prices.loc[investment_year].get(winner)
        ending_price = year_end_prices.loc[completed_year].get(winner)
        if pd.isna(purchase_price) or pd.isna(ending_price):
            raise RuntimeError(
                f"Missing purchase or ending price for {winner} "
                f"({investment_year}-{completed_year})."
            )

        shares = annual_investment / float(purchase_price)
        purchases.append(
            {
                "investment_year": investment_year,
                "winner_of_year": winner_year,
                "selected_company": winner,
                "winner_year_return_pct": float(returns.loc[winner]) * 100,
                "investment": annual_investment,
                "purchase_price": float(purchase_price),
                "shares": shares,
                "ending_price": float(ending_price),
                "ending_value": shares * float(ending_price),
            }
        )

    return pd.DataFrame(purchases)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Invest a fixed amount at the start of each year in the index member "
            "with the highest return over the previous calendar year, then hold "
            "each purchase through the end of the backtest."
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
        help="Amount contributed at the start of each year (default: 1000).",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=30,
        help="Number of completed investment years (default: 30).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "results" / "annual_purchases.csv",
        help="CSV file for annual purchase details.",
    )
    args = parser.parse_args()
    if not math.isfinite(args.amount) or args.amount <= 0:
        parser.error("--amount must be a finite number greater than zero.")
    if args.years <= 0:
        parser.error("--years must be greater than zero.")
    return args


def main() -> None:
    args = parse_args()
    completed_year = date.today().year - 1
    first_investment_year = completed_year - args.years + 1
    download_start = date(first_investment_year - 2, 1, 1)
    download_end = date(completed_year + 1, 1, 1)

    constituents = get_constituents(args.index)
    prices = download_adjusted_closes(constituents, download_start, download_end)
    purchases = run_backtest(prices, args.years, args.amount)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    purchases.to_csv(args.output, index=False)

    invested = float(purchases["investment"].sum())
    ending_value = float(purchases["ending_value"].sum())
    print(f"Index universe: {args.index} (today's constituents)")
    print(f"Investment years: {first_investment_year}-{completed_year}")
    print(f"Annual contribution: ${args.amount:,.2f}")
    print(f"Total invested: ${invested:,.2f}")
    print(f"Ending portfolio value: ${ending_value:,.2f}")
    print(f"Profit/loss: ${ending_value - invested:,.2f}")
    print(f"Annual purchases written to: {args.output}")


if __name__ == "__main__":
    main()
