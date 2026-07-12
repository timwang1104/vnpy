"""
tbot IndustryService API 合同回归测试。

验证 get_timeseries() 和 get_anomalies() 的返回格式
与前端期望一致，不依赖 HTTP 服务，但需要 data/market_overview_a.db
已通过 SQLite→DuckDB 迁移。

用法:
    cd /path/to/project
    python3 -m pytest tests/test_industry_service.py -v
    # 或直接用 unittest:
    python3 -m unittest tests.test_industry_service -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# 确保 tbot 包可 import
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── 检查数据库 ──────────────────────────────────────────────────────
_DATA_DIR = PROJECT_ROOT / "data"
_OVERVIEW_DB = _DATA_DIR / "market_overview_a.db"
_HAS_DB = _OVERVIEW_DB.exists() and _OVERVIEW_DB.stat().st_size > 10000

if _HAS_DB:
    from tbot.engines.database.manager import DatabaseManager
    from tbot.engines.database.service import DatabaseService
    from tbot.business.industry.service import IndustryService

    mgr = DatabaseManager(str(_DATA_DIR))
    db_svc = DatabaseService(mgr)
    svc = IndustryService(db_svc)

    # 选择一个已知存在的行业用于测试
    TEST_CODE = "BK0450.DC"  # 航运港口
    ALL_MODES = ["raw", "pct", "close", "buy_md", "share", "zscore"]

    TIMESERIES_REQUIRED_KEYS = {"dates", "values", "close", "pct_change", "meta", "name"}
    TIMESERIES_META_KEYS = {"mode_label", "date_min", "date_max"}
    ANOMALIES_REQUIRED_KEYS = {"threshold", "limit", "total_anomalies", "total_industries", "anomalies"}
    ANOMALY_ITEM_KEYS = {"ts_code", "name", "latest_zscore", "share"}

    # 预取所有 mode 的返回结果（共享给所有 test）
    _TIMESERIES_RESULTS: dict[str, dict] = {}
    for _mode in ALL_MODES:
        try:
            _TIMESERIES_RESULTS[_mode] = svc.get_timeseries(TEST_CODE, _mode)
        except Exception as e:
            _TIMESERIES_RESULTS[_mode] = {"_error": str(e)}


@unittest.skipIf(not _HAS_DB, "需要 data/market_overview_a.db（运行 migration 后可用）")
class TestIndustryTimeseriesStructure(unittest.TestCase):
    """行业时序 get_timeseries 返回格式验证。"""

    def _check_timeseries(self, mode: str) -> dict:
        data = _TIMESERIES_RESULTS[mode]
        err = data.get("_error")
        self.assertIsNone(err, f"[{mode}] get_timeseries() 抛出异常: {err}")
        self.assertIsInstance(data, dict, f"[{mode}] 返回类型应为 dict，实际 {type(data).__name__}")

        # 顶层 key 验证
        for key in TIMESERIES_REQUIRED_KEYS:
            self.assertIn(key, data, f"[{mode}] 缺少顶层 key: {key}")

        # meta key 验证
        meta = data.get("meta", {})
        for key in TIMESERIES_META_KEYS:
            self.assertIn(key, meta, f"[{mode}] meta 缺少 key: {key}")

        # 类型验证
        self.assertIsInstance(data["dates"], list, f"[{mode}] dates 应为 list")
        self.assertIsInstance(data["values"], list, f"[{mode}] values 应为 list")
        self.assertIsInstance(data["close"], list, f"[{mode}] close 应为 list")
        self.assertIsInstance(data["pct_change"], list, f"[{mode}] pct_change 应为 list")
        self.assertIsInstance(data["meta"], dict, f"[{mode}] meta 应为 dict")
        self.assertIsInstance(data["name"], str, f"[{mode}] name 应为 str")

        # 长度一致性（有数据时）
        if data["dates"]:
            self.assertEqual(
                len(data["dates"]), len(data["values"]),
                f"[{mode}] dates 与 values 长度不一致",
            )

        return data

    def test_all_modes_return_dict(self) -> None:
        """所有 6 种 mode 都返回 dict 且无异常。"""
        for mode in ALL_MODES:
            with self.subTest(mode=mode):
                self._check_timeseries(mode)

    def test_raw_mode(self) -> None:
        """raw mode：buy_md_amount 数值型，close/pct 完整。"""
        data = self._check_timeseries("raw")

    def test_pct_mode(self) -> None:
        """pct mode：values 是涨跌幅。"""
        data = self._check_timeseries("pct")
        meta = data["meta"]
        self.assertEqual(meta["mode_label"], "涨跌幅（%）", "pct mode_label 不符")

    def test_close_mode(self) -> None:
        """close mode：values 是收盘价。"""
        data = self._check_timeseries("close")
        meta = data["meta"]
        self.assertEqual(meta["mode_label"], "收盘价", "close mode_label 不符")

    def test_buy_md_mode(self) -> None:
        """buy_md mode：values 是资金流净额。"""
        data = self._check_timeseries("buy_md")
        meta = data["meta"]
        self.assertEqual(meta["mode_label"], "资金流净额", "buy_md mode_label 不符")

    def test_share_mode(self) -> None:
        """share mode：values 是资金流占比，close/pct_change 不可为空。"""
        data = self._check_timeseries("share")
        meta = data["meta"]
        self.assertEqual(meta["mode_label"], "资金流占比", "share mode_label 不符")

        # 核心：close 和 pct_change 必须有数据！
        self.assertGreater(
            len(data["close"]), 0,
            "share mode close 为空 → ECharts 副线不显示",
        )
        self.assertGreater(
            len(data["pct_change"]), 0,
            "share mode pct_change 为空 → ECharts 副线不显示",
        )

        # 验证值类型
        non_none = [v for v in data["values"] if v is not None]
        self.assertGreater(len(non_none), 0, "share mode 应包含有效 share 值")

    def test_zscore_mode(self) -> None:
        """zscore mode：前 60 个值为 None，close/pct 完整。"""
        data = self._check_timeseries("zscore")
        meta = data["meta"]
        self.assertEqual(meta["mode_label"], "Z-score（60日滚动）", "zscore mode_label 不符")

        values = data["values"]
        # 前 60 个为 None（rolling window）
        none_first_60 = sum(1 for v in values[:60] if v is None)
        self.assertEqual(none_first_60, 60, "zscore 前 60 个值应为 None")

        # 60 之后应有非 None 值
        after_60 = values[60:]
        non_none = [v for v in after_60 if v is not None]
        self.assertGreater(len(non_none), 0, "zscore 第 61 个值起应有非 None 值")

        # close/pct_change 应有数据
        self.assertGreater(len(data["close"]), 0, "zscore mode close 不应为空")
        self.assertGreater(len(data["pct_change"]), 0, "zscore mode pct_change 不应为空")

    def test_invalid_mode_raises(self) -> None:
        """未知 mode 应抛出 ValueError。"""
        with self.assertRaises(ValueError):
            svc.get_timeseries(TEST_CODE, "nonexistent_mode")


@unittest.skipIf(not _HAS_DB, "需要 data/market_overview_a.db")
class TestIndustryAnomaliesStructure(unittest.TestCase):
    """行业异常检测 get_anomalies 返回格式验证。"""

    DEFAULTS = {"threshold": 2.0, "limit": 10}

    def _check_anomalies(self, threshold: float = 2.0, limit: int = 10):
        data = svc.get_anomalies(threshold, limit)
        self.assertIsInstance(data, dict, f"anomalies 返回类型应为 dict，实际 {type(data).__name__}")

        # 顶层 key 验证
        for key in ANOMALIES_REQUIRED_KEYS:
            self.assertIn(key, data, f"anomalies 缺少顶层 key: {key}")

        # 类型验证
        self.assertIsInstance(data["threshold"], (int, float))
        self.assertIsInstance(data["limit"], int)
        self.assertIsInstance(data["total_anomalies"], int)
        self.assertIsInstance(data["total_industries"], int)
        self.assertIsInstance(data["anomalies"], list)

        # threshold/limit 值正确
        self.assertEqual(data["threshold"], threshold)
        self.assertEqual(data["limit"], limit)

        # anomalies 条目 key 验证
        for i, item in enumerate(data["anomalies"]):
            missing = ANOMALY_ITEM_KEYS - set(item.keys())
            self.assertSetEqual(missing, set(), f"anomalies[{i}] 缺少: {missing}")

            # ts_code 和 name 应为非空字符串
            self.assertIsInstance(item["ts_code"], str)
            self.assertIsInstance(item["name"], str)
            self.assertTrue(len(item["ts_code"]) > 0)
            self.assertTrue(len(item["name"]) > 0)

            # latest_zscore 应为数值
            self.assertIsInstance(item["latest_zscore"], (int, float))
            self.assertIsInstance(item["share"], (int, float))

        # total_anomalies ≥ len(anomalies) （因为 limit 截断）
        self.assertGreaterEqual(data["total_anomalies"], len(data["anomalies"]))

        return data

    def test_default_threshold(self) -> None:
        """默认 threshold=2.0, limit=10。"""
        self._check_anomalies(2.0, 10)

    def test_custom_threshold(self) -> None:
        """不同 threshold 均返回正确格式。"""
        for t in (1.5, 2.5, 3.0):
            with self.subTest(threshold=t):
                self._check_anomalies(t, 10)

    def test_limit_zero(self) -> None:
        """limit=1 只返回 1 条。"""
        data = self._check_anomalies(2.0, 1)
        self.assertLessEqual(len(data["anomalies"]), 1)

    def test_total_industries_positive(self) -> None:
        """total_industries 应 > 0。"""
        data = self._check_anomalies(2.0, 10)
        self.assertGreater(data["total_industries"], 0, "total_industries 应 > 0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
