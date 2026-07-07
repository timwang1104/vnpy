"""
TuShare datafeed for VeighNa.

实现 BaseDatafeed 三个钩子，通过代理（tt.xiaodefa.cn）走 15000 积分高权限 key
抓取行情/财务/实时数据。供 get_datafeed() 反射加载：模块须导出 `Datafeed`。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import pandas as pd

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.datafeed import BaseDatafeed
from vnpy.trader.object import BarData, HistoryRequest, TickData
from vnpy.trader.locale import _

from .tushare_data import (
    create_pro,
    exchange_to_suffix,
    interval_to_freq,
    load_proxy_url,
    load_token_datafeed,
    to_yyyymmdd,
)


class TuShareDatafeed(BaseDatafeed):
    """通过 tushare pro + 代理抓取行情的 VeighNa 数据服务。"""

    def __init__(self) -> None:
        """"""
        self.pro: object | None = None
        self.inited: bool = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def init(self, output: Callable = print) -> bool:
        """"""
        if self.inited:
            return True

        try:
            token: str = load_token_datafeed()
            proxy_url: str = load_proxy_url()
            self.pro = create_pro(token, proxy_url)
        except Exception as e:
            output(_("tushare 初始化失败：{}").format(e))
            return False

        # 连通性探活
        try:
            # noinspection PyUnresolvedReferences
            self.pro.stock_basic(limit=1)
        except Exception as e:
            output(_("tushare 探活失败（stock_basic）：{}").format(e))
            self.pro = None
            return False

        self.inited = True
        return True

    # ------------------------------------------------------------------
    # K线
    # ------------------------------------------------------------------
    def query_bar_history(self, req: HistoryRequest, output: Callable = print) -> list[BarData]:
        """"""
        if not self.inited and not self.init(output):
            return []

        suffix: str | None = exchange_to_suffix(req.exchange)
        if suffix is None:
            output(_("暂不支持的交易所：{}").format(req.exchange))
            return []

        ts_code: str = f"{req.symbol}{suffix}"
        start: str = to_yyyymmdd(req.start)
        end: str = to_yyyymmdd(req.end) if req.end else to_yyyymmdd(datetime.now())

        try:
            df: pd.DataFrame = self._query_bars(ts_code, req.interval, start, end)
        except Exception as e:
            output(_("查询K线数据失败：{}").format(e))
            return []

        if df is None or df.empty:
            return []

        bars: list[BarData] = []
        # trade_date 列：'YYYYMMDD'；分钟/小时/周线另有 trade_time 列
        for idx, row in df.iterrows():  # noqa: F841 — idx 仅为 iterrows 占位
            trade_dt = self._parse_bar_datetime(row, req.interval)
            bar = BarData(
                symbol=req.symbol,
                exchange=req.exchange,
                datetime=trade_dt,
                interval=req.interval,
                volume=float(row.get("vol", 0) or 0),
                turnover=float(row.get("amount", 0) or 0),
                open_interest=float(row.get("oi", 0) or 0),
                open_price=float(row["open"]),
                high_price=float(row["high"]),
                low_price=float(row["low"]),
                close_price=float(row["close"]),
                # 网关名称仅用于标识数据来源，不参与交易撮合
                gateway_name="TUSHARE",
            )
            bars.append(bar)
        return bars

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------
    def query_tick_history(self, req: HistoryRequest, output: Callable = print) -> list[TickData]:
        """"""
        if not self.inited and not self.init(output):
            return []

        suffix: str | None = exchange_to_suffix(req.exchange)
        if suffix is None:
            output(_("暂不支持的交易所：{}").format(req.exchange))
            return []

        # 15000 积分下 tushare tick 接口能力有限，此处按通用 stk_mins / tick 接口尝试
        output(_("tushare tick 历史接口当前未在 datafeed 中实现，请使用 examples/tushare_fetch/ 抓取后落库"))
        return []

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _query_bars(
        self,
        ts_code: str,
        interval: Interval,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """"""
        assert self.pro is not None

        if interval == Interval.DAILY:
            # 日线用 pro.daily（股票）。期货可后续按交易所分流到 fut_daily
            return self.pro.daily(ts_code=ts_code, start_date=start, end_date=end)

        # 分钟 / 小时 / 周线走模块级 pro_bar，必须传 api=pro 走代理
        import tushare as ts  # 延迟导入：仅在需要 pro_bar 时加载可选依赖

        freq: str | None = interval_to_freq(interval)
        if freq is None:
            raise ValueError(f"不支持的 interval: {interval}")
        return ts.pro_bar(
            ts_code=ts_code,
            api=self.pro,
            start_date=start,
            end_date=end,
            freq=freq,
            adj=None,
        )

    @staticmethod
    def _parse_bar_datetime(row: pd.Series, interval: Interval) -> datetime:
        """
        把 tushare 行解析为带时区的 datetime。
        日线用 trade_date('YYYYMMDD')；分钟/小时线用 trade_time。结果带上 Asia/Shanghai 时区。
        """
        # 分钟/小时线含 trade_time 列
        trade_time = row.get("trade_time")
        if isinstance(trade_time, pd.Timestamp):
            return trade_time.to_pydatetime().astimezone()

        # 日线：只含 trade_date
        trade_date = row["trade_date"]
        dt = datetime.strptime(str(trade_date), "%Y%m%d")
        # tzlocal 提示本机时区；这里依赖 BarData 侧 DB 时区配置，统一带本地时区
        return dt.astimezone()