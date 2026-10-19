import json
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import sp500_annual_winner_backtest as backtest


class PriceCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.temporary_directory.name)
        self.start = date(2023, 1, 1)
        self.end = date(2025, 1, 1)
        self.prices = pd.DataFrame(
            {"AAA": [10.0, 12.0]},
            index=pd.to_datetime(["2023-01-03", "2024-12-31"]),
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def get_cached_prices(self, cache_month, symbols=None, start=None):
        return backtest.get_cached_adjusted_closes(
            "sp500",
            symbols or ["AAA"],
            start or self.start,
            self.end,
            self.cache_dir,
            cache_month,
        )

    def test_reuses_cache_within_same_month(self):
        with patch.object(
            backtest, "download_adjusted_closes", return_value=self.prices
        ) as download:
            first = self.get_cached_prices("2026-10")
            second = self.get_cached_prices("2026-10")

        download.assert_called_once()
        pd.testing.assert_frame_equal(first, second)

    def test_refreshes_cache_when_month_changes(self):
        with patch.object(
            backtest, "download_adjusted_closes", return_value=self.prices
        ) as download:
            self.get_cached_prices("2026-10")
            self.get_cached_prices("2026-11")

        self.assertEqual(download.call_count, 2)
        metadata = json.loads(
            (self.cache_dir / "adjusted_closes_sp500.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(metadata["cache_month"], "2026-11")

    def test_refreshes_cache_when_request_changes(self):
        with patch.object(
            backtest, "download_adjusted_closes", return_value=self.prices
        ) as download:
            self.get_cached_prices("2026-10")
            self.get_cached_prices("2026-10", symbols=["AAA", "BBB"])

        self.assertEqual(download.call_count, 2)


class ConstituentCacheTests(unittest.TestCase):
    def test_reuses_cache_within_month_and_refreshes_next_month(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            with patch.object(
                backtest, "get_constituents", return_value=["AAA"]
            ) as get_constituents:
                first = backtest.get_cached_constituents(
                    "sp500", cache_dir, "2026-10"
                )
                second = backtest.get_cached_constituents(
                    "sp500", cache_dir, "2026-10"
                )
                third = backtest.get_cached_constituents(
                    "sp500", cache_dir, "2026-11"
                )

        self.assertEqual(first, ["AAA"])
        self.assertEqual(second, ["AAA"])
        self.assertEqual(third, ["AAA"])
        self.assertEqual(get_constituents.call_count, 2)


if __name__ == "__main__":
    unittest.main()
