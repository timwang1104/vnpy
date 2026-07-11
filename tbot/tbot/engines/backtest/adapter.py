# SPDX-License-Identifier: MIT
"""DuckDuckLab — DuckDB 驱动的回测数据中心。

替代 vnpy.alpha.lab.AlphaLab，所有 K 线数据直接从 DuckDB 分库读取。
兼容 vnpy.alpha.strategy.backtesting.BacktestingEngine 所依赖的接口。

用法::

    from tbot.engines.backtest.adapter import DuckDuckLab
    from tbot.engines.database.manager import DatabaseManager

    lab = DuckDuckLab("data")
    bars = lab.load_bar_data("000001.SZ", Interval.DAILY, start, end)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from vnpy.trader.constant import Interval
from vnpy.trader.object import BarData
from vnpy.trader.utility import extract_vt_symbol

from tbot.engines.database.manager import DatabaseManager

# ── 模块级日志 ────────────────────────────────────────

import logging

logger = logging.getLogger("tbot.backtest")


# ── 常量 ──────────────────────────────────────────────

DAILY_BARS_TABLE = "daily_bars"
CONTRACT_SETTINGS_KEY = "contract_settings"

# 默认合约参数（按 exchange 类型）
DEFAULT_LONG_RATE = 0.00025
DEFAULT_SHORT_RATE = 0.00025
DEFAULT_SIZE = 100
DEFAULT_PRICETICK = 0.01


# ── DuckDuckLab ───────────────────────────────────────

class DuckDuckLab:
    """DuckDB 版回测实验室。

    与 AlphaLab 保持接口兼容 (duck typing)：

    - load_bar_data(vt_symbol, interval, start, end) -> list[BarData]
    - load_contract_setttings() -> dict[str, dict]
    - add_contract_setting(vt_symbol, long_rate, short_rate, size, pricetick)
    """

    def __init__(self, data_dir: str | Path) -> None:
        """
        Args:
            data_dir: DuckDB 数据目录（与 DatabaseManager 共用）。
        """
        self._db = DatabaseManager(data_dir)

    # ── 数据接口 ──────────────────────────────────────

    def load_bar_data(
        self,
        vt_symbol: str,
        interval: Interval | str,
        start: datetime | str,
        end: datetime | str,
    ) -> list[BarData]:
        """从 DuckDB daily_bars 表加载日线数据。

        Args:
            vt_symbol: "000001.SZ" 格式的合约代码。
            interval: 仅支持 Interval.DAILY / "1d"。
            start: 起始日期。
            end: 结束日期。

        Returns:
            list[BarData]: vnpy BarData 对象列表。
        """
        # 类型归一化
        if isinstance(interval, str):
            interval = Interval(interval)

        if interval != Interval.DAILY:
            logger.warning("DuckDuckLab 当前仅支持日线 (Interval.DAILY)")
            return []

        # 解析 ts_code
        try:
            symbol, exchange = extract_vt_symbol(vt_symbol)
        except Exception:
            logger.error("无法解析 vt_symbol: %s", vt_symbol)
            return []

        # 时间格式归一化
        start_str = self._format_date(start)
        end_str = self._format_date(end)

        # 查询 DuckDB
        conn = self._db.get_market()
        try:
            rows = conn.execute(
                f"""
                SELECT trade_date, open, high, low, close, volume, amount
                FROM {DAILY_BARS_TABLE}
                WHERE ts_code = ?
                  AND trade_date >= ?
                  AND trade_date <= ?
                ORDER BY trade_date
                """,
                [vt_symbol, start_str, end_str],
            ).fetchall()
        finally:
            conn.close()

        # 转换为 vnpy BarData
        bars: list[BarData] = []
        for row in rows:
            trade_date_str: str = row[0]
            open_px: float = row[1] or 0.0
            high_px: float = row[2] or 0.0
            low_px: float = row[3] or 0.0
            close_px: float = row[4] or 0.0
            volume: float = row[5] or 0.0
            amount: float = row[6] or 0.0

            dt = datetime.strptime(trade_date_str, "%Y%m%d")

            bar = BarData(
                symbol=symbol,
                exchange=exchange,
                datetime=dt,
                interval=Interval.DAILY,
                open_price=open_px,
                high_price=high_px,
                low_price=low_px,
                close_price=close_px,
                volume=volume,
                turnover=amount,
                open_interest=0,
                gateway_name="DB",
            )
            bars.append(bar)

        return bars

    # ── 合约配置 ──────────────────────────────────────

    def load_contract_setttings(self) -> dict[str, dict[str, Any]]:
        """从 config.db 读取合约设置。

        Returns:
            {vt_symbol: {"long_rate": ..., "short_rate": ..., "size": ..., "pricetick": ...}}
        """
        conn = self._db.get_config()
        try:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key = ?",
                [CONTRACT_SETTINGS_KEY],
            ).fetchone()
            if row and row[0]:
                return json.loads(row[0])
        except Exception:
            logger.warning("config.db 无 system_config 表或 key 不存在，返回空配置")
        finally:
            conn.close()

        return {}

    def add_contract_setting(
        self,
        vt_symbol: str,
        long_rate: float = DEFAULT_LONG_RATE,
        short_rate: float = DEFAULT_SHORT_RATE,
        size: float = DEFAULT_SIZE,
        pricetick: float = DEFAULT_PRICETICK,
    ) -> None:
        """写入一条合约配置到 config.db。

        Args:
            vt_symbol: "000001.SZ"
            long_rate: 买入费率 (默认 0.00025)
            short_rate: 卖出费率 (默认 0.00025)
            size: 合约乘数 (默认 100)
            pricetick: 最小价格变动 (默认 0.01)
        """
        settings = self.load_contract_setttings()
        settings[vt_symbol] = {
            "long_rate": long_rate,
            "short_rate": short_rate,
            "size": size,
            "pricetick": pricetick,
        }
        raw = json.dumps(settings, ensure_ascii=False)

        conn = self._db.get_config()
        try:
            # 确保表存在
            conn.execute(
                "CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value TEXT)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)",
                [CONTRACT_SETTINGS_KEY, raw],
            )
        finally:
            conn.close()

    # ── 内部工具 ──────────────────────────────────────

    @staticmethod
    def _format_date(dt: datetime | str) -> str:
        """归一化为 YYYYMMDD 字符串。"""
        if isinstance(dt, datetime):
            return dt.strftime("%Y%m%d")
        if isinstance(dt, str):
            # 已为 YYYYMMDD 格式则直接返回
            if len(dt) == 8 and dt.isdigit():
                return dt
            # fallback: 尝试解析常见格式
            for fmt in ("%Y-%m-%d", "%Y%m%d"):
                try:
                    return datetime.strptime(dt, fmt).strftime("%Y%m%d")
                except ValueError:
                    continue
        raise ValueError(f"无法解析日期: {dt!r}")
