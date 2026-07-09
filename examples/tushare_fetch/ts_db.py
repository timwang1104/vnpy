"""
共用 SQLite 落库层：tushare 涨停池与资金流特色数据的建表、upsert、读回校验。

特色数据非 vnpy 标准 BarData/TickData，vnpy_sqlite 存不下，故用 sqlite3 标准库
自建表。列名直接采用 tushare 返回的英文字段名，避免映射歧义。
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any, Sequence

# ---------------------------------------------------------------------------
# 列定义：与 tushare 返回字段名一致
# ---------------------------------------------------------------------------

# 涨停池 limit_list_d
LIMIT_UP_COLS: list[str] = [
    "trade_date", "ts_code", "industry", "name", "close", "pct_chg",
    "amount", "limit_amount", "float_mv", "total_mv", "turnover_ratio",
    "fd_amount", "first_time", "last_time", "open_times", "up_stat",
    "limit_times", "limit",
]

# 行业板块资金流 moneyflow_ind_dc
IND_FF_COLS: list[str] = [
    "trade_date", "content_type", "ts_code", "name", "pct_change", "close",
    "net_amount", "net_amount_rate", "buy_elg_amount", "buy_elg_amount_rate",
    "buy_lg_amount", "buy_lg_amount_rate", "buy_md_amount", "buy_md_amount_rate",
    "buy_sm_amount", "buy_sm_amount_rate", "buy_sm_amount_stock", "rank",
]

# 大盘资金流 moneyflow_mkt_dc
MKT_FF_COLS: list[str] = [
    "trade_date", "close_sh", "pct_change_sh", "close_sz", "pct_change_sz",
    "net_amount", "net_amount_rate", "buy_elg_amount", "buy_elg_amount_rate",
    "buy_lg_amount", "buy_lg_amount_rate", "buy_md_amount", "buy_md_amount_rate",
    "buy_sm_amount", "buy_sm_amount_rate",
]

# 个股资金流 moneyflow
STOCK_FF_COLS: list[str] = [
    "ts_code", "trade_date", "buy_sm_vol", "buy_sm_amount", "sell_sm_vol",
    "sell_sm_amount", "buy_md_vol", "buy_md_amount", "sell_md_vol",
    "sell_md_amount", "buy_lg_vol", "buy_lg_amount", "sell_lg_vol",
    "sell_lg_amount", "buy_elg_vol", "buy_elg_amount", "sell_elg_vol",
    "sell_elg_amount", "net_mf_vol", "net_mf_amount",
]

# 表名 -> 主键列
TABLE_PK: dict[str, tuple[str, ...]] = {
    "limit_up_pool": ("trade_date", "ts_code"),
    "ind_fundflow": ("trade_date", "ts_code"),
    "mkt_fundflow": ("trade_date",),
    "stock_fundflow": ("ts_code", "trade_date"),
}

# 表名 -> 列清单
TABLE_COLS: dict[str, list[str]] = {
    "limit_up_pool": LIMIT_UP_COLS,
    "ind_fundflow": IND_FF_COLS,
    "mkt_fundflow": MKT_FF_COLS,
    "stock_fundflow": STOCK_FF_COLS,
}


# ---------------------------------------------------------------------------
# 连接与建表
# ---------------------------------------------------------------------------

def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开/创建 SQLite 数据库，建父目录，启用 WAL。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_tables(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS 四张表，PK 由 TABLE_PK 指定。"""
    for table, cols in TABLE_COLS.items():
        pk = TABLE_PK[table]
        col_defs = [f'"{c}"' for c in cols]
        # 主键列组合
        pk_cols = ", ".join(f'"{c}"' for c in pk)
        ddl = (
            f"CREATE TABLE IF NOT EXISTS {table} ("
            + ", ".join(col_defs)
            + f", PRIMARY KEY ({pk_cols})"
            + ")"
        )
        conn.execute(ddl)
    conn.commit()


# ---------------------------------------------------------------------------
# upsert 与查询
# ---------------------------------------------------------------------------

def _normalize(value: Any) -> Any:
    """pandas NaN / float('nan') -> None（sqlite 存 NULL）。"""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    # pandas NA / NaT
    try:
        import pandas as pd  # noqa: F401
        if value is pd.NA:
            return None
    except Exception:
        pass
    return value


def upsert_rows(
    conn: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> int:
    """
    批量 INSERT OR IGNORE。返回实际插入行数。
    :param rows: 每行值的顺序须与 columns 一致
    """
    if not rows:
        return 0
    cols_sql = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    sql = f'INSERT OR IGNORE INTO {table} ({cols_sql}) VALUES ({placeholders})'
    normalized = [tuple(_normalize(v) for v in row) for row in rows]
    cur = conn.executemany(sql, normalized)
    conn.commit()
    return cur.rowcount


def existing_dates(
    conn: sqlite3.Connection,
    table: str,
    start: str,
    end: str,
) -> set[str]:
    """返回该表在 [start, end] 范围内已存在的 trade_date 集合（用于断点续抓）。"""
    sql = f'SELECT DISTINCT trade_date FROM {table} WHERE trade_date >= ? AND trade_date <= ?'
    rows = conn.execute(sql, (start, end)).fetchall()
    return {r[0] for r in rows}


def existing_stock_codes(
    conn: sqlite3.Connection,
    start: str,
    end: str,
) -> list[str]:
    """从 limit_up_pool 查 [start, end] 范围内出现过的去重 ts_code（用于个股资金流）。"""
    sql = (
        'SELECT DISTINCT ts_code FROM limit_up_pool '
        'WHERE trade_date >= ? AND trade_date <= ? ORDER BY ts_code'
    )
    rows = conn.execute(sql, (start, end)).fetchall()
    return [r[0] for r in rows]


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    """返回该表总行数。"""
    return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


def dump_head(
    conn: sqlite3.Connection,
    table: str,
    n: int = 5,
) -> list[tuple]:
    """返回该表前 n 行（读回校验用）。"""
    return conn.execute(f'SELECT * FROM {table} LIMIT ?', (n,)).fetchall()