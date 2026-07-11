# SPDX-License-Identifier: MIT
"""Test level 2: compute functions with dynamic sqlite fixture (setUp/tearDown).

Each test case creates a temporary sqlite database, inserts known data,
calls compute functions, and asserts schema structure + key values.
No fixture files.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from market_research.compute.calendar import build_calendar
from market_research.compute.ind_fundflow import compute_ind_fundflow
from market_research.compute.limitup import compute_limitup, compute_limitup_history
from market_research.compute.overview import compute_overview


class TestComputeIndFundflow(unittest.TestCase):
    """compute_ind_fundflow with dynamic ind_fundflow + mkt_fundflow fixture."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = Path(self.tmp.name)
        self.tmp.close()
        self.con = sqlite3.connect(str(self.db_path))
        self._seed()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        self.con.close()
        self.db_path.unlink(missing_ok=True)

    def _seed(self) -> None:
        cur = self.con.cursor()
        # ind_fundflow — 4 industries, 3 dates
        cur.execute(
            "CREATE TABLE ind_fundflow ("
            "trade_date TEXT, content_type TEXT, ts_code TEXT, name TEXT, "
            "pct_change REAL, close REAL, net_amount REAL, buy_md_amount REAL"
            ")"
        )
        data = [
            # (trade_date, content_type, ts_code, name, pct_change, close, net_amount, buy_md_amount)
            ("20250901", "行业", "A", "行业A", 1.0, 10.0, 100.0, 100.0),
            ("20250901", "行业", "B", "行业B", 0.5, 20.0, -50.0, 50.0),
            ("20250901", "行业", "C", "行业C", -0.3, 15.0, 30.0, 30.0),
            ("20250901", "行业", "D", "行业D", 0.8, 25.0, 200.0, 200.0),
            ("20250902", "行业", "A", "行业A", 1.2, 11.0, 150.0, 150.0),
            ("20250902", "行业", "B", "行业B", 0.3, 21.0, -30.0, 30.0),
            ("20250902", "行业", "C", "行业C", -0.1, 16.0, 10.0, 10.0),
            ("20250902", "行业", "D", "行业D", -0.5, 24.0, -100.0, 0.0),
            ("20250903", "子行业", "C.C1", "子C1", 0.2, 17.0, 5.0, 5.0),  # post-cutover, excluded
        ]
        cur.executemany(
            "INSERT INTO ind_fundflow VALUES (?,?,?,?,?,?,?,?)", data
        )
        self.con.commit()

    def test_schema(self) -> None:
        result = compute_ind_fundflow(self.con, window=240)
        self.assertIn("meta", result)
        self.assertIn("kpi", result)
        self.assertIn("series", result)
        self.assertIn("tables", result)

        # meta
        self.assertEqual(result["meta"]["tab"], "industry")
        self.assertEqual(result["meta"]["window"], 240)

        # kpi
        kpi = result["kpi"]
        self.assertIn("n_industries", kpi)
        self.assertIn("last_date", kpi)
        self.assertIn("ic1_avg_5d", kpi)
        self.assertIn("ic3_avg_5d", kpi)

        # series
        series = result["series"]
        self.assertIn("share_heat", series)
        self.assertIn("ic_series", series)
        self.assertIn("quintile_perf", series)
        # whitelist: only 4 ind entries (excludes sub-industry)
        self.assertEqual(len(series["industries"]), 4)

        # tables
        tables = result["tables"]
        self.assertIn("last_ranking", tables)
        self.assertEqual(len(tables["last_ranking"]), 4)

    def test_whitelist_filter(self) -> None:
        """Sub-industry records with content_type='子行业' after cutover should be excluded."""
        result = compute_ind_fundflow(self.con, window=240)
        self.assertEqual(result["kpi"]["n_industries"], 4)
        self.assertEqual(result["kpi"]["whitelist_size"], 4)

    def test_window_truncation(self) -> None:
        """Small window should truncate dates."""
        result = compute_ind_fundflow(self.con, window=1)
        series = result["series"]
        self.assertLessEqual(len(series["dates"]), 1)


class TestComputeLimitup(unittest.TestCase):
    """compute_limitup with dynamic limit_up_pool fixture."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = Path(self.tmp.name)
        self.tmp.close()
        self.con = sqlite3.connect(str(self.db_path))
        self._seed()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        self.con.close()
        self.db_path.unlink(missing_ok=True)

    def _seed(self) -> None:
        cur = self.con.cursor()
        cur.execute(
            "CREATE TABLE limit_up_pool ("
            "trade_date TEXT, ts_code TEXT, industry TEXT, name TEXT, "
            "close REAL, pct_chg REAL, amount REAL, limit_amount REAL, "
            "float_mv REAL, total_mv REAL, turnover_ratio REAL, "
            "fd_amount REAL, first_time TEXT, last_time TEXT, "
            "open_times REAL, up_stat TEXT, limit_times REAL, "
            '"limit" TEXT'
            ")"
        )
        data = [
            # (trade_date, ts_code, industry, name, close, pct_chg, ..., limit_times, limit)
            ("20260707", "000001", "金融", "平安银行", 12.0, 10.0, 1e8, 1e7, 1e9, 5e9, 0.05,
             5e6, "09:30", "09:30", 1.0, "U", 3.0, "U"),
            ("20260707", "000002", "地产", "万科A", 8.0, 10.0, 5e7, 5e6, 5e8, 2e9, 0.03,
             2e6, "09:35", "14:00", 3.0, "U", 2.0, "U"),
            ("20260707", "000003", "金融", "招商银行", 40.0, -10.0, 2e8, 0, 1e10, 1e11, 0.01,
             0, "", "", 0, "D", 0, "D"),
            ("20260707", "000004", "消费", "茅台", 200.0, 10.0, 1e9, 1e8, 2e10, 3e10, 0.02,
             1e8, "09:25", "09:25", 1.0, "U", 5.0, "U"),
            ("20260707", "000005", "科技", "华为概念", 50.0, 10.0, 3e7, 3e6, 3e8, 1e9, 0.04,
             1e6, "10:00", "10:30", 2.0, "Z", 1.0, "Z"),
            ("20260708", "000001", "金融", "平安银行", 13.0, 8.0, 8e7, 1e7, 1e9, 5e9, 0.04,
             3e6, "09:30", "11:30", 1.0, "U", 4.0, "U"),
        ]
        cur.executemany(
            "INSERT INTO limit_up_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            data
        )
        self.con.commit()

    def test_limitup_schema(self) -> None:
        result = compute_limitup(self.con, "20260707")
        self.assertIn("meta", result)
        self.assertIn("kpi", result)
        self.assertIn("tables", result)

        kpi = result["kpi"]
        # 2 U, 1 Z, 1 D, 1 U with 5 limit_times
        self.assertEqual(kpi["limit_up_cnt"], 3)
        self.assertEqual(kpi["limit_break_cnt"], 1)
        self.assertEqual(kpi["limit_down_cnt"], 1)
        self.assertEqual(kpi["max_limit_times"], 5)

        tiers = result["tables"]["tiers"]
        self.assertEqual(len(tiers), 3)  # 3 distinct limit_times among Us: 5, 3, 2

        # highest tier: limit_times=5, 1 member
        self.assertEqual(tiers[0]["limit_times"], 5)
        self.assertEqual(tiers[0]["count"], 1)
        self.assertEqual(tiers[0]["members"][0]["name"], "茅台")

    def test_limitup_no_data(self) -> None:
        """No data for the given date -> empty KPI."""
        result = compute_limitup(self.con, "20260101")
        self.assertEqual(result["kpi"]["limit_up_cnt"], 0)
        self.assertEqual(result["kpi"]["max_limit_times"], 0)

    def test_limitup_history(self) -> None:
        results = list(compute_limitup_history(self.con))
        self.assertEqual(len(results), 2)  # 2 distinct dates
        dates = [r[0] for r in results]
        self.assertIn("20260707", dates)
        self.assertIn("20260708", dates)


class TestComputeOverview(unittest.TestCase):
    """compute_overview with dynamic mkt_fundflow + limit_up_pool fixture."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = Path(self.tmp.name)
        self.tmp.close()
        self.con = sqlite3.connect(str(self.db_path))
        self._seed()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        self.con.close()
        self.db_path.unlink(missing_ok=True)

    def _seed(self) -> None:
        cur = self.con.cursor()
        # mkt_fundflow
        cur.execute(
            "CREATE TABLE mkt_fundflow ("
            "trade_date TEXT, close_sh REAL, pct_change_sh REAL, "
            "close_sz REAL, pct_change_sz REAL, net_amount REAL, "
            "net_amount_rate REAL, buy_elg_amount REAL, buy_elg_amount_rate REAL, "
            "buy_lg_amount REAL, buy_lg_amount_rate REAL, "
            "buy_md_amount REAL, buy_md_amount_rate REAL, "
            "buy_sm_amount REAL, buy_sm_amount_rate REAL"
            ")"
        )
        cur.executemany(
            "INSERT INTO mkt_fundflow VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("20260707", 3200.0, 0.5, 10000.0, 0.8, -10e8, -0.02, -5e8, -0.01, -3e8, -0.005, -2e8, -0.003, -5e8, -0.012),
                ("20260706", 3180.0, -0.3, 9900.0, -0.5, 5e8, 0.01, 3e8, 0.005, 2e8, 0.003, 1e8, 0.002, 2e8, 0.005),
            ],
        )
        # limit_up_pool (minimal)
        cur.execute(
            "CREATE TABLE limit_up_pool ("
            "trade_date TEXT, ts_code TEXT, limit_times REAL, "
            '"limit" TEXT, amount REAL, name TEXT, industry TEXT, '
            "close REAL, pct_chg REAL, limit_amount REAL, "
            "float_mv REAL, total_mv REAL, turnover_ratio REAL, "
            "fd_amount REAL, first_time TEXT, last_time TEXT, "
            "open_times REAL, up_stat TEXT"
            ")"
        )
        cur.execute(
            "INSERT INTO limit_up_pool (trade_date, ts_code, limit_times, "
            '"limit", amount, name, industry) '
            "VALUES ('20260707','X',2.0,'U',1e6,'测试','科技')"
        )
        self.con.commit()

    def test_overview_schema(self) -> None:
        result = compute_overview(self.con, "20260707")
        self.assertIn("meta", result)
        self.assertIn("kpi", result)
        self.assertIn("series", result)

        kpi = result["kpi"]
        self.assertEqual(kpi["net_inflow"], -10e8)
        self.assertEqual(kpi["large_inflow"], -3e8)
        self.assertEqual(kpi["limit_up_cnt"], 1)
        self.assertEqual(kpi["max_limit_times"], 2)

        series = result["series"]
        self.assertIn("market_flow_mini", series)
        self.assertEqual(len(series["market_flow_mini"]), 2)


class TestBuildCalendar(unittest.TestCase):
    """build_calendar — requires exchange_calendars."""

    def test_simple(self) -> None:
        """Should return calendar with dates list and meta."""
        result = build_calendar("20260701", "20260710", ["20260701", "20260702", "20260707"])
        self.assertIn("meta", result)
        self.assertIn("dates", result)
        self.assertEqual(result["meta"]["exchange"], "XSHG")
        # At least 7 days (weekends excluded), mark has_data correctly
        for entry in result["dates"]:
            if entry["date"] in ("20260701", "20260702", "20260707"):
                self.assertTrue(entry["has_data"])
            elif entry["date"][:6] == "202607":
                pass  # non-trade days or days without data
