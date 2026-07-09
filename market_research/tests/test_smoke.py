# SPDX-License-Identifier: MIT
"""Test level 3: real db smoke test (skip if data/tushare.db not found).

Uses the actual data/tushare.db to verify build output structure and KPI ranges.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from market_research.builder import build

# 尝试定位真实 db
REPO_ROOT = Path(__file__).resolve().parents[2]  # market_research/tests → vnpy 仓库根
REAL_DB = REPO_ROOT / "data" / "tushare.db"


def _check_schema(data: dict, required_keys: list[str]) -> None:
    """Assert that a schema dict has the required top-level keys."""
    for k in required_keys:
        assert k in data, f"Missing required key: {k}"


@unittest.skipIf(not REAL_DB.exists(), f"真实数据库不存在: {REAL_DB}")
class TestSmoke(unittest.TestCase):
    """真实 db 冒烟测试 — 只断言产物结构 + KPI 合理范围。"""

    tmp_dir: ClassVar[Path]
    data_dir: ClassVar[Path]
    limitup_dir: ClassVar[Path]

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp_dir = Path(tempfile.mkdtemp(prefix="mr_smoke_"))
        build(str(REAL_DB), str(cls.tmp_dir), window=240)
        cls.data_dir = cls.tmp_dir / "data"
        cls.limitup_dir = cls.data_dir / "limitup"

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(str(cls.tmp_dir), ignore_errors=True)

    # ---------- 产物完整性 ----------

    def test_build_output_files(self) -> None:
        """Build 产物文件必须齐全。"""
        self.assertTrue((self.tmp_dir / "index.html").exists())
        self.assertTrue((self.tmp_dir / "static" / "app.js").exists())
        self.assertTrue((self.tmp_dir / "static" / "style.css").exists())
        self.assertTrue((self.data_dir / "industry.json").exists())
        self.assertTrue((self.data_dir / "limitup_main.json").exists())
        self.assertTrue((self.data_dir / "calendar.json").exists())

    def test_limitup_slices_exist(self) -> None:
        """历史日期分片目录非空。"""
        slices = list(self.limitup_dir.glob("*.json"))
        self.assertGreater(len(slices), 100)

    # ---------- schema 结构 ----------

    def test_industry_schema(self) -> None:
        with open(self.data_dir / "industry.json") as f:
            data = json.load(f)
        _check_schema(data, ["meta", "kpi", "series", "tables"])
        self.assertEqual(data["meta"]["tab"], "industry")
        self.assertIn("share_heat", data["series"])
        self.assertIn("ic_series", data["series"])
        self.assertIn("quintile_perf", data["series"])
        self.assertIn("last_ranking", data["tables"])
        self.assertGreater(len(data["series"]["industries"]), 50)  # ~75 行业
        self.assertGreater(len(data["series"]["dates"]), 100)

    def test_limitup_main_schema(self) -> None:
        with open(self.data_dir / "limitup_main.json") as f:
            data = json.load(f)
        _check_schema(data, ["meta", "dates", "by_date"])
        self.assertEqual(data["meta"]["tab"], "limitup")
        self.assertGreater(len(data["dates"]), 100)
        self.assertIn("overview", data)

    def test_limitup_slice_schema(self) -> None:
        slices = sorted(self.limitup_dir.glob("*.json"))
        self.assertGreater(len(slices), 0)
        with open(slices[0]) as f:
            day_data = json.load(f)
        _check_schema(day_data, ["meta", "kpi", "tables", "series"])

    def test_calendar_schema(self) -> None:
        with open(self.data_dir / "calendar.json") as f:
            data = json.load(f)
        _check_schema(data, ["meta", "dates"])
        self.assertEqual(data["meta"]["exchange"], "XSHG")
        self.assertGreater(data["meta"]["count"], 500)
        # some dates have data
        has_data = sum(1 for d in data["dates"] if d["has_data"])
        self.assertGreater(has_data, 100)

    # ---------- KPI 合理范围 ----------

    def test_industry_kpi_ranges(self) -> None:
        with open(self.data_dir / "industry.json") as f:
            data = json.load(f)
        kpi = data["kpi"]
        # IC
        if kpi["ic1_avg_5d"] is not None:
            self.assertGreater(kpi["ic1_avg_5d"], -0.1)
            self.assertLess(kpi["ic1_avg_5d"], 0.1)
        if kpi["ic3_avg_5d"] is not None:
            self.assertGreater(kpi["ic3_avg_5d"], -0.1)
            self.assertLess(kpi["ic3_avg_5d"], 0.1)
        # 行业数
        self.assertGreaterEqual(kpi["n_industries"], 50)
        self.assertLessEqual(kpi["n_industries"], 100)

    def test_limitup_kpi_ranges(self) -> None:
        with open(self.data_dir / "limitup_main.json") as f:
            data = json.load(f)
        # check a few dates have reasonable values
        sample_date = data["dates"][-1]
        day_data = data["by_date"][sample_date]
        kpi = day_data["kpi"]
        self.assertGreaterEqual(kpi["limit_up_cnt"], 0)
        self.assertLessEqual(kpi["limit_up_cnt"], 500)
        self.assertGreaterEqual(kpi["max_limit_times"], 0)
        self.assertLessEqual(kpi["max_limit_times"], 30)

    def test_overview_kpi_ranges(self) -> None:
        overview = self._load_overview()
        kpi = overview["kpi"]
        self.assertIsNotNone(kpi["net_inflow"])
        # net_inflow in reasonable range for A-share market
        if kpi["net_inflow"] is not None:
            self.assertGreater(kpi["net_inflow"], -1e11)  # -100B
            self.assertLess(kpi["net_inflow"], 1e11)
        # 涨停数
        self.assertGreaterEqual(kpi["limit_up_cnt"], 0)
        self.assertLessEqual(kpi["limit_up_cnt"], 500)

    def _load_overview(self) -> dict:
        with open(self.data_dir / "limitup_main.json") as f:
            return json.load(f).get("overview", {})  # type: ignore[no-any-return]
