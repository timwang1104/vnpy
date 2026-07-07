"""
示例：通过 tushare + 代理抓取 A 股实时行情三件套（15000 积分高权限 key）。

覆盖：
- realtime_quote   A 股实时行情
- realtime_tick    实时成交数据
- realtime_list    实时涨跌幅排名

实时接口除改写 pro._DataApi__http_url 外，还需改写
tushare.stock.cons.verify_token_url（由 setup_realtime_verify 完成）。

用法：
    export TUSHARE_API_KEY=<56位token>
    export TUSHARE_BASE_URL=https://tt.xiaodefa.cn   # 可选，留空用默认
    python examples/tushare_fetch/fetch_realtime.py
"""

from __future__ import annotations

import sys

import tushare as ts

from vnpy_tushare import (
    create_pro,
    load_proxy_url,
    load_token_script,
    setup_realtime_verify,
)


def main() -> int:
    try:
        token: str = load_token_script()
    except RuntimeError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1

    proxy_url: str = load_proxy_url()
    pro = create_pro(token, proxy_url)
    # 实时接口专用：改写 token 校验地址走代理
    setup_realtime_verify()

    codes: str = "600000.SH,000001.SZ,000001.SH"

    # realtime_quote：A 股实时行情
    quote = ts.realtime_quote(ts_code=codes)
    print(f"[realtime_quote] 行数: {len(quote)}")
    print(quote)

    # realtime_tick：实时成交数据
    tick = ts.realtime_tick(ts_code="600000.SH")
    print(f"[realtime_tick] 行数: {len(tick)}")
    print(tick.head() if hasattr(tick, "head") else tick)

    # realtime_list：实时涨跌幅排名
    # 注意：src='dc' 走 tushare 内部独立 http 端点，第三方代理通常不转发该路径，
    # 连接会被对端关闭。此处容错：失败时打印提示并跳过，不影响前两项结果。
    try:
        rank = ts.realtime_list(src="dc")
        print(f"[realtime_list] 行数: {len(rank)}")
        print(rank.head() if hasattr(rank, "head") else rank)
    except Exception as e:
        print(f"[realtime_list] 跳过：该接口走非代理端点，当前代理不支持（{type(e).__name__}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())