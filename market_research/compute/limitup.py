# SPDX-License-Identifier: MIT
"""compute_limitup — 涨停池每日快照计算。

单日梯队聚合 + 全历史迭代。
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable


def compute_limitup(db: sqlite3.Connection, date: str) -> dict:
    """单日涨停聚合。

    按 limit_times（连板高度）分组生成 tiers，含涨/炸/跌停计数。

    Args:
        db: data/tushare.db 连接
        date: YYYYMMDD 格式交易日

    Returns:
        {meta: {tab, date}, kpi: {limit_up_cnt, limit_break_cnt, limit_down_cnt, max_limit_times},
         tables: {tiers: [{limit_times, count, members: [{name, ts_code, industry, first_time, last_time, fd_amount}]}]}}
    """
    cur = db.cursor()
    cur.execute(
        "SELECT ts_code, name, industry, limit_times, first_time, last_time, "
        'fd_amount, "limit" '
        "FROM limit_up_pool WHERE trade_date=? ORDER BY limit_times DESC, amount DESC",
        (date,),
    )
    rows = cur.fetchall()
    return _aggregate_limitup(date, rows)


def _aggregate_limitup(date: str, rows: list[tuple]) -> dict:
    """纯函数：将 limit_up_pool 原始行聚合为前端 schema。

    rows 字段顺序: ts_code, name, industry, limit_times, first_time, last_time, fd_amount, limit
    """

    if not rows:
        return {
            "meta": {"tab": "limitup", "date": date},
            "kpi": {"limit_up_cnt": 0, "limit_break_cnt": 0, "limit_down_cnt": 0, "max_limit_times": 0},
            "tables": {"tiers": []},
        }

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


def compute_limitup_history(
    db: sqlite3.Connection,
) -> Iterable[tuple[str, dict]]:
    """批量查询全部交易日数据，逐日 yield (date, dict)。

    N 次日历查询合并为 1 次 SQL + Python 分组，大幅减少 DB 往返。
    builder 用此遍历写分片。
    """
    cur = db.cursor()
    cur.execute(
        "SELECT trade_date, ts_code, name, industry, limit_times, first_time, last_time, "
        'fd_amount, "limit" '
        "FROM limit_up_pool ORDER BY trade_date, limit_times DESC, amount DESC"
    )
    rows = cur.fetchall()

    # Python 分组
    from itertools import groupby

    for date_str, group in groupby(rows, key=lambda r: r[0]):
        group_rows = [r[1:] for r in group]  # strip trade_date
        yield (date_str, _aggregate_limitup(date_str, group_rows))
