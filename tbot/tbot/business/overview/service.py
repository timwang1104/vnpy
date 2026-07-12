# SPDX-License-Identifier: MIT
"""OverviewService — 大盘概览 KPI 计算。

沪深净流入 + 大/中/小单方向 + 涨停/炸板/跌停数 + 最高连板高度 + 迷你时序。
"""

from __future__ import annotations

from typing import Any

from tbot.engines.database.manager import DatabaseManager


class OverviewService:
    """大盘概览 KPI 计算服务。

    基于 DuckDB market_overview_a 分库查询 mkt_fundflow 和 limit_up_pool 表，
    计算沪深大盘核心指标与迷你时序。
    """

    def __init__(self, mgr: DatabaseManager) -> None:
        self._mgr = mgr

    def get_kpi(self, date: str) -> dict[str, Any]:
        """计算指定交易日的大盘概览 KPI。

        Parameters
        ----------
        date : str
            交易日期，格式 YYYYMMDD。

        Returns
        -------
        dict
            ``{meta: {tab, date}, kpi: {...}, series: {market_flow_mini: [...]}}``.
        """
        kpi = self._query_mkt_fundflow(date)
        self._merge_limit_up_stats(date, kpi)

        mini_series = self._query_mini_series()

        return {
            "meta": {"tab": "overview", "date": date},
            "kpi": kpi,
            "series": {
                "market_flow_mini": mini_series,
            },
        }

    # ── private helpers ──────────────────────────────────────────────

    def _query_mkt_fundflow(self, date: str) -> dict[str, Any]:
        """查询沪深大盘资金流，返回 KPI 基础字典。"""
        conn = self._mgr.get_overview()
        try:
            row = conn.execute(
                "SELECT net_amount, buy_lg_amount, buy_md_amount, buy_sm_amount, "
                "pct_change_sh, pct_change_sz, close_sh, close_sz "
                "FROM mkt_fundflow WHERE trade_date = ?",
                [date],
            ).fetchone()
        finally:
            conn.close()

        if row:
            return {
                "net_inflow": _to_float(row[0]),
                "large_inflow": _to_float(row[1]),
                "mid_inflow": _to_float(row[2]),
                "small_inflow": _to_float(row[3]),
                "pct_change_sh": _to_float(row[4]),
                "pct_change_sz": _to_float(row[5]),
                "close_sh": _to_float(row[6]),
                "close_sz": _to_float(row[7]),
            }

        return {
            "net_inflow": None,
            "large_inflow": None,
            "mid_inflow": None,
            "small_inflow": None,
            "pct_change_sh": None,
            "pct_change_sz": None,
            "close_sh": None,
            "close_sz": None,
        }

    def _merge_limit_up_stats(self, date: str, kpi: dict[str, Any]) -> None:
        """查询涨停快照统计并合并到 kpi 字典。"""
        conn = self._mgr.get_overview()
        try:
            row = conn.execute(
                "SELECT "
                '  SUM(CASE WHEN "limit" = \'U\' THEN 1 ELSE 0 END) as up,'
                '  SUM(CASE WHEN "limit" = \'Z\' THEN 1 ELSE 0 END) as zb,'
                '  SUM(CASE WHEN "limit" = \'D\' THEN 1 ELSE 0 END) as down,'
                "  MAX(CASE WHEN \"limit\" = 'U' THEN CAST(limit_times AS INTEGER) ELSE 0 END) as max_lt "
                "FROM limit_up_pool WHERE trade_date = ?",
                [date],
            ).fetchone()
        finally:
            conn.close()

        if row and row[0] is not None:
            kpi["limit_up_cnt"] = row[0]
            kpi["limit_break_cnt"] = row[1]
            kpi["limit_down_cnt"] = row[2]
            kpi["max_limit_times"] = int(row[3]) if row[3] is not None else 0
        else:
            kpi["limit_up_cnt"] = 0
            kpi["limit_break_cnt"] = 0
            kpi["limit_down_cnt"] = 0
            kpi["max_limit_times"] = 0

    def _query_mini_series(self) -> list[dict[str, Any]]:
        """查询近 20 个交易日的净流入迷你时序。"""
        conn = self._mgr.get_overview()
        try:
            rows = conn.execute(
                "SELECT trade_date, net_amount, buy_lg_amount "
                "FROM mkt_fundflow ORDER BY trade_date DESC LIMIT 20"
            ).fetchall()
        finally:
            conn.close()

        return [
            {"date": r[0], "net_amount": _to_float(r[1]), "large_inflow": _to_float(r[2])}
            for r in reversed(rows)
        ]


def _to_float(v: object) -> float | None:
    """将 DuckDB 可能返回的 VARCHAR / None 转换为 float 或 None。"""
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
