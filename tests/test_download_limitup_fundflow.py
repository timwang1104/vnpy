"""
download_limitup / download_fundflow 单元测试。

不依赖真实网络与真实 token：mock create_pro 返回桩 pro，
临时 sqlite 文件验证建表/upsert/断点续抓。
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

import pandas as pd

# 让 examples/tushare_fetch/ 内的 ts_db 可被 import
EXAMPLES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples", "tushare_fetch"))
sys.path.insert(0, EXAMPLES_DIR)

import ts_db  # noqa: E402
import download_limitup  # noqa: E402
import download_fundflow  # noqa: E402


def _stub_pro_limitup(df_limit: pd.DataFrame) -> mock.Mock:
    """limit_list_d 按请求 trade_date 动态返回（trade_date 列改为请求日）。"""
    pro = mock.Mock()
    pro.trade_cal.return_value = pd.DataFrame([{"cal_date": "20240102"}, {"cal_date": "20240103"}])

    def _limit_list_d(trade_date=None, **kw):
        df = df_limit.copy()
        df["trade_date"] = trade_date
        return df
    pro.limit_list_d.side_effect = _limit_list_d
    return pro


def _stub_pro_fundflow(df_ind, df_mkt, df_stock) -> mock.Mock:
    """资金流接口按请求 trade_date 动态返回（trade_date 列改为请求日）。"""
    pro = mock.Mock()
    pro.trade_cal.return_value = pd.DataFrame([{"cal_date": "20240102"}, {"cal_date": "20240103"}])

    def _ind(trade_date=None, **kw):
        df = df_ind.copy()
        df["trade_date"] = trade_date
        return df
    pro.moneyflow_ind_dc.side_effect = _ind

    def _mkt(trade_date=None, **kw):
        df = df_mkt.copy()
        df["trade_date"] = trade_date
        return df
    pro.moneyflow_mkt_dc.side_effect = _mkt

    pro.moneyflow.return_value = df_stock
    return pro


class TestTsDb(unittest.TestCase):
    """ts_db 纯逻辑：建表、upsert、断点续抓。"""

    def test_init_and_upsert_limit_up(self) -> None:
        conn = ts_db.connect(":memory:")
        ts_db.init_tables(conn)
        rows = [
            ["20240102", "000001.SZ", "银行", "平安银行", 10.0, 10.0, 1e8, None,
             1e9, 2e9, 5.0, None, 90000, None, 1, "1/1", 1, "U"],
            ["20240102", "000002.SZ", "房地产", "万科A", 9.0, 10.0, 2e8, None,
             3e9, 4e9, 6.0, None, 91000, None, 0, "1/1", 1, "U"],
        ]
        n = ts_db.upsert_rows(conn, "limit_up_pool", ts_db.LIMIT_UP_COLS, rows)
        self.assertEqual(n, 2)
        self.assertEqual(ts_db.count_rows(conn, "limit_up_pool"), 2)
        # 重复 upsert（OR IGNORE）不增行
        n2 = ts_db.upsert_rows(conn, "limit_up_pool", ts_db.LIMIT_UP_COLS, rows)
        self.assertEqual(n2, 0)
        self.assertEqual(ts_db.count_rows(conn, "limit_up_pool"), 2)

    def test_existing_dates(self) -> None:
        conn = ts_db.connect(":memory:")
        ts_db.init_tables(conn)
        rows = [["20240102", "000001.SZ"] + [None] * 16]
        ts_db.upsert_rows(conn, "limit_up_pool", ts_db.LIMIT_UP_COLS, rows)
        got = ts_db.existing_dates(conn, "limit_up_pool", "20240101", "20240105")
        self.assertEqual(got, {"20240102"})

    def test_existing_stock_codes(self) -> None:
        conn = ts_db.connect(":memory:")
        ts_db.init_tables(conn)
        rows = [
            ["20240102", "000001.SZ"] + [None] * 16,
            ["20240103", "000002.SZ"] + [None] * 16,
        ]
        ts_db.upsert_rows(conn, "limit_up_pool", ts_db.LIMIT_UP_COLS, rows)
        codes = ts_db.existing_stock_codes(conn, "20240101", "20240105")
        self.assertEqual(codes, ["000001.SZ", "000002.SZ"])

    def test_nan_to_none(self) -> None:
        conn = ts_db.connect(":memory:")
        ts_db.init_tables(conn)
        rows = [["20240102", "000001.SZ", "银行", "X", 10.0, 10.0, float("nan"), None,
                 1e9, 2e9, 5.0, None, 90000, None, 1, "1/1", 1, "U"]]
        ts_db.upsert_rows(conn, "limit_up_pool", ts_db.LIMIT_UP_COLS, rows)
        row = ts_db.dump_head(conn, "limit_up_pool", 1)[0]
        # amount 是 NaN -> 应存为 None
        idx = ts_db.LIMIT_UP_COLS.index("amount")
        self.assertIsNone(row[idx])


class TestDownloadLimitup(unittest.TestCase):
    """涨停池下载主流程（mock pro）。"""

    def _limit_df(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"trade_date": "20240102", "ts_code": "000001.SZ", "industry": "银行",
             "name": "平安银行", "close": 10.0, "pct_chg": 10.0, "amount": 1e8,
             "limit_amount": None, "float_mv": 1e9, "total_mv": 2e9, "turnover_ratio": 5.0,
             "fd_amount": None, "first_time": 90000, "last_time": None, "open_times": 1,
             "up_stat": "1/1", "limit_times": 1, "limit": "U"},
            {"trade_date": "20240102", "ts_code": "000002.SZ", "industry": "房地产",
             "name": "万科A", "close": 9.0, "pct_chg": 10.0, "amount": 2e8,
             "limit_amount": None, "float_mv": 3e9, "total_mv": 4e9, "turnover_ratio": 6.0,
             "fd_amount": None, "first_time": 91000, "last_time": None, "open_times": 0,
             "up_stat": "1/1", "limit_times": 1, "limit": "U"},
        ])

    def test_download_inserts_and_skips(self) -> None:
        import tempfile, os
        tmpdir = tempfile.mkdtemp()
        db = os.path.join(tmpdir, "t.db")
        conn = ts_db.connect(db)
        ts_db.init_tables(conn)
        pro = _stub_pro_limitup(self._limit_df())

        with mock.patch.object(download_limitup, "create_pro", return_value=pro), \
                mock.patch.object(download_limitup, "load_token_script", return_value="fake"), \
                mock.patch.object(download_limitup, "load_proxy_url", return_value="https://fake"), \
                mock.patch.object(download_limitup, "connect", return_value=conn), \
                mock.patch.object(download_limitup, "init_tables"):
            with mock.patch("sys.argv", ["x", "--start", "20240101", "--end", "20240105"]):
                rc = download_limitup.main()
                self.assertEqual(rc, 0)

        # main 会关闭 conn；另开连接断言。首跑 2 开市日 × 2 行 = 4 行
        conn2 = ts_db.connect(db)
        self.assertEqual(ts_db.count_rows(conn2, "limit_up_pool"), 4)
        # 第二次应全部跳过
        pro.limit_list_d.reset_mock()
        with mock.patch.object(download_limitup, "create_pro", return_value=pro), \
                mock.patch.object(download_limitup, "load_token_script", return_value="fake"), \
                mock.patch.object(download_limitup, "load_proxy_url", return_value="https://fake"), \
                mock.patch.object(download_limitup, "connect", return_value=conn2), \
                mock.patch.object(download_limitup, "init_tables"):
            with mock.patch("sys.argv", ["x", "--start", "20240101", "--end", "20240105"]):
                download_limitup.main()
        # 两个开市日都已存在，limit_list_d 不应再被调用
        pro.limit_list_d.assert_not_called()
        conn3 = ts_db.connect(db)
        self.assertEqual(ts_db.count_rows(conn3, "limit_up_pool"), 4)
        conn3.close()

    def test_no_token_exits_1(self) -> None:
        with mock.patch.object(download_limitup, "load_token_script",
                               side_effect=RuntimeError("no token")):
            with mock.patch("sys.argv", ["x", "--start", "20240101", "--end", "20240105"]):
                rc = download_limitup.main()
        self.assertEqual(rc, 1)


class TestDownloadFundflow(unittest.TestCase):
    """资金流下载主流程（mock pro）。"""

    def _ind_df(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"trade_date": "20240102", "content_type": "行业", "ts_code": "BK0475.DC",
             "name": "银行", "pct_change": 1.5, "close": 2794.0, "net_amount": 2e9,
             "net_amount_rate": 8.0, "buy_elg_amount": 1.7e9, "buy_elg_amount_rate": 6.9,
             "buy_lg_amount": 3e8, "buy_lg_amount_rate": 1.2, "buy_md_amount": -1e8,
             "buy_md_amount_rate": -0.4, "buy_sm_amount": 1e8, "buy_sm_amount_rate": 0.5,
             "buy_sm_amount_stock": "X", "rank": 1},
            {"trade_date": "20240102", "content_type": "行业", "ts_code": "BK0456.DC",
             "name": "家电行业", "pct_change": -0.4, "close": 100.0, "net_amount": 3e8,
             "net_amount_rate": 1.0, "buy_elg_amount": 2e8, "buy_elg_amount_rate": 0.7,
             "buy_lg_amount": 1e8, "buy_lg_amount_rate": 0.4, "buy_md_amount": -5e7,
             "buy_md_amount_rate": -0.2, "buy_sm_amount": 5e7, "buy_sm_amount_rate": 0.2,
             "buy_sm_amount_stock": "Y", "rank": 2},
        ])

    def _mkt_df(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "trade_date": "20240102", "close_sh": 2929.0, "pct_change_sh": -0.8,
            "close_sz": 9116.0, "pct_change_sz": -1.0, "net_amount": -3.2e10,
            "net_amount_rate": -4.4, "buy_elg_amount": -1.4e10, "buy_elg_amount_rate": -1.9,
            "buy_lg_amount": -1.8e10, "buy_lg_amount_rate": -2.5, "buy_md_amount": 2e9,
            "buy_md_amount_rate": 0.2, "buy_sm_amount": 3e10, "buy_sm_amount_rate": 4.1,
        }])

    def _stock_df(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "ts_code": "000001.SZ", "trade_date": "20240102",
            "buy_sm_vol": 1.0, "buy_sm_amount": 2.0, "sell_sm_vol": 3.0, "sell_sm_amount": 4.0,
            "buy_md_vol": 5.0, "buy_md_amount": 6.0, "sell_md_vol": 7.0, "sell_md_amount": 8.0,
            "buy_lg_vol": 9.0, "buy_lg_amount": 10.0, "sell_lg_vol": 11.0, "sell_lg_amount": 12.0,
            "buy_elg_vol": 13.0, "buy_elg_amount": 14.0, "sell_elg_vol": 15.0, "sell_elg_amount": 16.0,
            "net_mf_vol": 17.0, "net_mf_amount": 18.0,
        }])

    def test_fundflow_three_segments(self) -> None:
        import tempfile, os
        tmpdir = tempfile.mkdtemp()
        db = os.path.join(tmpdir, "t.db")
        conn = ts_db.connect(db)
        ts_db.init_tables(conn)
        # 先塞涨停池数据，供个股段取 ts_code
        lu = [["20240102", "000001.SZ"] + [None] * 16]
        ts_db.upsert_rows(conn, "limit_up_pool", ts_db.LIMIT_UP_COLS, lu)

        pro = _stub_pro_fundflow(self._ind_df(), self._mkt_df(), self._stock_df())
        with mock.patch.object(download_fundflow, "create_pro", return_value=pro), \
                mock.patch.object(download_fundflow, "load_token_script", return_value="fake"), \
                mock.patch.object(download_fundflow, "load_proxy_url", return_value="https://fake"), \
                mock.patch.object(download_fundflow, "connect", return_value=conn), \
                mock.patch.object(download_fundflow, "init_tables"):
            with mock.patch("sys.argv", ["x", "--start", "20240101", "--end", "20240105", "--sleep", "0"]):
                rc = download_fundflow.main()
                self.assertEqual(rc, 0)

        # main 关闭了 conn；另开连接断言
        conn2 = ts_db.connect(db)
        # 行业：2 个开市日 × 2 行 = 4 行
        self.assertEqual(ts_db.count_rows(conn2, "ind_fundflow"), 4)
        # 大盘：2 个开市日 × 1 行 = 2 行
        self.assertEqual(ts_db.count_rows(conn2, "mkt_fundflow"), 2)
        # 个股：1 只 × 1 行
        self.assertEqual(ts_db.count_rows(conn2, "stock_fundflow"), 1)
        conn2.close()

    def test_no_token_exits_1(self) -> None:
        with mock.patch.object(download_fundflow, "load_token_script",
                               side_effect=RuntimeError("no token")):
            with mock.patch("sys.argv", ["x", "--start", "20240101", "--end", "20240105"]):
                rc = download_fundflow.main()
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()