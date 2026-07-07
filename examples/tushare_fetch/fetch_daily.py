"""
示例：通过 tushare + 代理抓取日线行情与日线指标（15000 积分高权限 key）。

用法：
    export TUSHARE_API_KEY=<56位token>
    export TUSHARE_BASE_URL=https://tt.xiaodefa.cn   # 可选，留空用默认
    python examples/tushare_fetch/fetch_daily.py
    python examples/tushare_fetch/fetch_daily.py --csv out.csv
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from vnpy_tushare import create_pro, load_proxy_url, load_token_script


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取 tushare 日线与日线指标")
    parser.add_argument("--csv", default=None, help="若提供则将合并结果写出到该 CSV")
    args = parser.parse_args()

    try:
        token: str = load_token_script()
    except RuntimeError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1

    proxy_url: str = load_proxy_url()
    pro = create_pro(token, proxy_url)

    ts_code: str = "000001.SZ"
    start_date: str = "20240101"
    end_date: str = "20240201"

    # 日线行情
    daily: pd.DataFrame = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    print(f"[daily] {ts_code} 行数: {len(daily)}")
    print(daily.head())

    # 日线指标（pe / pb / 总市值等，需要相应积分）
    basic: pd.DataFrame = pro.daily_basic(
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
        fields="ts_code,trade_date,close,pe,pb,total_mv,circ_mv",
    )
    print(f"[daily_basic] {ts_code} 行数: {len(basic)}")
    print(basic.head())

    if args.csv:
        merged = daily.merge(basic, on=["ts_code", "trade_date"], how="outer")
        merged.to_csv(args.csv)
        print(f"[输出] 已写入 {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())