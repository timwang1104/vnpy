"""
示例：通过 tushare + 代理抓取财务指标与合约基本信息（15000 积分高权限 key）。

覆盖：
- fina_indicator 财务指标（每股收益、净资产收益率等）
- stock_basic    A 股合约基本信息
- fut_basic      期货合约基本信息（tushare 接口名为 fut_basic，非 futures_basic）
- index_daily    指数日线

用法：
    export TUSHARE_API_KEY=<56位token>
    export TUSHARE_BASE_URL=https://tt.xiaodefa.cn   # 可选，留空用默认
    python examples/tushare_fetch/fetch_financial.py
"""

from __future__ import annotations

import sys

import pandas as pd

from vnpy_tushare import create_pro, load_proxy_url, load_token_script


def main() -> int:
    try:
        token: str = load_token_script()
    except RuntimeError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1

    proxy_url: str = load_proxy_url()
    pro = create_pro(token, proxy_url)

    # 财务指标：贵州茅台最近两期
    fina: pd.DataFrame = pro.fina_indicator(
        ts_code="600519.SH",
        start_date="20240101",
        end_date="20240601",
        fields="ts_code,ann_date,end_date,eps,roe,bps,grossprofit_margin",
    )
    print(f"[fina_indicator] 600519.SH 行数: {len(fina)}")
    print(fina)

    # A 股合约基本信息（上市状态）
    stock_basic: pd.DataFrame = pro.stock_basic(list_status="L", limit=10)
    print(f"[stock_basic] A 股上市行数: {len(stock_basic)}")
    print(stock_basic[["ts_code", "symbol", "name", "exchange", "list_date"]].head())

    # 期货合约基本信息（接口名 fut_basic）
    fut_basic: pd.DataFrame = pro.fut_basic(limit=10)
    print(f"[fut_basic] 行数: {len(fut_basic)}")
    print(fut_basic.head())

    # 指数日线：上证指数
    idx: pd.DataFrame = pro.index_daily(ts_code="000001.SH", start_date="20240101", end_date="20240201")
    print(f"[index_daily] 000001.SH 行数: {len(idx)}")
    print(idx.head())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())