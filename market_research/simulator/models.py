# SPDX-License-Identifier: MIT
"""Data models for the simulator module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class BarData:
    """日线 K 线数据"""

    ts_code: str
    trade_date: str
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    amount: float = 0.0


@dataclass
class Signal:
    """策略发出的交易信号"""

    ts_code: str
    action: str  # "buy" or "sell"
    price: float
    volume: float  # 股数


@dataclass
class EquityPoint:
    """单日权益快照"""

    trade_date: str
    equity: float
    cash: float
    market_value: float


@dataclass
class PositionRecord:
    """持仓记录"""

    ts_code: str
    volume: float
    avg_price: float
    market_value: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0


@dataclass
class TradeRecord:
    """单笔成交记录"""

    ts_code: str
    direction: str  # "buy" or "sell"
    price: float
    volume: float
    amount: float = 0.0
    pnl: float = 0.0
    trade_date: str = ""
    comment: str = ""
