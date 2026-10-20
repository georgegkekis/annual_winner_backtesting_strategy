import json
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import backtest_common
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
            backtest_common, "download_adjusted_closes", return_value=self.prices
        ) as download:
            first = self.get_cached_prices("2026-10")
            second = self.get_cached_prices("2026-10")

        download.assert_called_once()
        pd.testing.assert_frame_equal(first, second)

    def test_refreshes_cache_when_month_changes(self):
        with patch.object(
            backtest_common, "download_adjusted_closes", return_value=self.prices
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
            backtest_common, "download_adjusted_closes", return_value=self.prices
        ) as download:
            self.get_cached_prices("2026-10")
            self.get_cached_prices("2026-10", symbols=["AAA", "BBB"])

        self.assertEqual(download.call_count, 2)


class ConstituentCacheTests(unittest.TestCase):
    def test_athex_uses_current_index_members_and_yahoo_tickers(self):
        response = {
            "data": [
                {
                    "s": "ATHEX:ALPHA",
                    "d": [
                        "ALPHA",
                        [
                            {"name": "ATHEX Composite", "proname": "ATHEX:GD"},
                        ],
                    ],
                },
                {
                    "s": "ATHEX:AAA",
                    "d": [
                        "AAA",
                        [{"name": "FTSE/ATHEX Large Cap", "proname": "ATHEX:FTSE"}],
                    ],
                },
                {
                    "s": "ATHEX:ETE",
                    "d": [
                        "ETE",
                        [
                            {"name": "ATHEX Composite", "proname": "ATHEX:GD"},
                            {"name": "FTSE/ATHEX Large Cap", "proname": "ATHEX:FTSE"},
                        ],
                    ],
                },
            ]
        }
        with patch("backtest_common.urlopen") as urlopen:
            urlopen.return_value.read.return_value = json.dumps(response).encode()
            symbols = backtest_common.get_constituents("athex")

        self.assertEqual(symbols, ["ALPHA.AT", "ETE.AT"])
        self.assertEqual(backtest_common.INDEXES["athex"]["benchmark_ticker"], "GD.AT")

    def test_nasdaq100_reads_tickers_from_components_list(self):
        table = pd.DataFrame(
            {
                "Ticker": ["AAPL", "BRK.B"],
                "Company": ["Apple", "Berkshire Hathaway"],
            }
        )
        with (
            patch(
                "backtest_common.urlopen"
            ) as urlopen,
            patch("backtest_common.pd.read_html", return_value=[table]),
        ):
            urlopen.return_value.read.return_value = b"<html></html>"
            symbols = backtest_common.get_constituents("nasdaq100")

        self.assertEqual(
            backtest_common.INDEXES["nasdaq100"]["constituents_url"],
            "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies",
        )
        self.assertEqual(symbols, ["AAPL", "BRK-B"])

    def test_russell2000_reads_current_symbols(self):
        table = pd.DataFrame(
            {
                "Symbol": ["ABC", "XYZ"],
                "Company Name": ["Alpha", "Example"],
            }
        )
        with (
            patch("backtest_common.urlopen") as urlopen,
            patch("backtest_common.pd.read_html", return_value=[table]),
        ):
            urlopen.return_value.read.return_value = b"<html></html>"
            symbols = backtest_common.get_constituents("russell2000")

        self.assertEqual(symbols, ["ABC", "XYZ"])
        self.assertEqual(
            backtest_common.INDEXES["russell2000"]["benchmark_ticker"], "^RUT"
        )

    def test_russell1000_reads_current_symbols(self):
        table = pd.DataFrame(
            {
                "Symbol": ["AAA", "BBB"],
                "Company": ["Alpha", "Beta"],
            }
        )
        with (
            patch("backtest_common.urlopen") as urlopen,
            patch("backtest_common.pd.read_html", return_value=[table]),
        ):
            urlopen.return_value.read.return_value = b"<html></html>"
            symbols = backtest_common.get_constituents("russell1000")

        self.assertEqual(symbols, ["AAA", "BBB"])
        self.assertEqual(
            backtest_common.INDEXES["russell1000"]["benchmark_ticker"], "^RUI"
        )
        self.assertEqual(
            backtest_common.INDEXES["russell1000"]["constituents_url"],
            "https://en.wikipedia.org/wiki/List_of_Russell_1000_companies",
        )

    def test_russell2000_reads_all_source_pages(self):
        first_page = pd.DataFrame(
            {"Symbol": [f"STK{number}" for number in range(500)]}
        )
        second_page = pd.DataFrame({"Symbol": ["LAST"]})
        with (
            patch("backtest_common.urlopen") as urlopen,
            patch(
                "backtest_common.pd.read_html",
                side_effect=[[first_page], [second_page]],
            ),
        ):
            urlopen.return_value.read.return_value = b"<html></html>"
            symbols = backtest_common.get_constituents("russell2000")

        self.assertEqual(len(symbols), 501)
        self.assertEqual(symbols[-1], "LAST")
        self.assertEqual(urlopen.call_count, 2)
        self.assertIn("?page=2", urlopen.call_args_list[1].args[0].full_url)

    def test_reuses_cache_within_month_and_refreshes_next_month(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            with patch.object(
                backtest_common, "get_constituents", return_value=["AAA"]
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
