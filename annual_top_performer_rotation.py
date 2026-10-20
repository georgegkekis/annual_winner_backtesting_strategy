#!/usr/bin/env python3
"""Monthly top-performer investing with an annual weakest-holding rotation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math

import pandas as pd

from backtest_common import (
    CACHE_DIR,
    DEFAULT_TOP_PERFORMER_COUNT,
    INDEXES,
    add_backtest_arguments,
    annualized_return,
    format_portfolio_header,
    format_portfolio_summary,
    format_purchase_details,
    get_backtest_window,
    get_cached_adjusted_closes,
    get_cached_constituents,
    get_year_end_prices,
    run_benchmark,
    select_top_performers,
    validate_backtest_arguments,
)


@dataclass
class BacktestResult:
    total_invested: float
    ending_value: float
    annual_return_pct: float
    purchases: pd.DataFrame
    holdings: pd.DataFrame
    sales: pd.DataFrame
    write_offs: pd.DataFrame
    annual_performance: pd.DataFrame


def format_annual_sales(sales: pd.DataFrame) -> list[str]:
    if sales.empty:
        return []
    return [
        "Companies sold at annual rotation:",
        f"{'Year':>6} | Company Sold",
        *(f"{sale.year:>6} | {sale.sold}" for sale in sales.itertuples(index=False)),
    ]


def format_write_offs(write_offs: pd.DataFrame) -> list[str]:
    if write_offs.empty:
        return []
    return [
        "Holdings written off at $0 due to ended price history:",
        f"{'Year':>6} | Company",
        *(
            f"{write_off.year:>6} | {write_off.ticker}"
            for write_off in write_offs.itertuples(index=False)
        ),
    ]


def format_final_holdings(holdings: pd.DataFrame) -> list[str]:
    lines = [
        "Final holdings at end of backtest:",
        f"{'Company':<12} | {'Shares':>14} | {'Ending Price':>14} | {'Ending Value':>14}",
    ]
    for holding in holdings.itertuples(index=False):
        lines.append(
            f"{holding.ticker:<12} | "
            f"{holding.shares:>14,.4f} | "
            f"${holding.ending_price:>13,.2f} | "
            f"${holding.ending_value:>13,.2f}"
        )
    return lines


def format_annual_portfolio_performance(performance: pd.DataFrame) -> list[str]:
    lines = [
        "Annual portfolio performance:",
        f"{'Year':>6} | {'Starting Value':>14} | {'New Investment':>14} | "
        f"{'Value at Exit':>14} | {'Annual Return':>13}",
    ]
    for row in performance.itertuples(index=False):
        lines.append(
            f"{row.year:>6} | "
            f"${row.starting_value:>13,.2f} | "
            f"${row.new_investment:>13,.2f} | "
            f"${row.ending_value:>13,.2f} | "
            f"{row.annual_return_pct:>+12.2f}%"
        )
    return lines


def run_backtest(
    prices: pd.DataFrame,
    start_year: int,
    end_year: int,
    monthly_investment: float = 1000,
    constituent_symbols: list[str] | None = None,
    top_performer_count: int = DEFAULT_TOP_PERFORMER_COUNT,
) -> BacktestResult:
    if start_year > end_year:
        raise ValueError("start_year must not be after end_year.")
    if monthly_investment <= 0 or not math.isfinite(monthly_investment):
        raise ValueError("monthly_investment must be finite and greater than zero.")
    if top_performer_count <= 0:
        raise ValueError("top_performer_count must be greater than zero.")

    members = (
        [str(symbol) for symbol in prices.columns]
        if constituent_symbols is None
        else constituent_symbols
    )
    year_end_prices = get_year_end_prices(prices)
    if end_year not in year_end_prices.index:
        raise RuntimeError(f"No price data available in {end_year}.")

    valuation_dates = prices.index[prices.index.year == end_year]
    if valuation_dates.empty:
        raise RuntimeError(f"No price data available in {end_year}.")
    valuation_date = valuation_dates[-1]

    lots: list[dict[str, object]] = []
    first_held_dates: dict[str, pd.Timestamp] = {}
    sales: list[dict[str, object]] = []
    write_offs: list[dict[str, int | str]] = []
    annual_performance: list[dict[str, float | int]] = []
    total_invested = 0.0

    for investment_year in range(start_year, end_year + 1):
        year_flows: list[tuple[pd.Timestamp, float]] = []
        if investment_year > start_year:
            opening_dates = prices.index[prices.index.year == investment_year - 1]
            if opening_dates.empty:
                raise RuntimeError(
                    f"No opening valuation date available for {investment_year}."
                )
            opening_date = opening_dates[-1]
            opening_prices = year_end_prices.loc[investment_year - 1]
            starting_value = 0.0
            for lot in lots:
                for ticker, shares in lot["positions"].items():
                    price = opening_prices.get(ticker)
                    if pd.isna(price):
                        raise RuntimeError(
                            f"No opening price available for held stock {ticker} "
                            f"in {investment_year - 1}."
                        )
                    starting_value += float(shares) * float(price)
            if starting_value > 0:
                year_flows.append((opening_date, starting_value))
        else:
            starting_value = 0.0

        winner_year = investment_year - 1
        top_performers = select_top_performers(
            year_end_prices, winner_year, members, top_performer_count
        )
        year_open_date: pd.Timestamp | None = None

        if investment_year > start_year:
            held_symbols = {
                ticker
                for lot in lots
                for ticker, shares in lot["positions"].items()
                if shares > 0
            }
            if held_symbols:
                if (
                    winner_year - 1 not in year_end_prices.index
                    or winner_year not in year_end_prices.index
                ):
                    raise RuntimeError(
                        f"Price history does not cover {winner_year - 1} and "
                        f"{winner_year}, which are needed to rank held stocks."
                    )

                prior_year_start = year_end_prices.loc[winner_year - 1]
                prior_year_end = year_end_prices.loc[winner_year]
                held_returns = (
                    prior_year_end.reindex(held_symbols)
                    / prior_year_start.reindex(held_symbols)
                    - 1
                ).replace([float("inf"), -float("inf")], pd.NA).dropna()
                eligible = []
                for ticker in held_returns.index:
                    if ticker not in first_held_dates or ticker not in prices.columns:
                        continue
                    ticker_prices = prices[ticker].dropna()
                    prior_year_quotes = ticker_prices[
                        ticker_prices.index.year == winner_year
                    ]
                    if (
                        not prior_year_quotes.empty
                        and first_held_dates[ticker] <= prior_year_quotes.index[0]
                        and not ticker_prices[
                            ticker_prices.index.year == investment_year
                        ].empty
                    ):
                        eligible.append(ticker)

                if eligible:
                    weakest_ticker = str(held_returns.loc[eligible].idxmin())
                    weak_prices = prices[weakest_ticker].dropna()
                    sale_quotes = weak_prices[
                        weak_prices.index.year == investment_year
                    ]
                    if sale_quotes.empty:
                        raise RuntimeError(
                            f"No sale price available for {weakest_ticker} in "
                            f"{investment_year}."
                        )
                    sale_date = sale_quotes.index[0]
                    sale_price = float(sale_quotes.iloc[0])
                    year_open_date = sale_date
                    proceeds = 0.0
                    reallocated_by_lot: list[tuple[dict[str, object], float]] = []

                    for lot in lots:
                        lot_positions = lot["positions"]
                        shares_sold = float(lot_positions.pop(weakest_ticker, 0.0))
                        lot_proceeds = shares_sold * sale_price
                        proceeds += lot_proceeds
                        if lot_proceeds:
                            reallocated_by_lot.append((lot, lot_proceeds))

                    first_held_dates.pop(weakest_ticker, None)
                    for ticker in top_performers.index:
                        ticker = str(ticker)
                        ticker_prices = prices[ticker].dropna()
                        reinvestment_quotes = ticker_prices[
                            (ticker_prices.index.year == investment_year)
                            & (ticker_prices.index >= sale_date)
                        ]
                        if reinvestment_quotes.empty:
                            raise RuntimeError(
                                f"No reinvestment price available for {ticker} in "
                                f"{investment_year} after {sale_date.date()}."
                            )
                        purchase_date = reinvestment_quotes.index[0]
                        purchase_price = float(reinvestment_quotes.iloc[0])
                        for lot, lot_proceeds in reallocated_by_lot:
                            lot_positions = lot["positions"]
                            lot_positions[ticker] = lot_positions.get(ticker, 0.0) + (
                                lot_proceeds
                                / len(top_performers)
                                / purchase_price
                            )
                        first_held_dates.setdefault(ticker, purchase_date)

                    sales.append(
                        {
                            "year": investment_year,
                            "investment_year": investment_year - 1,
                            "sold": weakest_ticker,
                            "prior_year_return_pct": float(
                                held_returns.loc[weakest_ticker]
                            )
                            * 100,
                            "sale_date": sale_date,
                            "sale_price": sale_price,
                            "proceeds": proceeds,
                            "reinvested_in": ", ".join(map(str, top_performers.index)),
                        }
                    )

        for month in range(1, 13):
            monthly_investment_per_stock = monthly_investment / len(top_performers)
            for ticker, winner_return in top_performers.items():
                ticker = str(ticker)
                winner_prices = prices[ticker].dropna()
                month_quotes = winner_prices[
                    (winner_prices.index.year == investment_year)
                    & (winner_prices.index.month == month)
                ]
                if month == 1 and year_open_date is not None:
                    month_quotes = winner_prices[
                        (winner_prices.index.year == investment_year)
                        & (winner_prices.index >= year_open_date)
                    ]
                elif month_quotes.empty:
                    month_quotes = winner_prices[
                        (winner_prices.index.year == investment_year)
                        & (
                            winner_prices.index
                            >= pd.Timestamp(investment_year, month, 1)
                        )
                    ]
                if month_quotes.empty:
                    raise RuntimeError(
                        f"No purchase price available for {ticker} in or after "
                        f"{investment_year}-{month:02d}."
                    )

                purchase_date = month_quotes.index[0]
                purchase_price = float(month_quotes.iloc[0])
                ending_price = year_end_prices.loc[end_year].get(ticker)
                shares = monthly_investment_per_stock / purchase_price
                lots.append(
                    {
                        "investment_year": investment_year,
                        "winner_of_year": winner_year,
                        "selected_company": ticker,
                        "winner_year_return_pct": float(winner_return) * 100,
                        "purchase_date": purchase_date,
                        "investment": monthly_investment_per_stock,
                        "purchase_price": purchase_price,
                        "shares": shares,
                        "ending_price": (
                            float(ending_price)
                            if pd.notna(ending_price)
                            else 0.0
                        ),
                        "ending_value": 0.0,
                        "positions": {ticker: shares},
                    }
                )
                first_held_dates.setdefault(ticker, purchase_date)
                total_invested += monthly_investment_per_stock
                year_flows.append((purchase_date, monthly_investment_per_stock))

        ending_year_prices = year_end_prices.loc[investment_year]
        year_ending_value = 0.0
        written_off_this_year: set[str] = set()
        for lot in lots:
            positions = lot["positions"]
            for ticker, shares in list(positions.items()):
                ticker_prices = prices[ticker].dropna()
                year_quotes = ticker_prices[
                    ticker_prices.index.year == investment_year
                ]
                if year_quotes.empty and (
                    ticker_prices.empty
                    or ticker_prices.index[-1].year < investment_year
                ):
                    positions.pop(ticker)
                    first_held_dates.pop(ticker, None)
                    if ticker not in written_off_this_year:
                        write_offs.append(
                            {"year": investment_year, "ticker": ticker}
                        )
                        written_off_this_year.add(ticker)
                    continue
                if year_quotes.empty:
                    available_quotes = ticker_prices[
                        ticker_prices.index.year < investment_year
                    ]
                    if available_quotes.empty:
                        raise RuntimeError(
                            f"No ending price available for held stock {ticker} "
                            f"in {investment_year}."
                        )
                    price = available_quotes.iloc[-1]
                else:
                    price = year_quotes.iloc[-1]
                year_ending_value += float(shares) * float(price)

        year_dates = prices.index[prices.index.year == investment_year]
        if year_dates.empty:
            raise RuntimeError(
                f"No valuation date available for {investment_year}."
            )
        year_valuation_date = year_dates[-1]
        annual_cashflows = pd.DataFrame(
            [
                {
                    "purchase_date": flow_date,
                    "investment": amount,
                    "ending_value": 0.0,
                }
                for flow_date, amount in year_flows
            ]
        )
        if annual_cashflows.empty:
            raise RuntimeError(
                f"No opening value or contributions available for {investment_year}."
            )
        annual_cashflows.loc[
            annual_cashflows.index[-1], "ending_value"
        ] = year_ending_value
        annual_performance.append(
            {
                "year": investment_year,
                "starting_value": starting_value,
                "new_investment": monthly_investment * 12,
                "ending_value": year_ending_value,
                "annual_return_pct": annualized_return(
                    annual_cashflows, year_valuation_date
                ),
            }
        )

    holdings_shares: dict[str, float] = {}
    for lot in lots:
        for ticker, shares in lot["positions"].items():
            holdings_shares[ticker] = holdings_shares.get(ticker, 0.0) + shares

    holding_rows = []
    for ticker, shares in holdings_shares.items():
        ending_price = year_end_prices.loc[end_year].get(ticker)
        if pd.isna(ending_price):
            raise RuntimeError(f"No ending price available for held stock {ticker}.")
        for lot in lots:
            lot_shares = float(lot["positions"].get(ticker, 0.0))
            lot["ending_value"] += lot_shares * float(ending_price)
        holding_rows.append(
            {
                "ticker": ticker,
                "shares": shares,
                "ending_price": float(ending_price),
                "ending_value": shares * float(ending_price),
            }
        )

    purchases = pd.DataFrame(
        [{key: value for key, value in lot.items() if key != "positions"} for lot in lots]
    )
    holdings = pd.DataFrame(holding_rows)
    ending_value = float(holdings["ending_value"].sum()) if not holdings.empty else 0.0
    return BacktestResult(
        total_invested=total_invested,
        ending_value=ending_value,
        annual_return_pct=annualized_return(purchases, valuation_date),
        purchases=purchases,
        holdings=holdings,
        sales=pd.DataFrame(sales),
        write_offs=pd.DataFrame(write_offs, columns=["year", "ticker"]),
        annual_performance=pd.DataFrame(annual_performance),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Invest monthly in the previous year's top performers; at each "
            "year-end rotation, sell the weakest eligible holding and reinvest "
            "the proceeds among the new year's top performers."
        )
    )
    add_backtest_arguments(
        parser,
        "Number of prior-year top performers to buy and share sale proceeds "
        f"with (default: {DEFAULT_TOP_PERFORMER_COUNT}).",
    )
    return validate_backtest_arguments(parser, parser.parse_args())


def main() -> None:
    args = parse_args()
    (
        completed_year,
        first_investment_year,
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
    result = run_backtest(
        prices,
        first_investment_year,
        completed_year,
        monthly_investment=args.amount,
        constituent_symbols=constituents,
        top_performer_count=args.top_performers,
    )
    benchmark_purchases = run_benchmark(
        prices, args.years, args.amount, benchmark_ticker, completed_year
    )
    valuation_date = prices.index[prices.index.year == completed_year][-1]

    sales_lines = format_annual_sales(result.sales)
    if sales_lines:
        print()
        print("\n".join(sales_lines))
    write_off_lines = format_write_offs(result.write_offs)
    if write_off_lines:
        print()
        print("\n".join(write_off_lines))
    print()
    print("\n".join(format_final_holdings(result.holdings)))
    print()
    print(
        "\n".join(
            format_annual_portfolio_performance(result.annual_performance)
        )
    )
    if args.top_performers > 1:
        print(
            f"\nMonthly purchase cohorts in each year's top {args.top_performers} "
            "(original company labels; ending value includes later reallocations):"
        )
        print("\n".join(format_purchase_details(result.purchases, valuation_date)))
    print()
    print(format_portfolio_header())
    print(
        format_portfolio_summary(
            INDEXES[args.index]["benchmark_name"],
            float(benchmark_purchases["investment"].sum()),
            float(benchmark_purchases["ending_value"].sum()),
            annualized_return(benchmark_purchases, valuation_date),
        )
    )
    print(
        format_portfolio_summary(
            "Previous-year winners",
            result.total_invested,
            result.ending_value,
            result.annual_return_pct,
        )
    )


if __name__ == "__main__":
    main()
