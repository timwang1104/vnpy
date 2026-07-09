# SPDX-License-Identifier: MIT
"""Test level 1: pure function exact-value assertions.

All tests use synthetic inputs with hand-computed known answers.
No database dependency.
"""
from __future__ import annotations

import unittest

from market_research.compute._stats import (
    daily_share,
    fwd_returns,
    ic_series,
    quintile_perf,
    rank_list,
    smoothed_share,
    spearman,
    total_ic,
)


class TestRankList(unittest.TestCase):
    """rank_list — O(n log n) average rank with tie handling."""

    def test_no_ties(self) -> None:
        """[3, 1, 2] -> 0-based average ranks [2.0, 0.0, 1.0]"""
        result = rank_list([3.0, 1.0, 2.0])
        self.assertEqual(result, [2.0, 0.0, 1.0])

    def test_with_ties(self) -> None:
        """[5, 3, 3, 1] -> ranks [3.0, 1.5, 1.5, 0.0]"""
        result = rank_list([5.0, 3.0, 3.0, 1.0])
        self.assertEqual(result, [3.0, 1.5, 1.5, 0.0])

    def test_all_tied(self) -> None:
        """[7, 7, 7] -> ranks [1.0, 1.0, 1.0]"""
        result = rank_list([7.0, 7.0, 7.0])
        self.assertEqual(result, [1.0, 1.0, 1.0])

    def test_single_element(self) -> None:
        """[42] -> rank [0.0]"""
        result = rank_list([42.0])
        self.assertEqual(result, [0.0])


class TestSpearman(unittest.TestCase):
    """spearman — Spearman rank correlation."""

    def test_perfect_positive(self) -> None:
        """x=[1,2,3], y=[2,4,6] -> Spearman = 1.0"""
        result = spearman([1.0, 2.0, 3.0], [2.0, 4.0, 6.0])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 1.0, places=6)  # type: ignore[arg-type]

    def test_perfect_negative(self) -> None:
        """x=[1,2,3], y=[6,4,2] -> Spearman = -1.0"""
        result = spearman([1.0, 2.0, 3.0], [6.0, 4.0, 2.0])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, -1.0, places=6)  # type: ignore[arg-type]

    def test_too_few(self) -> None:
        """n < 3 -> None"""
        self.assertIsNone(spearman([1.0, 2.0], [3.0, 4.0]))

    def test_zero_variance(self) -> None:
        """all xs equal -> None (sxx = 0)"""
        self.assertIsNone(spearman([5.0, 5.0, 5.0], [1.0, 2.0, 3.0]))

    def test_known_spearman(self) -> None:
        """x=[1,3,5,7,9], y=[2,4,1,8,6] -> hand-computed Spearman ~ 0.5

        ranks_x = [0.0, 1.0, 2.0, 3.0, 4.0]
        ranks_y: [2,4,1,8,6] -> sort: [1,2,4,6,8]
        ranks_y = [1.0, 2.0, 0.0, 4.0, 3.0]
        mx = 2, my = 2
        sxx = (0-2)^2+(1-2)^2+(2-2)^2+(3-2)^2+(4-2)^2 = 4+1+0+1+4 = 10
        syy = (1-2)^2+(2-2)^2+(0-2)^2+(4-2)^2+(3-2)^2 = 1+0+4+4+1 = 10
        sxy = (0-2)(1-2)+(1-2)(2-2)+(2-2)(0-2)+(3-2)(4-2)+(4-2)(3-2)
            = (-2)(-1)+(-1)(0)+(0)(-2)+(1)(2)+(2)(1)
            = 2 + 0 + 0 + 2 + 2 = 6
        r = 6 / (sqrt(10)*sqrt(10)) = 6/10 = 0.6
        """
        result = spearman([1.0, 3.0, 5.0, 7.0, 9.0], [2.0, 4.0, 1.0, 8.0, 6.0])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 0.6, places=6)  # type: ignore[arg-type]


class TestDailyShare(unittest.TestCase):
    """daily_share — single-day share matrix."""

    def test_basic(self) -> None:
        """3 industries with md values [-100, 300, 50] -> share = [-100/450, 300/450, 50/450]"""
        by_date = {
            "20260707": [
                ("A", -100.0, 1.0, 10.0, "IndA"),
                ("B", 300.0, 2.0, 20.0, "IndB"),
                ("C", 50.0, 0.5, 15.0, "IndC"),
            ]
        }
        result = daily_share(by_date, "20260707")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["A"], -100.0 / 450.0, places=6)  # type: ignore[index]
        self.assertAlmostEqual(result["B"], 300.0 / 450.0, places=6)  # type: ignore[index]
        self.assertAlmostEqual(result["C"], 50.0 / 450.0, places=6)  # type: ignore[index]

    def test_zero_sum(self) -> None:
        """all md zero -> None"""
        by_date = {"20260707": [("A", 0.0, 0.0, 10.0, "IndA")]}
        self.assertIsNone(daily_share(by_date, "20260707"))


class TestSmoothedShare(unittest.TestCase):
    """smoothed_share — k-day rolling share."""

    def test_k1_equals_daily(self) -> None:
        """k=1 should equal daily_share"""
        by_date = {
            "20260707": [("A", 100.0, 0.0, 10.0, "IndA")],
            "20260708": [("A", 200.0, 0.0, 10.0, "IndA")],
        }
        dates = ["20260707", "20260708"]
        result = smoothed_share(by_date, dates, di=0, k=1)
        direct = daily_share(by_date, "20260707")
        self.assertEqual(result, direct)

    def test_k2_smoothed(self) -> None:
        """k=2 at di=1: sum md over dates[0] and dates[1]"""
        by_date = {
            "20260707": [("A", 100.0, 0.0, 10.0, "IndA"), ("B", 50.0, 0.0, 20.0, "IndB")],
            "20260708": [("A", 200.0, 0.0, 10.0, "IndA"), ("B", -30.0, 0.0, 20.0, "IndB")],
        }
        dates = ["20260707", "20260708"]
        result = smoothed_share(by_date, dates, di=1, k=2)
        # agg: A=300, B=20; sum_abs=320
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["A"], 300.0 / 320.0, places=6)  # type: ignore[index]
        self.assertAlmostEqual(result["B"], 20.0 / 320.0, places=6)  # type: ignore[index]

    def test_early_date_no_lag(self) -> None:
        """at di=0, k=3 only has 1 actual day — no crash"""
        by_date = {"20260707": [("A", 100.0, 0.0, 10.0, "IndA")]}
        dates = ["20260707"]
        result = smoothed_share(by_date, dates, di=0, k=3)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["A"], 1.0, places=6)  # type: ignore[index]


class TestFwdReturns(unittest.TestCase):
    """fwd_returns — forward h-day return."""

    def test_basic(self) -> None:
        """code_close [10, 11, 12], date_idx maps 'd0'->0, h=1 at di=0 -> 10%"""
        code_close = {"A": [10.0, 11.0, 12.0]}
        date_idx = {"A": {"20260707": 0, "20260708": 1, "20260709": 2}}
        dates = ["20260707", "20260708", "20260709"]
        result = fwd_returns(code_close, date_idx, dates, "A", 0, 1)
        # (11/10 - 1) * 100 = 10%
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 10.0, places=6)  # type: ignore[arg-type]

    def test_not_enough_data(self) -> None:
        """h exceeds available data -> None"""
        code_close = {"A": [10.0]}
        date_idx = {"A": {"20260707": 0}}
        dates = ["20260707"]
        self.assertIsNone(fwd_returns(code_close, date_idx, dates, "A", 0, 5))


class TestIcSeries(unittest.TestCase):
    """ic_series — sampled IC at every `window` interval."""

    def test_simple_loop(self) -> None:
        """Minimal scenario: 1 industry, only 1 sample date, k=1, h=1, window=1."""
        by_date = {
            "20260707": [("A", 50.0, 0.0, 10.0, "IndA")],
            "20260708": [("A", 50.0, 1.0, 11.0, "IndA")],
        }
        dates = ["20260707", "20260708"]
        code_close = {"A": [10.0, 11.0]}
        date_idx = {"A": {"20260707": 0, "20260708": 1}}
        result = ic_series(by_date, dates, code_close, date_idx, k=1, h=1, window=1)
        # Only one date (di=0) can be sampled:
        #   share at di=0: {A: 1.0}; fwd return: (11/10-1)*100=10%
        #   single pair -> spearman returns None (< 3)
        self.assertEqual(result, [])  # Spearman needs n >= 3, so empty


class TestQuintilePerf(unittest.TestCase):
    """quintile_perf — N-group excess returns."""

    def test_2_groups_skipped(self) -> None:
        """With 2 industries and 2 groups, n < n_groups*2 per sample -> returns [None, None]"""
        by_date = {
            "20260707": [("A", 100.0, 0.0, 10.0, "IndA"), ("B", 50.0, 0.0, 20.0, "IndB")],
            "20260708": [("A", 101.0, 1.0, 11.0, "IndA"), ("B", 51.0, 0.5, 21.0, "IndB")],
        }
        dates = ["20260707", "20260708"]
        code_close = {"A": [10.0, 11.0], "B": [20.0, 21.0]}
        date_idx = {
            "A": {"20260707": 0, "20260708": 1},
            "B": {"20260707": 0, "20260708": 1},
        }
        result = quintile_perf(by_date, dates, code_close, date_idx, k=1, h=1, n_groups=2)
        # Each sample has n=2 < 4 -> skipped -> all groups get None
        self.assertEqual(result, [None, None])


class TestTotalIc(unittest.TestCase):
    """total_ic — pooled Spearman over all observations."""

    def test_simple(self) -> None:
        """Minimal pool."""
        by_date = {
            "20260707": [("A", 100.0, 0.0, 10.0, "IndA"), ("B", 50.0, 0.0, 20.0, "IndB")],
            "20260708": [("A", 101.0, 1.0, 11.0, "IndA"), ("B", 51.0, 0.5, 21.0, "IndB")],
        }
        dates = ["20260707", "20260708"]
        code_close = {"A": [10.0, 11.0], "B": [20.0, 21.0]}
        date_idx = {
            "A": {"20260707": 0, "20260708": 1},
            "B": {"20260707": 0, "20260708": 1},
        }
        result = total_ic(by_date, dates, code_close, date_idx, k=1, h=1)
        # Only 2 pairs -> n < 3 -> None
        self.assertIsNone(result)
