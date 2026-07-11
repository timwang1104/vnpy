# SPDX-License-Identifier: MIT
"""compute_overview — 大盘概览 KPI。

沪深净流入 + 大/中/小单方向 + 涨停/炸板/跌停数 + 最高连板高度 + 迷你时序。
"""
from __future__ import annotations

import sqlite3


def compute_overview(db: sqlite3.Connection, date: str) -> dict:
    """计算大盘概览 KPI + 迷你时序。

    返回 {meta, kpi, series}。
    """
    cur = db.cursor()

    # --- 沪深大盘资金流 ---
    cur.execute(
        "SELECT net_amount, buy_lg_amount, buy_md_amount, buy_sm_amount, "
        "pct_change_sh, pct_change_sz, close_sh, close_sz "
        "FROM mkt_fundflow WHERE trade_date=?",
        (date,),
    )
    mkt_row = cur.fetchone()
    if mkt_row:
        net_amount, lg, md_, sm, pct_sh, pct_sz, close_sh, close_sz = mkt_row
    else:
        net_amount = lg = md_ = sm = pct_sh = pct_sz = close_sh = close_sz = None

    # --- 涨停快照 ---
    cur.execute(
        "SELECT "
        '  SUM(CASE WHEN "limit"=\'U\' THEN 1 ELSE 0 END) as up,'
        '  SUM(CASE WHEN "limit"=\'Z\' THEN 1 ELSE 0 END) as zb,'
        '  SUM(CASE WHEN "limit"=\'D\' THEN 1 ELSE 0 END) as down,'
        "  MAX(CASE WHEN \"limit\"='U' THEN limit_times ELSE 0 END) as max_lt "
        "FROM limit_up_pool WHERE trade_date=?",
        (date,),
    )
    limit_row = cur.fetchone()
    if limit_row and limit_row[0] is not None:
        up_cnt, break_cnt, down_cnt, max_lt = limit_row
        max_lt = int(max_lt) if max_lt is not None else 0
    else:
        up_cnt = break_cnt = down_cnt = 0
        max_lt = 0

    # --- 迷你时序：近 20 日的净流入 ---
    cur.execute(
        "SELECT trade_date, net_amount, buy_lg_amount "
        "FROM mkt_fundflow ORDER BY trade_date DESC LIMIT 20"
    )
    mini_series = [
        {"date": r[0], "net_amount": r[1], "large_inflow": r[2]}
        for r in reversed(cur.fetchall())
    ]

    kpi: dict = {
        "net_inflow": net_amount,
        "large_inflow": lg,
        "mid_inflow": md_,
        "small_inflow": sm,
        "pct_change_sh": pct_sh,
        "pct_change_sz": pct_sz,
        "close_sh": close_sh,
        "close_sz": close_sz,
        "limit_up_cnt": up_cnt,
        "limit_break_cnt": break_cnt,
        "limit_down_cnt": down_cnt,
        "max_limit_times": max_lt,
    }

    return {
        "meta": {"tab": "overview", "date": date},
        "kpi": kpi,
        "series": {
            "market_flow_mini": mini_series,
        },
    }
