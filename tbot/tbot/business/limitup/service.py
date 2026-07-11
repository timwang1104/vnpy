# SPDX-License-Identifier: MIT
"""LimitUpService — 涨停板聚合查询。

将 limit_up_pool 原始行聚合为前端可消费的 schema（阶梯分组 + 行业聚集度）。
"""

from __future__ import annotations

from typing import Any

from market_research.compute.limitup import _aggregate_limitup

from tbot.engines.database.manager import DatabaseManager


class LimitUpService:
    """涨停板聚合查询服务。

    基于 market_overview_a 分库的 limit_up_pool 表，
    查询原始数据后通过 compute 层纯函数聚合为前端 schema。
    """

    def __init__(self, mgr: DatabaseManager) -> None:
        self._mgr = mgr

    def get_daily_aggregation(self, date: str) -> dict[str, Any]:
        """查询并聚合指定交易日的涨停数据。

        Parameters
        ----------
        date : str
            交易日期，格式 YYYYMMDD。

        Returns
        -------
        dict
            ``{meta: {tab, date}, kpi: {...}, series: {industry_concentration: [...]}, tables: {tiers: [...]}}``.
        """
        rows = self._fetch_raw(date)
        if not rows:
            return _empty_result(date)
        return _aggregate_limitup(date, rows)

    def get_history(self, window: int = 20) -> list[dict[str, Any]]:
        """查询最近 N 个交易日的涨停聚合历史。

        Parameters
        ----------
        window : int
            交易日数量，默认 20。

        Returns
        -------
        list[dict]
            每日涨停聚合结果列表（从远到近）。
        """
        recent = self._fetch_recent_dates(window)
        return [self.get_daily_aggregation(d) for d in recent]

    # ── helpers ──────────────────────────────────────────────────────

    def _fetch_raw(self, date: str) -> list[tuple]:
        """从 market_overview_a 查询 limit_up_pool 原始行。

        Returns list[tuple]，格式兼容 _aggregate_limitup。
        """
        conn = self._mgr.get_overview()
        try:
            rows = conn.execute(
                "SELECT ts_code, name, industry, limit_times, first_time, last_time, "
                'fd_amount, "limit" '
                "FROM limit_up_pool WHERE trade_date = ? "
                "ORDER BY limit_times DESC, amount DESC",
                [date],
            ).fetchall()
        finally:
            conn.close()

        return rows

    def _fetch_recent_dates(self, window: int) -> list[str]:
        """从 market_a.daily_bars 获取最近的 N 个交易日。"""
        conn = self._mgr.get_market()
        try:
            rows = conn.execute(
                "SELECT DISTINCT trade_date FROM daily_bars "
                "ORDER BY trade_date DESC LIMIT ?",
                [window],
            ).fetchall()
        finally:
            conn.close()

        return sorted(r[0] for r in rows)


def _empty_result(date: str) -> dict[str, Any]:
    """无数据时的空结果模板。"""
    return {
        "meta": {"tab": "limitup", "date": date},
        "kpi": {
            "limit_up_cnt": 0,
            "limit_break_cnt": 0,
            "limit_down_cnt": 0,
            "max_limit_times": 0,
        },
        "tables": {"tiers": []},
    }
