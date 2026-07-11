"""DatabaseManager — DuckDB 连接管理。"""

from __future__ import annotations

from pathlib import Path

import duckdb


class DatabaseManager:
    """统一管理 DuckDB 物理分库的连接。

    分库:
        market_a      — 日线行情（原 history.db）
        market_overview_a — 市场全景（行业资金流、涨停池等，原 tushare.db）
        research      — 回测结果
        config        — kv 配置

    用法:
        mgr = DatabaseManager("data")
        conn = mgr.get_overview()
        conn.execute("SELECT count(*) FROM ind_fundflow").fetchone()
    """

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def get_conn(self, db_name: str) -> duckdb.DuckDBPyConnection:
        """返回指定数据库的连接（自动拼接 .db 后缀）。"""
        path = str(self.data_dir / f"{db_name}.db")
        conn = duckdb.connect(path)
        conn.execute("SET threads TO 4")
        return conn

    def get_market(self) -> duckdb.DuckDBPyConnection:
        return self.get_conn("market_a")

    def get_overview(self) -> duckdb.DuckDBPyConnection:
        return self.get_conn("market_overview_a")

    def get_research(self) -> duckdb.DuckDBPyConnection:
        return self.get_conn("research")

    def get_config(self) -> duckdb.DuckDBPyConnection:
        return self.get_conn("config")
