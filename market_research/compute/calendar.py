# SPDX-License-Identifier: MIT
"""交易日历 — 用 exchange_calendars.XSHG 生成交易日列表 + 有无数据标记。

仅在 build 时调用，不进运行时。
"""
from __future__ import annotations

import exchange_calendars as ec  # type: ignore[import-untyped]


def build_calendar(start: str, end: str, trade_dates: list[str]) -> dict:
    """生成日历 JSON，标记每个交易日是否有数据。

    Args:
        start: 起始 YYYYMMDD
        end: 截止 YYYYMMDD
        trade_dates: db 中 DISTINCT trade_date 列表

    Returns:
        {"meta": {"start": ..., "end": ..., "count": ..., "exchange": "XSHG"},
         "dates": [{"date": "20260101", "has_data": true}, ...]}
    """
    xshg = ec.get_calendar("XSHG")
    # exchange_calendars 的 sessions_in_range 接收 datetime-like 参数
    from datetime import datetime

    start_dt = datetime.strptime(start, "%Y%m%d")
    end_dt = datetime.strptime(end, "%Y%m%d")
    sessions = xshg.sessions_in_range(start_dt, end_dt)

    data_set = set(trade_dates)
    dates_list = [
        {
            "date": s.strftime("%Y%m%d"),
            "has_data": s.strftime("%Y%m%d") in data_set,
        }
        for s in sessions
    ]

    return {
        "meta": {
            "start": start,
            "end": end,
            "count": len(dates_list),
            "exchange": "XSHG",
        },
        "dates": dates_list,
    }
