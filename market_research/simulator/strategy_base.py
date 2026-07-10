# SPDX-License-Identifier: MIT
"""Strategy base class for paper-trading simulator.

Usage:
    from market_research.simulator.strategy_base import SimStrategyBase
    from market_research.simulator.models import BarData, Signal
"""

from __future__ import annotations

from typing import Dict, List

from market_research.simulator.models import BarData, Signal


class SimStrategyBase:
    """模拟盘策略基类 — Phase 1 版本（日线批量回放）

    子类需定义:
        author (str): 作者名
        parameters (list[dict]): 参数声明，自动生成前端配置表单
            [{"name": "period", "type": "int", "default": 20, "label": "周期"}]
        variables (list[str]): 运行中可展示到前端的变量名
            ["ma_fast", "ma_slow"]

    子类需实现:
        on_bars(date, bars_dict) -> None

    子类可用:
        buy(ts_code, price, volume)  — 发出买入信号
        sell(ts_code, price, volume) — 发出卖出信号
    """

    author: str = ""
    parameters: List[dict] = []
    variables: List[str] = []

    def __init__(self, strategy_id: str, setting: dict | None = None):
        """
        Args:
            strategy_id: 策略唯一 ID
            setting: 参数字典 {param_name: value, ...}
        """
        self.strategy_id = strategy_id
        self.inited = False
        self.trading = False

        # 组合状态
        self.capital = 1_000_000  # 初始资金（可配置）
        self.cash = 1_000_000
        self.positions: Dict[str, dict] = {}  # {ts_code: {"volume": x, "avg_price": y}}
        self.current_date: str = ""

        # 信号队列（引擎取走后清空）
        self._signals: List[Signal] = []

        # 从 setting 中提取声明过的参数（使用默认值兜底）
        for param in self.parameters:
            name = param.get("name", "")
            default = param.get("default", None)
            value = (setting or {}).get(name, default)
            setattr(self, name, value)

        # 如果 capital 已经在 parameters 中声明过，上面的循环已设置
        # 否则使用默认值
        if not hasattr(self, "capital"):
            self.capital = 1_000_000
        self.cash = self.capital

    # ── 子类可重写 ──────────────────────────────────────

    def on_init(self) -> None:
        """策略初始化：在第一次 on_bars 前调用"""
        self.inited = True

    def on_bars(self, date: str, bars: Dict[str, BarData]) -> None:
        """每日全市场数据回调

        Args:
            date: 交易日期 "20240102"
            bars: {ts_code: BarData, ...} 当日所有标的的日K线
        """
        raise NotImplementedError  # pragma: no cover

    # ── 信号接口 ────────────────────────────────────────

    def buy(self, ts_code: str, price: float, volume: float) -> None:
        """发出买入信号"""
        self._signals.append(Signal(ts_code, "buy", price, volume))

    def sell(self, ts_code: str, price: float, volume: float) -> None:
        """发出卖出信号"""
        self._signals.append(Signal(ts_code, "sell", price, volume))

    def get_signals(self) -> List[Signal]:
        """供引擎调用的信号提取 + 清空"""
        signals = self._signals[:]
        self._signals.clear()
        return signals

    # ── 工具 ────────────────────────────────────────────

    def get_variables(self) -> dict:
        """返回当前运行时变量值（展示用）"""
        return {v: getattr(self, v, None) for v in self.variables}

    def get_position(self, ts_code: str) -> dict:
        """获取某股票的持仓信息"""
        return self.positions.get(ts_code, {"volume": 0, "avg_price": 0.0})
