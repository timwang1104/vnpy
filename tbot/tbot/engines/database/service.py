"""DatabaseService — DuckDB 业务查询封装。"""

from __future__ import annotations

from typing import Any

import duckdb

from tbot.engines.database.manager import DatabaseManager


class DatabaseService:
    """基于 DatabaseManager 提供业务查询方法，返回 list[dict]。"""

    def __init__(self, mgr: DatabaseManager) -> None:
        self._mgr = mgr

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _fetch_dicts(
        result: duckdb.DuckDBPyConnection,
    ) -> list[dict[str, Any]]:
        """将 DuckDB execute() 结果转换为 list[dict]。

        DuckDB 1.x 的 fetchall() 返回 list[tuple]，不支持 dict() 直接转换。
        """
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    # ── market_overview_a ──────────────────────────────────────────

    def get_ind_fundflow_raw(
        self, ts_code: str, content_type: str
    ) -> list[dict[str, Any]]:
        """查询行业/概念资金流。

        Parameters
        ----------
        ts_code : str
            股票或行业代码。
        content_type : str
            内容类型（如 "行业"、"概念"）。

        Returns
        -------
        list[dict]
        """
        conn = self._mgr.get_overview()
        try:
            result = conn.execute(
                "SELECT * FROM ind_fundflow "
                "WHERE ts_code = ? AND content_type = ? "
                "ORDER BY trade_date",
                [ts_code, content_type],
            )
            return self._fetch_dicts(result)
        finally:
            conn.close()

    def get_limitup_by_date(self, date: str) -> list[dict[str, Any]]:
        """查询指定日期的涨停池。

        Parameters
        ----------
        date : str
            交易日期，格式 YYYYMMDD。

        Returns
        -------
        list[dict]
        """
        conn = self._mgr.get_overview()
        try:
            result = conn.execute(
                "SELECT * FROM limit_up_pool "
                "WHERE trade_date = ? "
                "ORDER BY first_time",
                [date],
            )
            return self._fetch_dicts(result)
        finally:
            conn.close()

    def get_mkt_fundflow(self, date: str) -> list[dict[str, Any]]:
        """查询指定日期的市场资金流。

        Parameters
        ----------
        date : str
            交易日期，格式 YYYYMMDD。

        Returns
        -------
        list[dict]
        """
        conn = self._mgr.get_overview()
        try:
            result = conn.execute(
                "SELECT * FROM mkt_fundflow WHERE trade_date = ?",
                [date],
            )
            return self._fetch_dicts(result)
        finally:
            conn.close()

    # ── market_a ────────────────────────────────────────────────────

    def get_all_trade_dates(self) -> list[dict[str, Any]]:
        """查询所有交易日期，按升序排列。

        Returns
        -------
        list[dict]
            每项含 {"trade_date": str}。
        """
        conn = self._mgr.get_market()
        try:
            result = conn.execute(
                "SELECT DISTINCT trade_date FROM daily_bars ORDER BY trade_date"
            )
            return self._fetch_dicts(result)
        finally:
            conn.close()

    def get_daily_bars(
        self, symbol: str, start: str, end: str
    ) -> list[dict[str, Any]]:
        """查询个股日线行情。

        Parameters
        ----------
        symbol : str
            ts_code 股票代码。
        start : str
            起始日期 YYYYMMDD。
        end : str
            截止日期 YYYYMMDD。

        Returns
        -------
        list[dict]
        """
        conn = self._mgr.get_market()
        try:
            result = conn.execute(
                "SELECT * FROM daily_bars "
                "WHERE ts_code = ? AND trade_date >= ? AND trade_date <= ? "
                "ORDER BY trade_date",
                [symbol, start, end],
            )
            return self._fetch_dicts(result)
        finally:
            conn.close()
