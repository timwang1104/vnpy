"""
vnpy_tushare datafeed 单元测试。

不依赖真实网络与真实 token：用 unittest.mock 桩掉 pro 实例，
断言字段映射、时间换算、token 读取等纯逻辑路径。
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from unittest import mock

import pandas as pd

# 让 examples/ 之外的 import 能找到 vnpy_tushare（开发环境无需安装即可跑）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestTushareDataHelpers(unittest.TestCase):
    """测试 tushare_data.py 的纯函数。"""

    def test_exchange_suffix_map(self) -> None:
        from vnpy.trader.constant import Exchange
        from vnpy_tushare import exchange_to_suffix

        self.assertEqual(exchange_to_suffix(Exchange.SSE), ".SH")
        self.assertEqual(exchange_to_suffix(Exchange.SZSE), ".SZ")
        self.assertEqual(exchange_to_suffix(Exchange.BSE), ".BJ")
        self.assertEqual(exchange_to_suffix(Exchange.CFFEX), ".CFE")
        self.assertEqual(exchange_to_suffix(Exchange.SHFE), ".SHF")
        self.assertEqual(exchange_to_suffix(Exchange.DCE), ".DCE")

    def test_exchange_suffix_unknown_returns_none(self) -> None:
        from vnpy.trader.constant import Exchange
        from vnpy_tushare import exchange_to_suffix

        # NYSE 不在映射表内
        self.assertIsNone(exchange_to_suffix(Exchange.NYSE))

    def test_interval_freq_map(self) -> None:
        from vnpy.trader.constant import Interval
        from vnpy_tushare import interval_to_freq

        self.assertEqual(interval_to_freq(Interval.DAILY), "D")
        self.assertEqual(interval_to_freq(Interval.WEEKLY), "W")
        self.assertEqual(interval_to_freq(Interval.MINUTE), "min")
        self.assertEqual(interval_to_freq(Interval.HOUR), "60min")
        # TICK 不在 pro_bar 映射内
        self.assertIsNone(interval_to_freq(Interval.TICK))

    def test_to_yyyymmdd(self) -> None:
        from vnpy_tushare import to_yyyymmdd

        self.assertEqual(to_yyyymmdd(datetime(2026, 1, 1)), "20260101")
        self.assertEqual(to_yyyymmdd(datetime(2024, 12, 31)), "20241231")

    def test_load_token_script_raises_when_empty(self) -> None:
        from vnpy_tushare import load_token_script

        # 让环境变量与 SETTINGS 都为空
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("vnpy_tushare.tushare_data.SETTINGS", {"datafeed.password": ""}):
            with self.assertRaises(RuntimeError) as ctx:
                load_token_script()
            self.assertIn("TUSHARE_API_KEY", str(ctx.exception))

    def test_load_token_script_prefers_env(self) -> None:
        from vnpy_tushare import load_token_script

        with mock.patch.dict(os.environ, {"TUSHARE_API_KEY": "env_token"}, clear=True), \
                mock.patch("vnpy_tushare.tushare_data.SETTINGS", {"datafeed.password": "settings_token"}):
            self.assertEqual(load_token_script(), "env_token")

    def test_load_token_datafeed_prefers_settings(self) -> None:
        from vnpy_tushare import load_token_datafeed

        with mock.patch.dict(os.environ, {"TUSHARE_API_KEY": "env_token"}, clear=True), \
                mock.patch("vnpy_tushare.tushare_data.SETTINGS", {"datafeed.password": "settings_token"}):
            self.assertEqual(load_token_datafeed(), "settings_token")


class TestQueryBarHistory(unittest.TestCase):
    """用桩 pro 测试 query_bar_history 的转换逻辑。"""

    def test_daily_returns_bars(self) -> None:
        from vnpy.trader.constant import Exchange, Interval
        from vnpy.trader.object import HistoryRequest
        from vnpy_tushare.datafeed import TuShareDatafeed

        daily_df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "20240102",
                 "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
                 "vol": 100.0, "amount": 1000.0, "oi": 0.0},
                {"ts_code": "000001.SZ", "trade_date": "20240103",
                 "open": 10.2, "high": 10.6, "low": 10.1, "close": 10.4,
                 "vol": 80.0, "amount": 800.0, "oi": 0.0},
            ]
        )

        stub_pro = mock.Mock()
        stub_pro.stock_basic.return_value = pd.DataFrame([{"ts_code": "000001.SZ"}])
        stub_pro.daily.return_value = daily_df

        with mock.patch("vnpy_tushare.datafeed.create_pro", return_value=stub_pro), \
                mock.patch("vnpy_tushare.datafeed.load_token_datafeed", return_value="fake_token"), \
                mock.patch("vnpy_tushare.datafeed.load_proxy_url", return_value="https://fake"):
            dfd = TuShareDatafeed()
            self.assertTrue(dfd.init())

            req = HistoryRequest(
                symbol="000001",
                exchange=Exchange.SZSE,
                start=datetime(2024, 1, 1),
                end=datetime(2024, 1, 5),
                interval=Interval.DAILY,
            )
            bars = dfd.query_bar_history(req)

        self.assertEqual(len(bars), 2)
        bar = bars[0]
        self.assertEqual(bar.symbol, "000001")
        self.assertEqual(bar.exchange, Exchange.SZSE)
        self.assertEqual(bar.interval, Interval.DAILY)
        self.assertEqual(bar.open_price, 10.0)
        self.assertEqual(bar.close_price, 10.2)
        self.assertEqual(bar.volume, 100.0)
        # 调用 pro.daily 时 ts_code 应拼接后缀
        stub_pro.daily.assert_called_once()
        _, kwargs = stub_pro.daily.call_args
        self.assertEqual(kwargs["ts_code"], "000001.SZ")
        self.assertEqual(kwargs["start_date"], "20240101")
        self.assertEqual(kwargs["end_date"], "20240105")

    def test_unsupported_exchange_returns_empty(self) -> None:
        from vnpy.trader.constant import Exchange, Interval
        from vnpy.trader.object import HistoryRequest
        from vnpy_tushare.datafeed import TuShareDatafeed

        stub_pro = mock.Mock()
        stub_pro.stock_basic.return_value = pd.DataFrame([{"ts_code": "x"}])
        with mock.patch("vnpy_tushare.datafeed.create_pro", return_value=stub_pro), \
                mock.patch("vnpy_tushare.datafeed.load_token_datafeed", return_value="fake_token"), \
                mock.patch("vnpy_tushare.datafeed.load_proxy_url", return_value="https://fake"):
            dfd = TuShareDatafeed()
            dfd.init()

            req = HistoryRequest(
                symbol="AAPL",
                exchange=Exchange.NASDAQ,
                start=datetime(2024, 1, 1),
                end=datetime(2024, 1, 5),
                interval=Interval.DAILY,
            )
            bars = dfd.query_bar_history(req)

        self.assertEqual(bars, [])
        stub_pro.daily.assert_not_called()

    def test_init_fails_without_token(self) -> None:
        from vnpy_tushare.datafeed import TuShareDatafeed

        msgs: list[str] = []
        with mock.patch("vnpy_tushare.datafeed.load_token_datafeed",
                        side_effect=RuntimeError("no token")):
            dfd = TuShareDatafeed()
            ok = dfd.init(output=msgs.append)

        self.assertFalse(ok)
        self.assertFalse(dfd.inited)
        self.assertTrue(any("no token" in m for m in msgs))


if __name__ == "__main__":
    unittest.main()