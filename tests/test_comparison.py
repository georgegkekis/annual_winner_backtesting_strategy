from datetime import date
import unittest

import pandas as pd

import sp500_annual_winner_backtest as backtest


class ComparisonTests(unittest.TestCase):
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

    def test_sp500_benchmark_uses_same_annual_contribution(self):
        purchases = backtest.run_sp500_benchmark(self.prices, 1, 1000)

        self.assertEqual(purchases["investment"].sum(), 12000)
        self.assertEqual(len(purchases), 12)

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

    def test_purchase_details_group_monthly_purchases_by_company_and_year(self):
        purchases = pd.DataFrame(
            {
                "selected_company": ["AAA", "AAA"],
                "investment_year": [2025, 2025],
                "purchase_date": pd.to_datetime(["2025-01-01", "2025-02-03"]),
                "investment": [500.0, 500.0],
                "ending_value": [600.0, 610.0],
                "return_5y_pct": [None, None],
                "return_10y_pct": [None, None],
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
        self.assertIn("+10.24%", lines[1])
        self.assertTrue(lines[1].endswith("N/A |         N/A"))

    def test_forward_returns_use_first_available_price_on_or_after_anniversary(self):
        prices = pd.Series(
            [100.0, 120.0, 150.0],
            index=pd.to_datetime(["2020-01-02", "2025-01-03", "2030-01-03"]),
        )

        result = backtest.forward_return_pct(
            prices, pd.Timestamp("2020-01-02"), 5, pd.Timestamp("2030-01-03")
        )

        self.assertAlmostEqual(result, 20.0)

    def test_forward_return_is_unavailable_before_full_period(self):
        prices = pd.Series(
            [100.0, 120.0],
            index=pd.to_datetime(["2020-01-02", "2024-12-31"]),
        )

        result = backtest.forward_return_pct(
            prices, pd.Timestamp("2020-01-02"), 5, pd.Timestamp("2024-12-31")
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
