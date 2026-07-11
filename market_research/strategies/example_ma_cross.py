# SPDX-License-Identifier: MIT
"""双均线交叉策略 - 示例

金叉买入，死叉卖出。持仓达到上限后不再买入。

Parameters:
    fast_period: 短期均线周期 (默认 5)
    slow_period: 长期均线周期 (默认 20)
    max_positions: 最大持仓股票数 (默认 10)
    capital: 初始资金 (默认 1000000)
"""

from __future__ import annotations

from collections import defaultdict

from market_research.simulator.models import BarData
from market_research.simulator.strategy_base import SimStrategyBase


class MaCrossStrategy(SimStrategyBase):
    """双均线交叉策略"""

    author = "timwang"

    parameters = [
        {
            "name": "fast_period",
            "type": "int",
            "default": 5,
            "label": "短周期均线",
        },
        {
            "name": "slow_period",
            "type": "int",
            "default": 20,
            "label": "长周期均线",
        },
        {
            "name": "max_positions",
            "type": "int",
            "default": 10,
            "label": "最大持仓数",
        },
        {
            "name": "capital",
            "type": "float",
            "default": 1_000_000,
            "label": "初始资金",
        },
    ]

    variables = ["fast_period", "slow_period", "max_positions", "signal_counts"]

    def __init__(self, strategy_id: str, setting: dict | None = None):
        super().__init__(strategy_id, setting)

        # 缓存每只股票的历史收盘价
        self._history: dict[str, list[float]] = defaultdict(list)

        # 信号计数（展示用）
        self.signal_counts = {"buy": 0, "sell": 0}

    def on_bars(self, date: str, bars: dict[str, BarData]) -> None:
        """每日回调：计算均线，金叉买入，死叉卖出"""
        for ts_code, bar in bars.items():
            if bar.close <= 0:
                continue

            self._history[ts_code].append(bar.close)

            # 需要足够的历史数据
            if len(self._history[ts_code]) < self.slow_period:
                continue

            # 计算均线
            close_list = self._history[ts_code]
            fast_ma = sum(close_list[-self.fast_period:]) / self.fast_period
            slow_ma = sum(close_list[-self.slow_period:]) / self.slow_period

            # 判断金叉/死叉（基于前一天的均线）
            if len(close_list) > self.slow_period:
                prev_fast = sum(close_list[-(self.fast_period + 1):-1]) / self.fast_period
                prev_slow = sum(close_list[-(self.slow_period + 1):-1]) / self.slow_period

                pos = self.get_position(ts_code)

                # 金叉买入（prev_fast <= prev_slow, now fast > slow）
                if prev_fast <= prev_slow and fast_ma > slow_ma:
                    if pos["volume"] <= 0 and len(self.positions) < self.max_positions:
                        # 买入：使用 10% 的资金
                        buy_amount = self.cash * 0.1
                        volume = int(buy_amount / bar.close / 100) * 100
                        if volume >= 100:
                            self.buy(ts_code, bar.close, volume)
                            self.signal_counts["buy"] += 1

                # 死叉卖出
                elif prev_fast >= prev_slow and fast_ma < slow_ma:
                    if pos["volume"] > 0:
                        self.sell(ts_code, bar.close, pos["volume"])
                        self.signal_counts["sell"] += 1
