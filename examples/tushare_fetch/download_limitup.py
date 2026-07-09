"""
下载 A 股涨停股票池到本地 SQLite（tushare limit_list_d，经代理 tt.xiaodefa.cn）。

用法：
    export TUSHARE_API_KEY=<56位key>
    export TUSHARE_BASE_URL=https://tt.xiaodefa.cn   # 可选
    python examples/tushare_fetch/download_limitup.py --start 20240102 --end 20240105
    python examples/tushare_fetch/download_limitup.py --start 20240102 --end 20240105 --db data/tushare.db --force

limit 列含义：U 涨停 / Z 炸板 / D 跌停。
断点续抓：按 trade_date 去重，已存日期跳过（--force 强制重抓该日）。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

from vnpy_tushare import create_pro, load_proxy_url, load_token_script

from ts_db import (
    LIMIT_UP_COLS,
    connect,
    count_rows,
    dump_head,
    existing_dates,
    init_tables,
    upsert_rows,
)


def _iter_dates(start: str, end: str):
    """生成 [start, end] 之间 YYYYMMDD 字符串（含端点，按自然日）。"""
    s = datetime.strptime(start, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    d = s
    while d <= e:
        yield d.strftime("%Y%m%d")
        d += timedelta(days=1)


def _get_open_dates(pro, start: str, end: str) -> list[str]:
    """
    优先用 trade_cal 取开市日；代理不支持则回退自然日遍历。
    """
    try:
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
        if cal is not None and not cal.empty and "cal_date" in cal.columns:
            return sorted(cal["cal_date"].astype(str).tolist())
    except Exception as e:
        print(f"[提示] trade_cal 不可用，回退自然日遍历（{type(e).__name__}: {str(e)[:60]}）")
    return list(_iter_dates(start, end))


def download_one_date(pro, trade_date: str, conn) -> int:
    """抓单日涨停池并落库，返回插入行数。"""
    df: pd.DataFrame = pro.limit_list_d(trade_date=trade_date)
    if df is None or df.empty:
        return 0
    # 按 LIMIT_UP_COLS 顺序取列，缺失列补 None
    rows = []
    for _, row in df.iterrows():
        rows.append([row.get(c) for c in LIMIT_UP_COLS])
    return upsert_rows(conn, "limit_up_pool", LIMIT_UP_COLS, rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 A 股涨停股票池到本地 SQLite")
    parser.add_argument("--start", required=True, help="起始日期 YYYYMMDD")
    parser.add_argument("--end", required=True, help="结束日期 YYYYMMDD")
    parser.add_argument("--db", default="data/tushare.db", help="SQLite 路径，默认 data/tushare.db")
    parser.add_argument("--force", action="store_true", help="强制重抓（先删该日再插）")
    args = parser.parse_args()

    try:
        token = load_token_script()
    except RuntimeError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1

    proxy_url = load_proxy_url()
    pro = create_pro(token, proxy_url)

    conn = connect(args.db)
    init_tables(conn)

    open_dates = _get_open_dates(pro, args.start, args.end)
    print(f"[信息] 日期范围 {args.start}~{args.end}，开市日 {len(open_dates)} 个")

    if args.force:
        conn.execute(
            f"DELETE FROM limit_up_pool WHERE trade_date >= ? AND trade_date <= ?",
            (args.start, args.end),
        )
        conn.commit()
        skip_dates: set[str] = set()
    else:
        skip_dates = existing_dates(conn, "limit_up_pool", args.start, args.end)

    ok = skip_n = fail_n = 0
    for trade_date in open_dates:
        if trade_date in skip_dates:
            skip_n += 1
            continue
        try:
            inserted = download_one_date(pro, trade_date, conn)
            if inserted:
                ok += 1
                print(f"[成功] {trade_date} 插入 {inserted} 行")
            else:
                # 空结果视为该日无涨停（如非交易日回退），不计失败
                skip_n += 1
                print(f"[跳过] {trade_date} 无数据")
        except Exception as e:
            fail_n += 1
            print(f"[失败] {trade_date} {type(e).__name__}: {str(e)[:120]}")
        # limit_list_d 按日调用，轻限频
        time.sleep(0.1)

    print(f"\n[汇总] 成功 {ok} 日，跳过 {skip_n} 日，失败 {fail_n} 日")
    print(f"[库况] limit_up_pool 总行数: {count_rows(conn, 'limit_up_pool')}")
    head = dump_head(conn, "limit_up_pool", 3)
    if head:
        print(f"[样例] 前 3 行: {head}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())