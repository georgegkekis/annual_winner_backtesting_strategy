import unittest

import pandas as pd

import annual_top_performer_rotation as rotation


class AnnualRotationTests(unittest.TestCase):
    def setUp(self):
        self.symbols = ["AAA", "BBB", "CCC", "DDD"]
        year_end_values = {
            2018: [100.0, 100.0, 100.0, 100.0],
            2019: [120.0, 110.0, 100.0, 90.0],
            2020: [96.0, 121.0, 130.0, 90.0],
            2021: [90.0, 140.0, 150.0, 100.0],
        }
        rows = {
            pd.Timestamp(year, 12, 31): values
            for year, values in year_end_values.items()
        }
        for year in (2020, 2021):
            prior_values = year_end_values[year - 1]
            ending_values = year_end_values[year]
            for month in range(1, 13):
                date = pd.Timestamp(year, month, 2)
                progress = month / 12
                rows[date] = [
                    start + (end - start) * progress
                    for start, end in zip(prior_values, ending_values)
                ]
        self.prices = pd.DataFrame.from_dict(
            rows, orient="index", columns=self.symbols
        ).sort_index()

    def test_selects_prior_year_top_performers(self):
        top = rotation.select_top_performers(
            self.prices.groupby(self.prices.index.year).last(),
            2020,
            self.symbols,
            2,
        )

        self.assertEqual(list(top.index), ["CCC", "BBB"])

    def test_sells_weakest_eligible_holding_and_splits_proceeds_among_winners(self):
        result = rotation.run_backtest(
            self.prices,
            2020,
            2021,
            monthly_investment=1200,
            constituent_symbols=self.symbols,
            top_performer_count=2,
        )

        self.assertEqual(result.total_invested, 28_800)
        self.assertEqual(len(result.sales), 1)
        sale = result.sales.iloc[0]
        self.assertEqual(sale["year"], 2021)
        self.assertEqual(sale["sold"], "AAA")
        self.assertAlmostEqual(sale["prior_year_return_pct"], -20.0)
        self.assertEqual(sale["reinvested_in"], "CCC, BBB")
        self.assertEqual(sale["sale_price"], 95.5)
        self.assertEqual(
            rotation.format_annual_sales(result.sales),
            [
                "Companies sold at annual rotation:",
                "  Year | Company Sold",
                "  2021 | AAA",
            ],
        )
        self.assertNotIn("AAA", result.holdings["ticker"].tolist())
        self.assertEqual(set(result.holdings["ticker"]), {"BBB", "CCC"})
        self.assertGreater(result.ending_value, 0)
        self.assertAlmostEqual(result.purchases["ending_value"].sum(), result.ending_value)
        self.assertEqual(result.annual_performance["year"].tolist(), [2020, 2021])
        self.assertAlmostEqual(
            result.annual_performance.iloc[-1]["ending_value"],
            result.ending_value,
        )
        self.assertAlmostEqual(
            result.annual_performance.iloc[1]["starting_value"],
            result.annual_performance.iloc[0]["ending_value"],
        )
        self.assertTrue(
            result.annual_performance["annual_return_pct"].map(pd.notna).all()
        )

    def test_reinvests_after_sale_when_a_winner_has_no_later_january_quote(self):
        prices = self.prices.copy()
        prices.loc[pd.Timestamp(2021, 1, 2), "BBB"] = float("nan")

        result = rotation.run_backtest(
            prices,
            2020,
            2021,
            monthly_investment=1200,
            constituent_symbols=self.symbols,
            top_performer_count=2,
        )

        self.assertEqual(len(result.sales), 1)
        self.assertEqual(result.sales.iloc[0]["reinvested_in"], "CCC, BBB")
        bbb_purchases = result.purchases[
            result.purchases["selected_company"] == "BBB"
        ]
        self.assertTrue(
            (bbb_purchases["purchase_date"] >= pd.Timestamp(2021, 2, 2)).any()
        )

    def test_writes_off_holding_after_its_price_history_ends(self):
        prices = self.prices.copy()
        prices.loc[prices.index.year == 2021, "AAA"] = float("nan")

        result = rotation.run_backtest(
            prices,
            2020,
            2021,
            monthly_investment=1200,
            constituent_symbols=self.symbols,
            top_performer_count=2,
        )

        self.assertEqual(
            result.write_offs.to_dict("records"),
            [{"year": 2021, "ticker": "AAA"}],
        )
        self.assertNotIn("AAA", result.holdings["ticker"].tolist())
        self.assertEqual(
            rotation.format_write_offs(result.write_offs),
            [
                "Holdings written off at $0 due to ended price history:",
                "  Year | Company",
                "  2021 | AAA",
            ],
        )

    def test_defers_monthly_purchase_until_a_winners_next_available_quote(self):
        prices = self.prices.copy()
        prices.loc[pd.Timestamp(2021, 7, 2), "CCC"] = float("nan")

        result = rotation.run_backtest(
            prices,
            2020,
            2021,
            monthly_investment=1200,
            constituent_symbols=self.symbols,
            top_performer_count=2,
        )
        ccc_purchases = result.purchases[
            result.purchases["selected_company"] == "CCC"
        ]
        august_purchases = ccc_purchases[
            ccc_purchases["purchase_date"] == pd.Timestamp(2021, 8, 2)
        ]

        self.assertEqual(len(ccc_purchases), 12)
        self.assertEqual(len(august_purchases), 2)

    def test_default_strategy_selects_one_company(self):
        result = rotation.run_backtest(
            self.prices,
            2020,
            2021,
            monthly_investment=1000,
            constituent_symbols=self.symbols,
        )

        self.assertEqual(result.total_invested, 24_000)
        self.assertEqual(len(result.sales), 1)
        self.assertEqual(result.sales.iloc[0]["sold"], "AAA")
        self.assertEqual(result.sales.iloc[0]["reinvested_in"], "CCC")
        self.assertEqual(result.holdings["ticker"].tolist(), ["CCC"])
        self.assertAlmostEqual(
            result.holdings["ending_value"].sum(), result.ending_value
        )
        final_holding_lines = rotation.format_final_holdings(result.holdings)
        self.assertEqual(len(final_holding_lines), 3)
        self.assertIn("CCC", final_holding_lines[2])
        self.assertNotIn("AAA", final_holding_lines[2])
        annual_lines = rotation.format_annual_portfolio_performance(
            result.annual_performance
        )
        self.assertEqual(len(annual_lines), 4)
        first_year_fields = [field.strip() for field in annual_lines[2].split("|")]
        self.assertEqual(
            float(first_year_fields[1].replace("$", "").replace(",", "")),
            0,
        )
        second_year_fields = [field.strip() for field in annual_lines[3].split("|")]
        self.assertAlmostEqual(
            float(second_year_fields[3].replace("$", "").replace(",", "")),
            result.ending_value,
            places=2,
        )
        self.assertAlmostEqual(
            float(second_year_fields[1].replace("$", "").replace(",", "")),
            result.annual_performance.iloc[1]["starting_value"],
            places=2,
        )
        self.assertIn("Annual Return", annual_lines[1])


if __name__ == "__main__":
    unittest.main()
