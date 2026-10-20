#!/usr/bin/env python3
"""Backtest annual investments in the previous year's top index performers."""

from __future__ import annotations

import argparse
from datetime import date

import pandas as pd

from backtest_common import (
    CACHE_DIR,
    DOWNLOAD_CHUNK_SIZE,
    DEFAULT_TOP_PERFORMER_COUNT,
    INDEXES,
    SP500_BENCHMARK_TICKER,
    add_backtest_arguments,
    annualized_return,
    download_adjusted_closes,
    format_portfolio_header,
    format_portfolio_summary,
    format_purchase_details,
    forward_return_pct,
    get_backtest_window,
    get_cached_adjusted_closes,
    get_cached_constituents,
    get_constituents,
    get_year_end_prices,
    run_benchmark,
    run_sp500_benchmark,
    select_top_performers,
    validate_backtest_arguments,
)


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
    year_end_prices = get_year_end_prices(prices)
    if completed_year not in year_end_prices.index:
        raise RuntimeError(f"No price data available in {completed_year}.")
    purchases = []

    for investment_year in range(first_investment_year, completed_year + 1):
        winner_year = investment_year - 1
        top_performers = select_top_performers(
            year_end_prices,
            winner_year,
            constituent_symbols,
            top_performer_count,
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
                    month_start = pd.Timestamp(investment_year, month, 1)
                    month_prices = winner_prices[
                        (winner_prices.index.year == investment_year)
                        & (winner_prices.index >= month_start)
                    ]
                    if month_prices.empty:
                        raise RuntimeError(
                            f"No purchase price available for {ticker} in or "
                            f"after {investment_year}-{month:02d}."
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Invest monthly in the top index members by previous-calendar-year "
            "return, holding each purchase through the end of the backtest."
        )
    )
    add_backtest_arguments(
        parser,
        "Number of previous-year top performers to buy each month "
        f"(default: {DEFAULT_TOP_PERFORMER_COUNT}).",
    )
    return validate_backtest_arguments(parser, parser.parse_args())


def main() -> None:
    args = parse_args()
    (
        completed_year,
        _,
        download_start,
        download_end,
        cache_month,
    ) = get_backtest_window(args.years)

    constituents = get_cached_constituents(args.index, CACHE_DIR, cache_month)
    benchmark_ticker = INDEXES[args.index]["benchmark_ticker"]
    prices = get_cached_adjusted_closes(
        args.index,
        [*constituents, benchmark_ticker],
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
    benchmark_purchases = run_benchmark(
        prices, args.years, args.amount, benchmark_ticker
    )
    sp500_value = float(benchmark_purchases["ending_value"].sum())
    valuation_date = prices.index[prices.index.year == completed_year][-1]
    total_invested = args.years * 12 * args.amount

    print(format_portfolio_header())
    print(
        format_portfolio_summary(
            INDEXES[args.index]["benchmark_name"],
            float(benchmark_purchases["investment"].sum()),
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
