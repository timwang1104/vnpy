# SPDX-License-Identifier: MIT
"""LimitUpService — 涨停板聚合查询。

将 limit_up_pool 原始行聚合为前端可消费的 schema（阶梯分组 + 行业聚集度）。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

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


# ── 纯函数聚合（从 market_research/compute/limitup.py 迁移）───────


def _aggregate_limitup(date: str, rows: list[tuple]) -> dict[str, Any]:
    """纯函数：将 limit_up_pool 原始行聚合为前端 schema。

    rows 字段顺序: ts_code, name, industry, limit_times, first_time, last_time, fd_amount, limit
    """

    if not rows:
        return _empty_result(date)

    # --- 分类计数 ---
    up_cnt = sum(1 for r in rows if r[7] == "U")
    break_cnt = sum(1 for r in rows if r[7] == "Z")
    down_cnt = sum(1 for r in rows if r[7] == "D")
    all_limit_times = [
        int(r[3]) for r in rows if r[3] is not None and r[7] == "U"
    ]
    max_lt = max(all_limit_times) if all_limit_times else 0

    # --- 按 limit_times 分组（仅 U 涨停）---
    tiers: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        if r[7] != "U":
            continue
        lt = int(r[3]) if r[3] is not None else 0
        tiers[lt].append(
            {
                "ts_code": r[0],
                "name": r[1],
                "industry": r[2] or "",
                "first_time": r[4] or "",
                "last_time": r[5] or "",
                "fd_amount": r[6] if r[6] is not None else 0,
            }
        )

    sorted_tiers = [
        {
            "limit_times": lt,
            "count": len(members),
            "members": members,
        }
        for lt, members in sorted(tiers.items(), reverse=True)
    ]

    # --- 行业聚集度 ---
    industry_count: dict[str, int] = defaultdict(int)
    for r in rows:
        if r[7] == "U" and r[2]:
            industry_count[r[2]] += 1
    sorted_industries = [
        {"industry": ind, "count": cnt}
        for ind, cnt in sorted(industry_count.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "meta": {"tab": "limitup", "date": date},
        "kpi": {
            "limit_up_cnt": up_cnt,
            "limit_break_cnt": break_cnt,
            "limit_down_cnt": down_cnt,
            "max_limit_times": max_lt,
        },
        "series": {
            "industry_concentration": sorted_industries,
        },
        "tables": {
            "tiers": sorted_tiers,
        },
    }
