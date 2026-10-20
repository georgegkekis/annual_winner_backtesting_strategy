from datetime import date
from contextlib import redirect_stdout
from io import StringIO
from argparse import Namespace
import unittest
from unittest.mock import patch

import pandas as pd

from backtest_common import get_backtest_window, get_year_end_prices, run_benchmark
import sp500_annual_winner_backtest as backtest


class ComparisonTests(unittest.TestCase):
    def test_backtest_window_preserves_default_historical_range(self):
        window = get_backtest_window(30, date(2026, 10, 19))

        self.assertEqual(
            window,
            (
                2025,
                1996,
                date(1994, 1, 1),
                date(2026, 1, 1),
                "2026-10",
            ),
        )

    def test_year_end_prices_do_not_carry_stale_quotes_forward(self):
        prices = pd.DataFrame(
            {
                "STALE": [10.0, float("nan")],
                "ACTIVE": [20.0, 22.0],
            },
            index=pd.to_datetime(["2024-01-02", "2024-12-31"]),
        )

        year_end_prices = get_year_end_prices(prices)

        self.assertTrue(pd.isna(year_end_prices.loc[2024, "STALE"]))
        self.assertEqual(year_end_prices.loc[2024, "ACTIVE"], 22.0)

    def setUp(self):
        completed_year = date.today().year - 1
        investment_dates = [
            pd.Timestamp(completed_year, month, 3) for month in range(1, 13)
        ]
        dates = [
            pd.Timestamp(completed_year - 2, 12, 31),
            pd.Timestamp(completed_year - 1, 12, 31),
            *investment_dates,
            pd.Timestamp(completed_year, 12, 31),
        ]
        self.prices = pd.DataFrame(
            {
                "AAA": [10.0, 20.0, *[30 + month / 10 for month in range(1, 13)], 60.0],
                "BBB": [10.0, 12.0, *[15 + month / 10 for month in range(1, 13)], 20.0],
                "CCC": [10.0, 11.0, *[12 + month / 10 for month in range(1, 13)], 24.0],
                "DDD": [10.0, 10.5, *[11 + month / 10 for month in range(1, 13)], 18.0],
                "EEE": [10.0, 10.2, *[10.5 + month / 10 for month in range(1, 13)], 16.0],
                "FFF": [10.0, 10.1, *[10.2 + month / 10 for month in range(1, 13)], 14.0],
                "SPY": [100.0, 110.0, *[100 + month for month in range(1, 13)], 200.0],
                "^RUT": [100.0, 105.0, *[100 + month for month in range(1, 13)], 150.0],
            },
            index=pd.DatetimeIndex(dates),
        )

    def test_strategy_buys_top_five_equally_each_month_not_spy(self):
        purchases = backtest.run_backtest(
            self.prices,
            1,
            1000,
            constituent_symbols=["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
            top_performer_count=5,
        )

        self.assertEqual(len(purchases), 12 * 5)
        self.assertEqual(purchases["investment"].tolist(), [200] * (12 * 5))
        self.assertEqual(purchases["investment_year"].nunique(), 1)
        for month in range(1, 13):
            monthly = purchases[
                pd.to_datetime(purchases["purchase_date"]).dt.month == month
            ]
            self.assertEqual(
                set(monthly["selected_company"]),
                {"AAA", "BBB", "CCC", "DDD", "EEE"},
            )

    def test_each_company_is_bought_on_its_first_available_trading_day(self):
        prices = self.prices.copy()
        completed_year = date.today().year - 1
        first_day = pd.Timestamp(f"{completed_year}-01-03")
        second_day = pd.Timestamp(f"{completed_year}-01-04")
        prices.loc[first_day, "CCC"] = float("nan")
        second_day_prices = prices.loc[first_day].copy()
        second_day_prices["CCC"] = 12.0
        prices.loc[second_day] = second_day_prices
        prices = prices.sort_index()

        purchases = backtest.run_backtest(
            prices,
            1,
            1000,
            constituent_symbols=["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
            top_performer_count=5,
        )
        ccc_purchase = purchases.loc[
            (purchases["selected_company"] == "CCC")
            & (pd.to_datetime(purchases["purchase_date"]).dt.month == 1)
        ].iloc[0]

        self.assertEqual(ccc_purchase["purchase_date"], second_day)
        self.assertEqual(ccc_purchase["investment"], 200)

    def test_monthly_purchase_defers_until_a_winners_next_available_quote(self):
        prices = self.prices.copy()
        completed_year = date.today().year - 1
        prices.loc[pd.Timestamp(completed_year, 7, 3), "CCC"] = float("nan")

        purchases = backtest.run_backtest(
            prices,
            1,
            1000,
            constituent_symbols=["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
            top_performer_count=5,
        )
        ccc_purchases = purchases[
            purchases["selected_company"] == "CCC"
        ]
        august_purchases = ccc_purchases[
            pd.to_datetime(ccc_purchases["purchase_date"]).dt.month == 8
        ]

        self.assertEqual(len(ccc_purchases), 12)
        self.assertEqual(len(august_purchases), 2)

    def test_default_selects_and_allocates_to_one_performer(self):
        purchases = backtest.run_backtest(
            self.prices,
            1,
            1000,
            constituent_symbols=["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
        )

        self.assertEqual(purchases["selected_company"].tolist(), ["AAA"] * 12)
        self.assertEqual(len(purchases), 12)
        self.assertEqual(purchases["investment"].tolist(), [1000] * 12)

    def test_selection_handles_object_typed_prices_and_missing_history(self):
        prices = pd.DataFrame(
            {
                "AAA": [10.0, 15.0],
                "MISSING": [float("nan"), 20.0],
                "ZERO": [0.0, 1.0],
            },
            index=[2023, 2024],
            dtype=object,
        )

        selected = backtest.select_top_performers(
            prices, 2024, ["AAA", "MISSING", "ZERO"], 1
        )

        self.assertEqual(selected.index.tolist(), ["AAA"])
        self.assertEqual(selected.iloc[0], 0.5)

    def test_sp500_benchmark_uses_same_annual_contribution(self):
        purchases = backtest.run_sp500_benchmark(self.prices, 1, 1000)

        self.assertEqual(purchases["investment"].sum(), 12000)
        self.assertEqual(len(purchases), 12)

    def test_russell_benchmark_uses_russell_index_history(self):
        purchases = run_benchmark(
            self.prices,
            1,
            1000,
            "^RUT",
            date.today().year - 1,
        )

        self.assertEqual(purchases["investment"].sum(), 12000)
        self.assertEqual(purchases.iloc[0]["ending_price"], 150.0)

    def test_benchmark_skips_only_months_before_history_begins(self):
        dates = pd.to_datetime(
            ["2025-06-02", "2025-07-01", "2025-08-01", "2025-09-01",
             "2025-10-01", "2025-11-03", "2025-12-01"]
        )
        prices = pd.DataFrame(
            {"GD.AT": [100.0, 105.0, 110.0, 115.0, 120.0, 125.0, 130.0]},
            index=dates,
        )

        purchases = run_benchmark(
            prices,
            1,
            1000,
            "GD.AT",
            2025,
        )

        self.assertEqual(len(purchases), 7)
        self.assertEqual(purchases["investment"].sum(), 7000)
        self.assertEqual(purchases.iloc[0]["purchase_date"], pd.Timestamp("2025-06-02"))

    def test_benchmark_defers_contribution_when_month_has_no_quotes(self):
        prices = self.prices.copy()
        completed_year = date.today().year - 1
        prices.loc[pd.Timestamp(completed_year, 7, 3), "^RUT"] = float("nan")

        purchases = run_benchmark(
            prices,
            1,
            1000,
            "^RUT",
            completed_year,
        )

        august_purchases = purchases[
            pd.to_datetime(purchases["purchase_date"]).dt.month == 8
        ]
        self.assertEqual(len(purchases), 12)
        self.assertEqual(purchases["investment"].sum(), 12000)
        self.assertEqual(len(august_purchases), 2)

    def test_annualized_return_accounts_for_purchase_dates(self):
        purchases = pd.DataFrame(
            {
                "purchase_date": pd.to_datetime(["2025-01-01", "2026-01-01"]),
                "investment": [1000.0, 1000.0],
                "ending_value": [1210.0, 1100.0],
            }
        )

        result = backtest.annualized_return(
            purchases, pd.Timestamp("2027-01-01")
        )

        self.assertAlmostEqual(result, 10.0, places=6)

    def test_summary_line_includes_invested_value_and_profit_or_loss(self):
        line = backtest.format_portfolio_summary("S&P 500", 1000, 1250, 25)

        self.assertEqual(
            line,
            "S&P 500                | $      1,000.00 | $      1,250.00 | "
            "$       +250.00 |   +25.00% |   +25.00%",
        )

    def test_summary_line_formats_a_loss(self):
        line = backtest.format_portfolio_summary("S&P 500", 1000, 750, -10)

        self.assertTrue(line.endswith("$       -250.00 |   -25.00% |   -10.00%"))

    def test_main_prints_portfolio_summary_after_purchase_details(self):
        completed_year = date.today().year - 1
        prices = pd.DataFrame(
            {"SPY": [100.0]},
            index=pd.to_datetime([f"{completed_year}-12-31"]),
        )
        winner_purchases = pd.DataFrame(
            {"ending_value": [1500.0], "investment": [1000.0]}
        )
        benchmark_purchases = pd.DataFrame(
            {"ending_value": [1200.0], "investment": [1000.0]}
        )
        output = StringIO()

        with (
            patch.object(
                backtest,
                "parse_args",
                return_value=Namespace(
                    years=1,
                    amount=1000,
                    index="sp500",
                    top_performers=1,
                ),
            ),
            patch.object(
                backtest,
                "get_backtest_window",
                return_value=(
                    completed_year,
                    completed_year,
                    date(completed_year - 2, 1, 1),
                    date(completed_year + 1, 1, 1),
                    "2026-10",
                ),
            ),
            patch.object(backtest, "get_cached_constituents", return_value=["AAA"]),
            patch.object(backtest, "get_cached_adjusted_closes", return_value=prices),
            patch.object(backtest, "run_backtest", return_value=winner_purchases),
            patch.object(
                backtest, "run_benchmark", return_value=benchmark_purchases
            ),
            patch.object(
                backtest, "format_purchase_details", return_value=["Purchase detail"]
            ),
            patch.object(backtest, "annualized_return", return_value=5.0),
            redirect_stdout(output),
        ):
            backtest.main()

        lines = output.getvalue().splitlines()
        summary_header_index = next(
            index for index, line in enumerate(lines) if line.startswith("Strategy")
        )
        self.assertLess(lines.index("Purchase detail"), summary_header_index)
        self.assertEqual(lines[-1].split("|")[0].strip(), "Previous-year winners")

    def test_purchase_details_group_monthly_purchases_by_company_and_year(self):
        purchases = pd.DataFrame(
            {
                "selected_company": ["AAA", "AAA"],
                "investment_year": [2025, 2025],
                "purchase_date": pd.to_datetime(["2025-01-01", "2025-02-03"]),
                "investment": [500.0, 500.0],
                "ending_value": [600.0, 610.0],
            }
        )

        lines = backtest.format_purchase_details(
            purchases, pd.Timestamp("2027-01-01")
        )

        self.assertEqual(len(lines), 2)
        self.assertIn("Company", lines[0])
        self.assertIn("Years Held", lines[0])
        self.assertIn("AAA", lines[1])
        self.assertIn("2025", lines[1])
        self.assertIn("$     1,210.00", lines[1])
        self.assertIn("+21.00%", lines[1])
        self.assertNotIn("Next 5Y", lines[0])
        self.assertNotIn("Next 10Y", lines[0])
        self.assertEqual(len(lines[1].split("|")), 6)


if __name__ == "__main__":
    unittest.main()
