"""
下载资金流数据到本地 SQLite（三层面，经代理 tt.xiaodefa.cn）：
  1. 行业板块资金流 moneyflow_ind_dc -> ind_fundflow（约 86 行/天）
  2. 沪深大盘资金流 moneyflow_mkt_dc -> mkt_fundflow（1 行/天）
  3. 涨停池个股资金流 moneyflow(ts_code=) -> stock_fundflow（股票清单复用 limit_up_pool）

用法：
    export TUSHARE_API_KEY=<56位key>
    export TUSHARE_BASE_URL=https://tt.xiaodefa.cn   # 可选
    python examples/tushare_fetch/download_fundflow.py --start 20240102 --end 20240105
    python examples/tushare_fetch/download_fundflow.py --start 20240102 --end 20240105 --no-stock
    python examples/tushare_fetch/download_fundflow.py --start 20240102 --end 20240105 --db data/tushare.db --sleep 0.2

断点续抓：行业/大盘按 trade_date 去重；个股按 (ts_code, trade_date) 去重。
三段独立容错：某段接口失败不影响其它段。
概念板块资金流代理不支持，不在本次范围。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

from vnpy_tushare import create_pro, load_proxy_url, load_token_script

from ts_db import (
    IND_FF_COLS,
    MKT_FF_COLS,
    STOCK_FF_COLS,
    connect,
    count_rows,
    dump_head,
    existing_dates,
    existing_stock_codes,
    init_tables,
    upsert_rows,
)


def _iter_dates(start: str, end: str):
    s = datetime.strptime(start, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    d = s
    while d <= e:
        yield d.strftime("%Y%m%d")
        d += timedelta(days=1)


def _get_open_dates(pro, start: str, end: str) -> list[str]:
    try:
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
        if cal is not None and not cal.empty and "cal_date" in cal.columns:
            return sorted(cal["cal_date"].astype(str).tolist())
    except Exception as e:
        print(f"[提示] trade_cal 不可用，回退自然日遍历（{type(e).__name__}: {str(e)[:60]}）")
    return list(_iter_dates(start, end))


def _df_to_rows(df: pd.DataFrame, cols: list[str]) -> list[list]:
    rows = []
    for _, row in df.iterrows():
        rows.append([row.get(c) for c in cols])
    return rows


# ---------------------------------------------------------------------------
# 三段：行业 / 大盘 / 个股
# ---------------------------------------------------------------------------

def download_ind(pro, open_dates, skip_dates, conn) -> tuple[int, int]:
    ok = fail = 0
    for trade_date in open_dates:
        if trade_date in skip_dates:
            continue
        try:
            df = pro.moneyflow_ind_dc(trade_date=trade_date)
            if df is not None and not df.empty:
                rows = _df_to_rows(df, IND_FF_COLS)
                n = upsert_rows(conn, "ind_fundflow", IND_FF_COLS, rows)
                ok += 1
                print(f"[行业] {trade_date} 插入 {n} 行")
            else:
                print(f"[行业] {trade_date} 无数据")
        except Exception as e:
            fail += 1
            print(f"[行业] {trade_date} 失败 {type(e).__name__}: {str(e)[:100]}")
        time.sleep(0.1)
    return ok, fail


def download_mkt(pro, open_dates, skip_dates, conn) -> tuple[int, int]:
    ok = fail = 0
    for trade_date in open_dates:
        if trade_date in skip_dates:
            continue
        try:
            df = pro.moneyflow_mkt_dc(trade_date=trade_date)
            if df is not None and not df.empty:
                rows = _df_to_rows(df, MKT_FF_COLS)
                n = upsert_rows(conn, "mkt_fundflow", MKT_FF_COLS, rows)
                ok += 1
                print(f"[大盘] {trade_date} 插入 {n} 行")
            else:
                print(f"[大盘] {trade_date} 无数据")
        except Exception as e:
            fail += 1
            print(f"[大盘] {trade_date} 失败 {type(e).__name__}: {str(e)[:100]}")
        time.sleep(0.1)
    return ok, fail


def download_stock(pro, start, end, conn, sleep_s: float) -> tuple[int, int]:
    """按涨停池个股清单逐只抓 moneyflow。"""
    codes = existing_stock_codes(conn, start, end)
    if not codes:
        print("[个股] limit_up_pool 中该范围无 ts_code，跳过个股段（请先跑 download_limitup.py）")
        return 0, 0
    print(f"[个股] 待抓股票 {len(codes)} 只")
    ok = fail = 0
    for ts_code in codes:
        try:
            df = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end)
            if df is not None and not df.empty:
                rows = _df_to_rows(df, STOCK_FF_COLS)
                n = upsert_rows(conn, "stock_fundflow", STOCK_FF_COLS, rows)
                ok += 1
                if ok % 20 == 0:
                    print(f"[个股] 已完成 {ok}/{len(codes)}（{ts_code} 插入 {n} 行）")
        except Exception as e:
            fail += 1
            print(f"[个股] {ts_code} 失败 {type(e).__name__}: {str(e)[:80]}")
        time.sleep(sleep_s)
    return ok, fail


def main() -> int:
    parser = argparse.ArgumentParser(description="下载资金流（行业/大盘/个股）到本地 SQLite")
    parser.add_argument("--start", required=True, help="起始日期 YYYYMMDD")
    parser.add_argument("--end", required=True, help="结束日期 YYYYMMDD")
    parser.add_argument("--db", default="data/tushare.db", help="SQLite 路径")
    parser.add_argument("--no-ind", action="store_true", help="跳过行业板块段")
    parser.add_argument("--no-mkt", action="store_true", help="跳过大盘段")
    parser.add_argument("--no-stock", action="store_true", help="跳过个股段")
    parser.add_argument("--sleep", type=float, default=0.15, help="个股循环间隔秒，默认 0.15")
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

    if not args.no_ind:
        skip = existing_dates(conn, "ind_fundflow", args.start, args.end)
        ok, fail = download_ind(pro, open_dates, skip, conn)
        print(f"[行业汇总] 成功 {ok} 日，失败 {fail} 日；ind_fundflow 总行数 {count_rows(conn, 'ind_fundflow')}")

    if not args.no_mkt:
        skip = existing_dates(conn, "mkt_fundflow", args.start, args.end)
        ok, fail = download_mkt(pro, open_dates, skip, conn)
        print(f"[大盘汇总] 成功 {ok} 日，失败 {fail} 日；mkt_fundflow 总行数 {count_rows(conn, 'mkt_fundflow')}")

    if not args.no_stock:
        ok, fail = download_stock(pro, args.start, args.end, conn, args.sleep)
        print(f"[个股汇总] 成功 {ok} 只，失败 {fail} 只；stock_fundflow 总行数 {count_rows(conn, 'stock_fundflow')}")

    print("\n[库况样例]")
    for t in ("ind_fundflow", "mkt_fundflow", "stock_fundflow"):
        head = dump_head(conn, t, 2)
        if head:
            print(f"  {t} 前 2 行: {head}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())